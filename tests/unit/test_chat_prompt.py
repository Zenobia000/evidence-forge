"""對話的系統提示（`harness/pi/chat-system-prompt.md`）跟程式對得上（#109）。

訪談規則寫在提示詞裡，閘道（`cards.py`）是最後一道關；兩邊列的文體、工具名稱一漂移，
模型就會照提示詞叫一個被拒的選項或不存在的工具。這裡只查名字對得上，規則本身由閘道的測試管。
"""

import re

from evidence_forge.cards import GENRES, SELF_FREE_GENRES
from evidence_forge.chat.pi_rpc import chat_system_prompt
from evidence_forge.gateway.chat_tools import CHAT_TOOLS, UI_ONLY_TOOLS
from evidence_forge.harness import default_harness

PROMPT = chat_system_prompt(default_harness()).read_text(encoding="utf-8")


def _section(title: str) -> str:
    start = PROMPT.index(title)
    end = PROMPT.find("\n**Station", start + len(title))
    return PROMPT[start : end if end != -1 else None]


def test_第_1_關列的文體就是閘道收的七種() -> None:
    listed = set(re.findall(r"^  - `([a-z-]+)` ", _section("**Station 1"), re.MULTILINE))
    assert listed == GENRES


def test_你幫我選在第_1_關只列不需要作者經驗的文體() -> None:
    rule = next(line for line in PROMPT.splitlines() if line.strip().startswith("- Station 1: you may pick"))
    named = set(re.findall(r"`([a-z-]+)`", rule))
    assert named == SELF_FREE_GENRES


def test_提示詞提到的工具都在對話的工具清單裡_畫面才能叫的一個都不提() -> None:
    tools_part = PROMPT[PROMPT.index("Tools:") : PROMPT.index("How to work:")]
    # 每個工具都在開頭的清單裡介紹過；整份提示詞用「名稱(」寫的呼叫都是對話真的有的工具
    assert CHAT_TOOLS <= set(re.findall(r"`([a-z_]+)", tools_part))
    assert set(re.findall(r"`([a-z_]+)\(", PROMPT)) <= CHAT_TOOLS
    assert not any(f"`{name}" in PROMPT for name in UI_ONLY_TOOLS)


def test_訪談不叫_rewrite_寫新文章() -> None:
    writing = PROMPT[PROMPT.index("### Writing") :]
    assert "Do not call `rewrite` for an interview card" in writing
    # 舊的「寫第 N 個 → rewrite」規則拿掉了（ADR-0025 §3.5）
    assert "suggested_genre` to the topic" not in PROMPT
