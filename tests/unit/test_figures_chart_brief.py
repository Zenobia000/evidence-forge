"""證據帳本與 chart brief 的檢查：結構與互相的引用（不驗來源真假、數字、畫面）。"""

import json
from pathlib import Path
from typing import Any

import pytest
from figures_world import brief, evidence

from evidence_forge.figures.chart_brief import (
    TITLE_MAX,
    validate_brief,
    validate_charts,
    validate_evidence,
)


def _ledger() -> dict[str, dict[str, Any]]:
    errors, ledger = validate_evidence(evidence())
    assert errors == []
    return ledger


# ---- 證據帳本 ----


def test_範例帳本通過() -> None:
    errors, by_id = validate_evidence(evidence())

    assert errors == []
    assert set(by_id) == {"survey-limits", "jobs-change", "jobs-raw", "why"}


@pytest.mark.parametrize("ledger", [{}, {"claims": []}, {"claims": "x"}])
def test_帳本沒有_claims_就是錯(ledger: dict[str, Any]) -> None:
    errors, by_id = validate_evidence(ledger)

    assert errors == ["evidence: claims must be a nonempty list"]
    assert by_id == {}


def _with_claim_change(claim_id: str, **change: Any) -> list[str]:
    ledger = evidence()
    claim = next(c for c in ledger["claims"] if c["claim_id"] == claim_id)
    for key, value in change.items():
        if value is None:
            claim.pop(key, None)
        else:
            claim[key] = value
    return validate_evidence(ledger)[0]


@pytest.mark.parametrize(
    ("claim_id", "change", "expected"),
    [
        ("survey-limits", {"source_location": None}, "observed claims need source_location"),
        ("survey-limits", {"kind": "guess"}, "kind must be one of"),
        ("survey-limits", {"statement": " "}, "statement required"),
        ("survey-limits", {"unit": None}, "need unit"),
        ("survey-limits", {"period": ""}, "need period"),
        ("survey-limits", {"population": None}, "need population"),
        ("survey-limits", {"values": {"a": "38"}}, "values must be a nonempty"),
        ("survey-limits", {"values": {"a": True}}, "values must be a nonempty"),
        ("survey-limits", {"values": {"a": 1}, "kind": "forecast", "source_location": None}, "forecast claims need source_location"),
        ("jobs-change", {"formula": None}, "derived claims need formula"),
        ("jobs-change", {"input_claim_ids": []}, "derived claims need input_claim_ids"),
        ("jobs-change", {"input_claim_ids": ["nope"]}, "input_claim_id nope not in ledger"),
        ("why", {"values": {"x": 1}}, "hypotheses must not carry values"),
    ],
)
def test_帳本每筆_claim_缺欄位或引用不到會被擋(
    claim_id: str, change: dict[str, Any], expected: str
) -> None:
    errors = _with_claim_change(claim_id, **change)

    assert any(expected in e and claim_id in e for e in errors), errors


def test_claim_id_重複或空白會被擋() -> None:
    ledger = evidence()
    ledger["claims"].append({"claim_id": "jobs-raw", "statement": "dup", "kind": "hypothesis"})
    ledger["claims"].append({"statement": "no id", "kind": "hypothesis"})

    errors, by_id = validate_evidence(ledger)

    assert sum("missing or duplicated" in e for e in errors) == 2
    assert by_id["jobs-raw"]["statement"] == "Postings count"  # 留下先出現的那筆


# ---- brief ----


def test_範例_brief_通過() -> None:
    assert validate_brief(brief(), _ledger()) == []


def test_沒給帳本就不查_claim_引用() -> None:
    data = brief()
    data["evidence_ids"] = ["ghost"]
    data["annotations"][0]["claim_ids"] = ["ghost"]
    data["annotations"][1]["claim_ids"] = ["ghost"]

    assert validate_brief(data, None) == []
    assert any("evidence_id ghost not in evidence.json" in e for e in validate_brief(data, _ledger()))


def test_缺必填欄位逐一指出() -> None:
    errors = validate_brief({}, None)

    for key in ("version", "exhibit", "takeaway", "panels", "outputs"):
        assert f"Missing field: {key}" in errors


