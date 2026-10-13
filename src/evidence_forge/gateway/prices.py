"""價目表 `prices.yaml` 與金額的算法（v0.3 §4.1、§4.2）。

單位：價目是「每百萬 token 幾美元」，剛好等於「每個 token 幾微美元」，
所以金額直接以整數微美元算，不經過浮點數。不足 1 微美元的零頭一律進位（寧可多算）。

有的模型分兩段計價（Haiku 5.5）：一次請求的提示超過門檻，整個請求改用 `long_context` 的價。
提示 = 輸入＋快取寫入＋快取讀取（ADR-0022）。
"""

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from evidence_forge.gateway.ledger import Usage

PRICES_YAML = Path(__file__).with_name("prices.yaml")

# 每百萬 token 的美元價，不能是負的
PerMillion = Decimal


class Rates(BaseModel):
    """一組單價：輸入、輸出、快取寫入、快取讀取。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: PerMillion = Field(ge=0)
    output: PerMillion = Field(ge=0)
    cache_write: PerMillion = Field(ge=0)
    cache_read: PerMillion = Field(ge=0)


class LongContextPrice(Rates):
    """提示超過 `above_prompt_tokens` 時，整個請求用的單價。"""

    above_prompt_tokens: PositiveInt


class Price(Rates):
    # 沒寫就不分段：多長的提示都是同一個價
    long_context: LongContextPrice | None = None

    def rates(self, prompt_tokens: int) -> Rates:
        """這麼長的提示用哪一組單價。"""
        long = self.long_context
        if long is not None and prompt_tokens > long.above_prompt_tokens:
            return long
        return self


def load_prices(path: Path = PRICES_YAML) -> Mapping[str, Price]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 要是 YAML 對應表")
    return {str(model): Price.model_validate(value) for model, value in raw.items()}


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def cost_micros(price: Price, usage: Usage) -> int:
    """實際用量的金額（微美元）。"""
    rates = price.rates(
        usage.input_tokens + usage.cache_write_tokens + usage.cache_read_tokens
    )
    return _ceil(
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_write_tokens * rates.cache_write
        + usage.cache_read_tokens * rates.cache_read
    )


def estimate_input_tokens(request: bytes) -> int:
    """送出前估輸入 token：請求本文的 UTF-8 位元組數，對文字幾乎一定偏高。

    文字的 token 一般至少對到一個位元組，所以位元組數不會比 token 數少。中文一個字
    3 個位元組、大約一個 token，會多估約三倍；英文大約四個位元組一個 token；請求裡的 JSON
    括號與欄位名也都算進去。多預留的部分結算時照實際用量退回。後端另外加的少量格式 token
    不在本文裡，跟 `max_tokens` 那一份預留比起來很小。

    例外：圖片與 PDF 的 token 照尺寸與頁數算，不照位元組。base64 的大圖會估得很高
    （可能在預算邊緣誤擋）；很小的 PDF 反而可能估得比實際低。現在的請求是文字。

    舊的估法是字元數 ÷ 2：中文大約一個字一個 token，所以會少估一半
    （#54 審查實測：6,600 個中文字估出 3,324）。不打 `count_tokens`：不多一個連外的來回。
    """
    return len(request)


def reservation_micros(price: Price, request: bytes, max_tokens: int) -> int:
    """送出前要預留的金額：輸入用估的 token 數、輸出用 `max_tokens` 算最高可能的花費。

    輸入可能被寫進快取（比一般輸入貴），所以用兩者較貴的那個價。分段計價的模型照估的輸入
    決定用哪一段：估值偏高，所以實際提示可能超過門檻時一定用長提示價（寧可多留）。
    """
    tokens = estimate_input_tokens(request)
    rates = price.rates(tokens)
    input_price = max(rates.input, rates.cache_write)
    return _ceil(tokens * input_price + max_tokens * rates.output)
