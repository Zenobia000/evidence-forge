"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020、ADR-0024）：brief 六節、報告五節依序、
第一個標題前有一句話、報告沒有裸露的 K／S 編號、可以寫的題材有編號、清單上的編號與網址對得上。"""

from typing import Any

import pytest

from evidence_forge.checks import (
    BRIEF_SECTIONS,
    REPORT_SECTIONS,
    brief_checks,
    check_claim_coverage,
    check_fact_alignment,
    check_internal_ids,
    check_lead,
    check_sections,
    check_source_list_urls,
    check_source_refs,
    check_topic_alignment,
    check_topics_numbered,
    check_verdict_order,
    report_checks,
)
from evidence_forge.checks.text import normalize_url

pytestmark = pytest.mark.promise("FR-AN-01", "FR-IV-01")

REPORT = """這篇在說延長開放值不值得，要看晚上的人數撐不撐得住。

▶ 下一步：下面有 2 個可以寫的題材，說「寫第 2 個」就開始。

## 作者在說什麼
一段話。

## 查證（有疑慮的放最前面）
- 【有爭議】一條事實：價目表與廠商部落格的算法對不上。

## 作者的看法
- 看法，不是事實。

## 可以寫的題材

### 1. 題材一
- 角度：一句話。

### 2. 題材二
- 角度：一句話。

