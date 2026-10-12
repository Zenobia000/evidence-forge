"""閘道的設定，以及每個請求共用的「確認工單鑰匙」。"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast, get_args
from urllib.parse import urlsplit

from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import Alias, load_aliases
from evidence_forge.gateway.credentials import resolve_secrets_dir
from evidence_forge.gateway.prices import Price, load_prices
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import InvalidJobKey, JobKey
from evidence_forge.paths import DataDir

GatewayMode = Literal["live", "replay"]

# 測試與 UAT 用的故障注入（測試藍圖〈故障注入〉）；重播模式下也有效。正式執行時不能設（S0.6 開跑前檢查）。
# rate_limit：每個請求第一次先當成收到 429，退避後再照常；bad_output：回一份不合格的產出；
# backend_down：當成後端連不上；stream_cut：回應送到一半就斷掉。
Fault = Literal["rate_limit", "bad_output", "backend_down", "stream_cut"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class GatewayConfig:
    data: DataDir
    # live：轉送到後端；replay：只回放錄好的回應，沒錄過就報錯（NFR-COST-06）
    mode: GatewayMode
    aliases: Mapping[str, Alias] = field(default_factory=load_aliases)
    # 重播模式讀的錄音：裡面有 ledger.jsonl 與 blobs/
    replay_dir: Path | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    anthropic_url: str = "https://api.anthropic.com"
    # web_search 打的 Tavily（#69）；測試換成本機的假後端
    tavily_url: str = "https://api.tavily.com"
    # 雲端金鑰在 <secrets_dir>/anthropic、<secrets_dir>/tavily（ADR-0014）；要用才讀
    secrets_dir: Path = field(default_factory=resolve_secrets_dir)
    prices: Mapping[str, Price] = field(default_factory=load_prices)
    # 每月美元上限，依 Asia/Taipei 的日曆月（Q-30、NFR-COST-01）
    monthly_cap_usd: float = 10.0
    fault: Fault | None = None
    # 等後端回應的上限（秒）；本機模型冷啟動要 75 秒（#10 第 6 題）
    backend_timeout_s: float = 600.0
    # 雲端 429 的退避（模型與 Tavily 共用）：最多重試幾次；沒有 retry-after 時從 base 開始每次加倍，單次最多等 max
    rate_limit_retries: int = 4
    backoff_base_s: float = 1.0
    backoff_max_s: float = 60.0
    clock: Callable[[], datetime] = _utc_now

    def backoff_delay(self, retry_after: str | None, attempt: int) -> float:
        """第 `attempt` 次（從 0 算）收到 429 之後等幾秒：照後端的 `retry-after`（秒）；
        沒給或讀不懂就從 `backoff_base_s` 開始每次加倍。單次最多 `backoff_max_s`。"""
        try:
            delay = float(retry_after) if retry_after is not None else -1.0
        except ValueError:
            delay = -1.0
        if delay < 0:
            delay = self.backoff_base_s * (2**attempt)
        return min(delay, self.backoff_max_s)


def config_from_env(
    data: DataDir, env: Mapping[str, str] | None = None
) -> GatewayConfig:
    """`ef serve` 用：從環境變數讀模式、錄音位置、Ollama 與 Tavily 的位址。"""
    environ = os.environ if env is None else env
    mode = environ.get("EF_GATEWAY_MODE", "live").strip() or "live"
    if mode not in ("live", "replay"):
        raise ValueError(f"EF_GATEWAY_MODE 只能是 live 或 replay：{mode}")
    replay = environ.get("EF_GATEWAY_REPLAY_DIR", "").strip()
    if mode == "replay" and not replay:
        raise ValueError("重播模式要用 EF_GATEWAY_REPLAY_DIR 指定錄音的位置")
    fault = environ.get("EF_GATEWAY_FAULT", "").strip()
    if fault and fault not in get_args(Fault):
        raise ValueError(
            f"EF_GATEWAY_FAULT 只能是 {'、'.join(get_args(Fault))}：{fault}"
        )
    cap = environ.get("EF_MONTHLY_BUDGET_USD", "").strip()
    return GatewayConfig(
        data=data,
        mode="replay" if mode == "replay" else "live",
        replay_dir=Path(replay) if replay else None,
        ollama_url=environ.get("EF_OLLAMA_URL", "").strip() or "http://127.0.0.1:11434",
        tavily_url=_tavily_url(environ.get("EF_TAVILY_URL", "")),
        secrets_dir=resolve_secrets_dir(environ),
        monthly_cap_usd=_monthly_cap(cap) if cap else 10.0,
        fault=cast(Fault, fault) if fault else None,
    )


# 金鑰可以用沒加密的 http 送去的主機：只有這台機器自己（錄重播 fixture 的假 Tavily）
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _tavily_url(value: str) -> str:
    """Tavily 的位址。平常不設；錄重播 fixture 時換成本機的假 Tavily（tests/fixtures/web/）。

    每個搜尋請求都帶著 Tavily 金鑰，所以只准 https，或這台機器自己的 http；其他的啟動時就報錯。
    """
    url = value.strip().rstrip("/")
    if not url:
        return "https://api.tavily.com"
    parts = urlsplit(url)
    local_http = parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    if not parts.hostname or not (parts.scheme == "https" or local_http):
        raise ValueError(
            f"EF_TAVILY_URL 只能是 https，或本機（127.0.0.1、localhost）的 http：{url}"
        )
    return url


def _monthly_cap(value: str) -> float:
    try:
        cap = float(value)
    except ValueError:
        raise ValueError(f"EF_MONTHLY_BUDGET_USD 要是美元金額：{value}") from None
    if not 0 <= cap < float("inf"):
        raise ValueError(f"EF_MONTHLY_BUDGET_USD 要是 0 以上的有限金額：{value}")
    return cap


def require_active_key(header: str | None, stages: ActiveStages) -> JobKey:
    """讀出並確認工單鑰匙；不是正在跑的那一把就丟 `ToolRejected(403)`。"""
    try:
        key = JobKey.parse(header or "")
    except InvalidJobKey as exc:
        raise ToolRejected(403, f"工單鑰匙不對：{exc}") from None
    if not stages.is_active(key):
        raise ToolRejected(403, f"鑰匙 {key} 不是正在跑的那一段、那一次")
    return key
