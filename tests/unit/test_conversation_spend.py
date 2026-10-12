"""SEAM-04（單元）加上對話（ADR-0021 §3.3、§3.4）：對話的帳本算進每月總額、有自己的總額，
帳本一行分得出是工單還是對話，同時在跑的工單上限可以設定。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge.gateway.budget import SpendTally, taipei_month
from evidence_forge.gateway.config import config_from_env
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


def _write(path: Path, cost: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": NOW.isoformat(), "cost_usd": cost}) + "\n", encoding="utf-8"
    )


def test_每月總額包含對話的帳本(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    _write(data.job_dir("20261013-1") / "ledger.jsonl", 0.5)
    _write(data.conversation_dir("c1") / "ledger.jsonl", 0.25)
    assert SpendTally(data).month_micros(taipei_month(NOW)) == 750_000


def test_對話的總額讀對話資料夾裡的帳本(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    _write(data.conversation_dir("c1") / "ledger.jsonl", 0.25)
    assert SpendTally(data).job_micros("conv:c1") == 250_000


def test_工單資料夾的路徑不准有冒號(tmp_path: Path) -> None:
    # 冒號在 Windows 的磁碟上不能當檔名；對話的帳一律經 owner_dir 導到 conversations/
    with pytest.raises(ValueError):
        DataDir(tmp_path).job_dir("conv:c1")


def test_對話同時在跑的工單上限_預設_1_可以用環境變數改(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    assert config_from_env(data, {}).chat_max_running_jobs == 1
    env = {"EF_CHAT_MAX_RUNNING_JOBS": "3"}
    assert config_from_env(data, env).chat_max_running_jobs == 3
    for bad in ("0", "-1", "兩張"):
        with pytest.raises(ValueError, match="EF_CHAT_MAX_RUNNING_JOBS"):
            config_from_env(data, {"EF_CHAT_MAX_RUNNING_JOBS": bad})


def _row(**owner: object) -> dict[str, object]:
    return {
        "ts": NOW.isoformat(),
        "stage": "chat",
        "attempt": 1,
        "kind": "llm",
        "tier": "strong",
        "model": "claude-sonnet-4-5",
        "latency_ms": 1,
        "status": "ok",
        "request_hash": "0" * 64,
        **owner,
    }


def test_帳本一行只屬於一張工單或一個對話() -> None:
    assert LedgerEntry.model_validate(_row(job_id="j1")).conversation_id is None
    assert LedgerEntry.model_validate(_row(conversation_id="c1")).job_id is None
    with pytest.raises(ValueError):
        LedgerEntry.model_validate(_row())
    with pytest.raises(ValueError):
        LedgerEntry.model_validate(_row(job_id="j1", conversation_id="c1"))