## 來源清單
- 原文
- 一份報告
"""

OUTPUT: dict[str, Any] = {
    "queries": ["q"],
    "sources": [{"id": "S2", "url": "https://a.example/", "title": "t", "why": "w"}],
    "fact_checks": [{"claim": "K1", "verdict": "disputed", "sources": ["S2"]}],
    "topics": [{"title": "題材一", "sources": ["S2"]}, {"title": "題材二", "sources": ["S2"]}],
}
SCHEMA: dict[str, Any] = {"type": "object", "required": ["sources"]}


def _failed(entries: list[Any]) -> list[str]:
    return [entry.reason for entry in entries if entry.status == "fail"]


def test_報告的五節是新的節名與順序() -> None:
    # ADR-0024：事實併進查證、觀點獨立成「作者的看法」、研究題材換成可以寫的題材
    assert REPORT_SECTIONS == ("作者在說什麼", "查證", "作者的看法", "可以寫的題材", "來源清單")


def test_報告五節依序都在_通過() -> None:
    entries = check_sections(REPORT, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_報告標題前面有編號或粗體_一樣認得() -> None:
    numbered = (
        "## 1. 作者在說什麼\n\n## 二、**查證**\n\n## 作者的看法\n\n"
        "## 4) 可以寫的題材\n\n## 來源清單 ##\n"
    )
    entries = check_sections(numbered, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_報告還是舊的研究題材_點名少了可以寫的題材() -> None:
    entries = check_sections(
        REPORT.replace("## 可以寫的題材", "## 研究題材"),
        REPORT_SECTIONS,
        file="report.md",
        ordered=True,
    )
    assert _failed(entries) == ["少了「可以寫的題材」一節（要有一個標題是「可以寫的題材」）"]


def test_舊的事實與觀點一節頂替不了作者的看法() -> None:
    entries = check_sections(
        REPORT.replace("## 作者的看法", "## 事實與觀點"),
        REPORT_SECTIONS,
        file="report.md",
        ordered=True,
    )
    assert _failed(entries) == ["少了「作者的看法」一節（要有一個標題是「作者的看法」）"]


def test_作者的看法排在查證前面_順序不對() -> None:
    swapped = REPORT.replace("## 查證（有疑慮的放最前面）", "## 暫時").replace(
        "## 可以寫的題材", "## 查證\n\n## 可以寫的題材"
    )
    entries = check_sections(swapped, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == ["「作者的看法」一節要排在「查證」後面"]


def test_程式碼區塊裡的標題不算() -> None:
    fenced = REPORT.replace("## 可以寫的題材", "```\n## 可以寫的題材\n```")
    entries = check_sections(fenced, REPORT_SECTIONS, file="report.md", ordered=True)
    assert "少了「可以寫的題材」一節（要有一個標題是「可以寫的題材」）" in _failed(entries)


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


def test_可以寫的題材引用了清單上沒有的編號() -> None:
    output = {**OUTPUT, "topics": [{"title": "x", "sources": ["S2", "S3"]}]}
    assert _failed(check_source_refs(output, file="sources.json")) == [
        "可以寫的題材引用的 S3 不在來源清單上"
    ]


FETCHED = {"https://a.example"}


def test_報告的檢查合起來_Markdown_五節_一句話_沒有編號_題材有編號_schema() -> None:
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
    titled = "# 查證：AI 寫的新聞有多少錯\n\n" + REPORT
    entries = check_sections(titled, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == []


def test_三級標題不算一節() -> None:
    text = REPORT.replace("## 可以寫的題材", "### 可以寫的題材")
    entries = check_sections(text, REPORT_SECTIONS, file="report.md", ordered=True)
    assert _failed(entries) == ["少了「可以寫的題材」一節（要有一個標題是「可以寫的題材」）"]


SOURCE_LIST = REPORT.replace(
    "- 一份報告\n",
    "- 一份報告，https://a.example/report/。\n- [另一份](https://b.example/x)\n",
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
    # 內文提到的網址不是來源清單
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


# --- PR #88 第二輪審查：網址帶括號 ---------------------------------------------------

PAREN = "https://example.com/wiki/Example_(film)"


@pytest.mark.parametrize(
    "line",
    [
        f"- 電影條目，{PAREN}",
        f"- [電影條目]({PAREN})",
        f"- 電影條目（{PAREN}）",
        f"- 電影條目 ({PAREN}).",
    ],
    ids=["網址帶括號", "Markdown連結裡的網址帶括號", "全形括號包住", "半形括號包住加句點"],
)
def test_來源清單的網址帶括號_抓過完整網址就過(line: str) -> None:
    report = REPORT.replace("- 一份報告\n", line + "\n")
    allowed = {normalize_url(PAREN)}
    assert _failed(check_source_list_urls(report, allowed, file="report.md")) == []


@pytest.mark.parametrize(
    "line",
    [f"- 電影條目，{PAREN}", f"- [電影條目]({PAREN})"],
    ids=["網址帶括號", "Markdown連結裡的網址帶括號"],
)
def test_來源清單的網址帶括號_只抓過前綴不能放行(line: str) -> None:
    report = REPORT.replace("- 一份報告\n", line + "\n")
    allowed = {normalize_url("https://example.com/wiki/Example_")}
    assert _failed(check_source_list_urls(report, allowed, file="report.md")) == [
        f"來源清單列了 {PAREN}：這一段沒有用 fetch_url 抓到它，也不是原文"
    ]


# --- ADR-0024：第一個標題前的一句話、沒有裸露編號、可以寫的題材有編號 ---------------


def test_第一個標題前有一句話與下一步_題材數對得上_通過() -> None:
    assert _failed(check_lead(REPORT, file="report.md")) == []


@pytest.mark.parametrize(
    "report",
    [
        REPORT[REPORT.index("## 作者在說什麼") :],
        "# 原文的標題\n\n" + REPORT[REPORT.index("## 作者在說什麼") :],
    ],
    ids=["直接從第一節開始", "只有一個一級標題"],
)
def test_第一個標題前沒有那一句話_擋(report: str) -> None:
    assert "第一個「##」標題之前要先有一句話，講這篇在講什麼" in _failed(
        check_lead(report, file="report.md")
    )


def test_第一行就是下一步_沒有那一句話_擋() -> None:
    report = REPORT[REPORT.index("▶ 下一步") :]
    assert _failed(check_lead(report, file="report.md")) == [
        "第一個「##」標題之前的第一行要是一句話講這篇在講什麼，不是下一步"
    ]


def test_沒有下一步那一行_擋() -> None:
    report = REPORT.replace("▶ 下一步：下面有 2 個可以寫的題材，說「寫第 2 個」就開始。\n", "")
    assert _failed(check_lead(report, file="report.md")) == [
        "第一個「##」標題之前要有一行「▶ 下一步：下面有 N 個可以寫的題材…」"
    ]


@pytest.mark.parametrize("count", ["3", "三"])
def test_下一步寫的題材數跟題材對不上_擋(count: str) -> None:
    report = REPORT.replace("下面有 2 個", f"下面有 {count} 個")
    assert _failed(check_lead(report, file="report.md")) == [
        f"下一步那一行說有 {3} 個題材，「可以寫的題材」實際有 2 個"
    ]


def test_下一步寫中文數字也認得() -> None:
    report = REPORT.replace("下面有 2 個", "下面有兩個")
    assert _failed(check_lead(report, file="report.md")) == []


def test_報告沒有裸露的編號_通過() -> None:
    assert _failed(check_internal_ids(REPORT, file="report.md")) == []


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("- 【有佐證】K1 開放時間延長：見 S2。", ["K1", "S2"]),
        ("- 【有爭議】兩份說法不同 [S2、S3]。", ["S2", "S3"]),
        ("- S1 原文", ["S1"]),
        ("K12：數字對不上", ["K12"]),
    ],
    ids=["主張與來源編號", "方括號引用", "來源清單的原文編號", "兩位數"],
)
def test_報告露出內部編號_每個編號點名一次(line: str, found: list[str]) -> None:
    text = REPORT.replace("一段話。", line + "\n\n再提一次 " + found[0] + "。")
    failures = [e for e in check_internal_ids(text, file="report.md") if e.status == "fail"]
    assert [e.excerpt for e in failures] == found
    assert all(e.location is not None and e.location.line for e in failures)
    assert failures[0].reason == (
        f"報告裡出現內部編號 {found[0]}：K、S 編號只留在 sources.json，報告裡來源用名字"
    )


@pytest.mark.parametrize(
    "line",
    [
        "部署在 K8s 上，用 `S3` 存檔。",
        "SKU12 與 KS2023 都不是編號。",
        "見 https://a.example/S2/K1 這一頁。",
        "```\nS2 = 1\n```",
    ],
    ids=["字的一部分與程式碼", "較長的英數字", "網址裡的", "程式碼區塊"],
)
def test_不是內部編號的不算(line: str) -> None:
    text = REPORT.replace("一段話。", line)
    assert _failed(check_internal_ids(text, file="report.md")) == []


def test_給了用到的編號_只擋清單上的_產品名不誤擋() -> None:
    text = REPORT.replace("一段話。", "Amazon S3 與 Galaxy S24 都不是編號，見 S2 才是。")
    failures = _failed(check_internal_ids(text, file="report.md", known={"S1", "S2", "K1"}))
    assert len(failures) == 1 and "S2" in failures[0]


def test_報告的檢查合起來_只擋這一份用到的編號() -> None:
    entries = report_checks(
        report=REPORT.replace("一段話。", "存在 Amazon S3。"),
        report_file="report.md",
        output=OUTPUT,
        output_file="sources.json",
        schema=SCHEMA,
        allowed_urls=FETCHED,
        key_claims=["K1"],
    )
    assert _failed(entries) == []


def test_可以寫的題材兩個以上而且有編號_通過() -> None:
    assert _failed(check_topics_numbered(REPORT, file="report.md")) == []


def test_可以寫的題材寫成有編號的清單_不算_只認三級標題() -> None:
    # 畫面只認 `### 1.`；檢查跟畫面認同一種寫法，不然畫面上的編號會不見
    listed = REPORT.replace("### 1. 題材一\n- 角度：一句話。", "1. **題材一**\n   - 角度：一句話。")
    listed = listed.replace("### 2. 題材二\n- 角度：一句話。", "2. **題材二**\n   - 角度：一句話。")
    assert _failed(check_topics_numbered(listed, file="report.md")) == [
        "「可以寫的題材」要至少 2 個有編號的題材（### 1. 暫定標題），現在有 0 個"
    ]


def test_題材標題加粗也認得() -> None:
    bold = REPORT.replace("### 1. 題材一", "### 1. **題材一**")
    assert _failed(check_topics_numbered(bold, file="report.md")) == []
    assert _failed(check_topic_alignment(bold, OUTPUT, file="report.md")) == []


def test_可以寫的題材只有一個_擋() -> None:
    single = REPORT.replace("### 2. 題材二\n- 角度：一句話。\n", "")
    assert _failed(check_topics_numbered(single, file="report.md")) == [
        "「可以寫的題材」要至少 2 個有編號的題材（### 1. 暫定標題），現在有 1 個"
    ]


def test_可以寫的題材沒有編號_擋() -> None:
    bare = REPORT.replace("### 1. 題材一", "### 題材一").replace("### 2. 題材二", "### 題材二")
    assert _failed(check_topics_numbered(bare, file="report.md")) == [
        "「可以寫的題材」要至少 2 個有編號的題材（### 1. 暫定標題），現在有 0 個"
    ]


def test_可以寫的題材編號跳號_擋() -> None:
    skipped = REPORT.replace("### 2. 題材二", "### 3. 題材二")
    assert _failed(check_topics_numbered(skipped, file="report.md")) == [
        "可以寫的題材的編號要從 1 起連續，現在是 1、3"
    ]


def test_報告的檢查合起來_露出編號就不過() -> None:
    entries = report_checks(
        report=REPORT.replace("一段話。", "見 S2。"),
        report_file="report.md",
        output=OUTPUT,
        output_file="sources.json",
        schema=SCHEMA,
        allowed_urls=FETCHED,
        key_claims=["K1"],
    )
    assert any("S2" in reason for reason in _failed(entries))


# --- T3 第一輪：報告的查證、題材要跟 sources.json 對得上 --------------------------------


def _verdicts(*verdicts: str) -> dict[str, Any]:
    return {
        **OUTPUT,
        "fact_checks": [
            {"claim": f"K{i}", "verdict": v, "sources": ["S2"]} for i, v in enumerate(verdicts, 1)
        ],
    }


VERIFY_TWO = REPORT.replace(
    "- 【有爭議】一條事實：價目表與廠商部落格的算法對不上。",
    "- 【有爭議】一條事實：價目表與廠商部落格的算法對不上。\n  - 補一句說明\n- 【有佐證】另一條：延遲說明寫了。",
)


def test_查證的標記跟_fact_checks_一條一條對得上_通過() -> None:
    output = _verdicts("disputed", "supported")
    assert _failed(check_fact_alignment(VERIFY_TWO, output, file="report.md")) == []


@pytest.mark.parametrize(
    ("verdicts", "reason"),
    [
        (("supported", "disputed"), "報告是 有爭議、有佐證，sources.json 是 有佐證、有爭議"),
        (("disputed",), "報告是 有爭議、有佐證，sources.json 是 有爭議"),
        (("disputed", "supported", "not_found"), "報告是 有爭議、有佐證，sources.json 是 有爭議、有佐證、查不到"),
    ],
    ids=["順序不同", "報告多一條", "報告少一條"],
)
def test_查證的標記跟_fact_checks_對不上_擋(verdicts: tuple[str, ...], reason: str) -> None:
    [failure] = _failed(check_fact_alignment(VERIFY_TWO, _verdicts(*verdicts), file="report.md"))
    assert failure == "查證一節的標記要跟 sources.json 的 fact_checks 一條一條對上：" + reason


def test_查證有一條沒有標記_擋() -> None:
    text = VERIFY_TWO.replace("- 【有佐證】另一條", "- 另一條")
    [failure] = _failed(check_fact_alignment(text, _verdicts("disputed", "supported"), file="report.md"))
    assert "有爭議、沒有標記" in failure


def test_查證一個標記都沒有_擋() -> None:
    text = REPORT.replace("- 【有爭議】一條事實：", "一條事實有爭議：")
    [failure] = _failed(check_fact_alignment(text, OUTPUT, file="report.md"))
    assert "報告是 沒有標記，" in failure
    empty = REPORT.replace("- 【有爭議】一條事實：價目表與廠商部落格的算法對不上。\n", "")
    [failure] = _failed(check_fact_alignment(empty, OUTPUT, file="report.md"))
    assert "報告是 （沒有）" in failure


def test_題材的數目與標題跟_topics_對得上_通過() -> None:
    assert _failed(check_topic_alignment(REPORT, OUTPUT, file="report.md")) == []


def test_題材標題跟_topics_不一樣_擋() -> None:
    output = {**OUTPUT, "topics": [{"title": "題材一", "sources": ["S2"]}, {"title": "別的", "sources": ["S2"]}]}
    assert _failed(check_topic_alignment(REPORT, output, file="report.md")) == [
        "可以寫的題材要跟 sources.json 的 topics 一樣多、標題一樣、順序一樣：第 2 個報告是「題材二」，sources.json 是「別的」"
    ]


def test_題材數目跟_topics_不一樣_擋() -> None:
    output = {**OUTPUT, "topics": [*OUTPUT["topics"], {"title": "題材三", "sources": ["S2"]}]}
    assert _failed(check_topic_alignment(REPORT, output, file="report.md")) == [
        "可以寫的題材要跟 sources.json 的 topics 一樣多、標題一樣、順序一樣：報告有 2 個，sources.json 有 3 個"
    ]


def test_報告的檢查合起來_查證或題材對不上就不過() -> None:
    entries = report_checks(
        report=REPORT,
        report_file="report.md",
        output=_verdicts("supported"),
        output_file="sources.json",
        schema=SCHEMA,
        allowed_urls=FETCHED,
        key_claims=["K1"],
    )
    assert any("fact_checks" in reason for reason in _failed(entries))
