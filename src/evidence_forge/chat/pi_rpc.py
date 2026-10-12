"""一個對話 = 一個長駐的 Pi RPC 程序（ADR-0019 §3.1、#80 spike）。

指令列沿用段的 Pi 的所有封鎖旗標（`runner/pi_launch.py` 的說明），差別只有：

- `--mode rpc`，stdin／stdout 是 JSONL 指令與事件；沒有 `--print`、沒有 `--no-session`。
- `--session-dir <對話資料夾>/pi-session --session-id <對話 id>`：第一次開是新的，之後開就接回同一份紀錄。
- 鑰匙是對話鑰匙 `conv:<id>:<第幾次開>`，`EF_TOOLSET=conversation` 讓 ef-tools 只註冊對話的工具。
- 系統提示是 `harness/pi/chat-system-prompt.md`，不帶 skill。

不留孤兒程序靠兩道：
1. 主機（`ef serve`）死掉時 Pi 的 stdin 被關，Pi 照 RPC 的規矩自己收掉（rpc.md〈Shutdown〉，spike 實測約 1 秒）。
2. 萬一它沒收掉（例如卡住），程序群組編號記在 `run/chat-pids/<對話 id>.pid`；
   下次開這個對話（或主機重開）之前，`kill_leftover` 只殺環境變數裡拿著這個對話鑰匙的那一群。
"""

import itertools
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from evidence_forge.convkey import PREFIX, ConversationKey
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import Harness
from evidence_forge.paths import DataDir
from evidence_forge.runner.pi_launch import PI_CWD, agent_dir, record_pid

_log = logging.getLogger(__name__)

# 交給 Pi 的環境變數只有這幾個從外面帶進來；雲端金鑰、代理設定、HOME 一律不帶（同 pi_launch）
_INHERITED_ENV = ("PATH", "LANG", "LC_ALL", "TZ")

Record = dict[str, Any]


@dataclass(frozen=True)
class RpcCommand:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


def session_dir(data: DataDir, conversation_id: str) -> Path:
    return data.conversation_dir(conversation_id) / "pi-session"


def chat_system_prompt(harness: Harness) -> Path:
    return harness.root / "pi" / "chat-system-prompt.md"


def pid_file(data: DataDir, conversation_id: str) -> Path:
    return data.run / "chat-pids" / f"{conversation_id}.pid"


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


def _holds_conversation(pid: int, conversation_id: str) -> bool:
    """`pid` 的環境變數裡拿著這個對話（任何一次開）的鑰匙。pid 會被重用，所以不照名稱認。"""
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    prefix = f"EF_JOB_KEY={PREFIX}{conversation_id}:".encode()
    return any(entry.startswith(prefix) for entry in environ.split(b"\0"))


def kill_leftover(data: DataDir, conversation_id: str, *, wait_s: float = 5.0) -> int | None:
    """前一個主機留下、還活著的這個對話的 Pi：整個程序群組送 SIGKILL，等它死透。

    回傳殺掉的程序群組編號；沒有就是 None。`wait_s` 秒內還沒死透就丟 `RuntimeError`，
    這時不能開新的 Pi（舊的鑰匙雖然會被閘道拒絕，但兩個 Pi 會搶同一份對話紀錄）。
    """
    path = pid_file(data, conversation_id)
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    pgid = int(text) if text.isdigit() else None
    if pgid is None or not _holds_conversation(pgid, conversation_id):
        path.unlink(missing_ok=True)
        return None
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + wait_s
    while _holds_conversation(pgid, conversation_id):
        if time.monotonic() > deadline:
            raise RuntimeError(f"對話 {conversation_id} 前一次的 Pi（程序群組 {pgid}）殺不掉")
        time.sleep(0.01)
    path.unlink(missing_ok=True)
    return pgid


class PiExited(RuntimeError):
    """要送指令或等回應時，Pi 已經結束了。"""


