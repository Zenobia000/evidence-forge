"""閘道的告示板：每張工單此刻在等什麼、這一段有沒有被預算擋下（只在記憶體裡）。

- **等待狀態**（給 S0.6 合進 `job.md` 的進度欄位，NFR-OBS-02）：等 GPU、等限流、等模型回應，
  附開始時間。帳本是呼叫結束才寫，呼叫進行中只有閘道知道。
- **預算擋下**：Pi 收到 `budget_exceeded` 只會結束這一段；執行器在 Pi 結束後來這裡問，
  才分得出是「預算用完」還是「本月額度用完」，結果標 `incomplete` 而不是 `failed`。
- **後端故障**：Ollama 沒開、網路斷、後端回 5xx、200 的串流裡送 error 事件（過載）；
  搜尋的 Tavily 出錯、沒回應、金鑰不能用、限流到重試用完（`search.py`）。
  Pi 只看到一個錯誤，結束碼可能還是 0；執行器在 Pi 結束後來這裡問，
  這一段沒交出合格產出又碰到故障，就直接標失敗，不重試也不升級（ADR-0011 §3.3、NFR-RUN-05）。
  每個後端各記各的：模型呼叫成功只說明模型後端好了，不能洗掉 Tavily 的故障。
- **上下文放不下**：閘道估起來本機模型讀不下這個提示，沒有轉送（ADR-0022）。這不是故障，
  執行器照樣當成這一次不合格；但同一份輸入在同一級再試也一樣放不下，所以直接升級鏈的下一級。

閘道與執行器在同一個 `ef serve` 程序、不同執行緒，所以上鎖。重開 `ef serve` 就清空——
重開之後執行器會從所在段重跑，到時候閘道會再擋一次、再記一次。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from evidence_forge.gateway.budget import BudgetLimit
from evidence_forge.convkey import Principal
from evidence_forge.models import Waiting, WaitingKind

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outage:
    """一段一次碰到的後端故障：哪一個後端（`ollama`／`anthropic`／`tavily`）、閘道看到的原因。"""

    backend: str
    message: str


class JobBoard:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting: dict[str, Waiting] = {}
        self._budget_stops: dict[Principal, BudgetLimit] = {}
        # 段與次 → 後端 → 故障（照碰到的先後）
        self._outages: dict[Principal, dict[str, Outage]] = {}
        # 等待狀態變了要通知誰（對話主機推進度用，#85）
        self._listeners: list[Callable[[str], None]] = []
        # 段與次 → 閘道擋下的原因（本機上下文放不下）
        self._too_long: dict[Principal, str] = {}

    def listen(self, listener: Callable[[str], None]) -> None:
        """等待狀態一變（開始等、換一種等、等完）就呼叫 `listener(擁有者 id)`。

        在閘道的事件迴圈或執行緒上呼叫，不能卡住：只該把 id 丟進佇列。
        `done` 在帳本寫完之後才呼叫，所以聽到「等完」時這次呼叫的花費已經進帳本。
        """
        with self._lock:
            self._listeners.append(listener)

    def _notify(self, job_id: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(job_id)
            except Exception:
                _log.warning("告示板的變動通知失敗", exc_info=True)

    # --- 等待狀態 -------------------------------------------------------------

    def wait(self, job_id: str, kind: WaitingKind) -> None:
        """這張單從現在起在等 `kind`；已經在等同一種就保留原本的開始時間。"""
        with self._lock:
            current = self._waiting.get(job_id)
            changed = current is None or current.kind != kind
            if changed:
                self._waiting[job_id] = Waiting(kind=kind, since=self._clock())
        if changed:
            self._notify(job_id)

    def done(self, job_id: str) -> None:
        with self._lock:
            was_waiting = self._waiting.pop(job_id, None) is not None
        if was_waiting:
            self._notify(job_id)

    def waiting(self, job_id: str) -> Waiting | None:
        with self._lock:
            return self._waiting.get(job_id)

    # --- 預算擋下 -------------------------------------------------------------

    def stop_for_budget(self, key: Principal, limit: BudgetLimit) -> None:
        with self._lock:
            # 同一段同一次先被哪一道擋下就記哪一道
            self._budget_stops.setdefault(key, limit)

    def budget_stop(self, key: Principal) -> BudgetLimit | None:
        """這一段這一次有沒有被預算擋下、擋下的是哪一道上限。"""
        with self._lock:
            return self._budget_stops.get(key)

    # --- 後端故障 -------------------------------------------------------------

    def report_outage(self, key: Principal, backend: str, message: str) -> None:
        with self._lock:
            # 同一段同一次、同一個後端先碰到哪一個故障就記哪一個
            self._outages.setdefault(key, {}).setdefault(
                backend, Outage(backend, message)
            )

    def recovered(self, key: Principal, backend: str) -> None:
        """同一段同一次，這個後端後來有一次成功的呼叫：它先前的故障是暫時的，不算數。
        別的後端的故障照留。"""
        with self._lock:
            found = self._outages.get(key)
            if found is None:
                return
            found.pop(backend, None)
            if not found:
                del self._outages[key]

    def outage(self, key: Principal) -> Outage | None:
        """這一段這一次有沒有碰到還沒恢復的後端故障；好幾個的話回最早碰到的那一個。"""
        with self._lock:
            found = self._outages.get(key)
            return next(iter(found.values()), None) if found else None

    # --- 本機上下文放不下 -------------------------------------------------------

    def report_too_long(self, key: Principal, message: str) -> None:
        with self._lock:
            self._too_long.setdefault(key, message)

    def too_long(self, key: Principal) -> str | None:
        """這一段這一次有沒有提示被擋下，因為本機模型的上下文放不下；有的話回原因。"""
        with self._lock:
            return self._too_long.get(key)
