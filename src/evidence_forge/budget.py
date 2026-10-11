"""工單的預算上限（`job.md` 的 `budget` 欄位，v0.3 §2.3、§4.2）與金額的單位換算。

放在檔名含 `budget` 的模組，改這裡的 PR 會被當成 T3（PR #35 審查的後續）。
預留、結算、月上限的計算在 `gateway/budget.py`；這裡只有格式與換算，不 import 專案裡其他模組，
`models.py` 才能 import 它而不繞圈。

金額的加總一律用整數微美元（1 美元＝1,000,000 微美元），不用浮點數累加：
接近上限時，浮點數的誤差會讓「剛好用完」被判成「超過一點點」或反過來。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

# 金額與用量：不能是負的，也不能是無限大或 NaN（YAML 的 .inf 會被讀成無限大，
# 無限大的預算等於沒有上限）。
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]

MICROS_PER_USD = 1_000_000


def usd_to_micros(usd: float) -> int:
    """美元換成整數微美元，四捨五入到最接近的 1 微美元。

    先轉成十進位字串再乘，`0.1` 這種浮點數才不會變成 99999.99… 微美元。
    """
    micros = Decimal(repr(usd)) * MICROS_PER_USD
    return int(micros.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def micros_to_usd(micros: int) -> float:
    """寫進帳本的 `cost_usd`。`usd_to_micros` 讀回來會得到同一個整數。"""
    return micros / MICROS_PER_USD


class Budget(BaseModel):
    """這張工單的上限。金額是美元；Tavily 用 credits 計。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    usd: Amount
    tavily_credits: NonNegativeInt = 0
