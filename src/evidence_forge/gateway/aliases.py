"""等級別名對照表 `models.yaml`（ADR-0011 §3.1、架構草案 v0.3 §4.1）。

Pi 只認 `local`、`strong`、`max`；對到哪個實際模型只在這裡改。
S0.3 只接 Ollama（`local`）；Anthropic 後端與 `strong`、`max` 在 S0.4 加。
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, PositiveInt

MODELS_YAML = Path(__file__).with_name("models.yaml")


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["ollama"]
    model: str
    # 後端同時服務幾個請求；本機 GPU 一次一個（NFR-PAR-02，S0.4 實作排隊）
    concurrency: PositiveInt = 1


def load_aliases(path: Path = MODELS_YAML) -> Mapping[str, Alias]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 要是 YAML 對應表")
    return {str(name): Alias.model_validate(value) for name, value in raw.items()}
