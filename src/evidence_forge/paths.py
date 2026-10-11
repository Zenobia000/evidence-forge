"""資料目錄（`EF_DATA`）在哪裡、裡面有哪些固定位置。

位置規則見 ADR-0014：沒設 `EF_DATA` 就用專案根目錄的 `data/`（`.gitignore` 已排除）。
目錄骨架見架構草案 v0.3 §2.1。其他模組要找檔案，一律從 `DataDir` 拿路徑，不自己拼。
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 專案根目錄：本檔在 src/evidence_forge/ 底下，往上兩層。
# uv sync 以可編輯模式安裝，所以這裡指到的就是原始碼所在的 repo（或 worktree）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DataDir:
    """資料目錄與其中的固定位置。只算路徑；要建出骨架呼叫 `ensure()`。"""

    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def run(self) -> Path:
        """執行期的暫存：Pi 的設定資料夾（`run/pi-agent/`）、Pi 的事件紀錄。不是工單資料。"""
        return self.root / "run"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    def ensure(self) -> None:
        """建出 `inbox/`、`jobs/`、`cache/`、`blobs/`；已存在就略過，可重複呼叫。"""
        for directory in (self.inbox, self.jobs, self.cache, self.blobs):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_data_dir(env: Mapping[str, str] | None = None) -> DataDir:
    """依 `EF_DATA` 決定資料目錄；沒設或是空字串就用專案的 `data/`。

    相對路徑以目前工作目錄為準轉成絕對路徑，之後換目錄也不會指到別處。
    `env` 預設讀 `os.environ`，測試可以傳自己的字典。
    """
    environ = os.environ if env is None else env
    configured = environ.get("EF_DATA", "").strip()
    if not configured:
        return DataDir(_PROJECT_ROOT / "data")
    return DataDir(Path(configured).expanduser().resolve())
