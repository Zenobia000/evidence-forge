"""預算：送出前預留、回應後結算（v0.3 §4.2、NFR-COST-01）。

兩道上限，預留時都要過：

1. **這張工單**：`job.md` 的 `budget.usd` − 這張單帳本的總花費 − 這張單預留中的金額。
   對話本身的呼叫換成對話的 `conversation.json` 的 `budget_usd` 與對話的帳本（ADR-0021 §3.3）。
2. **這個月**：月上限 − 本月所有工單帳本、對話帳本與投入帳本的花費 − 所有預留中的金額。
   月份依 Asia/Taipei 的日曆月切。月上限每次預留都重問一次（費用設定頁改了不用重開）；
   **沒設（None）就不檢查這一道**，只靠第 1 道擋（ADR-0027 §3.6，預設不設）。

Tavily credits（`web_search`）另有一道：`job.md` 的 `budget.tavily_credits` − 這張單帳本的
`credits` 總和 − 這張單預留中的 credits（`CreditGuard`）。credits 沒有月上限（票 #69 沒要求）。
執行器登記了這一次的上限時，還有一道「這一段這一次」的：上限 − 這一次帳本的 credits − 這一次預留中的
（ADR-0022：本機那幾次用不光整張單的額度，升級上來的那一次還有得搜）。
credits 不夠時只拒絕那一次搜尋，不擋下那一段；單子在全部段跑完後以未完成進待審（`CREDITS_REASON`）。

結算的順序是「先把實際金額寫進帳本，再放掉預留」，中間沒有空檔讓這筆錢兩邊都沒算到。
帳本寫不進去時，實際金額改留在記憶體裡照算（`BudgetGuard.release` 的 `unbooked_micros`）。
金額一律是整數微美元（`budget.py`）；帳本的 `cost_usd` 讀進來就換成整數再加。

閘道的請求都在同一個事件迴圈裡處理，`reserve` 裡沒有 await，檢查與登記之間不會插進別的請求；
執行器在別的執行緒讀，所以還是上鎖。
"""

import itertools
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from evidence_forge.budget import usd_to_micros
from evidence_forge.paths import DataDir

TAIPEI = ZoneInfo("Asia/Taipei")

_log = logging.getLogger(__name__)

BudgetLimit = Literal["job", "month"]

# 進待審時寫在結果裡的原因（NFR-COST-01）
LIMIT_REASONS: dict[BudgetLimit, str] = {
    "job": "預算用完",
    "month": "本月額度用完",
}
# 搜尋額度（Tavily credits）用完的原因：也是這張單的預算用完，只是不擋下那一段，
# 全部段跑完才以未完成進待審（`gateway/search.py` 模組說明第 4 點）
CREDITS_REASON = "預算用完（搜尋額度）"


def taipei_month(ts: datetime) -> str:
    """這個時間點在台北是哪個月，例：`2026-10`。"""
    return ts.astimezone(TAIPEI).strftime("%Y-%m")


@dataclass
class _FileTally:
    """一份帳本讀到哪裡（位元組與行數）、讀過的部分各月花了多少。"""

    offset: int = 0
    lines: int = 0
    by_month: dict[str, int] = field(default_factory=dict)
    total: int = 0
    # Tavily credits 的總和（不分月份：credits 只有工單上限）
    credits: int = 0
    # 每一段每一次的 credits：（段, 第幾次）→ 加總
    credits_by_attempt: dict[tuple[str, int], int] = field(default_factory=dict)


