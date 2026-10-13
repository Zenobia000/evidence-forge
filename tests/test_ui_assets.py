"""UI 的靜態檔與模板守則：沒有建置、只用 token、字型自己放、不載外部資源（ADR-0007、ADR-0017 §3.3）。"""

import re
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


@pytest.mark.parametrize("path", [*TEMPLATES, *SCRIPTS], ids=lambda p: p.name)
def test_loads_nothing_from_outside(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.findall(r'(?:src|href)="https?://', text)
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
