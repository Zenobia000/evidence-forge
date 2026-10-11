"""價目表 `prices.yaml` 與金額的算法（v0.3 §4.1、§4.2）。

單位：價目是「每百萬 token 幾美元」，剛好等於「每個 token 幾微美元」，
所以金額直接以整數微美元算，不經過浮點數。不足 1 微美元的零頭一律進位（寧可多算）。
"""

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from evidence_forge.gateway.ledger import Usage

PRICES_YAML = Path(__file__).with_name("prices.yaml")

# 每百萬 token 的美元價，不能是負的
PerMillion = Decimal


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: PerMillion = Field(ge=0)
    output: PerMillion = Field(ge=0)
    cache_write: PerMillion = Field(ge=0)
    cache_read: PerMillion = Field(ge=0)


def load_prices(path: Path = PRICES_YAML) -> Mapping[str, Price]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 要是 YAML 對應表")
    return {str(model): Price.model_validate(value) for model, value in raw.items()}


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def cost_micros(price: Price, usage: Usage) -> int:
    """實際用量的金額（微美元）。"""
    return _ceil(
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_write_tokens * price.cache_write
        + usage.cache_read_tokens * price.cache_read
    )


def estimate_input_tokens(request: bytes) -> int:
    """送出前估輸入 token：請求的字元數 ÷ 2，無條件進位。

    故意偏高：中文大約一個字一個 token、英文大約四個字元一個 token，而請求裡還有 JSON 的括號與欄位名，
    除以 2 幾乎一定比實際多。準的做法是打免費的 `/v1/messages/count_tokens`，但每次多一個來回；
    反正結算時照實際用量退回，S0 先用這個（#22 系統架構留言）。
    """
    characters = len(request.decode("utf-8", errors="replace"))
    return (characters + 1) // 2


def reservation_micros(price: Price, request: bytes, max_tokens: int) -> int:
    """送出前要預留的金額：輸入用估的 token 數、輸出用 `max_tokens` 算最高可能的花費。

    輸入可能被寫進快取（比一般輸入貴），所以用兩者較貴的那個價。
    """
    input_price = max(price.input, price.cache_write)
    return _ceil(
        estimate_input_tokens(request) * input_price + max_tokens * price.output
    )