class SpendTally:
    """從帳本加總花費：每張工單的 `ledger.jsonl` 加上 `ledger/intake.jsonl`。

    每份檔只讀新增的部分（帳本只增不改），所以每次預留都重算也不貴；
    誰寫的都讀得到，投入帳本（S0.5 寫）不用另外通知這裡。
    還沒寫完的最後一行（沒有換行）先不算，下次讀到完整的才算。
    讀不懂的行（寫到一半被砍、之後被下一筆用換行封起來的半行）跳過，記一筆警告指出 `檔名:行號`。
    """

    def __init__(self, data: DataDir) -> None:
        self._data = data
        self._files: dict[Path, _FileTally] = {}

    def _paths(self) -> list[Path]:
        paths = sorted(self._data.jobs.glob("*/ledger.jsonl"))
        # 對話本身的模型呼叫也算進本月（ADR-0021 §3.3、chat.feature「對話本身的花費也入帳」）
        paths += sorted(self._data.conversations.glob("*/ledger.jsonl"))
        if self._data.intake_ledger.is_file():
            paths.append(self._data.intake_ledger)
        return paths

    def _refresh(self, path: Path) -> _FileTally:
        tally = self._files.setdefault(path, _FileTally())
        size = path.stat().st_size if path.is_file() else 0
        if size < tally.offset:
            # 帳本只增不改；變短表示被換掉了，整份重讀
            tally = self._files[path] = _FileTally()
        if size == tally.offset:
            return tally
        with path.open("rb") as handle:
            handle.seek(tally.offset)
            chunk = handle.read(size - tally.offset)
        complete = chunk.rfind(b"\n") + 1
        for line in chunk[:complete].splitlines():
            tally.lines += 1
            if line.strip() and not self._add(tally, line):
                _log.warning("帳本 %s:%d 這一行讀不懂，加總時跳過", path, tally.lines)
        tally.offset += complete
        return tally

    @staticmethod
    def _add(tally: _FileTally, line: bytes) -> bool:
        """把一行的金額加進去；讀不懂就什麼都不加，回 False。"""
        # 只讀加總需要的兩欄，工單帳本與投入帳本共用
        try:
            row = json.loads(line)
            month = taipei_month(datetime.fromisoformat(row["ts"]))
            micros = usd_to_micros(float(row.get("cost_usd", 0.0)))
        except (ValueError, KeyError, TypeError):
            return False
        tally.by_month[month] = tally.by_month.get(month, 0) + micros
        tally.total += micros
        credits = _credits(row)
        tally.credits += credits
        stage, attempt = row.get("stage"), row.get("attempt")
        if credits and isinstance(stage, str) and isinstance(attempt, int):
            slot = (stage, attempt)
            tally.credits_by_attempt[slot] = (
                tally.credits_by_attempt.get(slot, 0) + credits
            )
        return True

    def month_micros(self, month: str) -> int:
        return sum(self._refresh(path).by_month.get(month, 0) for path in self._paths())

    def job_micros(self, job_id: str) -> int:
        """一張工單（或 `conv:<對話 id>` 一個對話）帳本的總花費。"""
        return self._refresh(self._data.owner_dir(job_id) / "ledger.jsonl").total

    def job_credits(self, job_id: str) -> int:
        return self._refresh(self._data.owner_dir(job_id) / "ledger.jsonl").credits

    def attempt_credits(self, job_id: str, attempt: tuple[str, int]) -> int:
        """一張工單某一段某一次用掉的 Tavily credits。"""
        tally = self._refresh(self._data.owner_dir(job_id) / "ledger.jsonl")
        return tally.credits_by_attempt.get(attempt, 0)


