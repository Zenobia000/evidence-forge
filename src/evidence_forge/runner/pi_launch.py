"""組出並執行一段的 Pi 指令（SEAM-01、架構草案 v0.3 §3.3、#10 的結論）。

Pi 本身不做沙箱，工具以 Pi 程序的權限執行（v0.3 §3.3），所以安全邊界只有兩道：
這裡把 Pi 自己的能力全部關掉，剩下的工具（ef-tools）全部轉給閘道檢查。
這份檔的每一個旗標都有理由，刪掉任何一個之前先看 `tests/unit/test_pi_launch.py`：

- `-nc`：不讀上層目錄的 `CLAUDE.md`／`AGENTS.md`（#10 第 4 題：會把 repo 的開發規則讀進去）。
- `--no-builtin-tools`：沒有 shell、沒有內建讀寫檔。但它**不會**移除 MCP 工具，所以還要：
- `--no-mcp`：不連任何 MCP server。
- `-na`：不信任專案底下的 `.pi`。
- `-ne`：不自動載入擴充；`-e` 明確指定的 ef-tools 照樣載得進來。
- `--no-prompt-templates`、`--no-skills`：不自動找提示範本與 skill。
- `PI_CODING_AGENT_DIR`：指到 `$EF_DATA/run/pi-agent/`，不讀使用者 `~/.pi` 裡的 OAuth；
  Pi 每次跑都會在這裡寫 `auth.json`、`models-store.json`，所以不能指到 repo。
- stdin 接 `/dev/null`：Pi 會把 stdin 接在提示詞前面，stdin 開著就一直等。
- `--thinking`：明確指定，不靠模型設定的預設值。
- `EF_WEB_SEARCH=1`：只有配方給了 `tavily_credits` 的段才設，ef-tools 看到才註冊 `web_search`（#69）。
  工具清單是每個模型請求的一部分：沒有搜尋額度的段不多一個用不到的工具，請求也跟以前逐位元組相同。

skill 的內容用 `--append-system-prompt <SKILL.md>` 直接放進系統提示：實測 Pi 1.1.0 在換掉
系統提示、又沒有內建讀檔工具時，`--skill` 不會讓請求裡出現任何 skill 內容。

Pi 開在自己的程序群組（`start_new_session`），`kill -9` 掉 `ef serve` 時它會活下來。
所以 Pi 跑的期間，它的程序群組編號記在 `run/pi-pids/`；重開後重跑同一段同一次時鑰匙跟
當機前一模一樣，執行器登記鑰匙之前先用 `kill_leftover` 殺掉還活著的那一個（SEAM-12）。
"""

import json
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evidence_forge.jobkey import JobKey
from evidence_forge.models import Tier
from evidence_forge.paths import DataDir

# 交給 Pi 的環境變數只有這幾個從外面帶進來；其他（雲端金鑰、代理設定、HOME）一律不帶。
_INHERITED_ENV = ("PATH", "LANG", "LC_ALL", "TZ")

# Pi 的工作目錄固定是根目錄：agent 沒有自己的檔案工具，工作目錄只會出現在系統提示的
# `<cwd>` 裡。固定成同一個值，換台機器、換個資料目錄，送出的請求還是逐位元組相同，
# 閘道的快取與重播才對得上。
PI_CWD = Path("/")


@dataclass(frozen=True)
class PiHarness:
    """repo 裡 Pi 相關的檔案位置。"""

    pi_bin: Path
    system_prompt: Path
    ef_tools: Path
    # 只放範本（models.json）；執行時複製到 $EF_DATA/run/pi-agent/
    agent_template: Path


@dataclass(frozen=True)
class PiCommand:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    # 一律是 /dev/null：開著的 stdin 會讓 Pi 一直等著讀
    stdin: Path


def agent_dir(data: DataDir) -> Path:
    return data.run / "pi-agent"


def prepare_agent_dir(harness: PiHarness, data: DataDir, gateway_url: str) -> Path:
    """把 `models.json` 範本複製到 `$EF_DATA/run/pi-agent/`，填上閘道位址。

    先寫暫存檔再換上去：別的 Pi 程序同時在讀，也只會讀到完整的一份。
    """
    template = json.loads(
        (harness.agent_template / "models.json").read_text(encoding="utf-8")
    )
    template["providers"]["ef"]["baseUrl"] = gateway_url
    target = agent_dir(data)
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / f".models.json.{os.getpid()}.tmp"
    temporary.write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, target / "models.json")
    return target


