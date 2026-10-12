"""SEAM-04（單元）加上對話（ADR-0021 §3.3、§3.4）：對話的帳本算進每月總額、有自己的總額，
帳本一行分得出是工單還是對話，同時在跑的工單上限可以設定。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge import conversations
from evidence_forge.gateway.budget import SpendTally, taipei_month
from evidence_forge.gateway.config import config_from_env
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.jobkey import InvalidJobKey
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


@pytest.mark.parametrize("bad", ["", "../jobs/x", "a/b", "c:1", ".hidden", "a..b"])
def test_對話資料夾的路徑擋不合法的_id(tmp_path: Path, bad: str) -> None:
    data = DataDir(tmp_path)
    with pytest.raises(InvalidJobKey):
        data.conversation_dir(bad)
    with pytest.raises(InvalidJobKey):
        data.owner_dir(f"conv:{bad}")


def test_jobs_jsonl_有寫到一半的殘行_下一筆照樣讀得到(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    conversation = conversations.create(data, NOW)
    path = data.conversation_dir(conversation.id) / "jobs.jsonl"
    path.write_text('{"job_id": "J0", "ts": "2026-10', encoding="utf-8")
    conversations.add_job(
        data, conversation.id, conversations.ConversationJob(job_id="J1", ts=NOW)
    )
    assert [row.job_id for row in conversations.jobs(data, conversation.id)] == ["J1"]
    assert path.read_text(encoding="utf-8").endswith("\n")