def _brief_with(path: list[str | int], value: Any) -> list[str]:
    data = brief()
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return validate_brief(data, _ledger())


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (["version"], "2.0", "version must be 1.0"),
        (["exhibit", "title"], "字" * (TITLE_MAX + 1), f"keep the action title within {TITLE_MAX}"),
        (["exhibit", "number"], 0, "exhibit.number must be a positive integer"),
        (["exhibit", "number"], True, "exhibit.number must be a positive integer"),
        (["exhibit", "source"], None, "exhibit.source required"),
        (["engine"], "d3", "engine must be"),
        (["evidence_mode"], "vibes", "evidence_mode must be"),
        (["evidence_ids"], [], "Quantitative charts require evidence_ids"),
        (["evidence_ids"], ["jobs-change"], "claim_ids must be listed in evidence_ids"),  # 標註引用的 claim 不在清單內
        (["evidence_ids"], ["why"], "Quantitative chart cites no claim with values"),
        (["candidates"], [], "candidates must be nonempty"),
        (["candidates"], [{"form": "bars", "benefit": "b", "risk": "r"}], "At least two candidates"),
        (["candidates", 0, "risk"], "", "Each candidate requires form, benefit, risk"),
        (["canvas", "width"], 0, "canvas.width must be positive"),
        (["canvas", "unit"], "cm", "canvas.unit must be px, in or mm"),
        (["panels"], [], "panels must be nonempty"),
        (["panels", 0, "scale"], None, "Panel missing scale"),
        (["panels", 0, "anchor_ids"], ["bar-limit", "bar-limit"], "Duplicate anchor ID: bar-limit"),
        (["annotations", 0, "anchor_id"], "bar-ghost", "anchor_id not found in panels"),
        (["annotations", 0, "relation"], "decorate", "relation must be one of"),
        (["annotations", 0, "claim_ids"], ["jobs-raw"], "claim_ids must be listed in evidence_ids"),
        (["annotations", 1, "id"], "lbl", "Annotation IDs must be unique"),
        (["annotations"], [], "Empty annotations require annotation_decision"),
        (["source_notes"], "note", "source_notes must be a list"),
        (["outputs"], {"preview": "images/fig-1.png"}, "outputs requires preview and vector"),
    ],
)
def test_brief_的結構與引用錯誤會被擋(path: list[str | int], value: Any, expected: str) -> None:
    errors = _brief_with(path, value)

    assert any(expected in e for e in errors), errors


def test_一張圖最多一個_callout() -> None:
    data = brief()
    data["annotations"][0]["relation"] = "callout"

    assert "At most one callout (the 'so what' box) per chart" in validate_brief(data, _ledger())


def test_空標註加上_annotation_decision_就放行() -> None:
    data = brief()
    data["annotations"] = []
    data["annotation_decision"] = "Bars are self-explanatory"

    assert validate_brief(data, _ledger()) == []


def test_只有一個候選但寫了例外理由就放行() -> None:
    data = brief()
    data["candidates"] = data["candidates"][:1]
    data["candidate_exception"] = "Only one sensible form"

    assert validate_brief(data, _ledger()) == []


def test_定性圖不需要帶數字的_claim() -> None:
    data = brief()
    data["evidence_mode"] = "qualitative"
    data["evidence_ids"] = ["why"]
    data["annotations"] = []
    data["annotation_decision"] = "Diagram"

    assert validate_brief(data, _ledger()) == []


# ---- 整個 charts/ 資料夾 ----


def _charts_dir(root: Path) -> Path:
    charts = root / "charts"
    charts.mkdir()
    (charts / "evidence.json").write_text(json.dumps(evidence()), encoding="utf-8")
    (charts / "fig-1.brief.json").write_text(json.dumps(brief()), encoding="utf-8")
    return charts


def test_資料夾裡的帳本與_brief_一起驗過(tmp_path: Path) -> None:
    report = validate_charts(_charts_dir(tmp_path))

    assert report.ok
    assert report.briefs_checked == 1


def test_資料夾裡_brief_引用不存在的_claim_會指出是哪個檔(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)
    broken = brief()
    broken["evidence_ids"] = ["ghost"]
    (charts / "fig-2.brief.json").write_text(json.dumps(broken), encoding="utf-8")

    report = validate_charts(charts)

    assert not report.ok
    assert report.briefs_checked == 2
    assert any(e.startswith("fig-2.brief.json:") and "ghost" in e for e in report.errors)
    assert not any(e.startswith("fig-1.brief.json:") for e in report.errors)


def test_帳本本身壞了也會在資料夾檢查裡報(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)
    bad = evidence()
    bad["claims"][0].pop("source_location")
    (charts / "evidence.json").write_text(json.dumps(bad), encoding="utf-8")

    report = validate_charts(charts)

    assert any(e.startswith("evidence.json:") and "source_location" in e for e in report.errors)


