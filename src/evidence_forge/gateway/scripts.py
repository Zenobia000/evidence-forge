"""`run_script`：執行這一段 skill 自己的腳本（v0.3 §3.2、§3.4）。

規則：
- 只准跑這一段登記的 skill 資料夾裡、`SKILL.md` frontmatter 的 `scripts:` 清單上的腳本。
  清單只寫檔名（例：`split.py`），腳本放在 `<skill>/scripts/`。
- 名字只能是單純的檔名（不能有 `/`、`..`），而且那個檔不能是符號連結。
- 不經 shell：直接把 `[python, 腳本, *參數]` 交給作業系統，參數裡的 `$HOME`、`;` 都只是字。
- 工作目錄是這張工單的資料夾，讓腳本讀得到 `input/` 與前面各段的產出。
- 環境變數只給最少的幾個：閘道手上的金鑰不會傳給腳本（NFR-SEC-04）。
- 有逾時：超過就把整個行程群組殺掉，結果標 `timed_out`。
"""

import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from evidence_forge.documents import FrontmatterError, read_frontmatter
from evidence_forge.gateway.scope import ToolRejected

DEFAULT_TIMEOUT_S = 60.0
# 回給 agent 的輸出上限（字元）：腳本的輸出是給模型看的，太長只會塞爆上下文
MAX_OUTPUT_CHARS = 200_000
MAX_ARGS = 32

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.py$")


@dataclass(frozen=True)
class ScriptResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")


def allowed_scripts(skill_dir: Path) -> tuple[str, ...]:
    """`SKILL.md` frontmatter 的 `scripts:` 清單；沒寫就是空的。"""
    try:
        listed = read_frontmatter(skill_dir / "SKILL.md").get("scripts", [])
    except (OSError, FrontmatterError):
        return ()
    if not isinstance(listed, list):
        return ()
    return tuple(
        name for name in listed if isinstance(name, str) and _NAME.fullmatch(name)
    )


def resolve_script(skill_dir: Path | None, name: str) -> Path:
    """確認腳本在清單上、就在 `<skill>/scripts/` 底下、不是符號連結；不通過丟 `ToolRejected(403)`。"""
    if skill_dir is None:
        raise ToolRejected(403, "這一段沒有登記 skill，不能跑腳本")
    if not _NAME.fullmatch(name) or name not in allowed_scripts(skill_dir):
        raise ToolRejected(403, f"{name} 不在這個 skill 的腳本清單上")
    scripts = skill_dir / "scripts"
    path = scripts / name
    if scripts.is_symlink() or path.is_symlink() or not path.is_file():
        raise ToolRejected(403, f"{name} 不是 skill 資料夾裡的一般檔案")
    return path


def _clip(text: str) -> str:
    return (
        text
        if len(text) <= MAX_OUTPUT_CHARS
        else text[:MAX_OUTPUT_CHARS] + "\n…（後面截掉）"
    )


def run_script(
    script: Path, args: list[str], stdin: str, cwd: Path, timeout_s: float
) -> ScriptResult:
    if len(args) > MAX_ARGS or any("\x00" in arg for arg in args):
        raise ToolRejected(400, f"參數最多 {MAX_ARGS} 個，而且不能有 NUL 字元")
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }
    # -E 不讀 PYTHON* 環境變數，-s 不載入使用者的 site-packages
    command = [sys.executable, "-E", "-s", str(script), *args]
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # 自己一個行程群組：逾時時連腳本開的子行程一起殺
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            return ScriptResult(None, _clip(stdout), _clip(stderr), timed_out=True)
    return ScriptResult(
        process.returncode, _clip(stdout), _clip(stderr), timed_out=False
    )
