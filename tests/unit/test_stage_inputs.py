"""執行器開段時把文類定義、作家設定、轉換說明放進工單的 `input/`（ADR-0015 §3.7、ADR-0018）。

agent 只看得到系統提示裡的 `SKILL.md` 與工單資料夾，`harness/` 與 `$EF_DATA/authors/` 都讀不到。
"""

from pathlib import Path

import pytest

from evidence_forge.budget import Budget
from evidence_forge.harness import Harness, default_harness
from evidence_forge.models import FeedRef, Job
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import (
    InputError,
    default_genre,
    prepare_inputs,
)

VOICE = """---
id: me
default_genre: opinion
calibrated: true
---

## 語氣

測試用的作家聲音。
"""


def _job(genre: str | None) -> Job:
    return Job(
        id="j1",
        recipe="restructure",
        feed=FeedRef(connector="manual", name="manual", item="inbox/manual/x"),
        goal="寫一篇",
        target_genre=genre,
        budget=Budget(usd=0.5),
        status="queued",
    )


def _data(tmp_path: Path, *, styles: dict[str, str] | None = None) -> DataDir:
    data = DataDir(tmp_path / "data")
    author = data.root / "authors" / "me"
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
def test_文類定義_作家聲音_文類風格_轉換說明都複製進_input(tmp_path: Path) -> None:
    data = _data(tmp_path, styles={"opinion": "---\ncalibrated: true\n---\n觀點文風格"})
    job_dir = _job_dir(tmp_path)

    prepare_inputs(HARNESS, data, job_dir, _job("opinion"))

    inputs = job_dir / "input"
    genres = (inputs / "genres.md").read_text(encoding="utf-8")
    for definition in (HARNESS.root / "genres").glob("*.md"):
        assert definition.read_text(encoding="utf-8") in genres
    assert (inputs / "target-genre.md").read_bytes() == (
        HARNESS.root / "genres" / "opinion.md"
    ).read_bytes()
    assert (inputs / "author" / "voice.md").read_text(encoding="utf-8") == VOICE
    assert (
        (inputs / "author" / "genre-style.md")
        .read_text(encoding="utf-8")
        .endswith("觀點文風格")
    )
    for conversion in (HARNESS.root / "conversions").glob("*.md"):
        assert (inputs / "conversions" / conversion.name).read_bytes() == (
            conversion.read_bytes()
        )


@pytest.mark.promise("FR-CP-02")
def test_作家沒有目標文類的風格檔_就不放_genre_style_md(tmp_path: Path) -> None:
    data = _data(tmp_path, styles={"explainer": "解說文風格"})
    job_dir = _job_dir(tmp_path)

    prepare_inputs(HARNESS, data, job_dir, _job("opinion"))

    assert not (job_dir / "input" / "author" / "genre-style.md").exists()
    assert (job_dir / "input" / "target-genre.md").is_file()


def test_再開一次段不覆寫_工單記得當時用的版本(tmp_path: Path) -> None:
    data = _data(tmp_path)
    job_dir = _job_dir(tmp_path)
    prepare_inputs(HARNESS, data, job_dir, _job("opinion"))

    # 作家改了聲音；重跑同一張單仍用開段當時那一份（只增不改）
    (data.root / "authors" / "me" / "voice.md").write_text("改過", encoding="utf-8")
    prepare_inputs(HARNESS, data, job_dir, _job("opinion"))

    assert (job_dir / "input" / "author" / "voice.md").read_text(
        encoding="utf-8"
    ) == VOICE


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
    assert sorted(p.name for p in (job_dir / "input").iterdir()) == ["content.md"]


@pytest.mark.promise("FR-CP-03")
def test_作家的預設文類從_voice_md_的_frontmatter_讀(tmp_path: Path) -> None:
    assert default_genre(_data(tmp_path)) == "opinion"
    assert default_genre(DataDir(tmp_path / "empty")) is None
