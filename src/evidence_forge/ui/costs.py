"""費用設定頁與帳單頂部要的字：每月上限的狀態、各供應商的後台連結、設定頁送來的改動怎麼驗（#115、FR-LG-03）。

設定檔的讀寫在 `cost_settings`；這裡只把它整理成畫面上的東西，不碰 HTTP。
"""

from dataclasses import dataclass

from pydantic import ValidationError

from evidence_forge.budget import micros_to_usd, usd_to_micros
from evidence_forge.cost_settings import CostSettings, Effective

# 欄位在畫面上的名字（錯誤訊息也用這一份）
FIELD_LABELS = {
    "monthly_cap_usd": "每月上限",
    "analysis_budget_usd": "分析每張工單的預算",
    "rewrite_budget_usd": "寫稿每張工單的預算",
    "image_cap_usd": "生圖每張的上限",
}


@dataclass(frozen=True)
class Console:
    """雲端平台自己的後台：真正的硬上限設在那裡（ADR-0027 §3.6）。"""

    name: str
    what: str
    url: str


CONSOLES = (
    Console("Anthropic", "模型（Claude）", "https://console.anthropic.com/settings/limits"),
    Console("OpenAI", "生圖（封面）", "https://platform.openai.com/settings/organization/limits"),
    Console("Tavily", "網路搜尋", "https://app.tavily.com/home"),
)


@dataclass(frozen=True)
class CapStatus:
    # 閘道照著擋的每月上限；None 是不設
    cap_usd: float | None
    # 這個月還剩多少（超過就是 0）；不是這個月或沒設上限是 None
    left_usd: float | None
    # 每月上限是環境變數 EF_MONTHLY_BUDGET_USD 給的（蓋過設定檔）
    from_env: bool
    # 設定檔讀不懂的原因
    problem: str | None


def cap_status(state: Effective, spent_usd: float, *, current_month: bool) -> CapStatus:
    cap = state.monthly_cap_usd
    left = None
    if cap is not None and current_month:
        left = micros_to_usd(max(usd_to_micros(cap) - usd_to_micros(spent_usd), 0))
    return CapStatus(
        cap_usd=cap,
        left_usd=left,
        from_env=state.env_cap_usd is not None,
        problem=state.loaded.problem,
    )


def parse_update(body: object, current: CostSettings) -> CostSettings:
    """設定頁送來的 JSON：只給要改的欄位也行，其餘照現在的。不合法就丟 ValueError（訊息給人看）。

    金額要是 0 以上的有限數字（JSON 的數字，不收字串、布林）；只有每月上限可以是 null（不設）。
    不認得的欄位直接拒絕：打錯欄位名不能被當成「沒改」悄悄放過。
    """
    if not isinstance(body, dict):
        raise ValueError("請求內容要是 JSON 物件")
    unknown = sorted(str(key) for key in body if key not in FIELD_LABELS)
    if unknown:
        raise ValueError(f"不認得的欄位：{'、'.join(unknown)}")
    try:
        return CostSettings.model_validate({**current.model_dump(), **body})
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        problems = [
            f"{FIELD_LABELS.get(name, name)}要是 0 以上的金額（美元）"
            + ("，或 null（不設）" if name == "monthly_cap_usd" else "")
            for name in fields
        ]
        raise ValueError("；".join(problems) or "設定的格式不對") from None
