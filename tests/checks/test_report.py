"""檢查結果的格式與寫檔：`checks/checks.vN.json`；軟檢查 C6、C7 的介面。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from evidence_forge.checks import (
    CheckEntry,
    ChecksReport,
    Location,
    checks_report_schema,
    parse_soft_checks,
    write_checks_report,
)

pytestmark = pytest.mark.promise("FR-VF-01")

NOW = datetime(2026, 10, 11, 14, 3, tzinfo=UTC)


def _report(*entries: CheckEntry) -> ChecksReport:
    return ChecksReport(
        stage="compose",
        attempt=1,
        files=("drafts/draft.v1.md",),
        created_at=NOW,
        entries=entries,
    )


C1_FAIL = CheckEntry(
    check="C1",
    status="fail",
    reason="引句在原文裡找不到",
    location=Location(file="claims.json", pointer="/claims/0/quote"),
    excerpt="捏造的話",
)
C6_WARN = CheckEntry(check="C6", status="warn", reason="來源只部分支持這句")


def test_reports_are_written_as_new_versions_and_never_overwritten(
    tmp_path: Path,
) -> None:
    first = write_checks_report(tmp_path, _report(C1_FAIL))
    second = write_checks_report(tmp_path, _report(C6_WARN))
    assert first.path == tmp_path / "checks" / "checks.v1.json"
    assert second.path == tmp_path / "checks" / "checks.v2.json"
    reread = ChecksReport.model_validate_json(first.path.read_text(encoding="utf-8"))
    assert reread.entries == (C1_FAIL,)


def test_written_file_matches_the_published_schema(tmp_path: Path) -> None:
    written = write_checks_report(tmp_path, _report(C1_FAIL, C6_WARN))
    data = json.loads(written.path.read_text(encoding="utf-8"))
    Draft202012Validator(checks_report_schema()).validate(data)


def test_published_schema_rejects_an_unknown_status() -> None:
    data = json.loads(_report(C1_FAIL).model_dump_json())
    data["entries"][0]["status"] = "maybe"
    errors = list(Draft202012Validator(checks_report_schema()).iter_errors(data))
    assert errors


def test_failed_checks_list_each_hard_check_once_and_warnings_do_not_block() -> None:
    c3_summary = CheckEntry(check="C3", status="fail", reason="重疊太多")
    c3_span = CheckEntry(check="C3", status="fail", reason="這段一字不差")
    report = _report(c3_span, C6_WARN, C1_FAIL, c3_summary)
    assert report.failed_checks == ("C1", "C3")
    assert report.passed is False
    assert _report(C6_WARN).passed is True


def test_soft_checks_cannot_fail() -> None:
    with pytest.raises(ValueError, match="C7"):
        CheckEntry(check="C7", status="fail", reason="沒照目標寫")


def test_every_entry_needs_a_reason() -> None:
    with pytest.raises(ValueError):
        CheckEntry(check="C2", status="pass", reason="  ")


def test_verify_stage_output_yields_soft_warnings_with_reasons() -> None:
    payload = {
        "entries": [
            {
                "check": "C6",
                "status": "warn",
                "reason": "來源只說了成本下降，沒說降了九成",
                "location": {"file": "drafts/draft.v1.md", "line": 3},
                "excerpt": "推論成本一年降了九成[^S1]",
            },
            {"check": "C7", "status": "pass", "reason": "照目標寫了 1,500 字觀點文"},
        ]
    }
    entries = parse_soft_checks(payload)
    assert [(entry.check, entry.status) for entry in entries] == [
        ("C6", "warn"),
        ("C7", "pass"),
    ]


def test_verify_stage_cannot_report_hard_checks() -> None:
    # SEAM-06 的攻擊案例：模型自己寫一份「C1～C5 全部通過」，執行器不能收
    payload = {"entries": [{"check": "C1", "status": "pass", "reason": "都對"}]}
    with pytest.raises(ValueError):
        parse_soft_checks(payload)


def test_verify_stage_cannot_turn_a_soft_check_into_a_blocker() -> None:
    payload = {"entries": [{"check": "C6", "status": "fail", "reason": "不支持"}]}
    with pytest.raises(ValueError):
        parse_soft_checks(payload)


def test_soft_warning_without_a_reason_is_rejected() -> None:
    payload = {"entries": [{"check": "C6", "status": "warn", "reason": ""}]}
    with pytest.raises(ValueError):
        parse_soft_checks(payload)


def test_published_schema_rejects_a_failing_soft_check() -> None:
    data = json.loads(_report(C6_WARN).model_dump_json())
    data["entries"][0]["status"] = "fail"
    errors = list(Draft202012Validator(checks_report_schema()).iter_errors(data))
    assert errors
