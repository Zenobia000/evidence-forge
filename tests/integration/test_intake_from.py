"""從上一張工單接著做（`ef new --from`，ADR-0019 §3.6、`rewrite.feature`）：每一版都是一張新工單。

- 重構：從分析工單開，帶角色、文體、目標；沒給就用角色的預設文類與配方的預設目標。
- 再改：從上一版的重構工單開，帶一句意見；角色、文體、目標沿用上一版。
- 開單前就擋：上一張還沒跑完、交不出配方要的檔、角色不存在、配方跟 `--from` 對不上。
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.harness import default_harness
from evidence_forge.models import JobResult, Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job, open_job_from
from evidence_forge.runner.recipes import load_recipes

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))
LATER = datetime(2026, 10, 13, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
RECIPES = load_recipes(default_harness(), ("local", "strong", "max"))
ANALYZE, REWRITE = RECIPES["analyze"], RECIPES["rewrite"]
VOICE = "---\nid: me\ndefault_genre: opinion\n---\n\n測試用的聲音。\n"


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    (root.authors / "me").mkdir(parents=True)
    (root.authors / "me" / "voice.md").write_text(VOICE, encoding="utf-8")
    (root.authors / "_template").mkdir()
    return root


def _finish(
    data: DataDir,
    job_id: str,
    stage: str,
    files: dict[str, str],
    *,
    passed: bool = True,
) -> None:
    """當成這張單跑完了：寫一份段產出與執行器的檢查結果，狀態改成結束。"""
    job_dir = data.job_dir(job_id)
    base = job_dir / "stages" / stage / "1"
    base.mkdir(parents=True)
    for name, text in files.items():
        (base / name).write_text(text, encoding="utf-8")
    write_checks_report(
        job_dir,
        ChecksReport(
            stage=stage,
            attempt=1,
            files=tuple(f"stages/{stage}/1/{name}" for name in files),
            created_at=NOW,
            entries=(
                CheckEntry(
                    check="C5", status="pass" if passed else "fail", reason="測試"
                ),
            ),
        ),
    )
    doc = read_job(job_dir / "job.md")
    job = doc.job.model_copy(
        update={"status": "review", "result": JobResult(outcome="complete")}
    )
    write_job(job_dir / "job.md", JobDocument(job=job, notes=doc.notes))


def _analysis(data: DataDir, *, passed: bool = True) -> str:
    job_id = open_job(
        data, ANALYZE, PastedText("# 夜間圖書館\n\n原文全文。"), goal=None, now=NOW
    ).job.id
    _finish(
        data,
        job_id,
        "investigate",
        {"report.md": "報告", "sources.json": "{}"},
        passed=passed,
    )
    return job_id


def _rewrite(
    data: DataDir, parent: str, recipe: Recipe = REWRITE, **options: object
) -> str:
    return open_job_from(data, recipe, parent, now=LATER, **options).id  # type: ignore[arg-type]


@pytest.mark.promise("FR-RW-01", "FR-RW-02")
def test_從分析工單開重構_帶角色文體目標_原文全文不進新工單(data: DataDir) -> None:
    parent = _analysis(data)

    job_id = _rewrite(
        data, parent, persona="me", genre="tutorial", goal="寫給台灣 PM 看"
    )

    job = read_job(data.job_dir(job_id) / "job.md").job
    analysis = read_job(data.job_dir(parent) / "job.md").job
    assert (job.recipe, job.status, job.parent) == ("rewrite", "queued", parent)
    assert (job.persona, job.target_genre, job.goal) == (
        "me",
        "tutorial",
        "寫給台灣 PM 看",
    )
    assert job.feed == analysis.feed
    assert not (data.job_dir(job_id) / "input" / "content.md").exists()


@pytest.mark.promise("FR-RW-01")
def test_沒給目標與文體_用配方的預設目標與角色的預設文類(data: DataDir) -> None:
    job_id = _rewrite(data, _analysis(data))
    job = read_job(data.job_dir(job_id) / "job.md").job
    assert (job.goal, job.target_genre, job.persona) == (
        REWRITE.default_goal,
        "opinion",
        "me",
    )


@pytest.mark.promise("FR-RW-03")
def test_再改帶一句意見_沿用上一版的角色文體目標_每一版一張新工單(
    data: DataDir,
) -> None:
    v1 = _rewrite(data, _analysis(data), genre="tutorial", goal="寫給 PM")
    # v1 跑的時候執行器已經把分析報告放進它的 input/
    (data.job_dir(v1) / "input").mkdir(exist_ok=True)
    (data.job_dir(v1) / "input" / "report.md").write_text("報告", encoding="utf-8")
    _finish(data, v1, "rewrite", {"article.md": "# 第一版", "article.json": "{}"})

    v2 = _rewrite(data, v1, feedback="開頭太長，直接講結論")
    v3 = _rewrite(data, v1, feedback="開頭太長，直接講結論")

    assert len({v1, v2, v3}) == 3  # 再改不走去重
    job = read_job(data.job_dir(v2) / "job.md").job
    assert (job.parent, job.feedback) == (v1, "開頭太長，直接講結論")
    assert (job.goal, job.target_genre, job.persona) == ("寫給 PM", "tutorial", "me")


def test_上一張單還在跑_不開單(data: DataDir) -> None:
    parent = open_job(data, ANALYZE, PastedText("還沒跑完"), goal=None, now=NOW).job.id
    with pytest.raises(ValueError, match="還沒跑完"):
        _rewrite(data, parent)


def test_上一張單沒有合格的分析報告_不開單_點名少什麼(data: DataDir) -> None:
    with pytest.raises(ValueError, match="report.md"):
        _rewrite(data, _analysis(data, passed=False))


def test_上一張單不存在_不開單(data: DataDir) -> None:
    with pytest.raises(ValueError, match="20990101"):
        _rewrite(data, "20990101-000000-deadbeef")


@pytest.mark.promise("FR-RW-02")
def test_角色不存在_不開單_列出現有的角色(data: DataDir) -> None:
    with pytest.raises(ValueError, match="ghost") as caught:
        _rewrite(data, _analysis(data), persona="ghost")
    assert "me" in str(caught.value) and "_template" not in str(caught.value)


@pytest.mark.promise("FR-RW-03")
def test_要再改但上一張單沒有文章_不開單(data: DataDir) -> None:
    with pytest.raises(ValueError, match="article.md"):
        _rewrite(data, _analysis(data), feedback="改短一點")


def test_要上一張單的配方不帶_from_就不開單_不要的配方不收_from(data: DataDir) -> None:
    with pytest.raises(ValueError, match="--from"):
        open_job(data, REWRITE, PastedText("文章"), goal=None, now=NOW)
    with pytest.raises(ValueError, match="--from"):
        _rewrite(data, _analysis(data), recipe=ANALYZE)
