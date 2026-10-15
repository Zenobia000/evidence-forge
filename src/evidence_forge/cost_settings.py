"""費用設定：每月上限（可以不設）、分析與寫稿每張工單的預設預算、生圖每張的上限（FR-LG-03、ADR-0027 §3.6）。

存在 `$EF_DATA/settings/costs.json`（不進 repo），由聊天室的設定頁寫；每次改動在 `settings/changes.jsonl`
記一筆（時間、欄位、舊值、新值），只增不改。

- **沒有檔就是預設值：不設每月上限**（創作者 2026-10-15：雲端平台自己的後台有硬上限），單張預算照舊 US$0.5。
- **「不設」是 `null`**，不是一個很大的數字：閘道看到 `None` 就不檢查月額度，帳單寫「未設每月上限」。
- **金額有上下限**：每月上限 0～US$1,000,000（0 是什麼都不准花）；單張預算與生圖上限要大於 0
  （0 的單一開就失敗）、最多 US$1,000,000（再大換成微美元會出錯，每個付費請求都會失敗）。
- **讀不懂的檔不讓任何東西當掉**：每個欄位沿用 `changes.jsonl` 裡最後一次存的值（創作者設過的上限不會
  因為檔案壞掉就悄悄不見），沒記過的用預設值；原因交給設定頁標出來（`Loaded.problem`）。
  沒有檔（不是壞掉）照樣是預設值。
- **每次要用都重讀**（檔很小）：改了設定不用重開 `ef serve`。
- 環境變數 `EF_MONTHLY_BUDGET_USD` 蓋過設定檔的每月上限（給測試與 CI，`env_monthly_cap`）。
"""

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evidence_forge.paths import DataDir

ENV_MONTHLY_CAP = "EF_MONTHLY_BUDGET_USD"
DEFAULT_JOB_BUDGET_USD = 0.5
# 生圖每張的上限（#112 用）：比 OpenAI 高品質大圖一張的價錢再寬一點
DEFAULT_IMAGE_CAP_USD = 0.25
# 任何一個金額的上限：遠超過真的會用到的數字，又離微美元換算出錯的範圍很遠
MAX_USD = 1_000_000.0

# 每月上限：0 以上（0 是什麼都不准花）；單張預算與生圖上限：大於 0
CapUsd = Annotated[float, Field(ge=0, le=MAX_USD, allow_inf_nan=False)]
BudgetUsd = Annotated[float, Field(gt=0, le=MAX_USD, allow_inf_nan=False)]

_log = logging.getLogger(__name__)
# 同一個讀不懂的原因只記一次警告：閘道每次預留都會讀設定
_reported: set[str] = set()
_write_lock = threading.Lock()


class CostSettings(BaseModel):
    """設定檔的內容。金額是美元，範圍見模組說明；不能是無限大或 NaN。"""

    # 不認得的欄位略過：之後的版本多一個欄位，舊版讀到也不會整份當成壞掉、把每月上限丟掉
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    # None：不設每月上限
    monthly_cap_usd: CapUsd | None = None
    analysis_budget_usd: BudgetUsd = DEFAULT_JOB_BUDGET_USD
    rewrite_budget_usd: BudgetUsd = DEFAULT_JOB_BUDGET_USD
    image_cap_usd: BudgetUsd = DEFAULT_IMAGE_CAP_USD


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
    """讀設定檔；沒有檔用預設值。讀不懂就沿用改動紀錄裡最後存的值（見 `_last_saved`），並說明原因。

    閘道每次預留都讀這裡，所以什麼例外都不能丟出去，丟了每個模型請求都會失敗。
    """
    path = costs_file(data)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Loaded(CostSettings())
    except OSError as exc:
        return _broken(data, f"讀不到：{exc.strerror or exc}")
    try:
        return Loaded(CostSettings.model_validate(json.loads(raw.decode("utf-8"))))
    except (ValueError, RecursionError) as exc:
        # 不是 UTF-8（UnicodeDecodeError）、json 的錯誤、pydantic 的 ValidationError 都是 ValueError；
        # 極深的巢狀是 RecursionError
        return _broken(data, _reason(exc))


def _reason(exc: ValueError | RecursionError) -> str:
    if isinstance(exc, ValidationError):
        return "；".join(
            f"{'.'.join(str(p) for p in error['loc']) or '整份'}：{error['msg']}"
            for error in exc.errors()
        )
    if isinstance(exc, UnicodeDecodeError):
        return "不是 UTF-8 的文字檔"
    return f"不是 JSON（{exc}）"


