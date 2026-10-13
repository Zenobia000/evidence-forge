"""把 Markdown 轉成可以放進頁面的 HTML。報告與文章都是 agent 寫的，裡面引的網頁可能夾帶標記，
所以原始 HTML 一律不放行（變成純文字），連結只准 http、https、mailto。

這裡出來的字串已經是 HTML，用 `Markup` 標成安全的；其他地方的文字一律讓 Jinja 自己跳脫。
"""

from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup, escape

_ALLOWED_SCHEMES = ("http://", "https://", "mailto:")


def _link_open(
    self: RendererHTML,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    token = tokens[idx]
    # 出處連結開新分頁，且不讓對方拿到這個頁面（opener）
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


def _image(
    self: RendererHTML,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    # 圖不載入：報告與文章裡的 ![](網址) 是 agent 從抓來的網頁寫的，載圖就是一次帶資料的對外請求。
    # 只留說明文字；要看圖就點來源連結
    alt = tokens[idx].content.strip()
    return str(escape(f"（圖：{alt}）" if alt else "（圖）"))


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.add_render_rule("link_open", _link_open)
    md.add_render_rule("image", _image)
    # 預設的 validateLink 已擋 javascript: 之類；這裡再收窄成白名單
    default = md.validateLink

    def validate(url: str) -> bool:
        # `//host` 是省略協定的外站網址，不算站內路徑
        local = url.startswith(("/", "#")) and not url.startswith("//")
        return default(url) and (url.lower().startswith(_ALLOWED_SCHEMES) or local)

    md.validateLink = validate  # type: ignore[method-assign]
    return md


_MD = _build()


def render(text: str) -> Markup:
    """原始 HTML 已關掉，輸出都是跳脫過的，所以標成安全的。"""
    return Markup(_MD.render(text))


def render_inline(text: str) -> Markup:
    return Markup(_MD.renderInline(text))
