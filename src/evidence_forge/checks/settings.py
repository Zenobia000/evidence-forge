"""檢查的設定：預設值在套件裡的 `defaults.yaml`，創作者可以用 `$EF_DATA/checks.yaml` 蓋掉幾項。

`$EF_DATA/checks.yaml` 只寫要改的欄位，其他照預設；打錯欄位名稱直接拒絕，不默默忽略。
這裡只讀檔、不寫檔。
"""

from importlib.resources import files
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from evidence_forge.checks.overlap import OverlapThresholds
from evidence_forge.paths import DataDir


class CheckSettings(BaseModel):
    """所有檢查的設定。目前只有 C3 的門檻。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    c3: OverlapThresholds


def _read_mapping(text: str, where: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{where} 要是 YAML 對應表（key: value）")
    return data


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_check_settings(data_dir: DataDir) -> CheckSettings:
    """讀預設值，再套上 `<資料目錄>/checks.yaml`（有的話）。"""
    defaults = _read_mapping(
        files("evidence_forge.checks").joinpath("defaults.yaml").read_text("utf-8"),
        "defaults.yaml",
    )
    override_path = data_dir.check_settings
    if override_path.exists():
        override = _read_mapping(
            override_path.read_text(encoding="utf-8-sig"), str(override_path)
        )
        defaults = _merge(defaults, override)
    return CheckSettings.model_validate(defaults)
