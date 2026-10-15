"""費用設定：每月上限（可以不設）、分析與寫稿每張工單的預設預算、生圖每張的上限（FR-LG-03、ADR-0027 §3.6）。

存在 `$EF_DATA/settings/costs.json`（不進 repo），由聊天室的設定頁寫；每次改動在 `settings/changes.jsonl`
記一筆（時間、欄位、舊值、新值），只增不改。

- **沒有檔就是預設值：不設每月上限**（創作者 2026-10-15：雲端平台自己的後台有硬上限），單張預算照舊 US$0.5。
- **「不設」是 `null`**，不是一個很大的數字：閘道看到 `None` 就不檢查月額度，帳單寫「未設每月上限」。
- **讀不懂的檔不讓任何東西當掉**：用預設值，原因交給設定頁標出來（`Loaded.problem`）。
- **每次要用都重讀**（檔很小）：改了設定不用重開 `ef serve`。
- 環境變數 `EF_MONTHLY_BUDGET_USD` 蓋過設定檔的每月上限（給測試與 CI，`env_monthly_cap`）。
"""

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from evidence_forge.budget import Amount
from evidence_forge.paths import DataDir

ENV_MONTHLY_CAP = "EF_MONTHLY_BUDGET_USD"
DEFAULT_JOB_BUDGET_USD = 0.5
# 生圖每張的上限（#112 用）：比 OpenAI 高品質大圖一張的價錢再寬一點
DEFAULT_IMAGE_CAP_USD = 0.25

_log = logging.getLogger(__name__)
# 同一個讀不懂的原因只記一次警告：閘道每次預留都會讀設定
_reported: set[str] = set()
_write_lock = threading.Lock()


class CostSettings(BaseModel):
    """設定檔的內容。金額是美元，不能是負的、無限大或 NaN。"""

    # 不認得的欄位略過：之後的版本多一個欄位，舊版讀到也不會整份當成壞掉、把每月上限丟掉
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    # None：不設每月上限
    monthly_cap_usd: Amount | None = None
    analysis_budget_usd: Amount = DEFAULT_JOB_BUDGET_USD
    rewrite_budget_usd: Amount = DEFAULT_JOB_BUDGET_USD
    image_cap_usd: Amount = DEFAULT_IMAGE_CAP_USD


@dataclass(frozen=True)
class Loaded:
    settings: CostSettings
    # 設定檔讀不懂的原因；沒有檔或讀得懂是 None
    problem: str | None = None


def costs_file(data: DataDir) -> Path:
    return data.root / "settings" / "costs.json"


def changes_file(data: DataDir) -> Path:
    return data.root / "settings" / "changes.jsonl"


def load(data: DataDir) -> Loaded:
    """讀設定檔；沒有檔用預設值，讀不懂也用預設值並說明原因。"""
    path = costs_file(data)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Loaded(CostSettings())
    except OSError as exc:
        return _broken(path, f"讀不到：{exc.strerror or exc}")
    try:
        return Loaded(CostSettings.model_validate(json.loads(raw)))
    except ValueError as exc:
        # json 的錯誤與 pydantic 的 ValidationError 都是 ValueError
        return _broken(path, _reason(exc))


def _reason(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        return "；".join(
            f"{'.'.join(str(p) for p in error['loc']) or '整份'}：{error['msg']}"
            for error in exc.errors()
        )
    return f"不是 JSON（{exc}）"


def env_monthly_cap(environ: Mapping[str, str]) -> float | None:
    """`EF_MONTHLY_BUDGET_USD`：設了就蓋過設定檔的每月上限；沒設是 None。不是 0 以上的有限金額就丟 ValueError
    （`ef serve` 啟動時就報錯）。"""
    value = environ.get(ENV_MONTHLY_CAP, "").strip()
    if not value:
        return None
    try:
        cap = float(value)
    except ValueError:
        raise ValueError(f"{ENV_MONTHLY_CAP} 要是美元金額：{value}") from None
    if not 0 <= cap < float("inf"):
        raise ValueError(f"{ENV_MONTHLY_CAP} 要是 0 以上的有限金額：{value}")
    return cap


@dataclass(frozen=True)
class Effective:
    """設定頁與帳單要的：設定檔的值、讀不懂的原因、環境變數有沒有蓋過每月上限。"""

    loaded: Loaded
    # EF_MONTHLY_BUDGET_USD 蓋過設定檔時的值
    env_cap_usd: float | None = None

    @property
    def monthly_cap_usd(self) -> float | None:
        """閘道實際照著擋的每月上限；None 是不設。"""
        if self.env_cap_usd is not None:
            return self.env_cap_usd
        return self.loaded.settings.monthly_cap_usd


def effective(data: DataDir, environ: Mapping[str, str]) -> Effective:
    """給頁面用，不會丟例外：環境變數不對時 `ef serve` 根本不會啟動，這裡就當它沒設。"""
    try:
        env_cap = env_monthly_cap(environ)
    except ValueError:
        env_cap = None
    return Effective(load(data), env_cap)


def save(data: DataDir, new: CostSettings, *, now: datetime) -> list[dict[str, Any]]:
    """寫進設定檔，每個改到的欄位在 `changes.jsonl` 記一筆；回傳記下的那幾筆。

    舊值是寫之前實際在用的值：檔案讀不懂時就是預設值。先記改動再換檔：
    換檔失敗的話紀錄多一筆沒生效的，總比生效了卻沒有紀錄好。
    """
    with _write_lock:
        old = load(data).settings
        stamp = now.isoformat()
        changes = [
            {"ts": stamp, "field": name, "old": getattr(old, name), "new": getattr(new, name)}
            for name in CostSettings.model_fields
            if getattr(old, name) != getattr(new, name)
        ]
        path = costs_file(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        if changes:
            with changes_file(data).open("a", encoding="utf-8") as log:
                log.write("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in changes))
        # 先寫暫存檔再整個換上去：閘道讀的時候只會讀到舊版或新版
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(new.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return changes


def _broken(path: Path, reason: str) -> Loaded:
    problem = f"{path.name} 讀不懂，先用預設值（不設每月上限）：{reason}"
    if problem not in _reported:
        _reported.add(problem)
        _log.warning("費用設定 %s", problem)
    return Loaded(CostSettings(), problem)
