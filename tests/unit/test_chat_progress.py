"""工作進度列的資料（`ef_job`，IA §4.2、NFR-OBS-02）：第幾段、從什麼時候開始、在等什麼、花了多少。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evidence_forge.chat.progress import job_progress, same_progress
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import SpendTally
from evidence_forge.models import JobResult, Recipe, RecipeStage, StageProgress, Waiting
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
RECIPE = Recipe(
    name="analyze",
    description="test",
    stages=(
        RecipeStage(id="brief", skill="brief", tier="local", output=("brief.md",)),
        RecipeStage(
            id="investigate",
            skill="investigate",
            tier="strong",
            output=("report.md",),
            tavily_credits=12,
        ),
    ),
    on_feedback="investigate",
    default_goal="讀懂這篇",
)


def _job(tmp_path: Path) -> tuple[DataDir, str]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    job = open_job(
        data, RECIPE, PastedText("一篇文章"), goal=None, now=NOW, conversation="c1"
    ).job
    return data, job.id


def _update(data: DataDir, job_id: str, **changes: object) -> None:
    path = data.job_dir(job_id) / "job.md"
    job = read_job(path).job
    write_job(path, JobDocument(job=job.model_copy(update=changes)))


def _progress(data: DataDir, job_id: str, board: JobBoard | None = None) -> dict[str, object]:
    return job_progress(
        data,
        job_id,
        recipes={"analyze": RECIPE},
        board=board,
        tally=SpendTally(data),
        clock=lambda: NOW,
    )


def test_排隊中_還沒有段_花費是零(tmp_path: Path) -> None:
    data, job_id = _job(tmp_path)
    found = _progress(data, job_id)
    assert found["type"] == "ef_job"
    assert found["conversation_id"] == "c1"
    assert found["status"] == "queued"
    assert found["finished"] is False
    assert found["kind"] == "analysis"
    assert found["stage"] is None
    assert found["stages_total"] == 2
    assert found["spent_usd"] == 0
    assert found["budget_credits"] == 12


def test_第2段在跑_告示板說在等模型_帳本的花費與搜尋額度都算進來(tmp_path: Path) -> None:
    data, job_id = _job(tmp_path)
    started = NOW - timedelta(minutes=3, seconds=12)
    _update(
        data,
        job_id,
        status="running",
        stage=StageProgress(id="investigate", index=2, total=2, attempt=1, started_at=started, tier="strong"),
        waiting=Waiting(kind="tool", tool="web_search", since=started),
    )
    ledger = data.job_dir(job_id) / "ledger.jsonl"
    rows = [
        {"ts": NOW.isoformat(), "cost_usd": 0.1},
        {"ts": NOW.isoformat(), "cost_usd": 0.08, "kind": "search", "credits": 4},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    board = JobBoard(lambda: NOW)

    found = _progress(data, job_id, board)
    assert found["stage"] == {
        "id": "investigate",
        "skill": "investigate",
        "index": 2,
        "total": 2,
        "attempt": 1,
        "tier": "strong",
        "started_at": started.isoformat(),
    }
    # 告示板沒有東西：用 job.md 記的（執行器從 Pi 的事件看到正在搜尋）
    assert found["waiting"] == {"kind": "tool", "tool": "web_search", "since": started.isoformat()}
    assert found["spent_usd"] == pytest.approx(0.18)
    assert found["credits_used"] == 4

    # 告示板比 job.md 即時：閘道說在等 GPU 就是在等 GPU
    board.wait(job_id, "gpu")
    assert _progress(data, job_id, board)["waiting"] == {
        "kind": "gpu",
        "tool": None,
        "since": NOW.isoformat(),
    }


def test_結束了_帶結果與原因_告示板殘留的等待不算(tmp_path: Path) -> None:
    data, job_id = _job(tmp_path)
    _update(
        data,
        job_id,
        status="review",
        result=JobResult(outcome="incomplete", reason="預算用完"),
    )
    board = JobBoard(lambda: NOW)
    board.wait(job_id, "model")
    found = _progress(data, job_id, board)
    assert found["finished"] is True
    assert found["outcome"] == "incomplete"
    assert found["reason"] == "預算用完"
    assert found["waiting"] is None


def test_只差時間戳記算沒變(tmp_path: Path) -> None:
    data, job_id = _job(tmp_path)
    first = _progress(data, job_id)
    later = {**first, "at": "2026-10-13T06:00:00+00:00"}
    assert same_progress(first, later)
    assert not same_progress(first, {**first, "status": "running"})
    assert not same_progress(None, first)
