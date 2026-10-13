"""SEAM-09 加上對話（ADR-0021 §3.6）：投入去重的鍵含對話 id。

創作者 2026-10-13 決定兩個對話丟同一篇文章不共用分析：同一份素材、同樣目標，
只有在**同一個對話**裡才拿回舊工單。命令列的 `ef new` 沒有對話，照舊在沒有對話的工單之間去重
（那幾條在 `test_intake.py`）。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.models import JobResult, JobStatus, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import Intake, PastedText, open_job

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
RECIPE = Recipe(
    name="echo",
    description="test",
    stages=(RecipeStage(id="echo", skill="echo", tier="local", output=("a.json",)),),
    on_feedback="echo",
    default_goal="用兩三句話說明這篇文章在講什麼",
)
ARTICLE = PastedText("# Agents in production\n\n最難的是可觀測性。")


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    return root


def _submit(data: DataDir, conversation: str | None) -> Intake:
    return open_job(
        data, RECIPE, ARTICLE, goal=None, now=NOW, conversation=conversation
    )


@pytest.mark.promise("NFR-SEC-02")
def test_兩個對話丟同一篇文章_各開各的單(data: DataDir) -> None:
    mine = _submit(data, "c-mine")
    other = _submit(data, "c-other")
    assert mine.created and other.created
    assert mine.job.id != other.job.id
    # 素材項目照舊只有一份：不共用的是分析（工單），不是素材
    assert mine.job.feed.item == other.job.feed.item


def test_同一個對話再丟同一篇_拿回同一張(data: DataDir) -> None:
    first = _submit(data, "c-mine")
    again = _submit(data, "c-mine")
    assert (again.created, again.job.id) == (False, first.job.id)


@pytest.mark.promise("NFR-SEC-02")
def test_對話不會拿到命令列開的單(data: DataDir) -> None:
    from_cli = _submit(data, None)
    from_chat = _submit(data, "c-mine")
    assert from_chat.created
    assert from_chat.job.id != from_cli.job.id


def test_命令列不會拿到對話開的單(data: DataDir) -> None:
    from_chat = _submit(data, "c-mine")
    from_cli = _submit(data, None)
    assert from_cli.created
    assert from_cli.job.id != from_chat.job.id


@pytest.mark.parametrize("conversation", ["c-mine", None], ids=["對話", "命令列"])
def test_同素材同目標但配方不同_開新單(data: DataDir, conversation: str | None) -> None:
    # 去重的鍵是「對話 + 素材 + 配方 + 目標」：不同配方的單不能互相頂替，命令列也一樣
    first = _submit(data, conversation)
    other_recipe = RECIPE.model_copy(update={"name": "other"})
    again = open_job(
        data, other_recipe, ARTICLE, goal=None, now=NOW, conversation=conversation
    )
    assert again.created
    assert again.job.id != first.job.id


def test_開單的對話記在_job_md(data: DataDir) -> None:
    intake = _submit(data, "c-mine")
    assert read_job(data.job_dir(intake.job.id) / "job.md").job.conversation == "c-mine"
    cli = open_job(data, RECIPE, PastedText("另一篇"), goal=None, now=NOW)
    assert read_job(data.job_dir(cli.job.id) / "job.md").job.conversation is None


def _settle(data: DataDir, job_id: str, status: JobStatus, outcome: str | None) -> None:
    """執行器會做的事：把工單改成某個狀態與結果。"""
    path = data.job_dir(job_id) / "job.md"
    job = read_job(path).job
    result = (
        None
        if outcome is None
        else JobResult.model_validate({"outcome": outcome, "reason": "測試"})
    )
    write_job(
        path,
        JobDocument(job=job.model_copy(update={"status": status, "result": result})),
    )


@pytest.mark.parametrize("conversation", ["c-mine", None], ids=["對話", "命令列"])
@pytest.mark.parametrize(
    ("status", "outcome", "reused"),
    [
        ("queued", None, True),
        ("running", None, True),
        ("review", "complete", True),
        ("approved", "complete", True),
        ("review", "incomplete", False),
        ("review", "failed", False),
        ("discarded", "complete", False),
    ],
)
def test_只拿回排隊中_執行中或完成的單(
    data: DataDir,
    conversation: str | None,
    status: JobStatus,
    outcome: str | None,
    reused: bool,
) -> None:
    # 失敗或未完成的單拿回來也沒用：再丟一次要能重新分析（ADR-0021 §3.6）
    first = _submit(data, conversation)
    _settle(data, first.job.id, status, outcome)
    again = _submit(data, conversation)
    assert (again.job.id == first.job.id) is reused
    assert again.created is not reused


@pytest.mark.parametrize("conversation", ["c-mine", None], ids=["對話", "命令列"])
def test_again_不走去重_一定開新單(data: DataDir, conversation: str | None) -> None:
    first = _submit(data, conversation)
    redo = open_job(
        data, RECIPE, ARTICLE, goal=None, now=NOW, conversation=conversation, again=True
    )
    assert redo.created
    assert redo.job.id != first.job.id


def test_要開新單才問_before_create_拿回舊單不問(data: DataDir) -> None:
    asked: list[str] = []
    first = open_job(
        data,
        RECIPE,
        ARTICLE,
        goal=None,
        now=NOW,
        conversation="c-mine",
        before_create=lambda: asked.append("new"),
    )
    open_job(
        data,
        RECIPE,
        ARTICLE,
        goal=None,
        now=NOW,
        conversation="c-mine",
        before_create=lambda: asked.append("again"),
    )
    assert first.created
    assert asked == ["new"]


def test_before_create_丟例外就不開單(data: DataDir) -> None:
    def refuse() -> None:
        raise RuntimeError("上限")

    with pytest.raises(RuntimeError):
        open_job(
            data,
            RECIPE,
            ARTICLE,
            goal=None,
            now=NOW,
            conversation="c-mine",
            before_create=refuse,
        )
    assert list(data.jobs.iterdir()) == []
