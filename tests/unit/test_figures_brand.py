"""配圖套件的品牌設定：brand.yaml 的讀取、中性預設值、字型找不到的錯誤。

品牌內容（色票、字型、封面用語）不進 repo，所以這裡也要守住：套件原始碼裡沒有寫死的品牌。
"""

from pathlib import Path

import pytest

import evidence_forge.figures as figures_pkg
from evidence_forge.figures.brand import (
    DEFAULT_BRAND,
    Brand,
    BrandError,
    FontNotFoundError,
    FontSpec,
    brand_file,
    load_brand,
    load_brand_file,
    resolve_font,
)
from evidence_forge.figures.chart_style import ChartStyle
from evidence_forge.jobkey import InvalidJobKey
from evidence_forge.paths import DataDir


def _write_brand(data: DataDir, persona: str, text: str) -> Path:
    path = brand_file(data, persona)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_沒有_brand_yaml_就用中性預設值(tmp_path: Path) -> None:
    brand = load_brand(DataDir(tmp_path), "me")

    assert brand == DEFAULT_BRAND
    assert brand.name == ""  # 預設不印品牌名
    assert brand.palette.accent == "#2F6DB5"
    assert brand.font.family == "Noto Sans TC"
    assert brand.cover.presets == {}
    assert "painting" in brand.cover.forms  # 預設有一小組中性詞彙，沒有品牌檔也組得出 prompt


def test_brand_yaml_只蓋掉有寫的欄位(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    _write_brand(data, "me", 'name: "Example Site"\npalette:\n  accent: "#AA3355"\n')

    brand = load_brand(data, "me")

    assert brand.name == "Example Site"
    assert brand.palette.accent == "#AA3355"
    assert brand.palette.paper == DEFAULT_BRAND.palette.paper  # 沒寫的照預設
    assert brand.cover == DEFAULT_BRAND.cover


def test_空的_brand_yaml_等於預設值(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    _write_brand(data, "me", "")

    assert load_brand(data, "me").palette == DEFAULT_BRAND.palette


def test_封面詞彙整張字典一起換(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    _write_brand(
        data,
        "me",
        """
cover:
  forms: {mural: "A wall-sized mural."}
  media: {tempera: {forms: [mural], description: "Egg tempera."}}
  styles: {flat: "Flat colour."}
  presets: {wall: {form: mural, medium: tempera, style: flat, preamble: "Preamble."}}
  shared_rules: "Rule."
""",
    )

    cover = load_brand(data, "me").cover

    assert list(cover.forms) == ["mural"]  # 預設的 painting 等不會混進來
    assert cover.presets["wall"].preamble == "Preamble."
    assert cover.shared_rules == "Rule."


@pytest.mark.parametrize(
    "text",
    [
        "palete: {accent: '#000000'}",  # 打錯欄位名稱
        "palette: {accent: red}",  # 不是 #RRGGBB
        "- just\n- a list",  # 最上層不是對照
        "name: [unclosed",  # 不是合法 YAML
        "cover: {media: {x: {forms: [nope], description: d}}}",  # 媒材配的形式不存在
        "cover: {presets: {p: {form: painting, medium: oil, style: nope, preamble: x}}}",  # 預設指到不存在的風格
    ],
)
def test_壞的_brand_yaml_直接拒絕(tmp_path: Path, text: str) -> None:
    data = DataDir(tmp_path)
    path = _write_brand(data, "me", text)

    with pytest.raises(BrandError, match=path.name):
        load_brand(data, "me")


def test_角色名不能跳出_authors_資料夾(tmp_path: Path) -> None:
    with pytest.raises(InvalidJobKey):
        load_brand(DataDir(tmp_path), "../outside")


def test_字型資料夾相對路徑以_brand_yaml_所在處為準(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    path = _write_brand(data, "me", 'font:\n  dirs: ["my-fonts"]\n')

    brand = load_brand_file(path)

    base = path.parent
    # 自己宣告的排最前面，其次是角色資料夾的 fonts/ 與角色資料夾本身
    assert brand.font.dirs == (base / "my-fonts", base / "fonts", base)


def test_缺字型的錯誤講清楚缺什麼放哪裡(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))  # 模擬沒裝字型的機器
    spec = FontSpec(dirs=(tmp_path / "somewhere",))

    with pytest.raises(FontNotFoundError) as caught:
        resolve_font(spec, "bold")

    message = str(caught.value)
    assert "Noto Sans TC" in message  # 缺哪個字型
    assert "NotoSansTC-Bold.otf" in message  # 要找的檔名
    assert str(tmp_path / "somewhere" / "NotoSansTC-Bold.otf") in message  # 找過哪裡
    assert str(tmp_path / "empty-home" / ".fonts") in message  # 該放到哪裡
    assert "fonts/" in message and "font.dirs" in message


def test_建立樣式時就檢查字型而不是畫到一半才炸(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))

    with pytest.raises(FontNotFoundError, match="Noto Sans TC"):
        ChartStyle.from_brand(DEFAULT_BRAND)


def test_字型從_dirs_與家目錄的_fonts_找得到(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".fonts").mkdir(parents=True)
    (home / ".fonts" / "NotoSansTC-Regular.ttf").write_bytes(b"x")
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "NotoSansTC-Bold.otf").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(home))
    spec = FontSpec(dirs=(tmp_path / "mine",))

    assert resolve_font(spec, "regular") == home / ".fonts" / "NotoSansTC-Regular.ttf"
    assert resolve_font(spec, "bold") == tmp_path / "mine" / "NotoSansTC-Bold.otf"


def test_字型可以直接給絕對路徑(tmp_path: Path) -> None:
    font = tmp_path / "Custom.ttf"
    font.write_bytes(b"x")

    assert resolve_font(FontSpec(regular=str(font)), "regular") == font


def test_套件原始碼沒有寫死的品牌內容() -> None:
    """色票、品牌名、封面 prompt 屬於 brand.yaml；程式裡出現就是又寫死了。"""
    package = Path(figures_pkg.__file__).parent
    banned = ["Sunny", "F4F0E8", "315B73", "10202C", "B6856B", "vaporwave", "Joy Division"]
    for source in package.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{source.name} 含有品牌內容 {word!r}"


def test_預設品牌是不可變的() -> None:
    with pytest.raises(ValueError, match="frozen"):
        DEFAULT_BRAND.name = "changed"  # type: ignore[misc]
    assert isinstance(DEFAULT_BRAND, Brand)
