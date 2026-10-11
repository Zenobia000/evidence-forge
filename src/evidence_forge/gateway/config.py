"""閘道的設定，以及每個請求共用的「確認工單鑰匙」。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import Alias, load_aliases
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import InvalidJobKey, JobKey
from evidence_forge.paths import DataDir

GatewayMode = Literal["live", "replay"]


@dataclass(frozen=True)
class GatewayConfig:
    data: DataDir
    # live：轉送到後端；replay：只回放錄好的回應，沒錄過就報錯（NFR-COST-06）
    mode: GatewayMode
    aliases: Mapping[str, Alias] = field(default_factory=load_aliases)
    # 重播模式讀的錄音：裡面有 ledger.jsonl 與 blobs/
    replay_dir: Path | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    # 等後端回應的上限（秒）；本機模型冷啟動要 75 秒（#10 第 6 題）
    backend_timeout_s: float = 600.0


def config_from_env(
    data: DataDir, env: Mapping[str, str] | None = None
) -> GatewayConfig:
    """`ef serve` 用：從環境變數讀模式、錄音位置與 Ollama 位址。"""
    environ = os.environ if env is None else env
    mode = environ.get("EF_GATEWAY_MODE", "live").strip() or "live"
    if mode not in ("live", "replay"):
        raise ValueError(f"EF_GATEWAY_MODE 只能是 live 或 replay：{mode}")
    replay = environ.get("EF_GATEWAY_REPLAY_DIR", "").strip()
    if mode == "replay" and not replay:
        raise ValueError("重播模式要用 EF_GATEWAY_REPLAY_DIR 指定錄音的位置")
    return GatewayConfig(
        data=data,
        mode="replay" if mode == "replay" else "live",
        replay_dir=Path(replay) if replay else None,
        ollama_url=environ.get("EF_OLLAMA_URL", "").strip() or "http://127.0.0.1:11434",
    )


def require_active_key(header: str | None, stages: ActiveStages) -> JobKey:
    """讀出並確認工單鑰匙；不是正在跑的那一把就丟 `ToolRejected(403)`。"""
    try:
        key = JobKey.parse(header or "")
    except InvalidJobKey as exc:
        raise ToolRejected(403, f"工單鑰匙不對：{exc}") from None
    if not stages.is_active(key):
        raise ToolRejected(403, f"鑰匙 {key} 不是正在跑的那一段、那一次")
    return key
