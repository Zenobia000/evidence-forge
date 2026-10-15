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


def test_開了單就回_記下是哪個對話開的_記進對話之後才通知主機(data: DataDir) -> None:
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
    # 開單當下不通知：閘道記進 jobs.jsonl 之後叫 recorded 才通知（不然跑得快的單會漏掉結束通知）
    assert opened == []
    desk.recorded("c1", result["job_id"])
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


def test_列出角色_不含範本與備份_附預設文體與寫過的文體(data: DataDir) -> None:
    me = data.authors / "me"
    (me / "genres").mkdir(parents=True)
    (me / "voice.md").write_text("---\ndefault_genre: essay\n---\n聲音", encoding="utf-8")
    (me / "genres" / "essay.md").write_text("---\ncalibrated: true\n---\n風格", encoding="utf-8")
    # 寫了但還沒校準的、沒有 front matter 的，都只算寫過
    (me / "genres" / "explainer.md").write_text("---\ncalibrated: false\n---\n風格", encoding="utf-8")
    (me / "genres" / "trend.md").write_text("沒有 front matter", encoding="utf-8")
    # 舊的 12 種文類的檔、範本檔（`_` 開頭）都不是 7 種之一，不列
    (me / "genres" / "opinion.md").write_text("---\ncalibrated: true\n---\n舊的", encoding="utf-8")
    (me / "genres" / "_template.md").write_text("範本", encoding="utf-8")
    (data.authors / "b2b").mkdir()
    (data.authors / "_template").mkdir()
    # 匯入角色前的備份（scripts/import_persona.py）不是角色
    (data.authors / ".backup" / "me-20261015-120000").mkdir(parents=True)

    assert _desk(data).personas() == {
        "personas": [
            {"name": "b2b", "default_genre": None, "written_genres": [], "calibrated_genres": []},
            {
                "name": "me",
                "default_genre": "essay",
                "written_genres": ["essay", "explainer", "trend"],
                "calibrated_genres": ["essay"],
            },
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
    fields: dict[str, str | None] = {
        "persona": None,
        "genre": None,
        "suggested_genre": None,
        "goal": None,
        "feedback": None,
    }
    fields.update(options)
    return desk.open_rewrite(conversation_id="c1", from_job=from_job, **fields)


@pytest.fixture
def me(data: DataDir) -> DataDir:
    (data.authors / "me").mkdir(parents=True)
    (data.authors / "me" / "voice.md").write_text("---\ndefault_genre: essay\n---\n聲音", encoding="utf-8")
    return data


def test_重構開了就回_記下對話與上一張_記進對話之後才通知主機(me: DataDir) -> None:
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
        "essay",
        "題材 2：夜班。角度：成本",
    )
    assert opened == []
    desk.recorded("c1", job_id)
    assert opened == [("c1", job_id)]


def test_重構的文體可以給中文名_記成文體_slug(me: DataDir) -> None:
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), genre="理論教學")
    job_id = result["job_id"]
    assert isinstance(job_id, str)
    assert read_job(me.job_dir(job_id) / "job.md").job.target_genre == "explainer"


# 「觀點文」「opinion」是舊報告的建議文類：新程式不對照舊的 12 種（ADR-0026 §3.4），當成對不上
@pytest.mark.parametrize("written", ["評論", "深度報導", "觀點文", "opinion", "隨筆（trend）"])
def test_報告建議的文體對不上_照樣開單_用預設文體_附一句說明(me: DataDir, written: str) -> None:
    # 報告的「建議文體」是模型寫的自由文字；對不上就用角色的預設文體，不要讓「寫第 N 個」開不了
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), suggested_genre=written)
    job_id = result["job_id"]
    assert isinstance(job_id, str)
    assert read_job(me.job_dir(job_id) / "job.md").job.target_genre == "essay"
    assert result["genre"] == "essay"
    assert result["note"] == f"文體「{written}」對不上，改用預設的隨筆（essay）"


@pytest.mark.parametrize(
    ("written", "slug"),
    [
        ("理論教學", "explainer"),
        ("trend", "trend"),
        # 報告照 investigate 的 SKILL.md 寫「中文名（slug）」
        ("研究評論（research-review）", "research-review"),
        ("研究評論 (research-review)", "research-review"),
    ],
)
def test_報告建議的文體對得上_就用它(me: DataDir, written: str, slug: str) -> None:
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), suggested_genre=written)
    assert result["genre"] == slug and "note" not in result


def test_創作者指定的文類不認得_不開單_列出有哪些(me: DataDir) -> None:
    # 創作者明說「寫成打油詩」：不能默默改成別的文類，要問回去
    desk = _desk(me)
    analysis = _analysed(me, desk)
    result = _rewrite(desk, analysis, genre="打油詩", suggested_genre="隨筆")
    assert result["status"] == "rejected"
    reason = str(result["reason"])
    assert "沒有文體 打油詩" in reason and "essay（隨筆）" in reason
    assert [p.name for p in me.jobs.iterdir()] == [analysis]


def test_創作者指定的文類勝過報告建議的(me: DataDir) -> None:
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), genre="實證評測", suggested_genre="隨筆")
    assert result["genre"] == "benchmark" and "note" not in result


def test_沿用來的預設文體是舊的_不開單_列出_7_種(me: DataDir) -> None:
    # 舊角色的 default_genre 可能還是 12 種的 id：開了單也只會在執行器放參考資料時失敗
    desk = _desk(me)
    analysis = _analysed(me, desk)
    (me.authors / "me" / "voice.md").write_text("---\ndefault_genre: opinion\n---\n聲音", encoding="utf-8")
    result = _rewrite(desk, analysis)
    assert result["status"] == "rejected"
    reason = str(result["reason"])
    assert "opinion" in reason and "essay（隨筆）" in reason and "trend（趨勢解讀）" in reason
    assert [p.name for p in me.jobs.iterdir()] == [analysis]


def test_角色不存在_原因不帶資料目錄的完整路徑(me: DataDir) -> None:
    # 原因會交給對話的模型、再轉述給創作者：主機上的絕對路徑不該出現
    desk = _desk(me)
    result = _rewrite(desk, _analysed(me, desk), persona="nobody")
    assert result["status"] == "rejected"
    reason = str(result["reason"])
    assert "沒有角色 nobody" in reason and "authors/<角色>/" in reason
    assert str(me.root) not in reason


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
        for path in default_harness().genre_files()
    }
    assert defined == GENRE_NAME
