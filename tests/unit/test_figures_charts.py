"""數據圖與概念圖：能依品牌色票畫出 PNG、webp、SVG 三份檔案。

字型用 matplotlib 附的 DejaVu Sans（見 figures_world），不依賴 Noto Sans TC；
頁首的「圖 1」是中文，DejaVu 沒有這個字，所以忽略缺字警告。測試不比對字形，
只驗檔案、尺寸、底色與設定有沒有被用上。
"""

import json
from pathlib import Path

import pytest
from figures_world import evidence, font_style
from matplotlib.colors import to_hex
from PIL import Image

from evidence_forge.figures.brand import Palette
from evidence_forge.figures.chart_style import (
    BOTTOM,
    TOP,
    ChartStyle,
    bracket,
    callout,
    diff_label,
    exhibit,
    finish,
    frame,
    load_evidence,
    ref_line,
)
from evidence_forge.figures.concept_figure import ConceptFigure

pytestmark = pytest.mark.filterwarnings("ignore:Glyph .* missing from font")


PAPER = (0xF5, 0xF5, 0xF5)  # 預設色票 paper 的 RGB


def _corner(im: Image.Image) -> tuple[int, ...]:
    pixel = im.convert("RGB").getpixel((2, 2))
    assert isinstance(pixel, tuple)
    return pixel


def _ledger_dir(root: Path) -> Path:
    (root / "charts").mkdir()
    (root / "charts" / "evidence.json").write_text(json.dumps(evidence()), encoding="utf-8")
    return root


# ---- 證據帳本轉成資料表 ----


def test_數字從帳本來_順序照帳本(tmp_path: Path) -> None:
    ev = load_evidence(_ledger_dir(tmp_path))

    df = frame(ev, "survey-limits", "measure", "pct")

    assert list(df.columns) == ["measure", "pct"]
    assert df["measure"].tolist() == ["Usage limit", "Spend review", "Model routing"]
    assert df["pct"].tolist() == [38, 28, 27]


def test_沒有數字的_claim_轉不成資料表(tmp_path: Path) -> None:
    ev = load_evidence(_ledger_dir(tmp_path))

    with pytest.raises(KeyError, match="has no values"):
        frame(ev, "why")


@pytest.mark.parametrize(
    ("old", "new", "kind", "digits", "unit", "expected"),
    [
        (8.0, 6.3, "pct", 0, "", "−21%"),  # 真正的減號，不是連字號
        (8.0, 6.0, "pct", 1, "", "−25.0%"),
        (10, 12, "pct", 0, "", "+20%"),
        (30.0, 38.0, "pp", 0, "", "+8 個百分點"),
        (30.0, 25.0, "pp", 0, "", "−5 個百分點"),
        (8.0, 6.3, "abs", 1, "萬人", "−1.7萬人"),
    ],
)
def test_差額文字由數值算出(
    old: float, new: float, kind: str, digits: int, unit: str, expected: str
) -> None:
    assert diff_label(old, new, kind=kind, unit=unit, digits=digits) == expected


def test_差額文字不接受舊值為零或未知種類() -> None:
    with pytest.raises(ValueError, match="old value is 0"):
        diff_label(0, 5)
    with pytest.raises(ValueError, match="unknown kind"):
        diff_label(1, 2, kind="ratio")


# ---- 數據圖 ----


def test_數據圖輸出三份檔案(tmp_path: Path) -> None:
    style = font_style()
    ev = load_evidence(_ledger_dir(tmp_path))
    df = frame(ev, "survey-limits").sort_values("value")
    fig, ax = exhibit(style, 1, "Usage limits lead", "Share of respondents, %, 2026", source="Example Survey 2026")
    ax.barh(df["label"], df["value"], color=style.c["mist"])
    (tmp_path / "images").mkdir()

    written = finish(style, fig, tmp_path / "images" / "fig-1")

    png, svg, webp = tmp_path / "images/fig-1.png", tmp_path / "charts/fig-1.svg", tmp_path / "images/fig-1.webp"
    assert set(written) == {png, svg, webp}
    with Image.open(png) as im:
        assert im.size == (1600, 900)
        assert _corner(im) == PAPER  # 預設色票的底色
    with Image.open(webp) as im:
        assert im.format == "WEBP" and im.size == (1600, 900)
    assert "<text" in svg.read_text(encoding="utf-8")  # 文字保留成文字，可編輯


