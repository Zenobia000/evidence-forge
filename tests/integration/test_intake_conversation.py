"""SEAM-09 加上對話（ADR-0021 §3.6）：投入去重的鍵含對話 id。

創作者 2026-10-13 決定兩個對話丟同一篇文章不共用分析：同一份素材、同樣目標，
只有在**同一個對話**裡才拿回舊工單。命令列的 `ef new` 沒有對話，照舊在沒有對話的工單之間去重
（那幾條在 `test_intake.py`）。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge.documents import read_job
from evidence_forge.models import Recipe, RecipeStage
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


def test_開單的對話記在_job_md(data: DataDir) -> None:
    intake = _submit(data, "c-mine")
    assert read_job(data.job_dir(intake.job.id) / "job.md").job.conversation == "c-mine"
    cli = open_job(data, RECIPE, PastedText("另一篇"), goal=None, now=NOW)
    assert read_job(data.job_dir(cli.job.id) / "job.md").job.conversation is None