def _credits(row: dict[str, object]) -> int:
    """一行帳的 Tavily credits；沒寫或讀不懂是 0（閘道寫的行一定是非負整數，見 `LedgerEntry`）。

    跟美元分開讀：credits 欄位壞掉不能讓那一行的金額也不算。
    """
    value = row.get("credits", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


@dataclass(frozen=True)
class Reservation:
    id: int
    job_id: str
    micros: int


@dataclass(frozen=True)
class _Unbooked:
    """實際花掉、帳本卻沒寫進去的一筆錢（見 `BudgetGuard.release`）。"""

    job_id: str
    # 花在台北的哪個月：寫得進帳本的話，那一行的 ts 會落在這個月
    month: str
    micros: int


class BudgetExceeded(Exception):
    """預算不夠；`limit` 說是哪一道上限擋下的。"""

    def __init__(self, limit: BudgetLimit, message: str) -> None:
        super().__init__(message)
        self.limit = limit
        self.message = message


class BudgetGuard:
    def __init__(
        self,
        data: DataDir,
        *,
        monthly_cap_micros: Callable[[], int | None],
        clock: Callable[[], datetime],
    ) -> None:
        """`monthly_cap_micros()` 回這一刻的月上限（微美元），None 是不設。"""
        self._tally = SpendTally(data)
        self._cap = monthly_cap_micros
        self._clock = clock
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._outstanding: dict[int, Reservation] = {}
        # 實際花掉、帳本卻沒寫進去的金額（見 release）；程序重開之前一直算進上限
        self._unbooked: list[_Unbooked] = []

    def reserve(
        self, job_id: str, *, job_budget_micros: int, micros: int
    ) -> Reservation:
        """預留 `micros`；任一道上限不夠就丟 `BudgetExceeded`，什麼都不預留。"""
        # 在鎖外面讀設定檔：讀檔慢一點也不卡住別的預留
        cap = self._cap()
        with self._lock:
            # 還沒進帳本、但要算進上限的：預留中的，加上帳本寫不進去的花費。
            # 工單預算不分月份；月額度只算這個月的那幾筆（預留中的都是這一刻還在跑的呼叫）
            held_by_job = sum(
                r.micros for r in self._outstanding.values() if r.job_id == job_id
            ) + sum(u.micros for u in self._unbooked if u.job_id == job_id)
            job_left = job_budget_micros - self._tally.job_micros(job_id) - held_by_job
            if micros > job_left:
                raise BudgetExceeded(
                    "job",
                    f"工單預算不夠：這次要預留 US${micros / 1e6:.6f}，"
                    f"剩 US${max(job_left, 0) / 1e6:.6f}",
                )
            if cap is not None:
                month = taipei_month(self._clock())
                held = sum(r.micros for r in self._outstanding.values()) + sum(
                    u.micros for u in self._unbooked if u.month == month
                )
                month_left = cap - self._tally.month_micros(month) - held
                if micros > month_left:
                    raise BudgetExceeded(
                        "month",
                        f"本月額度不夠（{month}，上限 US${cap / 1e6:.2f}）："
                        f"這次要預留 US${micros / 1e6:.6f}，剩 US${max(month_left, 0) / 1e6:.6f}",
                    )
            reservation = Reservation(next(self._ids), job_id, micros)
            self._outstanding[reservation.id] = reservation
            return reservation

    def release(self, reservation: Reservation, *, unbooked_micros: int = 0) -> None:
        """放掉預留。結算時先把實際金額寫進帳本，再呼叫這個。重複呼叫無害。

        帳本寫不進去（例如磁碟滿）時，`unbooked_micros` 是這次實際花掉、帳本裡卻沒有的金額：
        預留照樣放掉，這個金額另外留著，照樣算進工單預算，以及花掉那個月（Asia/Taipei）的
        額度——跟它寫進帳本時會算進的地方一樣。寫不進帳本的這段期間上限才擋得住，
        到了下個月也不會一直佔著額度。程序重開之後就沒了，那時帳本也還是缺這一筆。
        """
        with self._lock:
            released = self._outstanding.pop(reservation.id, None)
            if released is not None and unbooked_micros > 0:
                month = taipei_month(self._clock())
                self._unbooked.append(
                    _Unbooked(reservation.job_id, month, unbooked_micros)
                )


@dataclass(frozen=True)
class CreditReservation:
    id: int
    job_id: str
    credits: int
    # 哪一段第幾次的預留；沒有這一次的上限時是 None
    attempt: tuple[str, int] | None = None


class CreditGuard:
    """Tavily credits 的預留與結算（`web_search`）：跟美元同一套順序，只有工單這一道上限。

    結算一樣是「先把實際用掉的 credits 寫進帳本，再 `release`」；帳本寫不進去時，
    實際用掉的 credits 用 `unbooked_credits` 交回來，程序重開之前照樣算進這張單。
    """

    def __init__(self, data: DataDir) -> None:
        self._tally = SpendTally(data)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._outstanding: dict[int, CreditReservation] = {}
        self._unbooked: dict[str, int] = {}
        # 帳本沒寫進去的 credits，照（工單, 段, 第幾次）記；這一次的上限也要算到它們
        self._unbooked_by_attempt: dict[tuple[str, str, int], int] = {}

    def reserve(
        self,
        job_id: str,
        *,
        job_credits: int,
        credits: int,
        attempt: tuple[str, int] | None = None,
        attempt_credits: int | None = None,
    ) -> CreditReservation:
        """預留 `credits`；這張單剩下的不夠，或這一段這一次（`attempt`）用到上限
        `attempt_credits`，就丟 `BudgetExceeded("job")`，什麼都不預留。"""
        with self._lock:
            if attempt is not None and attempt_credits is not None:
                held_here = sum(
                    r.credits
                    for r in self._outstanding.values()
                    if r.job_id == job_id and r.attempt == attempt
                ) + self._unbooked_by_attempt.get((job_id, *attempt), 0)
                left_here = (
                    attempt_credits
                    - self._tally.attempt_credits(job_id, attempt)
                    - held_here
                )
                if credits > left_here:
                    raise BudgetExceeded(
                        "job",
                        f"這一次的 Tavily credits 用完了：上限 {attempt_credits}，"
                        f"剩 {max(left_here, 0)}",
                    )
            held = sum(
                r.credits for r in self._outstanding.values() if r.job_id == job_id
            ) + self._unbooked.get(job_id, 0)
            left = job_credits - self._tally.job_credits(job_id) - held
            if credits > left:
                raise BudgetExceeded(
                    "job",
                    f"這張單的 Tavily credits 不夠：這次要 {credits}，"
                    f"剩 {max(left, 0)}（上限 {job_credits}）",
                )
            reservation = CreditReservation(next(self._ids), job_id, credits, attempt)
            self._outstanding[reservation.id] = reservation
            return reservation

    def release(
        self, reservation: CreditReservation, *, unbooked_credits: int = 0
    ) -> None:
        """放掉預留。實際用掉的 credits 先寫進帳本再呼叫；重複呼叫無害。"""
        with self._lock:
            released = self._outstanding.pop(reservation.id, None)
            if released is not None and unbooked_credits > 0:
                self._unbooked[released.job_id] = (
                    self._unbooked.get(released.job_id, 0) + unbooked_credits
                )
                if released.attempt is not None:
                    slot = (released.job_id, *released.attempt)
                    self._unbooked_by_attempt[slot] = (
                        self._unbooked_by_attempt.get(slot, 0) + unbooked_credits
                    )
