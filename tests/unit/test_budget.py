"""SEAM-04（單元）：預留、結算、退回的算術；月上限跨工單加總，月份依 Asia/Taipei 切。"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from evidence_forge.budget import micros_to_usd, usd_to_micros
from evidence_forge.gateway.budget import (
    BudgetExceeded,
    BudgetGuard,
    SpendTally,
    taipei_month,
)
from evidence_forge.gateway.ledger import Usage
from evidence_forge.gateway.prices import (
    Price,
    cost_micros,
    estimate_input_tokens,
    load_prices,
    reservation_micros,
)
from evidence_forge.paths import DataDir

OCT = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
SONNET = Price(
    input=Decimal("2"),
    output=Decimal("10"),
    cache_write=Decimal("2.5"),
    cache_read=Decimal("0.2"),
)


def _line(ts: datetime, cost: float, **extra: object) -> str:
    return json.dumps({"ts": ts.isoformat(), "cost_usd": cost, **extra}) + "\n"


def _job_ledger(data: DataDir, job_id: str, *lines: str) -> None:
    path = data.job_dir(job_id) / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(lines))


def _intake(data: DataDir, *lines: str) -> None:
    data.intake_ledger.parent.mkdir(parents=True, exist_ok=True)
    with data.intake_ledger.open("a", encoding="utf-8") as handle:
        handle.write("".join(lines))


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    return data


# --- 金額換算與價目 ---------------------------------------------------------


def test_美元與微美元互換不失真() -> None:
    assert usd_to_micros(0.1) == 100_000
    assert usd_to_micros(10) == 10_000_000
    assert usd_to_micros(micros_to_usd(123_457)) == 123_457


@pytest.mark.promise("NFR-COST-01")
def test_實際金額依價目表算_不足一微美元進位() -> None:
    usage = Usage(
        input_tokens=1000, output_tokens=500, cache_read_tokens=3, cache_write_tokens=0
    )
    # 1000×2 + 500×10 + 3×0.2 = 7000.6 → 7001 微美元
    assert cost_micros(SONNET, usage) == 7001


@pytest.mark.promise("NFR-COST-01")
def test_預留金額用偏高的輸入估計加上_max_tokens() -> None:
    # 300 個中文字是 900 個位元組
    request = ("字" * 300).encode()
    assert estimate_input_tokens(request) == 900
    # 輸入用快取寫入價（較貴）：900×2.5 = 2250；輸出 1000×10 = 10000
    assert reservation_micros(SONNET, request, max_tokens=1000) == 12_250


@pytest.mark.promise("NFR-COST-01")
def test_中文請求的輸入估值不少於字數_中文大約一個字一個_token() -> None:
    # #54 審查實測：6,600 個中文字的請求，字元數 ÷ 2 只估出 3,324，實際用量大約是兩倍。
    # 估少了，預留就少，月上限可能被超過
    text = "預算" * 3300
    request = json.dumps(
        {"messages": [{"role": "user", "content": text}]}, ensure_ascii=False
    ).encode()
    assert estimate_input_tokens(request) >= len(text)


def test_價目表涵蓋對照表裡每個雲端模型() -> None:
    from evidence_forge.gateway.aliases import load_aliases

    prices = load_prices()
    for alias in load_aliases().values():
        if alias.backend == "anthropic":
            assert alias.model in prices


# --- 月份 -------------------------------------------------------------------


@pytest.mark.promise("NFR-COST-01")
def test_月份依台北時間切_UTC_還在上個月時台北已經是下個月() -> None:
    # UTC 10/31 17:00 是台北 11/1 01:00
    assert taipei_month(datetime(2026, 10, 31, 17, 0, tzinfo=UTC)) == "2026-11"
    assert taipei_month(datetime(2026, 10, 31, 15, 59, tzinfo=UTC)) == "2026-10"


# --- 本月累計 ---------------------------------------------------------------


@pytest.mark.promise("NFR-COST-01", "FR-LG-01")
def test_本月累計跨工單加總_含投入帳本_不含別的月份(data: DataDir) -> None:
    _job_ledger(data, "a", _line(OCT, 0.25), _line(OCT, 0.5))
    _job_ledger(data, "b", _line(OCT, 1.0))
    _intake(data, _line(OCT, 0.125, url="https://example.com/a"))
    # 台北的 11 月，不算進 10 月
    _job_ledger(data, "b", _line(datetime(2026, 10, 31, 17, 0, tzinfo=UTC), 3.0))

    tally = SpendTally(data)
    assert tally.month_micros("2026-10") == 1_875_000
    assert tally.month_micros("2026-11") == 3_000_000
    assert tally.job_micros("a") == 750_000
    assert tally.job_micros("b") == 4_000_000


def test_本月累計會讀到之後新寫的行_還沒寫完的那行先不算(data: DataDir) -> None:
    tally = SpendTally(data)
    _job_ledger(data, "a", _line(OCT, 0.5))
    assert tally.month_micros("2026-10") == 500_000
    _job_ledger(data, "a", _line(OCT, 0.25), '{"ts": "2026-10-15T04:00:00+00:00"')
    assert tally.month_micros("2026-10") == 750_000
    _job_ledger(data, "a", ', "cost_usd": 1.0}\n')
    assert tally.month_micros("2026-10") == 1_750_000


# --- 預留 -------------------------------------------------------------------


def _guard(data: DataDir, cap_usd: float = 10.0) -> BudgetGuard:
    return BudgetGuard(
        data, monthly_cap_micros=usd_to_micros(cap_usd), clock=lambda: OCT
    )


@pytest.mark.promise("NFR-COST-01")
def test_工單剩下的預算不夠就拒絕_原因是工單預算(data: DataDir) -> None:
    _job_ledger(data, "a", _line(OCT, 0.9))
    guard = _guard(data)
    with pytest.raises(BudgetExceeded) as refused:
        guard.reserve("a", job_budget_micros=1_000_000, micros=200_000)
    assert refused.value.limit == "job"
    # 剛好用完不算超過
    guard.reserve("a", job_budget_micros=1_000_000, micros=100_000)


@pytest.mark.promise("NFR-COST-01")
def test_本月剩下的額度不夠就拒絕_原因是本月額度(data: DataDir) -> None:
    _intake(data, _line(OCT, 9.95))
    guard = _guard(data)
    with pytest.raises(BudgetExceeded) as refused:
        guard.reserve("a", job_budget_micros=5_000_000, micros=100_000)
    assert refused.value.limit == "month"


@pytest.mark.promise("NFR-COST-01")
def test_加總用整數_接近上限時不會被浮點誤差誤判(data: DataDir) -> None:
    # 浮點數 0.1+0.1+0.1+0.6 = 0.9000000000000001，再加 0.1 就「超過」1.0
    _job_ledger(data, "a", _line(OCT, 0.1), _line(OCT, 0.1), _line(OCT, 0.1))
    _job_ledger(data, "b", _line(OCT, 0.6))
    guard = _guard(data, cap_usd=1.0)
    guard.reserve("c", job_budget_micros=1_000_000, micros=100_000)


@pytest.mark.promise("NFR-COST-01")
def test_預留中的金額先算進去_退回之後才放出來(data: DataDir) -> None:
    guard = _guard(data, cap_usd=1.0)
    first = guard.reserve("a", job_budget_micros=1_000_000, micros=600_000)
    with pytest.raises(BudgetExceeded) as refused:
        guard.reserve("a", job_budget_micros=1_000_000, micros=600_000)
    assert refused.value.limit == "job"
    with pytest.raises(BudgetExceeded) as refused:
        guard.reserve("b", job_budget_micros=1_000_000, micros=600_000)
    assert refused.value.limit == "month"
    # 結算：實際花費寫進帳本，再放掉預留
    _job_ledger(data, "a", _line(OCT, 0.2))
    guard.release(first)
    guard.reserve("b", job_budget_micros=1_000_000, micros=600_000)
