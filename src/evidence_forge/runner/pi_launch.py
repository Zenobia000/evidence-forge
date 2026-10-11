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

skill 的內容用 `--append-system-prompt <SKILL.md>` 直接放進系統提示：實測 Pi 1.1.0 在換掉
系統提示、又沒有內建讀檔工具時，`--skill` 不會讓請求裡出現任何 skill 內容。
"""

import json
import os
import signal
import subprocess
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
) -> PiCommand:
    """組出一段一次的 Pi 指令列與環境變數。純函式，不碰檔案。"""
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


def run_pi(command: PiCommand, *, log_path: Path, timeout_s: float) -> PiRun:
    """執行 Pi，等它結束（或逾時殺掉整個程序群組），回傳結束碼與事件。

    事件原樣存到 `log_path`（在 `$EF_DATA/run/` 底下，不在工單的 `stages/`：
    `stages/` 只有 agent 經閘道寫）。函式回傳時 Pi 一定已經結束，
    呼叫端這時才撤銷鑰匙，前一次的 Pi 不會在撤銷後還拿著鑰匙。
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
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
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
