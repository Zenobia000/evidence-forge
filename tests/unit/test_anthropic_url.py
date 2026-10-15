"""`EF_ANTHROPIC_URL`（#85）：不花錢的端到端檢查把雲端等級轉到本機的假 Anthropic。

每個請求都帶著真的金鑰，所以跟 `EF_TAVILY_URL` 同一條規則：只准 https，或這台機器自己的 http。
"""

from pathlib import Path

import pytest

from evidence_forge.gateway.config import config_from_env
from evidence_forge.paths import DataDir


def test_沒設就是_Anthropic_本機的假後端可以(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    assert config_from_env(data, {}).anthropic_url == "https://api.anthropic.com"
    custom = config_from_env(data, {"EF_ANTHROPIC_URL": " http://127.0.0.1:9911/ "})
    assert custom.anthropic_url == "http://127.0.0.1:9911"


@pytest.mark.parametrize(
    "url",
    ["http://anthropic.example.com", "http://10.0.0.5:9911", "ftp://127.0.0.1:1", "https://"],
    ids=["外部主機用http", "內網位址用http", "不是http", "沒有主機"],
)
def test_金鑰不送去沒加密的外部主機(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="EF_ANTHROPIC_URL"):
        config_from_env(DataDir(tmp_path), {"EF_ANTHROPIC_URL": url})
