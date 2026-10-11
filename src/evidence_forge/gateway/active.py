"""目前哪張工單的哪一段、哪一次正在跑（SEAM-12）。

執行器開 Pi 之前登記鑰匙，Pi 結束（或被殺掉）之後撤銷。閘道只接受登記中的那一把：
前一次還沒死透的 Pi、別張工單、已經結束的工單拿舊鑰匙來，一律拒絕。
執行器與閘道在同一個 `ef serve` 程序裡，共用這一個物件；閘道在另一條執行緒，所以要上鎖。
"""

import threading

from evidence_forge.jobkey import JobKey


class ActiveStages:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 一張工單同一時間只會有一段一次在跑
        self._by_job: dict[str, JobKey] = {}

    def activate(self, key: JobKey) -> None:
        """登記這一段這一次；同一張單之前登記的鑰匙同時失效。"""
        with self._lock:
            self._by_job[key.job_id] = key

    def deactivate(self, key: JobKey) -> None:
        with self._lock:
            if self._by_job.get(key.job_id) == key:
                del self._by_job[key.job_id]

    def is_active(self, key: JobKey) -> bool:
        with self._lock:
            return self._by_job.get(key.job_id) == key
