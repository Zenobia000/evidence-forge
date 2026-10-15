"""數據圖的固定版面：顧問報告等級的 exhibit（編號、結論標題、副標、圖、頁尾）。

用法（數字只從證據帳本來）::

    style = ChartStyle.from_brand(brand)            # 缺字型在這裡就丟 FontNotFoundError
    ev = load_evidence(article_dir)                 # article_dir/charts/evidence.json
    df = frame(ev, "claim-id", "label", "value")
    fig, ax = exhibit(style, 1, "結論句標題", "量的是什麼・單位・母體・期間", source="機構《報告名》，日期")
    ...用 pandas 與 matplotlib 畫，顏色取自 style.c...
    finish(style, fig, article_dir / "images/fig-1")   # 另寫 article_dir/charts/fig-1.svg

每張圖固定由上到下五層：編號、結論標題、副標、圖、頁尾（註＋來源＋品牌名），
讓讀者用同一種方式掃每張圖。色票與字型都來自 `Brand`，這個模組沒有寫死任何品牌內容。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

# 不開視窗；在沒有顯示器的伺服器與測試裡也能畫
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager as fm
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from PIL import Image

from evidence_forge.figures.brand import DEFAULT_BRAND, Brand, check_font, resolve_font

TOP = 0.80  # 圖區上緣（標題區下面）
BOTTOM = 0.17  # 圖區下緣（頁尾上面）
# 上架用的 webp 固定寬度與品質
WEBP_WIDTH = 1600
WEBP_QUALITY = 82


@dataclass(frozen=True)
class ChartStyle:
    """已解析好的品牌樣式：色票與兩個字型。建立時就檢查字型在不在。"""

    brand: Brand
    font: fm.FontProperties
    font_bold: fm.FontProperties

    @classmethod
    def from_brand(cls, brand: Brand = DEFAULT_BRAND) -> "ChartStyle":
        """依品牌找字型；找不到丟 `FontNotFoundError`，打不開或缺繁中字形丟 `FontUnusableError`。

        不默默退回缺字的字型：缺字只會畫出豆腐方塊，不會報錯。
        """
        paths = [resolve_font(brand.font, "regular"), resolve_font(brand.font, "bold")]
        for path in paths:
            check_font(path, brand.font)
        return cls(
            brand=brand,
            font=fm.FontProperties(fname=str(paths[0])),
            font_bold=fm.FontProperties(fname=str(paths[1])),
        )

    @property
    def c(self) -> dict[str, str]:
        """色票字典：paper、ink、graphite、accent、warn、mist、grid。"""
        return self.brand.palette.model_dump()


def exhibit(
    style: ChartStyle,
    n: int,
    title: str,
    subtitle: str,
    source: str,
    note: str | None = None,
    size: tuple[float, float] = (16, 9),
) -> tuple[Figure, Axes]:
    """建立帶固定頁首與頁尾的圖，回傳 (fig, ax)。"""
    c = style.c
    fig, ax = plt.subplots(figsize=size, dpi=100)
    fig.patch.set_facecolor(c["paper"])
    ax.set_facecolor(c["paper"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(c["graphite"])
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=c["graphite"], labelsize=14, length=0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(style.font)

    fig.text(0.04, 0.95, f"圖 {n}", fontproperties=style.font_bold, fontsize=14, color=c["accent"])
    fig.text(0.04, 0.895, title, fontproperties=style.font_bold, fontsize=27, color=c["ink"])
    fig.text(0.04, 0.85, subtitle, fontproperties=style.font, fontsize=15, color=c["graphite"])
    fig.add_artist(Line2D([0.04, 0.96], [0.83, 0.83], color=c["ink"], linewidth=1.2))

    footer = f"註：{note}\n資料來源：{source}" if note else f"資料來源：{source}"
    fig.add_artist(Line2D([0.04, 0.96], [0.11, 0.11], color=c["mist"], linewidth=0.8))
    fig.text(
        0.04, 0.025, footer, fontproperties=style.font, fontsize=12, color=c["graphite"], linespacing=1.5
    )
    if style.brand.name:
        fig.text(
            0.96,
            0.025,
            style.brand.name,
            fontproperties=style.font_bold,
            fontsize=12,
            color=c["graphite"],
            ha="right",
        )
    return fig, ax


def axis_titles(style: ChartStyle, ax: Axes, x: str | None = None, y: str | None = None) -> None:
    """軸標題要寫意義與單位，例如「組織比例（%）」。"""
    c = style.c
    if x:
        ax.set_xlabel(x, fontproperties=style.font, fontsize=14, color=c["graphite"], labelpad=10)
    if y:
        ax.set_ylabel(y, fontproperties=style.font, fontsize=14, color=c["graphite"], labelpad=10)


def callout(
    style: ChartStyle,
    ax: Axes,
    xy: tuple[float, float],
    text: str,
    xytext: tuple[float, float],
    color: str | None = None,
) -> None:
    """「所以呢」註解框：一句話指向證明標題的那個資料點。"""
    c = style.c
    ax.annotate(
        text,
        xy=xy,
        xytext=xytext,
        textcoords="data",
        fontproperties=style.font,
        fontsize=14,
        color=c["ink"],
        ha="left",
        va="center",
        bbox={"boxstyle": "round,pad=0.5", "fc": "white", "ec": color or c["accent"], "lw": 1.2},
        arrowprops={"arrowstyle": "-", "color": color or c["accent"], "lw": 1.2},
    )


def load_evidence(article_dir: Path | str) -> dict[str, dict[str, Any]]:
    """讀 `<文章>/charts/evidence.json`，回傳 {claim_id: claim}。"""
    path = Path(article_dir) / "charts" / "evidence.json"
    claims = json.loads(path.read_text(encoding="utf-8"))["claims"]
    return {claim["claim_id"]: claim for claim in claims}


def frame(
    evidence: Mapping[str, Mapping[str, Any]],
    claim_id: str,
    label: str = "label",
    value: str = "value",
) -> pd.DataFrame:
    """一筆 claim 的 `values`（{標籤: 數字}）轉成兩欄 DataFrame，順序照帳本。"""
    values = evidence[claim_id].get("values")
    if not values:
        raise KeyError(f"claim {claim_id} has no values")
    return pd.DataFrame({label: list(values), value: list(values.values())})


def diff_label(old: float, new: float, kind: str = "pct", unit: str = "", digits: int = 0) -> str:
    """由數值算出差額文字，百分比與百分點不會混用。

    kind：`pct` 相對變化（%）、`pp` 兩個占比的差（百分點）、`abs` 以 `unit` 表示的 new - old。
    負號用真正的減號（U+2212），不用連字號。
    """
    if kind == "pct":
        if old == 0:
            raise ValueError("percentage change is undefined when the old value is 0")
        text = f"{(new - old) / old * 100:+.{digits}f}%"
    elif kind == "pp":
        text = f"{new - old:+.{digits}f} 個百分點"
    elif kind == "abs":
        text = f"{new - old:+.{digits}f}{unit}"
    else:
        raise ValueError(f"unknown kind: {kind}")
    return text.replace("-", "−", 1)


def bracket(
    style: ChartStyle,
    ax: Axes,
    x: float,
    y0: float,
    y1: float,
    text: str,
    color: str | None = None,
    tick: float = 0.04,
) -> None:
    """在資料座標 x 處、y0 與 y1 之間畫垂直差額括線，文字放右邊。"""
    color = color or style.c["ink"]
    span = ax.get_xlim()[1] - ax.get_xlim()[0]
    t = tick * span
    ax.plot([x - t, x, x, x - t], [y0, y0, y1, y1], color=color, lw=1.2, solid_capstyle="butt")
    ax.text(
        x + t * 0.8, (y0 + y1) / 2, text, fontproperties=style.font_bold, fontsize=15, color=color, va="center"
    )


def ref_line(
    style: ChartStyle, ax: Axes, value: float, text: str, axis: str = "y", color: str | None = None
) -> None:
    """參考線（目標、門檻、事件）。文字要寫明它是什麼、從哪來。"""
    color = color or style.c["graphite"]
    if axis == "y":
        ax.axhline(value, color=color, lw=1, ls=(0, (4, 3)))
        ax.text(
            ax.get_xlim()[1], value, f" {text}", fontproperties=style.font, fontsize=13, color=color,
            va="bottom", ha="right",
        )
    else:
        ax.axvline(value, color=color, lw=1, ls=(0, (4, 3)))
        ax.text(
            value, ax.get_ylim()[1], f" {text}", fontproperties=style.font, fontsize=13, color=color,
            va="top", ha="left",
        )


def save_outputs(
    fig: Figure,
    stem: Path | str,
    paper: str,
    vector: bool = True,
    vector_dir: Path | None = None,
) -> list[Path]:
    """存 PNG、1600px 寬的 webp（品質 82），預設再存一份可編輯的 SVG；回傳寫出的檔案。

    `stem` 是 `<文章>/images/fig-N`（不含副檔名）；SVG 預設放 `<文章>/charts/fig-N.svg`，
    文字保留成文字（要裝同一款字型才會完全一樣）。存完會關掉 fig。
    """
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    png = stem.with_suffix(".png")
    written = [png]
    fig.savefig(png, facecolor=paper)
    if vector:
        svg_dir = vector_dir or stem.parent.parent / "charts"
        svg_dir.mkdir(parents=True, exist_ok=True)
        svg = svg_dir / f"{stem.name}.svg"
        with matplotlib.rc_context({"svg.fonttype": "none"}):
            fig.savefig(svg, facecolor=paper)
        written.append(svg)
    plt.close(fig)
    with Image.open(png) as im:
        w, h = im.size
        webp = stem.with_suffix(".webp")
        im.resize((WEBP_WIDTH, round(h * WEBP_WIDTH / w)), Image.Resampling.LANCZOS).save(
            webp, "WEBP", quality=WEBP_QUALITY
        )
    written.append(webp)
    return written


def finish(
    style: ChartStyle,
    fig: Figure,
    stem: Path | str,
    left: float = 0.08,
    bottom: float = BOTTOM,
    vector: bool = True,
) -> list[Path]:
    """調好圖區邊界後輸出（見 `save_outputs`）。

    類別標籤很長時加大 `left`；x 軸同時有刻度標籤與軸標題時加大 `bottom`，
    軸標題才不會壓到頁尾分隔線。
    """
    fig.subplots_adjust(left=left, right=0.96, top=TOP, bottom=bottom)
    return save_outputs(fig, stem, style.c["paper"], vector=vector)
