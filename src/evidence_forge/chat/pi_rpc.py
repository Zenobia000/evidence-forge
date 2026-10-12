"""一個對話 = 一個長駐的 Pi RPC 程序（#80 spike、ADR-0019 §3.1）。

指令列沿用段的 Pi 的所有封鎖旗標（`runner/pi_launch.py` 的說明），差別只有：

- `--mode rpc`，stdin／stdout 是 JSONL 指令與事件；沒有 `--print`、沒有 `--no-session`。
- `--session-dir <對話資料夾>/pi-session --session-id <對話 id>`：第一次開是新的，之後開就接回同一份紀錄。
- 鑰匙是對話鑰匙 `conv:<id>:<第幾次開>`，`EF_TOOLSET=conversation` 讓 ef-tools 只註冊對話的工具。
- 系統提示是 `harness/pi/chat-system-prompt.md`，不帶 skill。

主機（`ef chat`）死掉時 Pi 的 stdin 會被關掉，Pi 照 RPC 的規矩自己收掉（rpc.md〈Shutdown〉），
所以不用像段的 Pi 那樣記程序群組編號給下一次殺。
"""

import json
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import Harness
from evidence_forge.paths import DataDir
from evidence_forge.runner.pi_launch import PI_CWD, agent_dir

_log = logging.getLogger(__name__)

_INHERITED_ENV = ("PATH", "LANG", "LC_ALL", "TZ")


@dataclass(frozen=True)
class RpcCommand:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


def session_dir(data: DataDir, conversation_id: str) -> Path:
    return data.conversation_dir(conversation_id) / "pi-session"


def chat_system_prompt(harness: Harness) -> Path:
    return harness.root / "pi" / "chat-system-prompt.md"


def build_rpc_command(
    *,
    harness: Harness,
    data: DataDir,
    key: ConversationKey,
    gateway_url: str,
    base_env: Mapping[str, str],
    tier: str = "strong",
    thinking: str = "low",
) -> RpcCommand:
    """組出對話的 Pi 指令列與環境變數。純函式，不碰檔案。"""
    argv = (
        str(harness.pi.pi_bin),
        "--mode",
        "rpc",
        "--session-dir",
        str(session_dir(data, key.conversation_id)),
        "--session-id",
        key.conversation_id,
        "-nc",
        "--no-builtin-tools",
        "--no-mcp",
        "-na",
        "-ne",
        "--no-prompt-templates",
        "--no-skills",
        "--offline",
        "--model",
        f"ef/{tier}",
        "--thinking",
        thinking,
        "--api-key",
        str(key),
        "--system-prompt",
        str(chat_system_prompt(harness)),
        "-e",
        str(harness.pi.ef_tools),
    )
    pi_agent_dir = agent_dir(data)
    env = {name: base_env[name] for name in _INHERITED_ENV if name in base_env}
    env.update(
        {
            "PI_CODING_AGENT_DIR": str(pi_agent_dir),
            "HOME": str(pi_agent_dir),
            "EF_GATEWAY_URL": gateway_url,
            "EF_JOB_KEY": str(key),
            "EF_TOOLSET": "conversation",
        }
    )
    return RpcCommand(argv=argv, env=env, cwd=PI_CWD)


class PiConversation:
    """開著的一個對話 Pi：送指令、收事件、等指令的回應、關掉。

    事件由讀取執行緒交給 `on_event`（在那條執行緒上呼叫，要快）。
    Pi 結束（被殺、自己掛掉）時撤銷鑰匙，並送一個 `{"type": "ef_pi_exit"}` 事件。
    """

    def __init__(
        self,
        command: RpcCommand,
        key: ConversationKey,
        stages: ActiveStages,
        on_event: Callable[[dict[str, Any]], None],
        stderr_path: Path,
    ) -> None:
        self.key = key
        self._command = command
        self._stages = stages
        self._on_event = on_event
        self._stderr_path = stderr_path
        self._process: subprocess.Popen[bytes] | None = None
        self._write_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._ids = 0

    def start(self) -> None:
        # 先登記鑰匙再開 Pi：Pi 一起來就可能打閘道
        self._stages.activate(self.key)
        self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr = self._stderr_path.open("ab")
        try:
            self._process = subprocess.Popen(
                self._command.argv,
                env=dict(self._command.env),
                cwd=self._command.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
            )
        except BaseException:
            self._stages.deactivate(self.key)
            raise
        finally:
            stderr.close()
        threading.Thread(target=self._read, daemon=True).start()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def send(self, command: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Pi 沒有開")
        line = (json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    def request(self, command: dict[str, Any], timeout_s: float = 10.0) -> dict[str, Any]:
        """送一個指令並等它的 `response`（照 id 對；Pi 的回應不保證照送出的順序）。"""
        with self._write_lock:
            self._ids += 1
            request_id = f"ef-{self._ids}"
        done = threading.Event()
        box: list[dict[str, Any]] = []
        self._pending[request_id] = (done, box)
        try:
            self.send({**command, "id": request_id})
            if not done.wait(timeout_s) or not box:
                raise TimeoutError(f"Pi 沒有回應 {command.get('type')}")
            return box[0]
        finally:
            self._pending.pop(request_id, None)

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stdout: IO[bytes] = process.stdout
        # 二進位逐行讀只在 LF 切開，JSON 字串裡的 U+2028 不會被切斷（rpc.md〈Framing〉）
        for raw in stdout:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "response":
                waiting = self._pending.get(str(record.get("id")))
                if waiting is not None:
                    waiting[1].append(record)
                    waiting[0].set()
                    continue
            try:
                self._on_event(record)
            except Exception:
                _log.exception("對話事件處理失敗")
        returncode = process.wait()
        # 還在等回應的 request 不要空等到逾時：叫醒它們，它們會看到沒有回應
        for done, _ in list(self._pending.values()):
            done.set()
        # Pi 已經結束才撤銷：撤銷之後不會再有這把鑰匙的請求
        self._stages.deactivate(self.key)
        self._on_event({"type": "ef_pi_exit", "returncode": returncode})

    def stop(self, wait_s: float = 5.0) -> None:
        """關 stdin 請 Pi 自己收掉；等不到就殺整個程序群組。"""
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def kill(self) -> None:
        """模擬當機：直接殺掉整個程序群組（spike 的「殺掉再接回」用）。"""
        process = self._process
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
