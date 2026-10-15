"""概念圖：不含數字的流程圖、架構圖，用程式畫在 16 x 9 的網格上。

為什麼用程式畫而不用生圖模型：生圖模型常把箭頭、分支與文字畫錯，程式畫的每個標籤和箭頭
都是精確的。圖的風格比照工程部落格：精確的向量線條、對齊網格、真正的字型、克制的配色。

用法::

    fig = ConceptFigure(style)                      # 16 x 9 網格，原點在左下
    fig.box((1, 4), "資料 Data", w=3)
    fig.box((6, 4), "模型 Model", w=3, accent=True)  # 圖說講的那個重點，一張圖只用一次
    fig.arrow((4, 4.6), (6, 4.6), "訓練")
    fig.save(article_dir / "images/fig-2")           # png＋1600px webp＋charts/fig-2.svg

helper 幫忙守的規則：一張圖一個重點色、警示色只標失敗路徑、沒有陰影漸層質感、
座標一律貼齊 0.5 的網格。色票與字型來自 `ChartStyle`。
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from evidence_forge.figures.chart_style import ChartStyle, save_outputs

W, H = 16, 9  # 網格單位；座標請用 0.5 的倍數
BOX_H = 1.2


def _snap(v: float) -> float:
    return round(v * 2) / 2


class ConceptFigure:
    """一張概念圖：畫布加上 box、arrow、label、group 四種元素。"""

    def __init__(self, style: ChartStyle, title: str | None = None) -> None:
        """紙色底、隱藏座標軸、16 x 9 網格。標題可省略，通常交給圖說。"""
        self.style = style
        self.fig = plt.figure(figsize=(16, 9), dpi=100)
        self.fig.patch.set_facecolor(style.c["paper"])
        self.ax: Axes = self.fig.add_axes((0, 0, 1, 1))
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(0, H)
        self.ax.axis("off")
        if title:
            self.ax.text(
                0.8, H - 0.8, title, fontproperties=style.font_bold, fontsize=24,
                color=style.c["ink"], va="top",
            )

    def box(
        self,
        xy: tuple[float, float],
        text: str,
        w: float = 3,
        h: float = BOX_H,
        accent: bool = False,
        warn: bool = False,
        sub: str | None = None,
    ) -> tuple[float, float, float, float]:
        """圓角節點。accent 是圖的重點（只用一次）；warn 是失敗或風險節點。"""
        c, s = self.style.c, self.style
        x, y = _snap(xy[0]), _snap(xy[1])
        edge = c["warn"] if warn else c["accent"] if accent else c["ink"]
        face = "white" if accent or warn else c["paper"]
        self.ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.15", fc=face, ec=edge,
                lw=2 if accent or warn else 1.2,
            )
        )
        cy = y + h / 2 + (0.18 if sub else 0)
        self.ax.text(x + w / 2, cy, text, fontproperties=s.font_bold, fontsize=16, color=c["ink"],
                     ha="center", va="center")
        if sub:
            self.ax.text(x + w / 2, y + h / 2 - 0.28, sub, fontproperties=s.font, fontsize=12,
                         color=c["graphite"], ha="center", va="center")
        return (x, y, w, h)

    def arrow(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        text: str | None = None,
        warn: bool = False,
        dashed: bool = False,
    ) -> None:
        """直線箭頭，只有一個箭頭頭；標籤放在中點上方。"""
        c = self.style.c
        color = c["warn"] if warn else c["ink"]
        self.ax.add_patch(
            FancyArrowPatch(
                start, end, arrowstyle="-|>", mutation_scale=16, color=color, lw=1.4,
                linestyle=(0, (4, 3)) if dashed else "-", shrinkA=2, shrinkB=2,
            )
        )
        if text:
            mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            self.ax.text(mx, my + 0.25, text, fontproperties=self.style.font, fontsize=13,
                         color=c["graphite"], ha="center", va="bottom")

    def label(
        self,
        xy: tuple[float, float],
        text: str,
        size: int = 13,
        bold: bool = False,
        color: str | None = None,
        ha: str = "left",
    ) -> None:
        self.ax.text(
            xy[0], xy[1], text, fontproperties=self.style.font_bold if bold else self.style.font,
            fontsize=size, color=color or self.style.c["graphite"], ha=ha, va="center",
        )

    def group(self, xy: tuple[float, float], w: float, h: float, title: str | None = None) -> None:
        """細的虛線外框，把節點圈成一組（系統邊界、團隊、階段）。"""
        c = self.style.c
        x, y = _snap(xy[0]), _snap(xy[1])
        self.ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.2", fc="none", ec=c["mist"],
                lw=1.2, linestyle=(0, (4, 3)),
            )
        )
        if title:
            self.ax.text(x + 0.3, y + h - 0.35, title, fontproperties=self.style.font_bold,
                         fontsize=12, color=c["graphite"], va="center")

    def save(self, stem: Path | str, vector: bool = True) -> list[Path]:
        """寫 `<stem>.png`、1600px 的 `<stem>.webp` 與 `charts/<名稱>.svg`；回傳寫出的檔案。"""
        return save_outputs(self.fig, stem, self.style.c["paper"], vector=vector)
