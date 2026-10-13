"""前端的純邏輯（事件新舊、進度列的字、送出的訊息）用 node 內建的 test runner 測，不連網路、不裝任何套件。

測試檔在 `tests/js/*.test.mjs`。這裡只是把它們接進 pytest，讓 `uv run pytest` 一次跑完；
機器上沒有 node 就跳過並說明（CI 的 ubuntu-latest 有）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))


def test_there_are_js_tests_to_run() -> None:
    # 擋住「路徑改了、glob 找不到、底下的測試變空轉」
    assert {p.name for p in JS_TESTS} >= {"state.test.mjs", "progress.test.mjs", "compose.test.mjs"}


@pytest.mark.skipif(shutil.which("node") is None, reason="沒有 node：前端的純邏輯測試跳過（用 nvm 裝 node）")
def test_front_end_logic_passes_under_node() -> None:
    result = subprocess.run(
        ["node", "--test", *map(str, JS_TESTS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
