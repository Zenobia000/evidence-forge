"""抽正文：網頁 HTML 與 PDF（FR-IN-01）。"""

from pathlib import Path

import pytest

from evidence_forge.extract import (
    ExtractError,
    html_to_text,
    looks_paywalled,
    pdf_to_text,
)

PDF = Path(__file__).parents[1] / "fixtures" / "pdf"

PAGE = """<!doctype html>
<html><head>
<title>Agents in production | Some Newsletter</title>
<meta name="author" content="Jane Doe">
<style>body { color: red }</style>
<script>var tracking = "不該出現";</script>
</head>
<body>
<header><nav><a href="/">首頁</a> <a href="/about">關於</a></nav></header>
<article>
  <h1>Agents in production</h1>
  <p>第一段：agent 上線之後，
     最難的是可觀測性。</p>
  <p>第二段有<a href="https://example.com">連結</a>與<b>粗體</b>。</p>
  <ul><li>第一點</li><li>第二點</li></ul>
</article>
<aside>相關文章：別的東西</aside>
<footer>© 2026 訂閱我們</footer>
</body></html>
"""


def test_抽出_article_裡的正文_去掉導覽列_頁尾_側欄與程式碼() -> None:
    page = html_to_text(PAGE)
    assert page.text == (
        "# Agents in production\n\n"
        "第一段：agent 上線之後， 最難的是可觀測性。\n\n"
        "第二段有連結與粗體。\n\n"
        "- 第一點\n\n"
        "- 第二點"
    )


def test_標題與作者從_head_讀() -> None:
    page = html_to_text(PAGE)
    assert (page.title, page.author) == (
        "Agents in production | Some Newsletter",
        "Jane Doe",
    )


def test_沒有_article_就用_main_再沒有就用_body() -> None:
    with_main = "<body><nav>選單</nav><main><p>主要內容</p></main><p>外面</p></body>"
    assert html_to_text(with_main).text == "主要內容"
    plain = "<body><nav>選單</nav><p>只有 body</p></body>"
    assert html_to_text(plain).text == "只有 body"


def test_好幾個_article_取字最多的那個() -> None:
    page = "<article><p>短</p></article><article><p>這一篇比較長的正文</p></article>"
    assert html_to_text(page).text == "這一篇比較長的正文"


@pytest.mark.promise("NFR-RUN-08")
def test_結構化資料標明不是免費閱讀_就算付費牆() -> None:
    page = (
        '<script type="application/ld+json">{"@type":"NewsArticle",'
        '"isAccessibleForFree": "False"}</script><article><p>前兩段</p></article>'
    )
    assert looks_paywalled(page)
    assert not looks_paywalled(PAGE)
    free = '<script type="application/ld+json">{"isAccessibleForFree": true}</script>'
    assert not looks_paywalled(free)


def test_PDF_抽出文字層() -> None:
    text = pdf_to_text((PDF / "article.pdf").read_bytes())
    assert text == (
        "Agents in production\nThe hardest part after launch is observability."
    )


def test_沒有文字層的_PDF_明確報錯() -> None:
    with pytest.raises(ExtractError, match="抽不到文字"):
        pdf_to_text((PDF / "scanned.pdf").read_bytes())


def test_壞掉的_PDF_明確報錯() -> None:
    with pytest.raises(ExtractError):
        pdf_to_text(b"%PDF-1.4 this is not really a pdf")
