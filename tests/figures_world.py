"""配圖測試共用：不依賴 Noto Sans TC 的品牌、範例證據帳本與 brief。

字型用 matplotlib 隨套件附的 DejaVu Sans（它自己的授權，隨 matplotlib 散布），
所以測試在沒裝 Noto Sans TC 的機器上也能畫圖；畫圖的測試文字一律用 ASCII，
不會碰到缺字。範例資料是為測試寫的，不是任何真實的文章。
"""

import copy
from pathlib import Path
from typing import Any

import matplotlib

from evidence_forge.figures.brand import Brand, FontSpec
from evidence_forge.figures.chart_style import ChartStyle

_TTF = Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def font_brand(**overrides: Any) -> Brand:
    """字型指到 matplotlib 附的 DejaVu Sans；其他欄位用預設，`overrides` 可蓋掉。"""
    font = FontSpec(
        family="DejaVu Sans",
        regular=str(_TTF / "DejaVuSans.ttf"),
        bold=str(_TTF / "DejaVuSans-Bold.ttf"),
    )
    return Brand(font=font, **overrides)


def font_style(**overrides: Any) -> ChartStyle:
    return ChartStyle.from_brand(font_brand(**overrides))


EVIDENCE: dict[str, Any] = {
    "claims": [
        {
            "claim_id": "survey-limits",
            "statement": "Share of respondents whose company set a usage limit",
            "kind": "observed",
            "source_location": "Example Survey 2026, table 4",
            "unit": "%",
            "period": "2026",
            "population": "Example Survey respondents",
            "values": {"Usage limit": 38, "Spend review": 28, "Model routing": 27},
        },
        {
            "claim_id": "jobs-change",
            "statement": "Change in postings between two periods",
            "kind": "derived",
            "formula": "(6.3 - 8.0) / 8.0",
            "input_claim_ids": ["jobs-raw"],
            "unit": "%",
            "period": "peak to early 2026",
            "population": "same as jobs-raw",
            "values": {"change": -21.25},
        },
        {
            "claim_id": "jobs-raw",
            "statement": "Postings count",
            "kind": "observed",
            "source_location": "Example report, p. 3",
            "unit": "10k",
            "period": "peak, early 2026",
            "population": "Example job board",
            "values": {"peak": 8.0, "early 2026": 6.3},
        },
        {"claim_id": "why", "statement": "Authors guess at the cause", "kind": "hypothesis"},
    ]
}

BRIEF: dict[str, Any] = {
    "version": "1.0",
    "exhibit": {
        "number": 1,
        "title": "Usage limits lead the controls",
        "subtitle": "Share of respondents, multiple choice, %, 2026",
        "source": "Example Survey 2026",
        "note": "Respondent count not listed.",
    },
    "reader_question": "Do companies manage AI like a utility bill?",
    "takeaway": "Usage limits are the most common control (38%).",
    "evidence_mode": "quantitative",
    "evidence_ids": ["survey-limits"],
    "candidates": [
        {"form": "sorted bars", "benefit": "long labels fit", "risk": "multiple choice reads as a sum"},
        {"form": "dot plot", "benefit": "gap is visible", "risk": "few points"},
    ],
    "selection_reason": "Categories compared by size.",
    "engine": "matplotlib",
    "canvas": {"width": 1600, "height": 900, "unit": "px", "use": "article body"},
    "panels": [
        {
            "id": "bars",
            "form": "horizontal-bar",
            "metric": "share of respondents",
            "unit": "%",
            "scale": "linear 0-60",
            "anchor_ids": ["bar-limit", "bar-review"],
        }
    ],
    "annotations": [
        {
            "id": "lbl",
            "text": "value at each bar end",
            "claim_ids": ["survey-limits"],
            "anchor_id": "bar-limit",
            "relation": "direct-label",
        },
        {
            "id": "so-what",
            "text": "things that get limits are things you rely on",
            "claim_ids": ["survey-limits"],
            "anchor_id": "bar-limit",
            "relation": "callout",
        },
    ],
    "source_notes": ["multiple choice"],
    "outputs": {"preview": "images/fig-1.png", "vector": "charts/fig-1.svg"},
}


def evidence() -> dict[str, Any]:
    return copy.deepcopy(EVIDENCE)


def brief() -> dict[str, Any]:
    return copy.deepcopy(BRIEF)