def test_有_brief_卻沒有帳本是錯_引用不存在的_claim_不能過關(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)
    (charts / "evidence.json").unlink()
    data = brief()
    data["evidence_ids"] = ["ghost"]
    data["annotations"][0]["claim_ids"] = ["ghost"]
    data["annotations"][1]["claim_ids"] = ["ghost"]
    (charts / "fig-1.brief.json").write_text(json.dumps(data), encoding="utf-8")

    report = validate_charts(charts)

    assert not report.ok
    assert any("evidence.json" in e and "not found" in e for e in report.errors)


def test_單一_brief_旁邊沒有帳本也是錯(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)
    (charts / "evidence.json").unlink()

    assert not validate_charts(charts / "fig-1.brief.json").ok


def test_資料夾沒有_brief_是錯(tmp_path: Path) -> None:
    (tmp_path / "charts").mkdir()

    report = validate_charts(tmp_path / "charts")

    assert not report.ok and "no fig-*.brief.json" in report.errors[0]


def test_單一_brief_檔會去旁邊找帳本(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)

    assert validate_charts(charts / "fig-1.brief.json").ok


def test_不是_JSON_物件的檔案回成錯誤而不是例外(tmp_path: Path) -> None:
    charts = _charts_dir(tmp_path)
    (charts / "fig-1.brief.json").write_text("[1, 2]", encoding="utf-8")

    report = validate_charts(charts)
    assert any("must be a JSON object" in e for e in report.errors)

    (charts / "fig-1.brief.json").write_text("{not json", encoding="utf-8")
    assert not validate_charts(charts).ok


# ---- 形狀不對的引用要回成錯誤，不能丟 TypeError ----


@pytest.mark.parametrize(
    ("claim_id", "field", "value"),
    [
        ("jobs-change", "input_claim_ids", [{"id": "jobs-raw"}]),  # 元素是 dict
        ("jobs-change", "input_claim_ids", [["jobs-raw"]]),  # 元素是 list
        ("jobs-change", "input_claim_ids", 5),  # 不是清單
        ("jobs-change", "input_claim_ids", "jobs-raw"),  # 字串不是清單
        ("jobs-change", "input_claim_ids", {"a": 1}),
        ("jobs-raw", "input_claim_ids", [{"x": 1}]),  # 非 derived 的 claim 也不能亂放
        ("jobs-raw", "claim_id", {"a": 1}),
        ("jobs-raw", "claim_id", ["jobs-raw"]),
        ("jobs-raw", "kind", ["observed"]),
        ("jobs-raw", "values", [1, 2]),
    ],
)
def test_帳本形狀不對回成錯誤而不是例外(claim_id: str, field: str, value: Any) -> None:
    ledger = evidence()
    claim = next(c for c in ledger["claims"] if c["claim_id"] == claim_id)
    claim[field] = value

    errors, _ = validate_evidence(ledger)

    assert errors  # 沒有 TypeError，而且真的報了錯


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["annotations", 0, "anchor_id"], ["bar-limit"]),  # list 不能進 set 成員測試
        (["annotations", 0, "anchor_id"], {"a": 1}),
        (["annotations", 0, "anchor_id"], 5),
        (["annotations", 0, "claim_ids"], [["survey-limits"]]),
        (["annotations", 0, "claim_ids"], [{"id": 1}]),
        (["annotations", 0, "claim_ids"], 5),
        (["annotations", 0, "id"], ["lbl"]),
        (["annotations", 0, "relation"], ["callout"]),
        (["panels", 0, "id"], ["bars"]),
        (["panels", 0, "anchor_ids"], [["bar-limit"]]),
        (["panels", 0, "anchor_ids"], 5),
        (["evidence_ids"], [{"a": 1}]),
        (["evidence_ids"], 5),
        (["exhibit"], ["x"]),
        (["exhibit", "title"], 5),
        (["exhibit", "number"], [1]),
        (["candidates"], [5, "x"]),
        (["canvas"], 5),
        (["canvas", "unit"], ["px"]),
        (["outputs"], ["a"]),
        (["engine"], {"a": 1}),
    ],
)
def test_brief_形狀不對回成錯誤而不是例外(path: list[str | int], value: Any) -> None:
    errors = _brief_with(path, value)

    assert errors
