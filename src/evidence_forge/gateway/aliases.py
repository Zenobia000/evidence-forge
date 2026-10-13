"""等級別名對照表 `models.yaml`（ADR-0011 §3.1、架構草案 v0.3 §4.1）。

Pi 只認 `local`、`fast`、`strong`、`max`；對到哪個實際模型、送多少 `max_tokens`、要不要思考，
只在這裡改。閘道轉送前用這裡的值**蓋掉** Pi 送來的 `max_tokens` 與 `thinking`：
Pi 預設每次要 16384 個 token，`strong` 還會自己開 8192 的 thinking（#10），
不改寫的話每次預留的金額偏高，而且新模型不收 `budget_tokens` 這種寫法。

本機模型另外寫 `context_window`：Ollama 遇到放不下的提示會默默截掉前面，模型只看到後半段。
閘道先估提示多長，估起來放不下就不轉送、回請求錯誤，這一次算失敗、照升級鏈往上（ADR-0022）。
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, PositiveInt

MODELS_YAML = Path(__file__).with_name("models.yaml")

Backend = Literal["ollama", "anthropic"]

# 本機模型估提示長度時，幾個位元組算一個 token（見 `Alias.fits`）
LOCAL_BYTES_PER_TOKEN = 3


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Backend
    model: str
    # 每次請求的輸出上限；思考的 token 也算在裡面。預留金額依它估（v0.3 §4.2）
    max_tokens: PositiveInt
    # 原樣放進請求的 thinking 欄位；一定要寫，不採用 Pi 送來的值
    thinking: dict[str, Any]
    # 同時服務幾個請求；沒寫就不限。本機 GPU 一次一個（NFR-PAR-02）
    concurrency: PositiveInt | None = None
    # 後端的上下文長度（token，提示加輸出）；要跟 Ollama 伺服器的 OLLAMA_CONTEXT_LENGTH 一樣。
    # 沒寫就不檢查（雲端模型的上下文是 1M，遠大於我們的請求）
    context_window: PositiveInt | None = None

    def fits(self, request: bytes) -> bool:
        """改寫後的請求放不放得進上下文：估的提示 token 加上 `max_tokens` 不超過 `context_window`。

        估法是位元組數 ÷ `LOCAL_BYTES_PER_TOKEN`。錄音裡 Qwen 的請求平均 3.6 個位元組一個 token，
        用 3 會估得偏多：寧可多擋一次升級（多花一點錢），不讓提示被截掉還當成正常產出。
        """
        if self.context_window is None:
            return True
        estimated = -(-len(request) // LOCAL_BYTES_PER_TOKEN)
        return estimated + self.max_tokens <= self.context_window


def load_aliases(path: Path = MODELS_YAML) -> Mapping[str, Alias]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 要是 YAML 對應表")
    return {str(name): Alias.model_validate(value) for name, value in raw.items()}


def rewrite_request(body: Mapping[str, Any], alias: Alias) -> dict[str, Any]:
    """要轉送給後端的請求：別名換成實際模型，`max_tokens` 與 `thinking` 換成對照表的值。

    其他欄位原樣保留。
    """
    return {
        **body,
        "model": alias.model,
        "max_tokens": alias.max_tokens,
        "thinking": dict(alias.thinking),
    }
