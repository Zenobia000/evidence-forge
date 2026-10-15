"""對話的工作台（`chat/desk.py`）：開分析單、開重構單、讀報告與文章、列角色。

開單用真的投入（`runner.intake.open_job`、`open_job_from`）；工單「跑完」由測試直接寫 `job.md` 與產出檔。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_forge.chat.desk import MAX_FILE_CHARS, Desk
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.documents import JobDocument, read_frontmatter, read_job, write_job
from evidence_forge.harness import default_harness
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.ui.format import GENRE_NAME

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
    needs=("report.md",),
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


# --- 重構 ---------------------------------------------------------------------


def _analysed(data: DataDir, desk: Desk) -> str:
    """開一張分析、跑完，交得出過了檢查的報告（重構從它接著做）。"""
    job_id = _open(desk)
    _finish(data, job_id)
    attempt = data.job_dir(job_id) / "stages" / "investigate" / "1"
    attempt.mkdir(parents=True)
    (attempt / "report.md").write_text("報告", encoding="utf-8")
    write_checks_report(
        data.job_dir(job_id),
        ChecksReport(
            stage="investigate",
            attempt=1,
            files=("stages/investigate/1/report.md",),
            created_at=NOW,
            entries=(CheckEntry(check="C5", status="pass", reason="測試"),),
        ),
    )
    return job_id


def _rewrite(desk: Desk, from_job: str, **options: str) -> dict[str, object]:
    fields: dict[str, str | None] = {"persona": None, "genre": None, "goal": None, "feedback": None}
    fields.update(options)
    return desk.open_rewrite(conversation_id="c1", from_job=from_job, **fields)


@pytest.fixture
def me(data: DataDir) -> DataDir:
    (data.authors / "me").mkdir(parents=True)
    (data.authors / "me" / "voice.md").write_text("---\ndefault_genre: opinion\n---\n聲音", encoding="utf-8")
    return data


def test_重構開了就回_記下對話與上一張_通知主機(me: DataDir) -> None:
    opened: list[tuple[str, str]] = []
    desk = _desk(me, opened)
    analysis = _analysed(me, desk)
    result = _rewrite(desk, analysis, goal="題材 2：夜班。角度：成本")
    assert (result["created"], result["status"], result["recipe"], result["stages"]) == (True, "queued", "rewrite", 1)
    job_id = result["job_id"]
    assert isinstance(job_id, str)
    job = read_job(me.job_dir(job_id) / "job.md").job
    assert (job.conversation, job.parent, job.persona, job.target_genre, job.goal) == (
        "c1",
        analysis,
        "me",
        "opinion",
        "題材 2：夜班。角度：成本",
    )
    assert opened[-1] == ("c1", job_id)


def test_重構的文類可以給中文名_記成文類_id(me: DataDir) -> None:
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), genre="解說文")
    job_id = result["job_id"]
    assert isinstance(job_id, str)
    assert read_job(me.job_dir(job_id) / "job.md").job.target_genre == "explainer"


def test_重構的文類不認得_不開單_列出有哪些(me: DataDir) -> None:
    desk = _desk(me)
    analysis = _analysed(me, desk)
    result = _rewrite(desk, analysis, genre="打油詩")
    assert result["status"] == "rejected"
    assert "打油詩" in str(result["reason"]) and "opinion（觀點文）" in str(result["reason"])
    assert [p.name for p in me.jobs.iterdir()] == [analysis]


def test_上一張交不出報告_不開單_原因交回(me: DataDir) -> None:
    desk = _desk(me)
    job_id = _open(desk)
    _finish(me, job_id)
    result = _rewrite(desk, job_id)
    assert result["status"] == "rejected"
    assert "交不出 report.md" in str(result["reason"])
    assert [p.name for p in me.jobs.iterdir()] == [job_id]


def test_沒有重構配方_不開單_說清楚原因(me: DataDir) -> None:
    desk = Desk(me, {"analyze": ANALYZE}, "analyze", clock=lambda: NOW)
    analysis = _analysed(me, desk)
    result = _rewrite(desk, analysis)
    assert result["status"] == "rejected"
    assert "沒有重構配方 rewrite" in str(result["reason"])


def test_工作台認得的文類跟_harness_genres_同一份() -> None:
    # 工作台拿畫面的文類表擋不認得的文類；兩邊飄開，就會擋掉執行器其實接受的文類，或放過一個開了才失敗的
    defined = {
        str(read_frontmatter(path)["id"]): str(read_frontmatter(path)["name"])
        for path in sorted(default_harness().genres.glob("*.md"))
    }
    assert defined == GENRE_NAME
