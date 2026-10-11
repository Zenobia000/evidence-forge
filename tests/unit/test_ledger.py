"""帳本模組（`gateway/ledger.py`）：投入帳本的格式與寫入、串流用量、重播索引。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_forge.gateway.ledger import (
    append_intake,
    responses_by_call,
    usage_from_sse,
)
from evidence_forge.ledger_models import IntakeEntry, LedgerEntry
from evidence_forge.paths import DataDir

TS = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
A, B, C = "a" * 64, "b" * 64, "c" * 64


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
    usage = usage_from_sse(stream)
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