class PiProcess:
    """開著的一個對話 Pi：送指令、照 `id` 對回應、把事件交出去、關掉。

    - 每個指令都帶 `id`（`ef-<n>`）。Pi 的回應不保證照送出的順序回來（擴充指令會插隊），
      所以 `request` 照 `id` 等自己那一個；沒有人在等的回應照樣交給 `on_event`。
    - 事件由讀取執行緒交給 `on_event`（在那條執行緒上呼叫，要快）。
    - Pi 結束（關掉、被殺、自己掛掉）時：叫醒所有在等回應的人、撤銷鑰匙、刪掉程序編號紀錄，
      最後呼叫 `on_exit(結束碼)`。
    """

    def __init__(
        self,
        command: RpcCommand,
        key: ConversationKey,
        stages: ActiveStages,
        *,
        on_event: Callable[[Record], None],
        on_exit: Callable[[int], None],
        stderr_path: Path,
        pid_path: Path,
    ) -> None:
        self.key = key
        self._command = command
        self._stages = stages
        self._on_event = on_event
        self._on_exit = on_exit
        self._stderr_path = stderr_path
        self._pid_path = pid_path
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, list[Record]]] = {}
        self._ids = itertools.count(1)
        self._exited = threading.Event()

    def start(self) -> None:
        """先登記鑰匙再開 Pi：Pi 一起來就可能打閘道。

        鑰匙比這個對話登記過的舊就丟 `StaleKey`（ADR-0021 §3.1），Pi 不開。
        """
        self._stages.activate(self.key)
        self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._stderr_path.open("ab") as stderr:
                self._process = subprocess.Popen(
                    self._command.argv,
                    env=dict(self._command.env),
                    cwd=self._command.cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    # 自己一個程序群組：關掉時連它開的子程序一起收；程序群組編號就是 pid
                    start_new_session=True,
                )
        except BaseException:
            self._stages.deactivate(self.key)
            raise
        record_pid(self._pid_path, self._process.pid)
        self._reader = threading.Thread(
            target=self._read, name=f"pi-{self.key}", daemon=True
        )
        self._reader.start()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def alive(self) -> bool:
        return (
            self._process is not None
            and self._process.poll() is None
            and not self._exited.is_set()
        )

    def send(self, command: Record) -> str:
        """送一個指令，不等回應；回傳它的 `id`。Pi 已經結束就丟 `PiExited`。"""
        request_id = f"ef-{next(self._ids)}"
        self._write(command, request_id)
        return request_id

    def request(self, command: Record, timeout_s: float = 15.0) -> Record:
        """送一個指令並等它的 `response`（照 `id` 對）。"""
        request_id = f"ef-{next(self._ids)}"
        done = threading.Event()
        box: list[Record] = []
        # 先登記再送：回應可能在寫完之前就到
        with self._pending_lock:
            self._pending[request_id] = (done, box)
        try:
            self._write(command, request_id)
            if not done.wait(timeout_s):
                raise TimeoutError(f"Pi 沒有回應 {command.get('type')}")
            if not box:
                raise PiExited(f"{self.key} 的 Pi 在回應 {command.get('type')} 之前結束了")
            return box[0]
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _write(self, command: Record, request_id: str) -> None:
        process = self._process
        if process is None or process.stdin is None or self._exited.is_set():
            raise PiExited(f"{self.key} 的 Pi 沒有開")
        line = json.dumps({**command, "id": request_id}, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                process.stdin.write(line.encode("utf-8"))
                process.stdin.flush()
            except (ValueError, OSError) as exc:
                # 關掉的 stdin 丟 ValueError，Pi 已經死掉丟 BrokenPipeError（OSError 的一種）
                raise PiExited(f"{self.key} 的 Pi 已經結束：{exc}") from None

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
                with self._pending_lock:
                    waiting = self._pending.get(str(record.get("id")))
                if waiting is not None:
                    waiting[1].append(record)
                    waiting[0].set()
                    continue
            try:
                self._on_event(record)
            except Exception:
                _log.exception("對話 %s 的事件處理失敗", self.key.conversation_id)
        returncode = process.wait()
        self._exited.set()
        # 還在等回應的不要空等到逾時：叫醒它們，它們會看到沒有回應
        with self._pending_lock:
            waiting_all = list(self._pending.values())
        for done, _ in waiting_all:
            done.set()
        # Pi 已經結束才撤銷：撤銷之後不會再有這把鑰匙的請求
        self._stages.deactivate(self.key)
        self._pid_path.unlink(missing_ok=True)
        try:
            self._on_exit(returncode)
        except Exception:
            _log.exception("對話 %s 的 Pi 結束處理失敗", self.key.conversation_id)

    def stop(self, wait_s: float = 5.0) -> None:
        """關 stdin 請 Pi 自己收掉；等不到就殺整個程序群組。

        回來的時候 Pi 一定已經結束、鑰匙已經撤銷：接回時照這個順序（先收舊的、再登記新的）。
        """
        process = self._process
        if process is None:
            return
        with self._write_lock:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
        try:
            process.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            self.kill()
        self.join()

    def kill(self) -> None:
        """直接殺掉整個程序群組（關不掉時用；測試用它模擬當機）。"""
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    def join(self, timeout_s: float = 10.0) -> None:
        """等讀取執行緒收完尾（撤銷鑰匙、呼叫 `on_exit`）。"""
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout_s)
        # 讀取執行緒沒收完（例如 Pi 的子程序還拿著 stdout）也要撤銷：鑰匙不能比 Pi 活得久
        self._stages.deactivate(self.key)
