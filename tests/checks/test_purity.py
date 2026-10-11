"""`checks/` 不呼叫模型、不碰網路（#21 完成條件；測試藍圖：C1～C5 是不碰網路的單元）。

用靜態的方式看 `checks/` 每個模組 import 了什麼：碰網路、開行程、叫模型的套件一個都不准出現。
之後有人在檢查裡偷接模型或抓網頁，這條會先紅。
"""

import ast
from pathlib import Path

import pytest

import evidence_forge.checks

pytestmark = pytest.mark.promise("FR-VF-01")

# 碰網路、開行程、叫模型的模組（比對 import 名稱的第一段）
FORBIDDEN = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "ftplib",
        "smtplib",
        "asyncio",
        "subprocess",
        "multiprocessing",
        "requests",
        "httpx",
        "aiohttp",
        "anthropic",
        "openai",
        "ollama",
        "litellm",
        "urllib3",
        "websockets",
    }
)

CHECKS_DIR = Path(evidence_forge.checks.__file__).parent


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "module", sorted(CHECKS_DIR.glob("*.py")), ids=lambda path: path.name
)
def test_checks_module_imports_nothing_that_reaches_a_network_or_a_model(
    module: Path,
) -> None:
    assert _imported_roots(module) & FORBIDDEN == set()


def test_the_guard_actually_sees_the_modules() -> None:
    # 防止路徑算錯、一個模組都沒掃到，上面那條就空轉通過
    assert {"quotes.py", "citations.py", "overlap.py", "assets.py", "format.py"} <= {
        path.name for path in CHECKS_DIR.glob("*.py")
    }
