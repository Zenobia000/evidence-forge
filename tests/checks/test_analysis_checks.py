"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020）：brief 六節、報告五節依序、引用的編號在清單上。"""

from typing import Any

import pytest

from evidence_forge.checks import (
    BRIEF_SECTIONS,
    REPORT_SECTIONS,
    brief_checks,
    check_sections,
    check_source_refs,
    report_checks,
)

pytestmark = pytest.mark.promise("FR-AN-01", "FR-IV-01")

REPORT = """# 標題

## 作者在說什麼
一段話。

## 事實與觀點
事實與觀點。

## 查證（有疑慮的放最前面）
- K1 有爭議 [S2]

## 研究題材
- 題材 [S2]

## 來源清單
- S1 原文
- S2 一份報告
"""

OUTPUT: dict[str, Any] = {
    "queries": ["q"],
    "sources": [{"id": "S2", "url": "https://a.example/", "title": "t", "why": "w"}],
    "fact_checks": [{"claim": "K1", "verdict": "disputed", "sources": ["S2"]}],
    "topics": [{"title": "題材", "sources": ["S2"]}],
}
SCHEMA: dict[str, Any] = {"type": "object", "required": ["sources"]}


def _failed(entries: list[Any]) -> list[str]:
    return [entry.reason for entry in entries if entry.status == "fail"]


def test_報告五節依序都在_通過() -> None:
    entries = check_sections(REPORT, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_報告標題前面有編號或粗體_一樣認得() -> None:
    numbered = (
        "## 1. 作者在說什麼\n\n## 二、事實與觀點\n\n## **查證**\n\n"
        "### 4) 研究題材\n\n## 來源清單 ##\n"
    )
    entries = check_sections(numbered, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_報告少了研究題材_點名那一節() -> None:
    entries = check_sections(
        REPORT.replace("## 研究題材", "## 延伸閱讀"),
        REPORT_SECTIONS,
        file="report.md",
        ordered=True,
    )
    assert _failed(entries) == ["少了「研究題材」一節（要有一個標題是「研究題材」）"]


def test_報告的查證排在研究題材後面_順序不對() -> None:
    swapped = REPORT.replace("## 查證（有疑慮的放最前面）", "## 暫時").replace(
        "## 來源清單", "## 查證\n\n## 來源清單"
    )
    entries = check_sections(swapped, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == ["「研究題材」一節要排在「查證」後面"]


def test_程式碼區塊裡的標題不算() -> None:
    fenced = REPORT.replace("## 研究題材", "```\n## 研究題材\n```")
    entries = check_sections(fenced, REPORT_SECTIONS, file="report.md", ordered=True)
    assert "少了「研究題材」一節（要有一個標題是「研究題材」）" in _failed(entries)


def test_內文裡提到節名不算一節() -> None:
    # 「查證」只出現在內文、不是標題：還是少一節
    text = REPORT.replace("## 查證（有疑慮的放最前面）\n", "查證的結果如下。\n")
    entries = check_sections(text, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == ["少了「查證」一節（要有一個標題是「查證」）"]


BRIEF = "\n\n".join(f"## {title}\n內容" for title in reversed(BRIEF_SECTIONS))


def test_brief_六節都在就過_順序不拘() -> None:
    entries = check_sections(BRIEF, BRIEF_SECTIONS, file="brief.md", ordered=False)
    assert _failed(entries) == []


def test_brief_的觀點一節不會被事實與數據頂替() -> None:
    # 「觀點」不能因為別的標題裡有這兩個字就算數
    text = BRIEF.replace("## 觀點\n", "## 想法\n")
    entries = check_sections(text, BRIEF_SECTIONS, file="brief.md", ordered=False)
    assert _failed(entries) == ["少了「觀點」一節（要有一個標題是「觀點」）"]


def test_查證與題材引用的編號都在清單上_通過() -> None:
    assert _failed(check_source_refs(OUTPUT, file="sources.json")) == []


def test_查證引用了清單上沒有的編號_指到那一格() -> None:
    output = {
        **OUTPUT,
        "fact_checks": [{"claim": "K1", "verdict": "supported", "sources": ["S9"]}],
    }
    [entry] = check_source_refs(output, file="sources.json")
    assert entry.status == "fail"
    assert entry.reason == "查證引用的 S9 不在來源清單上"
    assert entry.location is not None
    assert entry.location.pointer == "/fact_checks/0/sources/0"


def test_研究題材引用了清單上沒有的編號() -> None:
    output = {**OUTPUT, "topics": [{"title": "x", "sources": ["S2", "S3"]}]}
    assert _failed(check_source_refs(output, file="sources.json")) == [
        "研究題材引用的 S3 不在來源清單上"
    ]


def test_報告的檢查合起來_Markdown_五節_schema_引用() -> None:
    entries = report_checks(
        report=REPORT,
        report_file="report.md",
        output=OUTPUT,
        output_file="sources.json",
        schema=SCHEMA,
    )
    assert _failed(entries) == []
    assert {entry.check for entry in entries} == {"C5"}


def test_brief_的_JSON_不合_schema_一樣擋() -> None:
    entries = brief_checks(
        brief=BRIEF,
        brief_file="brief.md",
        output={"topics": []},
        output_file="brief.json",
        schema={"type": "object", "required": ["key_claims"]},
    )
    assert any("key_claims" in reason for reason in _failed(entries))


def test_報告整份包在程式碼區塊裡_不是標準_Markdown() -> None:
    entries = report_checks(
        report=f"```markdown\n{REPORT}```\n",
        report_file="report.md",
        output=OUTPUT,
        output_file="sources.json",
        schema=SCHEMA,
    )
    assert "整份稿子包在程式碼區塊裡" in _failed(entries)
