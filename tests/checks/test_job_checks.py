"""執行器呼叫檢查的入口：給工單資料夾與要檢查的產出路徑，拿回 `checks.vN.json` 的條目（SEAM-06）。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.checks import (
    CheckEntry,
    ChecksReport,
    Location,
    OverlapThresholds,
    SchemaError,
    run_compose_checks,
    run_digest_checks,
    write_checks_report,
)

pytestmark = pytest.mark.promise("FR-VF-01")

SOURCE = "# 推論變便宜了\n\n大型語言模型的推論成本，在過去一年降了九成。\n"

COMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["facts"],
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "required": ["fact"]}}
    },
}

THRESHOLDS = OverlapThresholds(
    ngram_size=5, max_ngram_overlap=0.3, max_common_tokens=12
)

CLEAN_DRAFT = "# 錢花在哪\n\n模型的帳單一年縮成一成[^S1]。\n\n[^S1]: 原文\n"
CLEAN_OUTPUT = {"facts": [{"fact": "模型的帳單一年縮成一成[^S1]"}]}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _job(tmp_path: Path, *, draft: str, output: object) -> tuple[Path, Path]:
    """建一個工單資料夾：原文、稿子、二創的 JSON 產出；skill 的 schema 放在工單外面。"""
    job = tmp_path / "jobs" / "job-1"
    _write(job / "input" / "content.md", SOURCE)
    _write(job / "drafts" / "draft.v1.md", draft)
    _write(job / "stages" / "compose" / "1" / "facts.json", json.dumps(output))
    schema = _write(
        tmp_path / "skills" / "compose" / "schema.json", json.dumps(COMPOSE_SCHEMA)
    )
    return job, schema


def _compose(job: Path, schema: Path) -> list[CheckEntry]:
    return run_compose_checks(
        job,
        draft=Path("drafts/draft.v1.md"),
        output=Path("stages/compose/1/facts.json"),
        schema=schema,
        sources={"S1": Path("input/content.md")},
        thresholds=THRESHOLDS,
    )


def test_image_copied_from_the_original_fails_c4_at_its_line_in_the_draft(
    tmp_path: Path,
) -> None:
    draft = CLEAN_DRAFT + "\n![原文的圖](https://example.com/a.png)\n"
    job, schema = _job(tmp_path, draft=draft, output=CLEAN_OUTPUT)

    c4 = [entry for entry in _compose(job, schema) if entry.check == "C4"]

    assert [(e.status, e.excerpt) for e in c4] == [
        ("fail", "https://example.com/a.png")
    ]
    assert c4[0].location is not None
    assert (c4[0].location.file, c4[0].location.line) == ("drafts/draft.v1.md", 7)


def test_image_listed_in_the_job_assets_passes_c4(tmp_path: Path) -> None:
    draft = CLEAN_DRAFT + "\n![授權過的圖](https://example.com/a.png)\n"
    job, schema = _job(tmp_path, draft=draft, output=CLEAN_OUTPUT)
    _write(
        job / "assets" / "assets.json",
        json.dumps([{"url": "https://example.com/a.png"}]),
    )

    c4 = [entry for entry in _compose(job, schema) if entry.check == "C4"]

    assert [entry.status for entry in c4] == ["pass"]


def test_clean_compose_output_passes_and_lands_in_checks_v1(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)

    report = ChecksReport(
        stage="compose",
        attempt=1,
        files=("drafts/draft.v1.md", "stages/compose/1/facts.json"),
        created_at=datetime(2026, 10, 11, 14, 3, tzinfo=UTC),
        entries=tuple(_compose(job, schema)),
    )
    written = write_checks_report(job, report)

    assert report.passed
    assert written.path == job / "checks" / "checks.v1.json"
    stored = json.loads(written.path.read_text(encoding="utf-8"))
    assert {entry["check"] for entry in stored["entries"]} == {"C2", "C3", "C4", "C5"}


def test_fabricated_quote_fails_c1_pointing_into_the_digest_output(
    tmp_path: Path,
) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    _write(
        job / "stages" / "digest" / "1" / "claims.json",
        json.dumps({"claims": [{"quote": "推論成本一點都沒降"}]}, ensure_ascii=False),
    )
    digest_schema = _write(
        tmp_path / "skills" / "digest" / "schema.json", json.dumps({"type": "object"})
    )

    entries = run_digest_checks(
        job,
        output=Path("stages/digest/1/claims.json"),
        schema=digest_schema,
        source=Path("input/content.md"),
    )

    c1 = [entry for entry in entries if entry.check == "C1"]
    assert [entry.status for entry in c1] == ["fail"]
    assert c1[0].location == Location(
        file="stages/digest/1/claims.json", pointer="/claims/0/quote"
    )


@pytest.mark.parametrize("broken", [None, '{"claims": [', "\udcff"])
def test_digest_output_that_is_missing_or_not_json_fails_c1_and_c5(
    tmp_path: Path, broken: str | None
) -> None:
    # 讀不出來的產出不能被當成「沒有引句」而讓 C1 過關
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    output = job / "stages" / "digest" / "1" / "claims.json"
    if broken is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(broken.encode("utf-8", "surrogateescape"))

    entries = run_digest_checks(
        job,
        output=Path("stages/digest/1/claims.json"),
        schema=schema,
        source=Path("input/content.md"),
    )

    assert [(entry.check, entry.status) for entry in entries] == [
        ("C1", "fail"),
        ("C5", "fail"),
    ]
    assert all(
        entry.location == Location(file="stages/digest/1/claims.json")
        for entry in entries
    )


def test_missing_draft_fails_every_compose_check(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    (job / "drafts" / "draft.v1.md").unlink()

    entries = _compose(job, schema)

    assert [(entry.check, entry.status) for entry in entries] == [
        ("C2", "fail"),
        ("C3", "fail"),
        ("C4", "fail"),
        ("C5", "fail"),
    ]


def test_compose_output_that_is_not_json_fails_c2_and_c5(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    (job / "stages" / "compose" / "1" / "facts.json").write_text("not json", "utf-8")

    failed = {entry.check for entry in _compose(job, schema) if entry.status == "fail"}

    assert failed == {"C2", "C5"}


@pytest.mark.parametrize("path", ["../other-job/drafts/draft.v1.md", "/etc/passwd"])
def test_paths_outside_the_job_folder_are_refused(tmp_path: Path, path: str) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)

    with pytest.raises(ValueError, match="工單資料夾"):
        run_compose_checks(
            job,
            draft=Path(path),
            output=Path("stages/compose/1/facts.json"),
            schema=schema,
            sources={"S1": Path("input/content.md")},
            thresholds=THRESHOLDS,
        )


def test_broken_skill_schema_is_a_setup_error_not_a_failed_output(
    tmp_path: Path,
) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    schema.write_text("[1, 2", encoding="utf-8")

    with pytest.raises(SchemaError):
        _compose(job, schema)


@pytest.mark.parametrize("contents", [None, "", "  \n\n"])
def test_compose_without_any_source_text_is_a_setup_error(
    tmp_path: Path, contents: str | None
) -> None:
    # 沒有原文可比，C3 不能因此判 pass：那是執行器沒把來源放好
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    sources = {"S1": Path("input/content.md")}
    if contents is None:
        sources = {}
    else:
        (job / "input" / "content.md").write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="來源"):
        run_compose_checks(
            job,
            draft=Path("drafts/draft.v1.md"),
            output=Path("stages/compose/1/facts.json"),
            schema=schema,
            sources=sources,
            thresholds=THRESHOLDS,
        )


def test_digest_with_an_empty_source_is_a_setup_error(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    (job / "input" / "content.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="原文"):
        run_digest_checks(
            job,
            output=Path("stages/compose/1/facts.json"),
            schema=schema,
            source=Path("input/content.md"),
        )


def test_unreadable_compose_output_still_reports_what_is_wrong_with_the_draft(
    tmp_path: Path,
) -> None:
    # JSON 壞了也要把稿子的問題（這裡是原文的圖）一起交回去，省一輪修正
    draft = CLEAN_DRAFT + "\n![原文的圖](https://example.com/a.png)\n"
    job, schema = _job(tmp_path, draft=draft, output=CLEAN_OUTPUT)
    (job / "stages" / "compose" / "1" / "facts.json").write_text("oops", "utf-8")

    statuses = {(entry.check, entry.status) for entry in _compose(job, schema)}

    assert {("C2", "fail"), ("C4", "fail"), ("C5", "fail")} <= statuses


def test_output_json_with_a_byte_order_mark_is_still_read(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, draft=CLEAN_DRAFT, output=CLEAN_OUTPUT)
    output = job / "stages" / "compose" / "1" / "facts.json"
    output.write_text(json.dumps(CLEAN_OUTPUT), encoding="utf-8-sig")

    assert all(entry.status == "pass" for entry in _compose(job, schema))