def test_不要向量檔就不寫_svg(tmp_path: Path) -> None:
    style = font_style()
    fig, _ax = exhibit(style, 1, "T", "S", source="X")

    written = finish(style, fig, tmp_path / "images" / "fig-1", vector=False)

    assert [p.suffix for p in written] == [".png", ".webp"]
    assert not (tmp_path / "charts").exists()


def test_圖用的是品牌色票() -> None:
    style = font_style(palette=Palette(paper="#102030", accent="#AA3355"))

    fig, ax = exhibit(style, 3, "T", "S", source="X")

    assert to_hex(fig.patch.get_facecolor()).upper() == "#102030"
    number = next(t for t in fig.texts if t.get_text() == "圖 3")
    assert to_hex(number.get_color()).upper() == "#AA3355"


def test_頁尾有註與來源_品牌名有設才印() -> None:
    plain = font_style()
    named = font_style(name="Example Site")

    fig1, _ = exhibit(plain, 1, "T", "S", source="Example Survey", note="Sample is small")
    fig2, _ = exhibit(named, 1, "T", "S", source="Example Survey")

    footer = next(t.get_text() for t in fig1.texts if "資料來源" in t.get_text())
    assert footer == "註：Sample is small\n資料來源：Example Survey"
    assert "Example Site" not in [t.get_text() for t in fig1.texts]  # 預設不印品牌名
    assert "Example Site" in [t.get_text() for t in fig2.texts]
    assert next(t.get_text() for t in fig2.texts if "資料來源" in t.get_text()) == "資料來源：Example Survey"


def test_標註元件都能畫上去() -> None:
    style = font_style()
    fig, ax = exhibit(style, 1, "T", "S", source="X")
    ax.bar(["a", "b"], [10, 4])

    callout(style, ax, (0, 10), "so what", (0.3, 8))
    bracket(style, ax, 1.2, 4, 10, diff_label(10, 4))
    ref_line(style, ax, 6, "target")
    ref_line(style, ax, 0.5, "event", axis="x")

    texts = [t.get_text() for t in ax.texts]
    assert {"so what", "−60%", " target", " event"} <= set(texts)


def test_圖區邊界常數把圖區夾在標題與頁尾之間() -> None:
    assert BOTTOM < TOP < 0.83  # 0.83 是標題區下緣的分隔線


def test_字型找不到時不會畫出缺字的圖(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evidence_forge.figures.brand import FontNotFoundError

    monkeypatch.setenv("HOME", str(tmp_path))  # 沒有 ~/.fonts

    with pytest.raises(FontNotFoundError):
        ChartStyle.from_brand()


# ---- 概念圖 ----


def test_概念圖輸出三份檔案(tmp_path: Path) -> None:
    figure = ConceptFigure(font_style())
    figure.group((0.5, 2), 9, 4, "Boundary")
    figure.box((1, 3.5), "Data", w=3, sub="raw")
    figure.box((6, 3.5), "Model", w=3, accent=True)
    figure.box((11, 3.5), "Failure", w=3, warn=True)
    figure.arrow((4, 4.1), (6, 4.1), "train")
    figure.arrow((9, 4.1), (11, 4.1), warn=True, dashed=True)
    figure.label((1, 1), "A note", bold=True)

    written = figure.save(tmp_path / "images" / "fig-2")

    assert {p.name for p in written} == {"fig-2.png", "fig-2.svg", "fig-2.webp"}
    assert all(p.stat().st_size > 0 for p in written)
    with Image.open(tmp_path / "images/fig-2.png") as im:
        assert im.size == (1600, 900)
        assert _corner(im) == PAPER


def test_概念圖的座標貼齊半格() -> None:
    figure = ConceptFigure(font_style())

    assert figure.box((1.2, 4.3), "A") == (1.0, 4.5, 3, 1.2)
    assert figure.box((1.8, 4.1), "B", w=2) == (2.0, 4.0, 2, 1.2)


def test_概念圖的重點色與警示色來自品牌() -> None:
    style = font_style(palette=Palette(accent="#AA3355", warn="#336699"))
    figure = ConceptFigure(style)

    figure.box((1, 1), "Focus", accent=True)
    figure.box((5, 1), "Risk", warn=True)

    edges = [to_hex(p.get_edgecolor()).upper() for p in figure.ax.patches]
    assert edges == ["#AA3355", "#336699"]