def _last_saved(data: DataDir) -> dict[str, Any]:
    """`changes.jsonl` 裡每個欄位最近一筆合法的值（`new`）。讀不到紀錄就是空的。

    設定檔只經 `update` 寫，每次寫都把跟原本不同的欄位記進來，所以這就是壞掉之前最後一份好的設定
    （有人手改過設定檔又沒留紀錄的話例外，那時就以紀錄為準）。

    閘道每次預留都可能走到這裡，所以每一行都先驗形狀：不是物件、`field` 不是認得的欄位名（陣列、
    物件、數字都算）、沒有 `new`、或 `new` 不合法的行一律跳過——不合法的那筆不蓋掉前面合法的。
    """
    try:
        lines = changes_file(data).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return {}
    found: dict[str, Any] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue  # 寫到一半的半行
        if not isinstance(row, dict) or "new" not in row:
            continue
        name = row.get("field")
        if not isinstance(name, str) or name not in CostSettings.model_fields:
            continue
        try:
            CostSettings.model_validate({name: row["new"]})
        except ValidationError:
            continue
        found[name] = row["new"]
    return found


def env_monthly_cap(environ: Mapping[str, str]) -> float | None:
    """`EF_MONTHLY_BUDGET_USD`：設了就蓋過設定檔的每月上限；沒設是 None。不是 0～`MAX_USD` 的金額就丟
    ValueError（`ef serve` 啟動時就報錯）。"""
    value = environ.get(ENV_MONTHLY_CAP, "").strip()
    if not value:
        return None
    try:
        cap = float(value)
    except ValueError:
        raise ValueError(f"{ENV_MONTHLY_CAP} 要是美元金額：{value}") from None
    if not 0 <= cap <= MAX_USD:
        raise ValueError(f"{ENV_MONTHLY_CAP} 要是 0 到 {MAX_USD:,.0f} 的美元金額：{value}")
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


@dataclass(frozen=True)
class Saved:
    # 改到的欄位（每個一筆：時間、欄位、舊值、新值）
    changes: list[dict[str, Any]]
    # 設定已經生效、改動紀錄卻沒寫進去時的原因；寫進去了（或沒有改動）是 None
    log_error: str | None = None


def save(data: DataDir, new: CostSettings, *, now: datetime) -> Saved:
    """整份換成 `new`；見 `update`。"""
    return update(data, lambda _: new, now=now)


def update(
    data: DataDir, change: Callable[[CostSettings], CostSettings], *, now: datetime
) -> Saved:
    """讀出現在的設定、交給 `change` 改、寫回去，三步在同一把鎖裡（同時改不同欄位不會互相蓋掉）。
    跟寫之前實際在用的值（檔案讀不懂時就是 `load` 沿用的那一份）不同的欄位，在 `changes.jsonl` 各記一筆。
    `change` 丟的例外原樣丟出，什麼都不寫；換檔失敗的例外也原樣丟出。

    先換檔再記改動：換檔失敗就什麼都沒記（紀錄裡不會有沒生效的改動）。換好檔之後紀錄寫不進去，
    設定已經生效，不算存檔失敗：原因放在 `Saved.log_error` 交給畫面說。換好檔、還沒記到紀錄就當掉的話，
    設定已經生效、紀錄少這一筆——接受：比紀錄說改了、其實沒改好查。
    """
    with _write_lock:
        old = load(data).settings
        new = change(old)
        stamp = now.isoformat()
        changes = [
            {"ts": stamp, "field": name, "old": getattr(old, name), "new": getattr(new, name)}
            for name in CostSettings.model_fields
            if getattr(old, name) != getattr(new, name)
        ]
        path = costs_file(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先寫暫存檔再整個換上去：閘道讀的時候只會讀到舊版或新版
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temporary.write_text(new.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        if not changes:
            return Saved(changes)
        try:
            with changes_file(data).open("a", encoding="utf-8") as log:
                log.write("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in changes))
        except OSError as exc:
            reason = f"{changes_file(data).name} 寫不進去：{exc.strerror or exc}"
            _log.warning("費用設定已經生效，但%s", reason)
            return Saved(changes, reason)
        return Saved(changes)


def _broken(data: DataDir, reason: str) -> Loaded:
    saved = _last_saved(data)
    settings = CostSettings.model_validate(saved)
    used = "changes.jsonl 裡最後一次存的值" if saved else "預設值"
    if len(saved) < len(CostSettings.model_fields) and saved:
        used += "（沒存過的欄位用預設值）"
    problem = f"{costs_file(data).name} 讀不懂（{reason}），先用{used}"
    if problem not in _reported:
        _reported.add(problem)
        _log.warning("費用設定 %s", problem)
    return Loaded(settings, problem)
