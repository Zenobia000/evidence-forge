"""等級別名對照表 `models.yaml`（ADR-0011 §3.1、架構草案 v0.3 §4.1）。

Pi 只認 `local`、`strong`、`max`；對到哪個實際模型、送多少 `max_tokens`、要不要思考，
只在這裡改。閘道轉送前用這裡的值**蓋掉** Pi 送來的 `max_tokens` 與 `thinking`：
Pi 預設每次要 16384 個 token，`strong` 還會自己開 8192 的 thinking（#10），
不改寫的話每次預留的金額偏高，而且新模型不收 `budget_tokens` 這種寫法。
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, PositiveInt

MODELS_YAML = Path(__file__).with_name("models.yaml")

Backend = Literal["ollama", "anthropic"]


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
