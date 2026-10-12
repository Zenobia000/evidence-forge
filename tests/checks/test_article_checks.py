"""重構段的程式檢查（PRD v3.0 §7、ADR-0020 §3.4、`rewrite.feature`）：

- 擋：文章是標準 Markdown；結尾的「參考來源」一定列原文出處（有網址看網址，沒有看標題）。
- 只顯示、不擋：文章正文跟原文、跟每一份來源的逐字重疊率，各一條 C3，只會 pass 或 warn。
"""

import pytest

from evidence_forge.checks import (
    CheckEntry,
    OverlapThresholds,
    article_checks,
    check_references,
)

pytestmark = pytest.mark.promise("FR-RW-01")

FILE = "stages/rewrite/1/article.md"
URL = "https://seed.example/posts/night-library"
TITLE = "夜間圖書館的帳"
THRESHOLDS = OverlapThresholds(
    ngram_size=5, max_ngram_overlap=0.1, max_common_tokens=12
)
SCHEMA = {"type": "object", "required": ["title"]}

BODY = "# 圖書館開到半夜，值得嗎\n\n我認為延長開放的成本不在電費，在人。\n\n"
REFERENCES = (
    "## 參考來源\n\n"
    f"- 王小明，〈{TITLE}〉，{URL}\n"
    "- 〈加班的成本〉，https://b.example/2\n"
)


def _check(article: str, **changes: object) -> list[CheckEntry]:
    arguments: dict[str, object] = {
        "article": article,
        "article_file": FILE,
        "output": {"title": "圖書館開到半夜，值得嗎"},
        "output_file": "stages/rewrite/1/article.json",
        "schema": SCHEMA,
        "original_url": URL,
        "original_title": TITLE,
        "texts": {"原文": "完全不同的原文。", "S2": "完全不同的來源。"},
        "thresholds": THRESHOLDS,
    }
    arguments.update(changes)
    return article_checks(**arguments)  # type: ignore[arg-type]


def _references(article: str, url: str | None = URL) -> list[CheckEntry]:
    return check_references(article, file=FILE, original_url=url, original_title=TITLE)


def _failed(entries: list[CheckEntry]) -> list[CheckEntry]:
    return [entry for entry in entries if entry.status == "fail"]


def test_結尾參考來源列了原文網址_全部通過() -> None:
    entries = _check(BODY + REFERENCES)
    assert _failed(entries) == []
    assert {entry.check for entry in entries} == {"C3", "C5"}


def test_參考來源沒列原文網址_擋下並說要列原文出處() -> None:
    article = BODY + "## 參考來源\n\n- 〈加班的成本〉，https://b.example/2\n"
    [failure] = _failed(_references(article))
    assert "原文" in failure.reason and URL in failure.reason


def test_沒有參考來源一節_擋下() -> None:
    [failure] = _failed(_references(BODY))
    assert "參考來源" in failure.reason


def test_參考來源不在結尾_後面還有別節_擋下() -> None:
    article = BODY + REFERENCES + "\n## 後記\n\n還有一段。\n"
    [failure] = _failed(_references(article))
    assert "結尾" in failure.reason


def test_原文網址只出現在正文_參考來源沒列_照樣擋() -> None:
    article = (
        f"# 標題\n\n原文在 {URL}。\n\n## 參考來源\n\n- 別的，https://b.example/2\n"
    )
    assert _failed(_references(article))


def test_網址結尾多一個斜線也認得() -> None:
    article = BODY + f"## 參考來源\n\n- {TITLE}，{URL}/\n"
    assert not _failed(_references(article))


def test_貼上的原文沒有網址_參考來源列標題就算() -> None:
    article = BODY + f"## 參考來源\n\n- 〈{TITLE}〉（創作者貼上的原文）\n"
    assert not _failed(_references(article, url=None))
    missing = BODY + "## 參考來源\n\n- 〈加班的成本〉\n"
    assert _failed(_references(missing, url=None))


@pytest.mark.promise("FR-RW-01", "NFR-RUN-03")
def test_照抄原文一大段_重疊率只提醒不擋_每份來源各一條() -> None:
    copied = "延長開放的成本不在電費而在人力排班與夜間安全"
    article = f"# 標題\n\n{copied}，這是我的看法。\n\n" + REFERENCES
    entries = _check(
        article, texts={"原文": f"作者說{copied}。", "S2": "完全不同的來源。"}
    )

    assert _failed(entries) == []
    overlap = [entry for entry in entries if entry.check == "C3"]
    summaries = [entry for entry in overlap if entry.location is None]
    assert [entry.reason.split("：", 1)[0] for entry in summaries] == ["原文", "S2"]
    assert [entry.status for entry in summaries] == ["warn", "pass"]
    assert any(entry.excerpt == copied for entry in overlap)


def test_參考來源裡照列的標題與網址不算重疊() -> None:
    # 參考來源本來就照抄標題與網址；算進去的話，標題一長就被提醒
    long_title = "城市夜間閱讀空間的排班與讀者行為十年觀察紀錄"
    article = BODY + f"## 參考來源\n\n- 〈{long_title}〉，{URL}\n"
    entries = _check(
        article, original_title=long_title, texts={"原文": long_title + "。正文。"}
    )
    assert {entry.status for entry in entries if entry.check == "C3"} == {"pass"}


def test_文章不是標準_Markdown_擋下() -> None:
    article = "<html><body>文章</body></html>\n\n" + REFERENCES
    assert any(entry.check == "C5" for entry in _failed(_check(article)))


def test_article_json_不合_schema_擋下() -> None:
    assert _failed(_check(BODY + REFERENCES, output={"headline": "打錯欄位"}))
