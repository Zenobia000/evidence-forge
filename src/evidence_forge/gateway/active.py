"""目前哪張工單的哪一段、哪一次正在跑，哪個對話開著第幾次的 Pi（SEAM-12、ADR-0021）。

執行器開 Pi 之前登記鑰匙，Pi 結束（或被殺掉）之後撤銷。閘道只接受登記中的那一把：
前一次還沒死透的 Pi、別張工單、已經結束的工單拿舊鑰匙來，一律拒絕。
登記時一併記下這一段用的 skill 資料夾：`run_script` 只准跑那個 skill 的腳本；
這一次是從哪一級升上來的（`escalated_from`）也由執行器在這裡告訴閘道，閘道照著記帳（ADR-0011 §3.3）。
執行器與閘道在同一個 `ef serve` 程序裡，共用這一個物件；閘道在另一條執行緒，所以要上鎖。
"""

import threading
from dataclasses import dataclass
from pathlib import Path

from evidence_forge.convkey import Principal


@dataclass(frozen=True)
class _Entry:
    key: Principal
    skill_dir: Path | None
    escalated_from: str | None


class ActiveStages:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 一張工單同一時間只會有一段一次在跑
        self._by_job: dict[str, _Entry] = {}

    def activate(
        self,
        key: Principal,
        skill_dir: Path | None = None,
        escalated_from: str | None = None,
    ) -> None:
        """登記這一段這一次（與它的 skill 資料夾、升級前的等級）；同一張單之前登記的鑰匙同時失效。"""
        with self._lock:
            self._by_job[key.job_id] = _Entry(key, skill_dir, escalated_from)

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
