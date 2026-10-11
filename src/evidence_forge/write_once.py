"""只增不改的寫檔工具（NFR-KEEP-01、架構草案 v0.3 §2.2）。

產出、稿子、決定都是「新增一個版本」，從不覆寫。這裡的兩支工具是唯一的寫法：

- `write_once(path, data)`：用獨佔模式建檔，檔案已經存在就丟 `FileExistsError`。
- `write_next_version(directory, stem, suffix, data)`：寫成 `<stem>.v<N><suffix>`，
  N 是目前最大號加一；同一刻有別的程序搶到同一號，就換下一號，直到搶到為止。

注意：獨佔建檔只保證「不會兩個人拿到同一個檔名」，不保證別人讀到時已經寫完。
讀取別人剛寫的版本檔（例如執行器讀 `review/decision.vN.json`）要能容忍內容還不完整，
下一輪掃描再讀一次。
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VersionedFile:
    """`write_next_version` 搶到的版本號與實際的檔案路徑。"""

    version: int
    path: Path


def write_once(path: Path, data: str | bytes) -> None:
    """建立 `path` 並寫入 `data`；檔案已存在就丟 `FileExistsError`，原檔不動。

    文字一律用 UTF-8、不轉換換行。上層目錄不存在會自動建立。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    # "xb"：O_CREAT | O_EXCL，檔名的判斷與建立是作業系統的一個原子動作。
    with path.open("xb") as handle:
        handle.write(payload)


def _version_pattern(stem: str, suffix: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(stem)}\.v([1-9][0-9]*){re.escape(suffix)}")


def latest_version(directory: Path, stem: str, suffix: str) -> int:
    """`directory` 裡 `<stem>.v<N><suffix>` 的最大 N；一份都沒有（或目錄不存在）是 0。"""
    if not directory.is_dir():
        return 0
    pattern = _version_pattern(stem, suffix)
    versions = [
        int(match.group(1))
        for entry in directory.iterdir()
        if (match := pattern.fullmatch(entry.name))
    ]
    return max(versions, default=0)


def write_next_version(
    directory: Path, stem: str, suffix: str, data: str | bytes
) -> VersionedFile:
    """把 `data` 寫成下一個版本，回傳搶到的版本號與路徑。

    撞號（別的程序剛好先建了同一號）就往下一號試，所以多個程序同時寫，
    每一號只會有一個程序拿到，號碼也不會跳。
    """
    # 目錄先在迴圈外建好：這裡的 FileExistsError（位置被檔案佔住）是設定錯誤，
    # 要直接丟出去；迴圈裡的 FileExistsError 才是「這一號被搶走了」。
    directory.mkdir(parents=True, exist_ok=True)
    version = latest_version(directory, stem, suffix) + 1
    while True:
        path = directory / f"{stem}.v{version}{suffix}"
        try:
            write_once(path, data)
        except FileExistsError:
            version += 1
            continue
        return VersionedFile(version=version, path=path)
