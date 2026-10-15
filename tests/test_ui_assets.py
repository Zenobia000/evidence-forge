"""UI 的靜態檔與模板守則：沒有建置、只用 token、字型自己放、不載外部資源（ADR-0007、ADR-0017 §3.3）。"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "evidence_forge" / "ui"
STATIC = UI_DIR / "static"
TEMPLATES = sorted((UI_DIR / "templates").glob("*.html"))
SCRIPTS = sorted((STATIC / "js").glob("*.js"))

# 設計稿用到的六個字重：斜體不用。
EXPECTED_FONT_FILES = {
    "inter-latin-400-normal.woff2",
    "inter-latin-500-normal.woff2",
    "inter-latin-600-normal.woff2",
    "source-serif-4-latin-600-normal.woff2",
    "jetbrains-mono-latin-400-normal.woff2",
    "jetbrains-mono-latin-600-normal.woff2",
}


def test_templates_and_scripts_exist_to_scan() -> None:
    # 擋住「路徑改了、glob 找不到、底下的檢查變空轉」。
    assert len(TEMPLATES) >= 6
    assert {p.name for p in SCRIPTS} >= {"app.js", "state.js", "progress.js", "compose.js"}


@pytest.mark.parametrize("path", [*TEMPLATES, *SCRIPTS], ids=lambda p: p.name)
def test_no_hard_coded_colors(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", text)


class _LoadFinder(HTMLParser):
    """收集會讓瀏覽器自己去外面載東西的屬性：任何標籤的 `src`、`<link>` 的 `href`（樣式、字型、預載）。

    用 HTML 解析器而不是正規表示式：只認真的屬性（單雙引號都算），`data-src` 這種別的屬性、
    屬性值裡或標籤外的文字都不算。`<a href>` 是給人點的連結，不是載入，照樣准（費用設定頁連到雲端平台的後台，票 115）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            loads = name == "src" or (tag == "link" and name == "href")
            if loads and value is not None and re.match(r"\s*https?://", value, re.IGNORECASE):
                self.found.append(f"<{tag} {name}={value}>")

    handle_startendtag = handle_starttag


def _outside_loads(text: str) -> list[str]:
    finder = _LoadFinder()
    finder.feed(text)
    finder.close()
    return finder.found


def test_outside_loads_catches_loaded_resources_but_not_links() -> None:
    # 擋住「規則改寬了、底下的檢查變空轉」
    assert _outside_loads('<script src="https://cdn.example/x.js"></script>')
    assert _outside_loads('<img alt="" src="http://cdn.example/a.png">')
    assert _outside_loads('<link rel="stylesheet" href="https://fonts.example/a.css">')
    assert _outside_loads('<iframe src="https://evil.example/"></iframe>')
    assert not _outside_loads('<a href="https://console.anthropic.com/settings/limits">Anthropic</a>')
    assert not _outside_loads('<link rel="stylesheet" href="/static/css/s0.css">')
    # 單引號一樣是載入
    assert _outside_loads("<script src='https://cdn.example/x.js'></script>")
    assert _outside_loads("<link rel='stylesheet' href='https://fonts.example/a.css'>")
    # 屬性名只認整個字：data-src 不是瀏覽器會去載的屬性
    assert not _outside_loads('<img alt="" data-src="https://cdn.example/a.png" src="/static/a.png">')
    assert not _outside_loads('<link rel="stylesheet" data-href="https://x.example/a.css" href="/static/a.css">')
    # 不是真的屬性：屬性值裡、或標籤外的文字出現 src="https://… 不算
    assert not _outside_loads('<a title="src=\'https://x.example\'" href="/settings">x</a>')
    assert not _outside_loads('範例寫法 src="https://cdn.example/x.js" 不要這樣寫')


@pytest.mark.parametrize("path", [*TEMPLATES, *SCRIPTS], ids=lambda p: p.name)
def test_loads_nothing_from_outside(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _outside_loads(text)
    assert "fonts.googleapis" not in text and "Noto" not in text
    assert not re.search(r"import\s+.*from\s+['\"]https?://", text)


def test_the_only_scripts_are_our_own_modules() -> None:
    shell = (UI_DIR / "templates" / "chat.html").read_text(encoding="utf-8")
    assert re.findall(r'<script[^>]*src="([^"]+)"', shell) == ["/static/js/app.js"]
    for name in ("bills.html", "base.html", "error.html"):
        assert "<script" not in (UI_DIR / "templates" / name).read_text(encoding="utf-8"), name


def test_inner_html_only_receives_server_fragments() -> None:
    # 伺服器的片段已經跳脫過；畫面自己組的東西（進度列、串流中的字）一律 textContent
    lines = [
        line.strip()
        for line in (STATIC / "js" / "app.js").read_text(encoding="utf-8").splitlines()
        if "innerHTML" in line and not line.lstrip().startswith("//")
    ]
    assert lines, "app.js 沒有 innerHTML？檢查變空轉了"
    assert all(line.endswith("= html;") for line in lines), lines
    for name in ("state.js", "progress.js", "compose.js"):
        assert "innerHTML" not in (STATIC / "js" / name).read_text(encoding="utf-8"), name


def test_the_layout_loads_tokens_before_s0_and_chat_last() -> None:
    html = (UI_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert hrefs == [
        "/static/css/fonts.css",
        "/static/css/tokens.css",
        "/static/css/s0.css",
        "/static/css/chat.css",
    ]


def test_only_the_six_design_font_weights_are_shipped_as_woff2() -> None:
    shipped = {p.name for p in (STATIC / "fonts").glob("*.woff2")}
    assert shipped == EXPECTED_FONT_FILES


@pytest.mark.parametrize("name", ["OFL-Inter.txt", "OFL-SourceSerif4.txt", "OFL-JetBrainsMono.txt"])
def test_each_font_family_ships_its_ofl_license_text(name: str) -> None:
    text = (STATIC / "fonts" / name).read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text


def test_font_faces_swap_and_use_the_names_s0_css_lists() -> None:
    css = (STATIC / "css" / "fonts.css").read_text(encoding="utf-8")
    s0 = (STATIC / "css" / "s0.css").read_text(encoding="utf-8")

    faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    assert len(faces) == 6
    for face in faces:
        assert "font-display: swap" in face
        assert "font-style: normal" in face
    families = {re.search(r'font-family: "([^"]+)"', f).group(1) for f in faces}  # type: ignore[union-attr]
    assert families == {"Inter", "Source Serif 4", "JetBrains Mono"}
    for family in families:
        assert f'"{family}"' in s0
    for url in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (STATIC / "fonts" / url).is_file()


def test_the_old_board_pieces_are_gone() -> None:
    names = {p.name for p in (UI_DIR / "templates").glob("*.html")}
    assert names.isdisjoint({"board.html", "job.html", "_board.html", "_job_view.html", "_card.html"})
    assert not (STATIC / "js" / "htmx.min.js").exists()
    assert not (UI_DIR / "app.py").exists() and not (UI_DIR / "detail.py").exists()


def test_every_event_stream_connect_refetches_the_thread() -> None:
    # 第一次連上和每次重連都重抓串流（主機重開、積太多被斷開、載入到連上之間的空檔）；
    # app.js 要在瀏覽器裡才跑得起來，這裡擋住有人把那一行拿掉
    text = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    onopen = re.search(r"source\.onopen = \(\) => \{(.*?)\n  \};", text, re.S)
    assert onopen is not None and "refreshThread()" in onopen.group(1)


def test_a_failed_send_puts_the_text_back() -> None:
    text = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "restoreAfterFailure" in text and "restore: true" in text
