"""閘道的告示板：每張工單此刻在等什麼、這一段有沒有被預算擋下（只在記憶體裡）。

- **等待狀態**（給 S0.6 合進 `job.md` 的進度欄位，NFR-OBS-02）：等 GPU、等限流、等模型回應，
  附開始時間。帳本是呼叫結束才寫，呼叫進行中只有閘道知道。
- **預算擋下**：Pi 收到 `budget_exceeded` 只會結束這一段；執行器在 Pi 結束後來這裡問，
  才分得出是「預算用完」還是「本月額度用完」，結果標 `incomplete` 而不是 `failed`。
- **後端故障**：Ollama 沒開、網路斷、後端回 5xx。Pi 只看到一個錯誤，結束碼可能還是 0；
  執行器在 Pi 結束後來這裡問，有故障就直接標失敗，不重試也不升級（ADR-0011 §3.3、NFR-RUN-05）。

閘道與執行器在同一個 `ef serve` 程序、不同執行緒，所以上鎖。重開 `ef serve` 就清空——
重開之後執行器會從所在段重跑，到時候閘道會再擋一次、再記一次。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from evidence_forge.gateway.budget import BudgetLimit
from evidence_forge.jobkey import JobKey
from evidence_forge.models import Waiting, WaitingKind


@dataclass(frozen=True)
class Outage:
    """一段一次碰到的後端故障：哪一個後端（`ollama`／`anthropic`）、閘道看到的原因。"""

    backend: str
    message: str


class JobBoard:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting: dict[str, Waiting] = {}
        self._budget_stops: dict[JobKey, BudgetLimit] = {}
        self._outages: dict[JobKey, Outage] = {}

    # --- 等待狀態 -------------------------------------------------------------

    def wait(self, job_id: str, kind: WaitingKind) -> None:
        """這張單從現在起在等 `kind`；已經在等同一種就保留原本的開始時間。"""
        with self._lock:
            current = self._waiting.get(job_id)
            if current is None or current.kind != kind:
                self._waiting[job_id] = Waiting(kind=kind, since=self._clock())

    def done(self, job_id: str) -> None:
        with self._lock:
            self._waiting.pop(job_id, None)

    def waiting(self, job_id: str) -> Waiting | None:
        with self._lock:
            return self._waiting.get(job_id)

    # --- 預算擋下 -------------------------------------------------------------

    def stop_for_budget(self, key: JobKey, limit: BudgetLimit) -> None:
        with self._lock:
            # 同一段同一次先被哪一道擋下就記哪一道
            self._budget_stops.setdefault(key, limit)

    def budget_stop(self, key: JobKey) -> BudgetLimit | None:
        """這一段這一次有沒有被預算擋下、擋下的是哪一道上限。"""
        with self._lock:
            return self._budget_stops.get(key)

    # --- 後端故障 -------------------------------------------------------------

    def report_outage(self, key: JobKey, backend: str, message: str) -> None:
        with self._lock:
            # 同一段同一次先碰到哪一個故障就記哪一個
            self._outages.setdefault(key, Outage(backend, message))

    def recovered(self, key: JobKey) -> None:
        """同一段同一次後來有一次成功的呼叫：先前的故障是暫時的，不算數。"""
        with self._lock:
            self._outages.pop(key, None)

    def outage(self, key: JobKey) -> Outage | None:
        """這一段這一次有沒有碰到後端故障。"""
        with self._lock:
            return self._outages.get(key)
