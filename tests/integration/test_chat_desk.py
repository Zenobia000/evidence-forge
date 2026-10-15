"""對話的工作台（`chat/desk.py`）：開分析單、讀報告與文章、列角色。

開單用真的投入（`runner.intake.open_job`）；工單「跑完」由測試直接寫 `job.md` 與產出檔。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge.chat.desk import MAX_FILE_CHARS, Desk
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
ANALYZE = Recipe(
    name="analyze",
    description="test",
    stages=(
        RecipeStage(id="brief", skill="brief", tier="local", output=("brief.md",)),
        RecipeStage(
            id="investigate",
            skill="investigate",
            tier="strong",
            output=("report.md", "sources.json"),
        ),
    ),
    on_feedback="investigate",
    default_goal="讀懂這篇",
)
REWRITE = Recipe(
    name="rewrite",
    description="test",
    stages=(
        RecipeStage(id="rewrite", skill="rewrite", tier="strong", output=("article.md", "article.json")),
    ),
    on_feedback="rewrite",
    default_goal="寫一篇",
)


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    return data


def _desk(data: DataDir, opened: list[tuple[str, str]] | None = None, recipe: str = "analyze") -> Desk:
    return Desk(
        data,
        {"analyze": ANALYZE, "rewrite": REWRITE},
        recipe,
        clock=lambda: NOW,
        on_opened=(lambda c, j: opened.append((c, j))) if opened is not None else None,
    )


def _open(desk: Desk, text: str = "# 一篇\n\n內容") -> str:
    opened = desk.open_analysis(
        conversation_id="c1", url=None, text=text, goal=None, again=False, before_create=lambda: None
    )
    job_id = opened["job_id"]
    assert isinstance(job_id, str)
    return job_id


def _finish(data: DataDir, job_id: str, recipe: str = "analyze") -> None:
    path = data.job_dir(job_id) / "job.md"
    job = read_job(path).job
    done = job.model_copy(
        update={
            "recipe": recipe,
            "status": "done",
            "result": JobResult(outcome="complete", reason="跑完"),
        }
    )
    write_job(path, JobDocument(job=done))


def test_開了單就回_記下是哪個對話開的_通知主機(data: DataDir) -> None:
    opened: list[tuple[str, str]] = []
    desk = _desk(data, opened)
    result = desk.open_analysis(
        conversation_id="c1", url=None, text="# 一篇\n\n內容", goal=None, again=False, before_create=lambda: None
    )
    assert result["created"] is True
    assert result["status"] == "queued"
    assert result["stages"] == 2
    job = read_job(data.job_dir(result["job_id"]) / "job.md").job
    assert job.conversation == "c1"
    assert opened == [("c1", result["job_id"])]


def test_沒有分析配方_不開單_說清楚原因(data: DataDir) -> None:
    desk = _desk(data, recipe="nope")
    result = desk.open_analysis(
        conversation_id="c1", url=None, text="# a\n\nb", goal=None, again=False, before_create=lambda: None
    )
    assert result["status"] == "rejected"
    assert "沒有分析配方 nope" in result["reason"]
    assert not any(data.jobs.iterdir())


def test_還沒跑完_報告只回狀態(data: DataDir) -> None:
    desk = _desk(data)
    job_id = _open(desk)
    found = desk.report(job_id)
    assert found["status"] == "queued"
    assert "還沒跑完" in found["note"]
    assert "files" not in found


def test_報告讀產出_report_md_的那一段最新一次_太長截掉(data: DataDir) -> None:
    desk = _desk(data)
    job_id = _open(desk)
    _finish(data, job_id)
    stage = data.job_dir(job_id) / "stages" / "investigate"
    (stage / "1").mkdir(parents=True)
    (stage / "1" / "report.md").write_text("第一次", encoding="utf-8")
    (stage / "2").mkdir()
    (stage / "2" / "report.md").write_text("長" * (MAX_FILE_CHARS + 5), encoding="utf-8")
    (stage / "2" / "sources.json").write_text("[]", encoding="utf-8")

    found = desk.report(job_id)

    assert found["outcome"] == "complete"
    assert set(found["files"]) == {"stages/investigate/2/report.md", "stages/investigate/2/sources.json"}
    assert found["files"]["stages/investigate/2/report.md"].endswith("（後面截掉了）")
    # 分析的單沒有文章
    article = desk.article(job_id)
    assert article["files"] == {}
    assert "沒有文章" in article["note"]


def test_文章讀產出_article_md_的那一段_重構的單不算報告(data: DataDir) -> None:
    desk = _desk(data)
    job_id = _open(desk)
    _finish(data, job_id, recipe="rewrite")
    attempt = data.job_dir(job_id) / "stages" / "rewrite" / "1"
    attempt.mkdir(parents=True)
    (attempt / "article.md").write_text("# 新文章", encoding="utf-8")

    assert desk.article(job_id)["files"] == {"stages/rewrite/1/article.md": "# 新文章"}
    assert desk.report(job_id)["files"] == {}


def test_列出角色_不含範本_附預設文類與寫過的文類(data: DataDir) -> None:
    me = data.authors / "me"
    (me / "genres").mkdir(parents=True)
    (me / "voice.md").write_text("---\ndefault_genre: opinion\n---\n聲音", encoding="utf-8")
    (me / "genres" / "opinion.md").write_text("風格", encoding="utf-8")
    (data.authors / "b2b").mkdir()
    (data.authors / "_template").mkdir()

    assert _desk(data).personas() == {
        "personas": [
            {"name": "b2b", "default_genre": None, "calibrated_genres": []},
            {"name": "me", "default_genre": "opinion", "calibrated_genres": ["opinion"]},
        ]
    }
