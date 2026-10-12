"""雲端金鑰：只有閘道讀，放在 `$EF_SECRETS/anthropic`、`$EF_SECRETS/tavily`（ADR-0014、NFR-SEC-04）。

`EF_SECRETS` 沒設就用 `~/.config/evidence-forge/secrets`。金鑰每次要用才讀檔、用完不留：
不進日誌、不進錯誤訊息、不進帳本與 `blobs/`（`blobs/` 存的是 Pi 送來的請求本文，
金鑰只在轉送時放進標頭）。錯誤訊息只說「哪個檔」與「哪裡不對」，從不帶檔案內容。
"""

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_SECRETS_DIR = Path("~/.config/evidence-forge/secrets")
ANTHROPIC_KEY_FILE = "anthropic"
TAVILY_KEY_FILE = "tavily"


class MissingCredential(Exception):
    """金鑰檔不存在、讀不到或是空的。訊息裡不會有金鑰內容。"""


def resolve_secrets_dir(env: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    configured = environ.get("EF_SECRETS", "").strip()
    return Path(configured or DEFAULT_SECRETS_DIR).expanduser()


def read_anthropic_key(secrets_dir: Path) -> str:
    return _read_key(secrets_dir / ANTHROPIC_KEY_FILE, "Anthropic")


def read_tavily_key(secrets_dir: Path) -> str:
    """`web_search` 用的 Tavily 金鑰；跟 Anthropic 金鑰同一套規則。"""
    return _read_key(secrets_dir / TAVILY_KEY_FILE, "Tavily")


def _read_key(path: Path, service: str) -> str:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        # 只帶例外的種類，不帶 str(exc)：保守起見，任何可能含檔案內容的字串都不往外送
        raise MissingCredential(
            f"讀不到 {service} 金鑰 {path}（{type(exc).__name__}）"
        ) from None
    if not key:
        raise MissingCredential(f"{service} 金鑰檔是空的：{path}")
    return key
