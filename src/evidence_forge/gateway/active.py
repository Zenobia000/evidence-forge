"""目前哪張工單的哪一段、哪一次正在跑，哪個對話開著第幾次的 Pi（SEAM-12、ADR-0021）。

執行器開 Pi 之前登記鑰匙，Pi 結束（或被殺掉）之後撤銷。閘道只接受登記中的那一把：
前一次還沒死透的 Pi、別張工單、已經結束的工單拿舊鑰匙來，一律拒絕。
登記時一併記下這一段用的 skill 資料夾：`run_script` 只准跑那個 skill 的腳本；
這一次是從哪一級升上來的（`escalated_from`）也由執行器在這裡告訴閘道，閘道照著記帳（ADR-0011 §3.3）。
這一段能不能上網（`online`：配方給了這一段搜尋額度）也在這裡登記：不能上網的段，閘道拒絕
`fetch_url` 與 `web_search`。例：重構段不准讀原文全文，原文網址就在任務說明裡（#92 審查）。
執行器與閘道在同一個 `ef serve` 程序裡，共用這一個物件；閘道在另一條執行緒，所以要上鎖。
"""

import threading
from dataclasses import dataclass
from pathlib import Path

from evidence_forge.convkey import ConversationKey, Principal


class StaleKey(ValueError):
    """對話鑰匙比這個對話登記過的還舊（或一樣）：延遲到的前一次開 Pi，不准登記。"""


@dataclass(frozen=True)
class _Entry:
    key: Principal
    skill_dir: Path | None
    escalated_from: str | None
    online: bool


class ActiveStages:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 一張工單同一時間只會有一段一次在跑
        self._by_job: dict[str, _Entry] = {}
        # 每個對話登記過最大的第幾次開；撤銷之後也記著，舊的那一把永遠登記不回來
        self._latest_launch: dict[str, int] = {}

    def activate(
        self,
        key: Principal,
        skill_dir: Path | None = None,
        escalated_from: str | None = None,
        online: bool = False,
    ) -> None:
        """登記這一段這一次（與它的 skill 資料夾、升級前的等級、能不能上網）；
        同一張單之前登記的鑰匙同時失效。沒說能上網就是不能（預設關著，少給權限）。"""
        with self._lock:
            if isinstance(key, ConversationKey):
                # 只認最新那一把（ADR-0021 §3.1）：第 2 次開先登記好、延遲的第 1 次開才來，
                # 直接覆蓋的話舊鑰匙就復活了
                latest = self._latest_launch.get(key.conversation_id, 0)
                if key.launch <= latest:
                    raise StaleKey(
                        f"對話 {key.conversation_id} 已經登記過第 {latest} 次開，"
                        f"不能再登記第 {key.launch} 次"
                    )
                self._latest_launch[key.conversation_id] = key.launch
            self._by_job[key.job_id] = _Entry(key, skill_dir, escalated_from, online)

    def deactivate(self, key: Principal) -> None:
        with self._lock:
            entry = self._by_job.get(key.job_id)
            if entry is not None and entry.key == key:
                del self._by_job[key.job_id]

    def _entry(self, key: Principal) -> _Entry | None:
        with self._lock:
            entry = self._by_job.get(key.job_id)
            return entry if entry is not None and entry.key == key else None

    def is_active(self, key: Principal) -> bool:
        return self._entry(key) is not None

    def skill_dir(self, key: Principal) -> Path | None:
        """這把鑰匙登記的 skill 資料夾；鑰匙不在登記中或沒登記 skill 都是 None。"""
        entry = self._entry(key)
        return entry.skill_dir if entry is not None else None

    def escalated_from(self, key: Principal) -> str | None:
        """這一次是從哪一級升上來的；沒升級、或鑰匙不在登記中都是 None。"""
        entry = self._entry(key)
        return entry.escalated_from if entry is not None else None

    def online(self, key: JobKey) -> bool:
        """這一段能不能用 `fetch_url`、`web_search`；鑰匙不在登記中也是不能。"""
        entry = self._entry(key)
        return entry is not None and entry.online