def build_pi_command(
    *,
    harness: PiHarness,
    data: DataDir,
    key: JobKey,
    tier: Tier,
    thinking: str,
    skill_file: Path,
    gateway_url: str,
    prompt: str,
    base_env: Mapping[str, str],
    web_search: bool = False,
) -> PiCommand:
    """組出一段一次的 Pi 指令列與環境變數。純函式，不碰檔案。

    `web_search`：這一段能不能搜尋（配方這段的 `tavily_credits` 大於 0）。
    """
    argv = (
        str(harness.pi_bin),
        "--print",
        "--mode",
        "json",
        "--no-session",
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
        str(harness.system_prompt),
        "--append-system-prompt",
        str(skill_file),
        "-e",
        str(harness.ef_tools),
        "--",
        prompt,
    )
    pi_agent_dir = agent_dir(data)
    env = {name: base_env[name] for name in _INHERITED_ENV if name in base_env}
    env.update(
        {
            "PI_CODING_AGENT_DIR": str(pi_agent_dir),
            # HOME 也指到 Pi 的設定資料夾：Pi 與 Node 找不到使用者家目錄裡的任何東西
            "HOME": str(pi_agent_dir),
            "EF_GATEWAY_URL": gateway_url,
            "EF_JOB_KEY": str(key),
        }
    )
    if web_search:
        env["EF_WEB_SEARCH"] = "1"
    return PiCommand(argv=argv, env=env, cwd=PI_CWD, stdin=Path(os.devnull))


@dataclass(frozen=True)
class PiRun:
    """一次 Pi 程序的結果。"""

    returncode: int
    timed_out: bool
    stderr_tail: str
    # Pi `--mode json` 每行一個事件；壞掉的行略過
    events: tuple[dict[str, object], ...]

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def pid_file(data: DataDir, key: JobKey) -> Path:
    """這一段這一次的 Pi 的程序群組編號記在哪裡；Pi 結束就刪掉。"""
    return data.run / "pi-pids" / f"{key.job_id}-{key.stage}-{key.attempt}.pid"


def record_pid(path: Path, pgid: int) -> None:
    # 先寫暫存檔再換上去：重開時讀到的一定是完整的編號
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{pgid}\n", encoding="ascii")
    os.replace(temporary, path)


def _holds_key(pid: int, key: JobKey) -> bool:
    """`pid` 這個程序的環境變數裡 `EF_JOB_KEY` 是不是這把鑰匙。

    pid 會被重用：紀錄裡的編號現在可能是別人的程序，對不上就不是我們開的那個 Pi。
    已經死掉、還沒被收屍的程序讀不到環境變數，也算對不上。只有 Linux 有 /proc。
    """
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    return f"EF_JOB_KEY={key}".encode() in environ.split(b"\0")


class LeftoverPi(RuntimeError):
    """前一次的 Pi 送了 SIGKILL 還沒死透；這時登記鑰匙，它的請求會被閘道接受。"""


def kill_leftover(data: DataDir, key: JobKey, *, wait_s: float = 5.0) -> int | None:
    """登記鑰匙之前呼叫：前一個 `ef serve` 被硬殺時留下、還拿著這把鑰匙的 Pi，
    對它整個程序群組送 SIGKILL，等它死透才回來。回傳殺掉的程序群組編號；沒有就是 None。

    只殺環境變數裡 `EF_JOB_KEY` 正是這把鑰匙的程序（`_holds_key`），不照名稱找程序殺：
    同一台機器上別的 worktree、別的 `ef serve` 也有叫 pi 的程序。
    `wait_s` 秒內還沒死透就丟 `LeftoverPi`（工單以失敗進待審），這把鑰匙不登記；
    紀錄留在 `run/pi-pids/`，給人查是哪一個程序。
    """
    path = pid_file(data, key)
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    pgid = int(text) if text.isdigit() else None
    if pgid is None or not _holds_key(pgid, key):
        # 早就結束了，或編號已經是別人的程序
        path.unlink(missing_ok=True)
        return None
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + wait_s
    while _holds_key(pgid, key):
        if time.monotonic() > deadline:
            raise LeftoverPi(f"前一次的 Pi（程序群組 {pgid}）送了 SIGKILL 還沒結束")
        time.sleep(0.01)
    path.unlink(missing_ok=True)
    return pgid


def run_pi(
    command: PiCommand,
    *,
    log_path: Path,
    timeout_s: float,
    pid_path: Path | None = None,
) -> PiRun:
    """執行 Pi，等它結束（或逾時殺掉整個程序群組），回傳結束碼與事件。

    事件原樣存到 `log_path`（在 `$EF_DATA/run/` 底下，不在工單的 `stages/`：
    `stages/` 只有 agent 經閘道寫，ADR-0010 §3 第 3 點；執行器自己的紀錄放在
    `events.jsonl` 與 `provenance/`）。函式回傳時 Pi 一定已經結束，
    呼叫端這時才撤銷鑰匙，前一次的 Pi 不會在撤銷後還拿著鑰匙。
    有給 `pid_path` 的話，Pi 跑的期間它的程序群組編號記在那裡（`pid_file`），結束才刪。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = log_path.with_suffix(".stderr")
    timed_out = False
    with (
        command.stdin.open("rb") as stdin,
        log_path.open("wb") as stdout,
        stderr_path.open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command.argv,
            env=dict(command.env),
            cwd=command.cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        if pid_path is not None:
            # 自己一個 session：程序群組編號就是 Pi 的 pid
            record_pid(pid_path, process.pid)
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    # 走到這裡 Pi 一定已經結束；中途丟例外的話紀錄留著，下次重跑先殺它
    if pid_path is not None:
        pid_path.unlink(missing_ok=True)
    events: list[dict[str, object]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    return PiRun(
        returncode=process.returncode,
        timed_out=timed_out,
        stderr_tail=stderr_text[-2000:],
        events=tuple(events),
    )
