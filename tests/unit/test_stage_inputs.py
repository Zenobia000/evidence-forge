"""執行器開段時把文體定義與角色設定放進工單的 `input/`（ADR-0015 §3.7、ADR-0018、ADR-0026）。

agent 只看得到系統提示裡的 `SKILL.md` 與工單資料夾，`harness/` 與 `$EF_DATA/authors/` 都讀不到。
"""

import json
from pathlib import Path

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from evidence_forge.budget import Budget
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.harness import Harness, default_harness
from evidence_forge.models import FeedRef, Job
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import (
    InputError,
    carried_files,
    default_genre,
    personas,
    prepare_inputs,
    theme_stance,
    uncalibrated,
)

VOICE = """---
id: me
default_genre: essay
calibrated: true
---

## 語氣

測試用的作家聲音。
"""


def _job(
    genre: str | None,
    *,
    persona: str | None = None,
    parent: str | None = None,
    feedback: str | None = None,
) -> Job:
    return Job(
        id="j1",
        recipe="rewrite",
        feed=FeedRef(connector="manual", name="manual", item="inbox/manual/x"),
        goal="寫一篇",
        target_genre=genre,
        persona=persona,
        parent=parent,
        feedback=feedback,
        budget=Budget(usd=0.5),
        status="queued",
    )


def _data(tmp_path: Path, *, styles: dict[str, str] | None = None) -> DataDir:
    data = DataDir(tmp_path / "data")
    author = data.authors / "me"
    (author / "genres").mkdir(parents=True)
    (author / "voice.md").write_text(VOICE, encoding="utf-8")
    for genre, text in (styles or {}).items():
        (author / "genres" / f"{genre}.md").write_text(text, encoding="utf-8")
    return data


def _job_dir(tmp_path: Path) -> Path:
    job_dir = tmp_path / "data" / "jobs" / "j1"
    (job_dir / "input").mkdir(parents=True)
    (job_dir / "input" / "content.md").write_text("原文", encoding="utf-8")
    return job_dir


HARNESS = default_harness()


@pytest.mark.promise("FR-CP-02", "FR-DG-02")
def test_文體定義_角色聲音_文體風格都複製進_input_主題清單不算文體(tmp_path: Path) -> None:
    data = _data(tmp_path, styles={"essay": "---\ncalibrated: true\n---\n隨筆風格"})
    job_dir = _job_dir(tmp_path)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    inputs = job_dir / "input"
    genres = (inputs / "genres.md").read_text(encoding="utf-8")
    for definition in HARNESS.genre_files():
        assert definition.read_text(encoding="utf-8") in genres
    # 主題清單跟文體放同一個資料夾，但不是文體定義，不接進 genres.md
    assert HARNESS.themes.read_text(encoding="utf-8") not in genres
    assert (inputs / "target-genre.md").read_bytes() == (
        HARNESS.genres / "essay.md"
    ).read_bytes()
    assert (inputs / "author" / "voice.md").read_text(encoding="utf-8") == VOICE
    assert (
        (inputs / "author" / "genre-style.md")
        .read_text(encoding="utf-8")
        .endswith("隨筆風格")
    )
    # 舊的轉換說明是用舊 12 種 id 寫的，新程式不放（ADR-0026 §3.4）
    assert not (inputs / "conversions").exists()


