"""預算：送出前預留、回應後結算（v0.3 §4.2、NFR-COST-01）。

兩道上限，預留時都要過：

1. **這張工單**：`job.md` 的 `budget.usd` − 這張單帳本的總花費 − 這張單預留中的金額。
2. **這個月**：月上限（設定值，預設 US$10，Q-30）− 本月所有工單帳本與投入帳本的花費 − 所有預留中的金額。
   月份依 Asia/Taipei 的日曆月切。

結算的順序是「先把實際金額寫進帳本，再放掉預留」，中間沒有空檔讓這筆錢兩邊都沒算到。
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
        return True

    def month_micros(self, month: str) -> int:
        return sum(self._refresh(path).by_month.get(month, 0) for path in self._paths())

    def job_micros(self, job_id: str) -> int:
        return self._refresh(self._data.job_dir(job_id) / "ledger.jsonl").total


@dataclass(frozen=True)
class Reservation:
    id: int
    job_id: str
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
        monthly_cap_micros: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._tally = SpendTally(data)
        self._cap = monthly_cap_micros
        self._clock = clock
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._outstanding: dict[int, Reservation] = {}

    def reserve(
        self, job_id: str, *, job_budget_micros: int, micros: int
    ) -> Reservation:
        """預留 `micros`；任一道上限不夠就丟 `BudgetExceeded`，什麼都不預留。"""
        with self._lock:
            held_by_job = sum(
                r.micros for r in self._outstanding.values() if r.job_id == job_id
            )
            job_left = job_budget_micros - self._tally.job_micros(job_id) - held_by_job
            if micros > job_left:
                raise BudgetExceeded(
                    "job",
                    f"工單預算不夠：這次要預留 US${micros / 1e6:.6f}，"
                    f"剩 US${max(job_left, 0) / 1e6:.6f}",
                )
            month = taipei_month(self._clock())
            held = sum(r.micros for r in self._outstanding.values())
            month_left = self._cap - self._tally.month_micros(month) - held
            if micros > month_left:
                raise BudgetExceeded(
                    "month",
                    f"本月額度不夠（{month}，上限 US${self._cap / 1e6:.2f}）："
                    f"這次要預留 US${micros / 1e6:.6f}，剩 US${max(month_left, 0) / 1e6:.6f}",
                )
            reservation = Reservation(next(self._ids), job_id, micros)
            self._outstanding[reservation.id] = reservation
            return reservation

    def release(self, reservation: Reservation) -> None:
        """放掉預留。結算時先把實際金額寫進帳本，再呼叫這個。重複呼叫無害。"""
        with self._lock:
            self._outstanding.pop(reservation.id, None)
