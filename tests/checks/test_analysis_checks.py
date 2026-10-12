"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020）：brief 六節、報告五節依序、引用的編號在清單上。"""

from typing import Any

import pytest

from evidence_forge.checks import (
    BRIEF_SECTIONS,
    REPORT_SECTIONS,
    brief_checks,
    check_claim_coverage,
    check_report_citations,
    check_sections,
    check_source_list_urls,
    check_source_refs,
    check_verdict_order,
    report_checks,
)
from evidence_forge.checks.text import normalize_url

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
        "## 4) 研究題材\n\n## 來源清單 ##\n"
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


FETCHED = {"https://a.example"}


def test_報告的檢查合起來_Markdown_五節_schema_引用() -> None:
    entries = report_checks(
        report=REPORT,
        report_file="report.md",
        output=OUTPUT,
        output_file="sources.json",
        schema=SCHEMA,
        allowed_urls=FETCHED,
        key_claims=["K1"],
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
        allowed_urls=FETCHED,
        key_claims=None,
    )
    assert "整份稿子包在程式碼區塊裡" in _failed(entries)


# --- PR #88 第一輪審查：H1、報告本身的出處、關鍵主張、有疑慮的排前面 -----------------


def test_文章標題以節名開頭也不算一節_只看二級標題() -> None:
    # 分析一篇標題是「查證：…」的文章：H1 不能被當成「查證」一節，害順序檢查每次都錯
    titled = "# 查證：AI 寫的新聞有多少錯\n\n" + REPORT.split("\n", 1)[1]
    entries = check_sections(titled, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_三級標題不算一節() -> None:
    text = REPORT.replace("## 研究題材", "### 研究題材")
    entries = check_sections(text, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == ["少了「研究題材」一節（要有一個標題是「研究題材」）"]


def test_報告引用的編號都在清單上或是原文_通過() -> None:
    text = REPORT + "\n原文的說法 [S1]，另見 [S2]。\n"
    assert _failed(check_report_citations(text, {"S2"}, file="report.md")) == []


def test_報告引用了清單上沒有的編號_點名那一個_只報一次() -> None:
    text = REPORT + "\n另見 [S9]，又見 [S2、S9]，再見 [S2, S7]。\n"
    assert _failed(check_report_citations(text, {"S2"}, file="report.md")) == [
        "報告引用的 S9 不在來源清單上",
        "報告引用的 S7 不在來源清單上",
    ]


def test_程式碼裡的編號不算引用() -> None:
    text = REPORT + "\n`[S9]` 是引用的寫法。\n"
    assert _failed(check_report_citations(text, {"S2"}, file="report.md")) == []


SOURCE_LIST = REPORT.replace(
    "- S2 一份報告\n",
    "- S2 一份報告，https://a.example/report/。\n- S3 [另一份](https://b.example/x)\n",
)


def test_來源清單的網址都抓過_通過_結尾斜線與標點不算差別() -> None:
    allowed = {"https://a.example/report", "https://b.example/x"}
    assert _failed(check_source_list_urls(SOURCE_LIST, allowed, file="report.md")) == []


def test_來源清單列了沒抓過的網址_點名那一個() -> None:
    allowed = {"https://a.example/report"}
    assert _failed(check_source_list_urls(SOURCE_LIST, allowed, file="report.md")) == [
        "來源清單列了 https://b.example/x：這一段沒有用 fetch_url 抓到它，也不是原文"
    ]


def test_來源清單以外的網址不歸這項管() -> None:
    # 內文提到的網址不是來源清單；引用靠 [S<n>] 對到清單
    text = REPORT.replace("一段話。", "見 https://elsewhere.example/a。")
    assert _failed(check_source_list_urls(text, set(), file="report.md")) == []


def _checks(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {
        "fact_checks": [
            {"claim": claim, "verdict": verdict, "sources": ["S2"]}
            for claim, verdict in pairs
        ]
    }


def test_每條關鍵主張剛好一筆查證_通過() -> None:
    output = _checks(("K2", "not_found"), ("K1", "supported"))
    assert _failed(check_claim_coverage(output, ["K1", "K2"], file="sources.json")) == []


def test_關鍵主張漏查_重複_多查都擋() -> None:
    output = _checks(("K1", "supported"), ("K1", "disputed"), ("K4", "supported"))
    assert _failed(check_claim_coverage(output, ["K1", "K2"], file="sources.json")) == [
        "K1 查證了兩次（第 1 筆與第 2 筆），每條關鍵主張只能一筆",
        "查證了 K4，brief 沒有這條關鍵主張",
        "brief 的關鍵主張 K2 沒有查證結果",
    ]


def test_有疑慮的都在有佐證前面_通過() -> None:
    output = _checks(
        ("K3", "disputed"), ("K2", "outdated"), ("K4", "not_found"), ("K1", "supported")
    )
    assert _failed(check_verdict_order(output, file="sources.json")) == []


def test_有爭議排在有佐證後面_擋() -> None:
    output = _checks(("K1", "supported"), ("K2", "disputed"))
    assert _failed(check_verdict_order(output, file="sources.json")) == [
        "K2（disputed）有疑慮，要排在有佐證的 K1 前面"
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://A.Example/x/", "https://a.example/x"),
        (" http://a.example ", "http://a.example"),
        ("https://a.example/p?q=1#frag", "https://a.example/p?q=1"),
        ("https://a.example/?", "https://a.example"),
        ("https://a.example/p/?a=1&b=2", "https://a.example/p?a=1&b=2"),
        ("https://a.example:8080/中文/", "https://a.example:8080/中文"),
        ("不是網址#x", "不是網址"),
    ],
)
def test_網址正規化_執行器與報告檢查用同一套(url: str, expected: str) -> None:
    assert normalize_url(url) == expected
