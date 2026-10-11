"""帳本模組（`gateway/ledger.py`）：投入帳本的格式與寫入、串流用量、重播索引。"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_forge.gateway.budget import SpendTally
from evidence_forge.gateway.ledger import (
    append,
    append_intake,
    read,
    read_reply,
    responses_by_call,
)
from evidence_forge.ledger_models import IntakeEntry, LedgerEntry
from evidence_forge.paths import DataDir

TS = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
A, B, C = "a" * 64, "b" * 64, "c" * 64
# 程序在寫一行的途中被砍，檔尾留下的半行（沒有換行）
HALF_LINE = b'{"ts": "2026-10-11T04:00:00+00:00", "cost_us'


@pytest.mark.promise("FR-LG-01", "NFR-OBS-01")
def test_投入時的抓取寫進_ledger_intake_jsonl_一次一行(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    ok = IntakeEntry(
        ts=TS, kind="fetch", url="https://example.com/a", latency_ms=120, status="ok"
    )
    refused = IntakeEntry(
        ts=TS,
        kind="fetch",
        url="https://example.com/paywall",
        latency_ms=80,
        status="error",
    )
    append_intake(data, ok)
    append_intake(data, refused)
    lines = data.intake_ledger.read_text(encoding="utf-8").splitlines()
    assert data.intake_ledger == data.root / "ledger" / "intake.jsonl"
    assert [IntakeEntry.model_validate_json(line) for line in lines] == [ok, refused]
    # 欄位跟工單帳本同名（NFR-OBS-01），月上限的加總讀得懂
    row = json.loads(lines[0])
    assert {
        "ts",
        "kind",
        "cost_usd",
        "credits",
        "latency_ms",
        "queue_ms",
        "status",
    } <= set(row)


def test_投入帳本不收模型呼叫() -> None:
    with pytest.raises(ValidationError):
        IntakeEntry(ts=TS, kind="llm", latency_ms=1, status="ok")


def test_投入帳本多出不認得的欄位就拒絕() -> None:
    with pytest.raises(ValidationError):
        IntakeEntry.model_validate(
            {"ts": TS, "kind": "fetch", "latency_ms": 1, "status": "ok", "job_id": "x"}
        )


def test_串流用量含快取讀寫_message_delta_有帶就以它為準() -> None:
    start = {
        "type": "message_start",
        "message": {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 1,
                "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 40,
            }
        },
    }
    delta = {
        "type": "message_delta",
        "usage": {"output_tokens": 77, "input_tokens": 12},
    }
    stream = f"data: {json.dumps(start)}\n\ndata: {json.dumps(delta)}\n\n".encode()
    usage = read_reply(stream).usage
    assert (usage.input_tokens, usage.output_tokens) == (12, 77)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (300, 40)


def _entry(
    request: str, response: str, status: str = "ok", attempt: int = 1
) -> LedgerEntry:
    return LedgerEntry.model_validate(
        {
            "ts": TS,
            "job_id": "j",
            "stage": "echo",
            "attempt": attempt,
            "kind": "llm",
            "tier": "local",
            "model": "qwen3.5:9b",
            "latency_ms": 1,
            "status": status,
            "request_hash": request,
            "response_hash": response,
        }
    )


@pytest.mark.promise("NFR-COST-02")
def test_重播索引保留順序_同一個請求的每一份回應都在() -> None:
    index = responses_by_call(
        [
            _entry(A, B),
            _entry(A, C),
            _entry(A, A, status="error"),
            _entry(A, A, attempt=2),
        ],
        ("ok",),
    )
    assert index[("echo", 1, A)] == [B, C]
    assert index[("echo", 2, A)] == [A]


# --- 寫到一半就中斷的帳本 -----------------------------------------------------


@pytest.mark.promise("FR-LG-01", "NFR-COST-01")
@pytest.mark.parametrize("which", ["job", "intake"])
def test_上一行寫到一半就中斷_下一筆不會黏上去_金額照樣算得到(
    tmp_path: Path, which: str
) -> None:
    # 黏成一行的話，那一行解析失敗、整行略過：月額度與工單預算都少算（#54 審查必修 1）
    data = DataDir(tmp_path / "data")
    if which == "job":
        path = data.job_dir("j") / "ledger.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(HALF_LINE)
        append(data.job_dir("j"), _entry(A, B).model_copy(update={"cost_usd": 0.25}))
    else:
        data.intake_ledger.parent.mkdir(parents=True)
        data.intake_ledger.write_bytes(HALF_LINE)
        append_intake(
            data,
            IntakeEntry(ts=TS, kind="fetch", cost_usd=0.25, latency_ms=1, status="ok"),
        )
    assert SpendTally(data).month_micros("2026-10") == 250_000


def test_帳本有一行壞掉_讀的時候跳過並記下是哪一行_其餘照讀(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first, second = _entry(A, B), _entry(A, C)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(
        first.model_dump_json().encode()
        + b"\n"
        + HALF_LINE
        + b"\n"
        + second.model_dump_json().encode()
        + b"\n"
    )
    with caplog.at_level(logging.WARNING):
        entries = read(path)
    assert entries == [first, second]
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert f"{path}:2" in caplog.records[0].getMessage()


def test_加總月額度時壞掉的那一行跳過並記下是哪一行_其餘照算(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = DataDir(tmp_path / "data")
    path = data.job_dir("j") / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    good = _entry(A, B).model_copy(update={"cost_usd": 0.5}).model_dump_json().encode()
    path.write_bytes(good + b"\n" + HALF_LINE + b"\n" + good + b"\n")
    with caplog.at_level(logging.WARNING):
        assert SpendTally(data).month_micros("2026-10") == 1_000_000
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert f"{path}:2" in caplog.records[0].getMessage()


# 兩種寫法各自就要認得：只有 `event: error` 那一行，或只有 data 裡的 "type": "error"
ERROR_EVENTS = {
    "event 行": 'event: error\ndata: {"error": {"type": "overloaded_error"}}\n\n',
    "data 的 type": 'data: {"type": "error", "error": {"type": "overloaded_error"}}\n\n',
}


@pytest.mark.parametrize("error", ERROR_EVENTS.values(), ids=ERROR_EVENTS.keys())
def test_串流裡有_error_事件就不是一則完整的訊息_就算開頭與結尾的用量都讀到了(
    error: str,
) -> None:
    start = {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}
    delta = {"type": "message_delta", "usage": {"output_tokens": 5}}
    whole = f"data: {json.dumps(start)}\n\ndata: {json.dumps(delta)}\n\n"
    assert read_reply(whole.encode()).whole
    assert not read_reply((whole + error).encode()).whole
