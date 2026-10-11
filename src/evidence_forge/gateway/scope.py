"""agent 的工具能碰哪些檔（SEAM-02、SEAM-12、NFR-SEC-02）。

agent 給的路徑一律相對於工單資料夾。規則：

- 絕對路徑、`..`、反斜線、空字串：直接拒絕，不做「正規化之後還在裡面就放行」。
- 路徑上任何一節是符號連結：拒絕（連結可能指到工單資料夾外）。
- 讀與列：工單資料夾內都可以。
- 寫：只准 `stages/<這一段>/<這一次>/` 底下，而且檔案已存在就拒絕（只增不改）。
  `review/`、`job.md`、`events.jsonl`、`ledger.jsonl`、別段、別次都不行——
  不然原文裡的提示注入就能寫出審稿決定，讓工單自己核准自己。
"""

from pathlib import Path, PurePosixPath

from evidence_forge.jobkey import JobKey
from evidence_forge.write_once import write_once

# 讀檔上限：agent 讀的是文章與各段產出，超過這個大小多半是讀錯檔。
MAX_READ_BYTES = 1_000_000


class ToolRejected(Exception):
    """工具呼叫被拒；`status` 是要回給 ef-tools 的 HTTP 狀態碼。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _parts(relative: str) -> tuple[str, ...]:
    if not relative or "\x00" in relative:
        raise ToolRejected(400, "路徑是空的")
    if "\\" in relative:
        raise ToolRejected(400, "路徑不能有反斜線，用 / 分隔")
    pure = PurePosixPath(relative)
    if pure.is_absolute():
        raise ToolRejected(403, "只能用相對於工單資料夾的路徑")
    if ".." in pure.parts:
        raise ToolRejected(403, "路徑不能有 ..")
    return pure.parts


def _resolve(job_dir: Path, parts: tuple[str, ...]) -> Path:
    """一節一節往下走，碰到符號連結就拒絕。"""
    current = job_dir
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ToolRejected(403, "路徑經過符號連結")
    return current


def read_file(job_dir: Path, relative: str) -> str:
    target = _resolve(job_dir, _parts(relative))
    if not target.is_file():
        raise ToolRejected(404, f"找不到檔案：{relative}")
    if target.stat().st_size > MAX_READ_BYTES:
        raise ToolRejected(413, f"檔案超過 {MAX_READ_BYTES} bytes：{relative}")
    return target.read_text(encoding="utf-8", errors="replace")


def list_files(job_dir: Path, relative: str) -> list[str]:
    parts = () if relative in ("", ".") else _parts(relative)
    target = _resolve(job_dir, parts)
    if not target.is_dir():
        raise ToolRejected(404, f"找不到資料夾：{relative}")
    return sorted(
        f"{entry.name}/" if entry.is_dir() and not entry.is_symlink() else entry.name
        for entry in target.iterdir()
    )


def stage_output_dir(key: JobKey) -> tuple[str, ...]:
    """這一段這一次唯一能寫的地方，相對於工單資料夾。"""
    return ("stages", key.stage, str(key.attempt))


def write_file(job_dir: Path, key: JobKey, relative: str, content: str) -> str:
    """寫一個新檔到這一段這一次的輸出目錄；回傳寫入的相對路徑。"""
    parts = _parts(relative)
    allowed = stage_output_dir(key)
    if parts[: len(allowed)] != allowed or len(parts) <= len(allowed):
        raise ToolRejected(
            403, f"只能寫到 {'/'.join(allowed)}/ 底下，不能寫 {relative}"
        )
    target = _resolve(job_dir, parts)
    try:
        # 獨佔建立：檔案（或同名的符號連結）已存在就失敗，原檔不動
        write_once(target, content)
    except FileExistsError:
        raise ToolRejected(409, f"檔案已存在，不能覆寫：{relative}") from None
    return "/".join(parts)
