"""UI 的靜態檔與模板守則：沒有建置、只用 token、字型自己放、順序對（ADR-0007、ADR-0017 §3.3）。"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evidence_forge.paths import DataDir
from evidence_forge.ui.app import create_app
from ui_folder import Folder

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "evidence_forge" / "ui"
STATIC = UI_DIR / "static"
TEMPLATES = sorted((UI_DIR / "templates").glob("*.html"))

# 設計稿用到的六個字重（視覺設計補的三點之一）：斜體不用。
EXPECTED_FONT_FILES = {
    "inter-latin-400-normal.woff2",
    "inter-latin-500-normal.woff2",
    "inter-latin-600-normal.woff2",
    "source-serif-4-latin-600-normal.woff2",
    "jetbrains-mono-latin-400-normal.woff2",
    "jetbrains-mono-latin-600-normal.woff2",
}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(DataDir(tmp_path)))


def test_templates_exist_to_scan() -> None:
    # 擋住「路徑改了、glob 找不到、底下的檢查變空轉」。
    assert len(TEMPLATES) >= 10


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_templates_hard_code_no_colors(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", text)
    assert not re.findall(r"\b(?:rgba?|hsla?)\(", text)
    # style 裡的 color／background 只能是 var(--…)
    for value in re.findall(r"(?:color|background)\s*:\s*([^;\"]+)", text):
        assert value.strip().startswith("var(--"), value


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_templates_load_nothing_from_outside_and_carry_no_inline_script(
    template: Path,
) -> None:
    text = template.read_text(encoding="utf-8")
    assert not re.search(r"(?:src|href)=\"(?:https?:)?//", text)
    assert not re.search(r"<script(?![^>]*\bsrc=)", text)  # 行內 JS 以後加 CSP 會被擋
    assert not re.search(r"\bon(?:click|load|change|submit)=", text)


@pytest.mark.parametrize("name", ["fonts.css", "ui.css"])
def test_extra_stylesheets_use_no_hard_coded_colors_or_external_urls(name: str) -> None:
    body = re.sub(
        r"/\*.*?\*/",
        "",
        (STATIC / "css" / name).read_text(encoding="utf-8"),
        flags=re.S,
    )
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not re.findall(r"\b(?:rgba?|hsla?)\(", body)
    assert body.strip(), f"{name} 是空的"
    assert "@import" not in body
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", body)


def test_the_page_loads_tokens_before_s0_and_never_loads_a_chinese_web_font(
    tmp_path: Path,
) -> None:
    html = _client(tmp_path).get("/").text

    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert hrefs.index("/static/css/tokens.css") < hrefs.index("/static/css/s0.css")
    assert "Noto" not in html and "fonts.googleapis" not in html


def test_only_the_six_design_font_weights_are_shipped_as_woff2() -> None:
    shipped = {p.name for p in (STATIC / "fonts").glob("*.woff2")}
    assert shipped == EXPECTED_FONT_FILES


@pytest.mark.parametrize(
    "name", ["OFL-Inter.txt", "OFL-SourceSerif4.txt", "OFL-JetBrainsMono.txt"]
)
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
    # @font-face 指到的檔案都真的在
    for url in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (STATIC / "fonts" / url).is_file()


def test_static_files_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/static/css/s0.css").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200
    assert client.get("/static/js/htmx.min.js").status_code == 200
    font = client.get("/static/fonts/inter-latin-400-normal.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"


def test_the_only_script_is_the_vendored_htmx(tmp_path: Path) -> None:
    html = _client(tmp_path).get("/").text

    assert re.findall(r'<script src="([^"]+)"', html) == ["/static/js/htmx.min.js"]


def test_ui_package_is_importable_without_a_data_folder(folder: Folder) -> None:
    # 資料夾什麼都沒有也不會炸：UI 只讀，不建立任何東西。
    before = sorted(p.name for p in folder.data.root.iterdir())

    TestClient(create_app(folder.data)).get("/")

    assert sorted(p.name for p in folder.data.root.iterdir()) == before