def test_主題清單不能當目標文體(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    with pytest.raises(InputError, match="themes"):
        prepare_inputs(HARNESS, data, job_dir, _job("themes"))


@pytest.mark.promise("FR-CP-02")
def test_作家沒有目標文類的風格檔_就不放_genre_style_md(tmp_path: Path) -> None:
    data = _data(tmp_path, styles={"explainer": "理論教學風格"})
    job_dir = _job_dir(tmp_path)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    assert not (job_dir / "input" / "author" / "genre-style.md").exists()
    assert (job_dir / "input" / "target-genre.md").is_file()


def test_再開一次段不覆寫_工單記得當時用的版本(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    # 作家改了聲音；重跑同一張單仍用開段當時那一份（只增不改）
    (data.authors / "me" / "voice.md").write_text("改過", encoding="utf-8")
    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    assert (job_dir / "input" / "author" / "voice.md").read_text(
        encoding="utf-8"
    ) == VOICE


def test_放過一次之後才新增的風格檔_重跑不補進來_請求不變(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    # 當機重開前作家補寫了風格檔；這張單仍是開段當時的樣子，閘道快取才對得上
    (data.authors / "me" / "genres" / "essay.md").write_text(
        "新風格", encoding="utf-8"
    )
    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    assert not (job_dir / "input" / "author" / "genre-style.md").exists()


def test_上次當機留下半截暫存檔_重開放的是完整的檔(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    # 模擬寫 genres.md 寫到一半被砍：只留下半截的暫存檔，正式檔名還沒出現
    (job_dir / "input" / ".genres.md.partial").write_text("半截", encoding="utf-8")

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    expected = "\n\n".join(
        path.read_text(encoding="utf-8") for path in HARNESS.genre_files()
    )
    assert (job_dir / "input" / "genres.md").read_text(encoding="utf-8") == expected
    assert list(job_dir.rglob("*.partial")) == []


def test_目標文類不在_harness_genres_裡_丟_InputError_點名文類(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    with pytest.raises(InputError, match="poem"):
        prepare_inputs(HARNESS, data, job_dir, _job("poem"))


def test_沒有作家設定也沒有目標文類_只放文類定義(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    job_dir = _job_dir(tmp_path)

    prepare_inputs(HARNESS, data, job_dir, _job(None))

    assert (job_dir / "input" / "genres.md").is_file()
    assert not (job_dir / "input" / "target-genre.md").exists()
    assert not (job_dir / "input" / "author").exists()


def test_harness_沒有文類資料夾也不出錯(tmp_path: Path) -> None:
    harness = Harness(root=tmp_path / "harness", pi=HARNESS.pi)
    job_dir = _job_dir(tmp_path)
    prepare_inputs(harness, DataDir(tmp_path / "data"), job_dir, _job(None))
    assert sorted(p.name for p in (job_dir / "input").iterdir()) == [
        ".prepared.json",
        "content.md",
    ]


@pytest.mark.promise("FR-CP-03")
def test_作家的預設文類從_voice_md_的_frontmatter_讀(tmp_path: Path) -> None:
    assert default_genre(_data(tmp_path)) == "essay"
    assert default_genre(DataDir(tmp_path / "empty")) is None


def test_預設文類_voice_md_或角色資料夾是連結_不讀連結指到的檔(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.md").write_text("---\ndefault_genre: essay\n---\n外面的", encoding="utf-8")
    data = DataDir(tmp_path / "data")
    (data.authors / "a").mkdir(parents=True)
    (data.authors / "a" / "voice.md").symlink_to(outside / "voice.md")
    (data.authors / "b").symlink_to(outside, target_is_directory=True)

    assert default_genre(data, "a") is None
    assert default_genre(data, "b") is None


# --- 角色、標題與金句規則、從上一張單接著做（#79、ADR-0019 §3.6） -----------------

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))


def _checked(
    job_dir: Path, stage: str, attempt: int, files: dict[str, str], *, passed: bool
) -> None:
    """上一張單某段某次的產出，加上執行器替它寫的檢查結果。"""
    base = job_dir / "stages" / stage / str(attempt)
    base.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (base / name).write_text(text, encoding="utf-8")
    write_checks_report(
        job_dir,
        ChecksReport(
            stage=stage,
            attempt=attempt,
            files=tuple(f"stages/{stage}/{attempt}/{name}" for name in files),
            created_at=NOW,
            entries=(
                CheckEntry(
                    check="C5",
                    status="pass" if passed else "fail",
                    reason="測試",
                ),
            ),
        ),
    )


def _analysis(data: DataDir) -> Path:
    """一張跑完的分析工單：brief 一次過、查證第一次沒過第二次過、放了兩份來源。"""
    parent = data.job_dir("analysis")
    (parent / "input" / "sources").mkdir(parents=True)
    (parent / "input" / "content.md").write_text("原文全文", encoding="utf-8")
    for sid in ("S2", "S3"):
        (parent / "input" / "sources" / f"{sid}.md").write_text(
            f"{sid} 的正文", encoding="utf-8"
        )
    _checked(parent, "brief", 1, {"brief.md": "brief", "brief.json": "{}"}, passed=True)
    _checked(parent, "investigate", 1, {"report.md": "沒過的報告"}, passed=False)
    _checked(parent, "investigate", 2, {"report.md": "合格的報告"}, passed=True)
    return parent


def _rewrite_job_dir(data: DataDir, job_id: str = "j1") -> Path:
    # 重構工單沒有原文全文：開單時不放 input/content.md
    job_dir = data.job_dir(job_id)
    (job_dir / "input").mkdir(parents=True)
    return job_dir


@pytest.mark.promise("FR-RW-01")
def test_從分析工單開的重構_拿到合格的報告_brief_與來源正文_沒有原文全文(
    tmp_path: Path,
) -> None:
    data = _data(tmp_path)
    _analysis(data)
    job_dir = _rewrite_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="analysis"))

    inputs = job_dir / "input"
    assert (inputs / "report.md").read_text(encoding="utf-8") == "合格的報告"
    assert (inputs / "brief.md").read_text(encoding="utf-8") == "brief"
    assert (inputs / "brief.json").is_file()
    assert sorted(p.name for p in (inputs / "sources").iterdir()) == ["S2.md", "S3.md"]
    assert not (inputs / "content.md").exists()
    assert not (inputs / "article.md").exists()


@pytest.mark.promise("FR-RW-03")
def test_再改從上一版重構工單開_拿到上一版文章_報告與來源跟著帶過來(
    tmp_path: Path,
) -> None:
    data = _data(tmp_path)
    _analysis(data)
    v1 = _rewrite_job_dir(data, "v1")
    prepare_inputs(HARNESS, data, v1, _job("essay", parent="analysis"))
    _checked(
        v1, "rewrite", 1, {"article.md": "# 第一版", "article.json": "{}"}, passed=True
    )
    v2 = _rewrite_job_dir(data, "v2")

    prepare_inputs(HARNESS, data, v2, _job("essay", parent="v1", feedback="改短"))

    inputs = v2 / "input"
    assert (inputs / "article.md").read_text(encoding="utf-8") == "# 第一版"
    assert (inputs / "report.md").read_text(encoding="utf-8") == "合格的報告"
    assert sorted(p.name for p in (inputs / "sources").iterdir()) == ["S2.md", "S3.md"]
    # 上一版自己的檔一個都不改
    assert (v1 / "stages" / "rewrite" / "1" / "article.md").read_text(
        encoding="utf-8"
    ) == "# 第一版"


def test_上一張單的產出沒過程式檢查_不帶過去(tmp_path: Path) -> None:
    data = _data(tmp_path)
    parent = data.job_dir("analysis")
    _checked(parent, "investigate", 1, {"report.md": "沒過的報告"}, passed=False)
    assert carried_files(parent) == {}


def test_上一張單不存在_丟_InputError(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _rewrite_job_dir(data)
    with pytest.raises(InputError, match="ghost"):
        prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="ghost"))


@pytest.mark.promise("FR-RW-02")
def test_用指定角色的聲音與文類風格(tmp_path: Path) -> None:
    data = _data(tmp_path)
    other = data.authors / "b2b"
    (other / "genres").mkdir(parents=True)
    (other / "voice.md").write_text("b2b 的聲音", encoding="utf-8")
    (other / "genres" / "essay.md").write_text("b2b 的隨筆", encoding="utf-8")
    job_dir = _rewrite_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay", persona="b2b"))

    author = job_dir / "input" / "author"
    assert (author / "voice.md").read_text(encoding="utf-8") == "b2b 的聲音"
    assert (author / "genre-style.md").read_text(encoding="utf-8") == "b2b 的隨筆"
    assert personas(data) == ["b2b", "me"]


@pytest.mark.promise("FR-RW-02")
def test_標題與金句規則從資料目錄放進工單(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.headline_rules.parent.mkdir(parents=True)
    data.headline_rules.write_text("十四個技巧", encoding="utf-8")
    job_dir = _rewrite_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    assert (job_dir / "input" / "headline-punchline.md").read_text(
        encoding="utf-8"
    ) == "十四個技巧"


@pytest.mark.promise("FR-RW-02")
def test_角色沒有目標文類的風格_標未校準_有而且校準過就不標(tmp_path: Path) -> None:
    data = _data(tmp_path, styles={"essay": "---\ncalibrated: true\n---\n風格"})
    plain = _rewrite_job_dir(data, "a")
    prepare_inputs(HARNESS, data, plain, _job("benchmark"))
    styled = _rewrite_job_dir(data, "b")
    prepare_inputs(HARNESS, data, styled, _job("essay"))

    note = uncalibrated(plain, _job("benchmark"))
    assert note is not None and "未校準" in note and "benchmark" in note
    assert uncalibrated(styled, _job("essay")) is None


@pytest.mark.promise("FR-RW-03")
def test_上一版的文章沒過檢查_不拿更早那一版充數(tmp_path: Path) -> None:
    # v2 帶著 v1 修到上限還沒過；再從 v2 改時，「上一版」不能悄悄變回 v1
    data = _data(tmp_path)
    _analysis(data)
    v1 = _rewrite_job_dir(data, "v1")
    prepare_inputs(HARNESS, data, v1, _job("essay", parent="analysis"))
    _checked(v1, "rewrite", 1, {"article.md": "# 第一版"}, passed=True)
    v2 = _rewrite_job_dir(data, "v2")
    prepare_inputs(HARNESS, data, v2, _job("essay", parent="v1", feedback="改短"))
    _checked(v2, "rewrite", 1, {"article.md": "# 第二版"}, passed=False)

    assert "input/article.md" not in carried_files(v2)
    assert "input/report.md" in carried_files(v2)


def test_換角色重寫不帶意見_不放上一版文章(tmp_path: Path) -> None:
    data = _data(tmp_path)
    _analysis(data)
    v1 = _rewrite_job_dir(data, "v1")
    prepare_inputs(HARNESS, data, v1, _job("essay", parent="analysis"))
    _checked(v1, "rewrite", 1, {"article.md": "# 第一版"}, passed=True)
    fresh = _rewrite_job_dir(data, "fresh")

    prepare_inputs(HARNESS, data, fresh, _job("essay", parent="v1"))

    assert not (fresh / "input" / "article.md").exists()
    assert (fresh / "input" / "report.md").is_file()


def test_上一張工單的資料夾是指到_jobs_外面的連結_丟_InputError(tmp_path: Path) -> None:
    # job.md 被改過、或資料夾被換成連結：開工前照樣擋，不從 jobs/ 以外的地方複製檔案
    data = _data(tmp_path)
    outside = tmp_path / "outside"
    (outside / "input").mkdir(parents=True)
    (outside / "input" / "report.md").write_text("外面的檔", encoding="utf-8")
    data.jobs.mkdir(parents=True, exist_ok=True)
    (data.jobs / "linked").symlink_to(outside)
    job_dir = _rewrite_job_dir(data)

    with pytest.raises(InputError, match="jobs"):
        prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="linked"))
    assert not (job_dir / "input" / "report.md").exists()


# --- 照編輯卡寫稿：只送這一篇用得到的角色資料（ADR-0026 §3.3、#110）----------------------------

THEMES_MD = """---
author: me
themes: [tools, adoption]
---

## 用哪幾個主題

| slug | 這個角色怎麼稱呼它 | 常寫的文體 |
|---|---|---|
| `tools` | 工具挑選 | `benchmark` |
| `adoption` | 導入現場 | `postmortem` |

## 對每個主題的主張

### tools

挑工具先看退場成本。

### adoption

導入先算維運的人。
"""


def _card_job_dir(data: DataDir, theme: str = "tools", job_id: str = "j1") -> Path:
    """照編輯卡開的寫稿工單：開單時已經放了 `input/card/`（`intake.open_job_from_card`）。"""
    job_dir = _rewrite_job_dir(data, job_id)
    card = job_dir / "input" / "card"
    card.mkdir(parents=True)
    (card / "card.json").write_text(
        json.dumps({"card_id": "card-1", "conversation_id": "c-1", "theme": theme, "stations": []}),
        encoding="utf-8",
    )
    (card / "card.md").write_text("# 編輯卡\n", encoding="utf-8")
    (card / "materials").mkdir()
    (card / "materials" / ("a" * 64)).write_text("作者的筆記", encoding="utf-8")
    return job_dir


def _persona_parts(data: DataDir, lessons: int = 12) -> None:
    author = data.authors / "me"
    (author / "craft").mkdir()
    for name in ("openings", "titles", "golden-lines-and-endings", "layout", "figures", "charts"):
        (author / "craft" / f"{name}.md").write_text(f"{name} 的規則", encoding="utf-8")
    (author / "themes.md").write_text(THEMES_MD, encoding="utf-8")
    (author / "lessons.md").write_text(
        "".join(f"- 第 {n} 條\n" for n in range(1, lessons + 1)), encoding="utf-8"
    )


@pytest.mark.promise("FR-RW-02")
def test_照編輯卡寫稿_只送寫稿用得到的_craft_這個主題的主張與最近十筆_lessons(tmp_path: Path) -> None:
    data = _data(tmp_path)
    _persona_parts(data)
    data.headline_rules.parent.mkdir(parents=True)
    data.headline_rules.write_text("共用的標題規則", encoding="utf-8")
    job_dir = _card_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    author = job_dir / "input" / "author"
    assert sorted(p.name for p in (author / "craft").iterdir()) == [
        "golden-lines-and-endings.md",
        "layout.md",
        "openings.md",
        "titles.md",
    ]
    theme = (author / "theme.md").read_text(encoding="utf-8")
    assert "挑工具先看退場成本" in theme and "工具挑選" in theme
    assert "導入先算維運的人" not in theme and "導入現場" not in theme
    lessons = (author / "lessons.md").read_text(encoding="utf-8")
    assert "第 3 條" in lessons and "第 12 條" in lessons and "第 2 條" not in lessons
    # 角色有自己的標題與金句手藝：不再放共用的規則
    assert not (job_dir / "input" / "headline-punchline.md").exists()


def test_照編輯卡寫稿_角色沒有標題與金句的_craft_才讀共用的規則(tmp_path: Path) -> None:
    data = _data(tmp_path)
    data.headline_rules.parent.mkdir(parents=True)
    data.headline_rules.write_text("共用的標題規則", encoding="utf-8")
    job_dir = _card_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    assert (job_dir / "input" / "headline-punchline.md").read_text(encoding="utf-8") == "共用的標題規則"
    author = job_dir / "input" / "author"
    assert not (author / "craft").exists()
    assert not (author / "theme.md").exists() and not (author / "lessons.md").exists()


def test_不是照編輯卡寫的單_不放_craft_主題與_lessons(tmp_path: Path) -> None:
    data = _data(tmp_path)
    _persona_parts(data)
    job_dir = _rewrite_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    author = job_dir / "input" / "author"
    assert not (author / "craft").exists() and not (author / "lessons.md").exists()


def test_角色的_craft_或_lessons_是符號連結_不讀(tmp_path: Path) -> None:
    data = _data(tmp_path)
    _persona_parts(data)
    secret = tmp_path / "secret.md"
    secret.write_text("- 外面的檔", encoding="utf-8")
    author = data.authors / "me"
    (author / "craft" / "openings.md").unlink()
    (author / "craft" / "openings.md").symlink_to(secret)
    (author / "lessons.md").unlink()
    (author / "lessons.md").symlink_to(secret)
    job_dir = _card_job_dir(data)

    prepare_inputs(HARNESS, data, job_dir, _job("essay"))

    placed = job_dir / "input" / "author"
    assert not (placed / "craft" / "openings.md").exists()
    assert not (placed / "lessons.md").exists()


@pytest.mark.promise("FR-RW-03")
def test_再改照編輯卡寫的文章_編輯卡快照跟著帶過來(tmp_path: Path) -> None:
    data = _data(tmp_path)
    v1 = _card_job_dir(data, job_id="v1")
    prepare_inputs(HARNESS, data, v1, _job("essay"))
    _checked(v1, "rewrite", 1, {"article.md": "# 第一版", "article.json": "{}"}, passed=True)
    v2 = _rewrite_job_dir(data, "v2")

    prepare_inputs(HARNESS, data, v2, _job("essay", parent="v1", feedback="改短"))

    card = v2 / "input" / "card"
    assert (card / "card.md").read_text(encoding="utf-8") == "# 編輯卡\n"
    assert (card / "materials" / ("a" * 64)).read_text(encoding="utf-8") == "作者的筆記"
    assert (v2 / "input" / "article.md").read_text(encoding="utf-8") == "# 第一版"


def test_主題的主張_挑出這個主題那一節與表格那一列() -> None:
    found = theme_stance(THEMES_MD, "adoption")

    assert found is not None
    assert "導入先算維運的人" in found and "導入現場" in found
    assert "挑工具先看退場成本" not in found
    assert theme_stance(THEMES_MD, "startup") is None


# --- 本機 32K 塞得下：只帶這個題材的來源、不帶 brief、沒有圖就不帶圖文編排（#110 T3）--------------


def _card_from_report(data: DataDir, topic: int, storyboard: list[dict[str, str]] | None = None) -> Path:
    """從分析工單第 `topic` 個題材開的寫稿工單（開單時放好的 `input/card/`）。"""
    parent = _analysis(data)
    # 查證段合格那一次的來源清單：題材 1 用 S2，題材 2 用 S3
    _checked(
        parent,
        "investigate",
        3,
        {
            "report.md": "合格的報告",
            "sources.json": json.dumps({"topics": [{"title": "一", "sources": ["S2"]}, {"title": "二", "sources": ["S3"]}]}),
        },
        passed=True,
    )
    job_dir = _rewrite_job_dir(data)
    card = job_dir / "input" / "card"
    card.mkdir(parents=True)
    state: dict[str, object] = {
        "card_id": "card-1",
        "conversation_id": "c-1",
        "theme": "tools",
        "stations": [],
        "origin": {"kind": "report_topic", "job_id": "analysis", "topic": topic},
    }
    if storyboard is not None:
        state["storyboard"] = storyboard
    (card / "card.json").write_text(json.dumps(state), encoding="utf-8")
    (card / "card.md").write_text("# 編輯卡\n", encoding="utf-8")
    return job_dir


@pytest.mark.promise("FR-RW-01")
def test_從報告的題材寫稿_只帶那個題材的來源_不帶_brief(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _card_from_report(data, topic=2)

    prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="analysis"))

    inputs = job_dir / "input"
    assert sorted(p.name for p in (inputs / "sources").iterdir()) == ["S3.md"]
    assert (inputs / "report.md").read_text(encoding="utf-8") == "合格的報告"
    assert not (inputs / "brief.md").exists() and not (inputs / "brief.json").exists()


