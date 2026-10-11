"""抽正文：網頁 HTML → 純文字（加一點 Markdown 記號）、PDF → 文字層（FR-IN-01）。

閘道的 `fetch_url` 與手動投入都用這裡，不各寫一份。

網頁的做法刻意簡單、只用標準庫：
- 丟掉 `script`、`style`、導覽列、頁首頁尾、側欄、表單這些一定不是正文的區塊。
- 有 `<article>` 就取字最多的那一個；沒有就取 `<main>`；再沒有就取整個 `<body>`。
- 段落之間空一行；標題前面加 `#`，清單項目前面加 `- `。
抽得不好的網站，創作者可以改用貼文字或上傳檔案。
"""

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from pypdf import PdfReader

# 這些區塊整塊不要（連同裡面的字）
_SKIP = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "iframe",
        "button",
        "select",
        "title",
    }
)
# 這些標籤的開頭與結尾是段落的分界
_BLOCK = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ul",
        "ol",
        "blockquote",
        "pre",
        "table",
        "tr",
        "br",
        "hr",
        "figure",
        "figcaption",
        "dd",
        "dt",
    }
)
_HEADINGS = {f"h{level}": "#" * level + " " for level in range(1, 7)}

# schema.org 的「不是免費閱讀」標記：新聞網站與 Substack 的付費文章會帶
_PAYWALL = re.compile(
    r"""["']?isAccessibleForFree["']?\s*(?::|content\s*=)\s*["']?false["']?""",
    re.IGNORECASE,
)


class ExtractError(ValueError):
    """抽不到正文；訊息寫給創作者看。"""


@dataclass(frozen=True)
class Page:
    title: str | None
    author: str | None
    text: str


@dataclass
class _Block:
    article: int | None
    in_main: bool
    prefix: str = ""
    parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        joined = " ".join("".join(self.parts).split())
        return f"{self.prefix}{joined}" if joined else ""


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.article_depth = 0
        self.article_count = 0
        self.main_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.blocks: list[_Block] = [_Block(article=None, in_main=False)]

    def _current_article(self) -> int | None:
        return self.article_count if self.article_depth else None

    def _break(self, prefix: str = "") -> None:
        self.blocks.append(
            _Block(
                article=self._current_article(),
                in_main=self.main_depth > 0,
                prefix=prefix,
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            named = dict(attrs)
            key = named.get("name") or named.get("property")
            if key and named.get("content"):
                self.meta.setdefault(key.lower(), str(named["content"]))
            return
        if tag == "title":
            self.in_title = True
        if tag in _SKIP:
            self.skip_depth += 1
            return
        if tag == "article":
            if not self.article_depth:
                self.article_count += 1
            self.article_depth += 1
        elif tag == "main":
            self.main_depth += 1
        if tag in _BLOCK:
            prefix = _HEADINGS.get(tag, "- " if tag == "li" else "")
            self._break(prefix)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in _SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in _BLOCK:
            # 先換段再離開 article／main：結尾之後的字不算在裡面
            if tag == "article":
                self.article_depth = max(0, self.article_depth - 1)
            elif tag == "main":
                self.main_depth = max(0, self.main_depth - 1)
            self._break()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_depth:
            return
        self.blocks[-1].parts.append(data)


def _choose(blocks: list[_Block]) -> list[_Block]:
    articles: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.article is not None:
            articles.setdefault(block.article, []).append(block)
    if articles:
        return max(
            articles.values(), key=lambda group: sum(len(b.text()) for b in group)
        )
    in_main = [block for block in blocks if block.in_main]
    return in_main or blocks


def html_to_text(html: str) -> Page:
    """抽出網頁的標題、作者與正文。"""
    parser = _Parser()
    parser.feed(html)
    parser.close()
    texts = [text for block in _choose(parser.blocks) if (text := block.text())]
    title = " ".join("".join(parser.title_parts).split()) or parser.meta.get("og:title")
    author = parser.meta.get("author") or parser.meta.get("article:author")
    return Page(title=title or None, author=author, text="\n\n".join(texts))


def looks_paywalled(html: str) -> bool:
    """網頁自己標明這篇不是免費閱讀（schema.org `isAccessibleForFree: false`）。"""
    return _PAYWALL.search(html) is not None


def pdf_to_text(data: bytes) -> str:
    """抽出 PDF 的文字層；讀不了或抽不到字（掃描圖）就丟 `ExtractError`。"""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # pypdf 對壞檔會丟各式各樣的例外
        raise ExtractError(f"這個 PDF 讀不了：{exc}") from None
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise ExtractError("這個 PDF 抽不到文字（可能是掃描的圖），請改貼文字")
    return text
