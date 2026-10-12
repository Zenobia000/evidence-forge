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
    def authors(self) -> Path:
        """作家設定：`authors/<作家>/voice.md` 與 `authors/<作家>/genres/<文類>.md`。"""
        return self.root / "authors"

    @property
    def fetch_cache(self) -> Path:
        """抓網頁的快取：`cache/fetch/<網址 hash>/<抓取時間>.json`，跨工單共用、只增不改。"""
        return self.cache / "fetch"

    @property
    def search_cache(self) -> Path:
        """搜尋的快取：`cache/search/<請求 hash>/<搜尋時間>.json`，跨工單共用、只增不改。"""
        return self.cache / "search"

    @property
    def run(self) -> Path:
        """執行期的暫存：Pi 的設定資料夾（`run/pi-agent/`）、Pi 的事件紀錄、
        正在跑的 Pi 的程序群組編號（`run/pi-pids/`）。不是工單資料。"""
        return self.root / "run"

    @property
    def intake_ledger(self) -> Path:
        """投入時的抓取記在這裡（還沒有工單）；只有閘道寫，月上限的加總包含它（FR-LG-01）。"""
        return self.root / "ledger" / "intake.jsonl"

    @property
    def check_settings(self) -> Path:
        """自動檢查的設定（只寫要蓋掉預設值的那幾項）；不存在就全用預設值。"""
        return self.root / "checks.yaml"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    @property
    def conversations(self) -> Path:
        """對話資料夾：`conversations/<對話 id>/`（#80 spike、ADR-0021 草稿）。"""
        return self.root / "conversations"

    def conversation_dir(self, conversation_id: str) -> Path:
        return self.conversations / conversation_id

    def owner_dir(self, owner_id: str) -> Path:
        """帳本跟著誰：工單 id 是 `jobs/<id>/`，`conv:<id>` 是 `conversations/<id>/`。

        閘道的帳本、預算、快取一律經過這裡找資料夾，對話與工單共用同一套規則。
        """
        if owner_id.startswith("conv:"):
            return self.conversation_dir(owner_id[len("conv:") :])
        return self.job_dir(owner_id)

    def ensure(self) -> None:
        """建出 `inbox/`、`jobs/`、`cache/`、`blobs/`；已存在就略過，可重複呼叫。"""
        for directory in (self.inbox, self.jobs, self.cache, self.blobs):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class JobDir:
    """一張工單的資料夾（`jobs/<id>/`）裡的固定位置。只算路徑，不建資料夾。

    目前只放用到的幾個；其他位置等用到的模組出現再加。
    """

    root: Path

    @property
    def assets_file(self) -> Path:
        """素材清單：素材與授權狀態。"""
        return self.root / "assets" / "assets.json"

    @property
    def checks(self) -> Path:
        """檢查結果 `checks.vN.json` 所在的資料夾。"""
        return self.root / "checks"

    @property
    def provenance(self) -> Path:
        """版本紀錄 `<段>.<第幾次>.json` 所在的資料夾；只有執行器寫，只增不改。

        不放在 `stages/` 底下：那裡只有 agent 經閘道寫（ADR-0010 §3 第 3 點）。
        """
        return self.root / "provenance"


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