def test_報告沒有題材的來源清單_照舊帶全部的來源_但說出來(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = _data(tmp_path)
    _analysis(data)
    job_dir = _rewrite_job_dir(data)
    card = job_dir / "input" / "card"
    card.mkdir(parents=True)
    (card / "card.json").write_text(
        json.dumps({"card_id": "c", "conversation_id": "c-1", "theme": "tools", "stations": [],
                    "origin": {"kind": "report_topic", "job_id": "analysis", "topic": 1}}),
        encoding="utf-8",
    )

    notes = prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="analysis"))

    assert sorted(p.name for p in (job_dir / "input" / "sources").iterdir()) == ["S2.md", "S3.md"]
    # 多帶的來源可能讓本機的上下文放不下：交回給執行器記進事件紀錄，也寫進記錄檔
    assert [n for n in notes if n.startswith("題材的來源對不上，帶了全部來源")]
    assert "題材的來源對不上" in caplog.text


def test_分鏡整篇不配圖_不帶圖文編排的手藝(tmp_path: Path) -> None:
    data = _data(tmp_path)
    _persona_parts(data)
    job_dir = _card_from_report(data, topic=1, storyboard=[])

    prepare_inputs(HARNESS, data, job_dir, _job("essay", parent="analysis"))

    assert sorted(p.name for p in (job_dir / "input" / "author" / "craft").iterdir()) == [
        "golden-lines-and-endings.md",
        "openings.md",
        "titles.md",
    ]


def test_改稿開單時放了新的快照_不拿上一版的蓋過去(tmp_path: Path) -> None:
    data = _data(tmp_path)
    v1 = _card_job_dir(data, job_id="v1")
    prepare_inputs(HARNESS, data, v1, _job("essay"))
    _checked(v1, "rewrite", 1, {"article.md": "# 第一版", "article.json": "{}"}, passed=True)
    v2 = _card_job_dir(data, job_id="v2")
    (v2 / "input" / "card" / "card.md").write_text("# 新的快照\n", encoding="utf-8")

    prepare_inputs(HARNESS, data, v2, _job("essay", parent="v1", feedback="改短"))

    assert (v2 / "input" / "card" / "card.md").read_text(encoding="utf-8") == "# 新的快照\n"
    placed = json.loads((v2 / "input" / ".prepared.json").read_text(encoding="utf-8"))["files"]
    assert not [f for f in placed if f.startswith("input/card/")]
