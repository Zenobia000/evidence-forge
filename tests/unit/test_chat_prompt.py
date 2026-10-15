"""對話的系統提示（`harness/pi/chat-system-prompt.md`）跟程式對得上（#109）。

訪談規則寫在提示詞裡，閘道（`cards.py`）是最後一道關；兩邊列的文體、工具名稱一漂移，
模型就會照提示詞叫一個被拒的選項或不存在的工具。這裡只查名字對得上，規則本身由閘道的測試管。
"""

import re

import pytest

from evidence_forge.cards import GENRES, SELF_FREE_GENRES, THEMES
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
    rule = next(
        (line for line in PROMPT.splitlines() if line.strip().startswith("- Station 1: you may pick")),
        None,
    )
    assert rule is not None, "提示詞找不到「你幫我選」第 1 關的那一條"
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


def test_打字說沒有例子_記成_no_example_閘道才擋得到() -> None:
    # 記成 other_text 的話閘道當成作者給了內容，要作者經驗的文體就漏過第 3 關（cards._example_problem）
    typed = PROMPT[PROMPT.index("### Answers typed in the chat") : PROMPT.index("### The six stations")]
    assert "`no_example: true`, never as `other_text`" in typed


def test_第_1_關列的主題就是閘道收的八種() -> None:
    theme_line = next(line for line in PROMPT.splitlines() if line.startswith("- One theme card"))
    assert set(re.findall(r"`([a-z-]+)` \(", theme_line)) == THEMES
    # #106 把通用清單放進 harness/genres/themes.md 之後，兩邊也要對得上
    themes_md = default_harness().root / "genres" / "themes.md"
    if themes_md.is_file():
        text = themes_md.read_text(encoding="utf-8")
        assert set(re.findall(r"^\| `([a-z-]+)` \|", text, re.MULTILINE)) == THEMES


# 訪談的硬規則：改提示詞時漏掉一條，模型就不知道那條規則（閘道只擋得到其中幾條）
HARD_RULES = {
    "不替作者編經歷": "Never invent the author's experience, data or examples",
    "沒有例子只有作者能答": "You never answer 「沒有例子」 for them.",
    "不替創作者記點選": "you never record an answer the creator clicked",
    "每一輪先讀編輯卡": "Begin every turn of an interview by calling `read_card`",
    "要作者經驗的文體停在第 3 關": "Without it, stop at station 3",
    "隨筆沒有例子只有從報告開始": "`essay` without an example is possible only when the card started from a report",
    "你幫我選第 1 關停下來問": "If the genre you ranked first is any other, do not pick: ask the creator to choose the genre.",
    "你幫我選第 3 關只問作者": "Station 3: only 「尚待作者提供」 is recorded, which is not a decision.",
    "不蓋掉已經有決定的關": "Never overwrite a decided station.",
    "寫第 N 個開始訪談": "「寫第 N 個」 (or the topic's title) after an analysis completed.",
    "新文章不叫 rewrite": "A new article starts with an interview, never with `rewrite`.",
    "一次一關": "One station at a time, in order: 1 → 6.",
    "文體只有七種": "Never put anything outside the seven on the card",
    "口述在聊天裡確認": "do not propose a confirmation card",
    "讀不到 lessons 照樣往下": "continue the interview without them",
}


@pytest.mark.parametrize("sentence", HARD_RULES.values(), ids=HARD_RULES.keys())
def test_訪談的硬規則都還在提示詞裡(sentence: str) -> None:
    assert sentence in PROMPT
