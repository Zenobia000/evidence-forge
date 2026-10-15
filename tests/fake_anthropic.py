"""照劇本回答的假 Anthropic（#85）：不花錢跑一次「丟文章 → 開單 → 工單跑完 → 通知 → 讀報告 → 整理」。

測試直接用 `create_app()`；手動的端到端檢查可以單獨開起來，閘道用 `EF_ANTHROPIC_URL` 指過來：

    uv run python tests/fake_anthropic.py --port 9911

劇本只看請求本身，不記狀態：
- 對話的 Pi（工具清單裡有 `analyze`）：
  - 最後一則是工具結果：`analyze` 的 → 「開始分析了。」；`rewrite` 的 → 「開始寫了。」；
    `read_report` 的 → 「報告重點：」加報告結尾；`read_article` 的 → 「文章重點：」加文章結尾；其他 → 「好。」
  - 最後一則訊息有 `[ef-job]` → 照通知說的叫 `read_report` 或 `read_article`（工單 id 從通知裡拿）。
  - 最後一則訊息有 `[ef-card]` → 叫 `read_card`（卡 id 從通知裡拿）；讀完回「編輯卡：」加編輯卡的結尾。
  - 有網址 → 叫 `analyze(url)`；以「分析：」開頭 → 叫 `analyze(text)`。
  - 訪談（#109，照 `chat-system-prompt.md` 的〈Interview〉走，見 `_interview`）：
    - 起點：「寫第 N 個」（報告的題材）、「想寫：<想法>」、「素材：<作者素材>」→ （素材先 `add_material`）
      `list_personas` → `read_lessons` →（題材先 `read_report`）→ `propose_questions` 第 1 關，帶 `origin`。
    - 一輪訪談先 `read_card`（不知道卡 id 就先列）再接著做：`[ef-card]` 通知 → 提出下一關（重問的那一關就重新提出）；
      「你幫我選」→ `record_interpreted_answers(pick_for_me)`，第 3 關記了「尚待作者提供」就重新提出第 3 關；
      「回答第 N 關：<文字>」→ 已經有決定的關先重新提出，再記這段話（「沒有例子」記成 `no_example`）。
    - 被閘道拒絕 → 「沒記：<原因>」；六關都有決定 → 「編輯卡六關都有決定了。」，不叫 `rewrite`。
  - 「直接重構第 N 個」而且有 `rewrite` → 叫 `rewrite(from_job=最近一張分析的工單, goal="題材 N")`
    （分析的工單從「叫你讀報告」的 `[ef-job]` 通知拿）。用來證明閘道擋下：新文章從訪談開始（#109）。
  - 「改寫 <工單 id>：<意見>」→ 叫 `rewrite(from_job=那張重構單, feedback=意見)`（改已經有的文章）。
  - `rewrite` 被拒 → 「沒開：<原因>」；開了 → 「開始寫了。」
  - 其他 → 「回覆：<原句>」。
- 段的 Pi（echo 這類 skill）：先 `read_file input/content.md`，再把摘要 `write_file` 到任務說明裡的輸出目錄，最後一句「寫好了。」

`EF_FAKE_ANTHROPIC_DELAY_S`（或 `create_app(delay_s=…)`）讓每次回答先等一下，給「Pi 正在回答時送訊息」用。
"""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

_OUTPUT_DIR = re.compile(r"stages/[A-Za-z0-9._-]+/\d+/")
_JOB_ID = re.compile(r"job_id: (\S+)")
_CARD_ID = re.compile(r"card: (\S+)")
_URL = re.compile(r"https?://\S+")
_WRITE_TOPIC = re.compile(r"^寫第\s*(\d+)\s*個")
_REWRITE_TOPIC = re.compile(r"直接重構第\s*(\d+)\s*個")
_REVISE = re.compile(r"^改寫 (\S+)：(.+)$", re.DOTALL)
_TYPED = re.compile(r"^回答第\s*([1-6])\s*關：(.+)$", re.DOTALL)
_STATION_LINE = re.compile(r"^Station (\d) .*: (\w+)$")
_QUESTION_LINE = re.compile(r"^  (q\d+) \[")


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in _blocks(message):
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                parts.append(inner)
            elif isinstance(inner, list):
                parts.extend(str(b.get("text", "")) for b in inner if isinstance(b, dict))
    return "\n".join(parts)


def _tool_names(body: dict[str, Any]) -> set[str]:
    return {str(t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)}


def _answered_tool(messages: list[dict[str, Any]]) -> str | None:
    """最後一則是工具結果的話，回它在回答哪個工具。"""
    last = messages[-1]
    results = [b for b in _blocks(last) if b.get("type") == "tool_result"]
    if not results:
        return None
    wanted = results[-1].get("tool_use_id")
    for message in reversed(messages[:-1]):
        for block in _blocks(message):
            if block.get("type") == "tool_use" and block.get("id") == wanted:
                return str(block.get("name"))
    return "?"


# --- 訪談（#109）：照 chat-system-prompt.md 的〈Interview〉走的劇本 --------------------------------

_GENRE_NAMES = {
    "essay": "隨筆",
    "research-review": "研究評論",
    "product-case": "產品個案",
    "benchmark": "實證評測",
    "postmortem": "工程復盤",
    "explainer": "理論教學",
    "trend": "趨勢解讀",
}
# 第 1 關文體卡列哪幾種，照起點：從想法開始的全是要作者經驗的文體（「你幫我選」會被拒）
_START_GENRES = {
    "report_topic": ("research-review", "product-case"),
    "idea": ("product-case", "essay", "benchmark"),
    "material": ("explainer", "postmortem"),
}
_DECIDED = frozenset({"decided", "changed", "kept"})


@dataclass
class _Call:
    name: str
    input: dict[str, Any]
    result: str
    error: bool


@dataclass
class _Card:
    card_id: str
    origin: str
    writable: bool
    current: int | None
    statuses: dict[int, str]
    questions: dict[int, list[str]]


def _turn(messages: list[dict[str, Any]]) -> tuple[str, list[_Call]]:
    """這一輪：開頭那則（創作者的話或通知）與這一輪叫過的工具，照順序。"""
    start = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") == "user"
            and any(b.get("type") == "text" for b in _blocks(messages[i]))
        ),
        None,
    )
    if start is None:
        return "", []
    opening = "\n".join(
        str(b.get("text", "")) for b in _blocks(messages[start]) if b.get("type") == "text"
    )
    uses: dict[Any, dict[str, Any]] = {}
    calls: list[_Call] = []
    for message in messages[start + 1 :]:
        for block in _blocks(message):
            if block.get("type") == "tool_use":
                uses[block.get("id")] = block
            elif block.get("type") == "tool_result":
                use = uses.get(block.get("tool_use_id"), {})
                calls.append(
                    _Call(
                        name=str(use.get("name")),
                        input=dict(use.get("input") or {}),
                        result=_text({"content": [block]}),
                        error=bool(block.get("is_error")),
                    )
                )
    return opening.strip(), calls


def _parse_card(text: str) -> "_Card | None":
    """ef-tools 的 describeCard 印出來的編輯卡。"""
    found = _CARD_ID.search(text)
    if found is None or "Station 1 " not in text:
        return None
    statuses: dict[int, str] = {}
    questions: dict[int, list[str]] = {}
    station: int | None = None
    current: int | None = None
    origin = ""
    for line in text.splitlines():
        if line.startswith("origin: "):
            origin = line.split()[1]
        if "current station: " in line:
            value = line.rsplit("current station: ", 1)[1].strip()
            current = int(value) if value.isdigit() else None
        head = _STATION_LINE.match(line)
        if head:
            station = int(head.group(1))
            statuses[station] = head.group(2)
            questions[station] = []
            continue
        question = _QUESTION_LINE.match(line)
        if question and station is not None:
            questions[station].append(question.group(1))
    return _Card(
        card_id=found.group(1),
        origin=origin,
        writable="writable: yes" in text,
        current=current,
        statuses=statuses,
        questions=questions,
    )


def _option(label: str, description: str, value: str | None = None) -> dict[str, str]:
    option = {"label": label, "description": description}
    if value is not None:
        option["value"] = value
    return option


def station_questions(station: int, origin: str, personas: int = 1) -> list[dict[str, Any]]:
    """每一關的問題卡（固定的劇本；第 1 關的文體照起點）。"""
    if station == 1:
        genres = _START_GENRES.get(origin, _START_GENRES["report_topic"])
        cards: list[dict[str, Any]] = [
            {
                "kind": "genre",
                "header": "文體",
                "question": "這篇的主證據是什麼、讀者讀完要做哪個決定？",
                "options": [_option(_GENRE_NAMES[g], f"{g} 的主證據", g) for g in genres],
            },
            {
                "kind": "theme",
                "header": "主題",
                "question": "讀者做決定時最在意哪一個？",
                "options": [
                    _option("工具與工作流程選型", "讀者要挑工具", "tools"),
                    _option("做產品的方法與工程實務", "讀者要做產品", "builder-method"),
                ],
            },
        ]
        if personas > 1:
            cards.append(
                {
                    "kind": "persona",
                    "header": "角色",
                    "question": "用誰的聲音寫？",
                    "options": [
                        _option("創作者自己", "用 me 的聲音", "me"),
                        _option("另一個角色", "用 ally 的聲音", "ally"),
                    ],
                }
            )
        return cards
    if station == 2:
        return [
            {
                "header": "觀點",
                "question": "這篇的核心觀點叫什麼？",
                "options": [
                    _option("夜班是公共服務", "延長開放是在補上班族的缺口"),
                    _option("夜班是成本問題", "人事費從購書預算挪過來"),
                ],
            }
        ]
    if station == 3:
        return [
            {
                "header": "例子",
                "question": "有沒有只有你能給的例子？",
                "options": [
                    _option("你有沒有自己夜裡去過圖書館？", "寫下你看到的"),
                    _option("你量過晚上的人數嗎？", "寫下你量到的數字"),
                ],
            }
        ]
    if station == 4:
        return [
            {
                "header": "成立條件",
                "question": "這個觀點在什麼條件下成立？",
                "options": [
                    _option("晚上人數穩定", "半年後仍有三成"),
                    _option("預算不再被挪", "購書另有來源"),
                ],
            },
            {
                "header": "失效條件",
                "question": "什麼情況下不成立？",
                "options": [
                    _option("人數掉到十人以下", "新鮮感過了"),
                    _option("書架空了", "購書停擺"),
                ],
            },
        ]
    if station == 5:
        return [
            {
                "header": "工作標題",
                "question": "先用哪個當錨？",
                "options": [_option("夜班的帳", "直接講成本"), _option("十點的圖書館", "講場景")],
            }
        ]
    return [
        {
            "header": "框架",
            "question": "用哪種結構？",
            "options": [
                _option("問題 → 證據 → 判斷", "推薦"),
                _option("場景 → 數字 → 取捨", "換個開頭"),
            ],
        },
        {
            "header": "大綱",
            "question": "各節的順序與主張？",
            "options": [_option("四節", "缺口、成本、數字、取捨"), _option("三節", "缺口、成本、取捨")],
        },
        {
            "header": "圖文分鏡",
            "question": "每節配不配圖？",
            "options": [
                _option("第 3 節數據圖", "其餘不配圖；第 2 節用 Markdown 表格"),
                _option("第 1 節概念圖", "其餘不配圖"),
            ],
        },
        {
            "header": "主張",
            "question": "1. 晚上平均 42 人（錯在：館方數字只有頭兩個月）"
            "2. 人事費每月六萬（錯在：預算書不是這個數）",
            "options": [
                _option("照這份寫", "寫稿照這份主張"),
                _option("要刪改（寫在補充）", "補充寫要改哪條"),
            ],
        },
    ]


def _tool(name: str, arguments: dict[str, Any]) -> tuple[str, Any]:
    return "tool", (name, arguments)


def _personas(calls: list[_Call]) -> int:
    listed = next((c.result for c in calls if c.name == "list_personas"), "")
    return sum(1 for line in listed.splitlines() if line.startswith("- "))


def _analysis_job(messages: list[dict[str, Any]]) -> str | None:
    """最近一張分析的工單（從「叫你讀報告」的 `[ef-job]` 通知拿）。"""
    found = [
        job.group(1)
        for message in messages
        if "[ef-job]" in _text(message)
        and "read_report" in _text(message)
        and (job := _JOB_ID.search(_text(message)))
    ]
    return found[-1] if found else None


def _start(
    origin: str, opening: str, messages: list[dict[str, Any]], calls: list[_Call]
) -> tuple[str, Any] | None:
    """三種起點：讀角色與 lessons（題材先讀報告），再提出第 1 關，帶 origin。"""
    done = {c.name for c in calls}
    job = _analysis_job(messages)
    if origin == "report_topic" and job is None:
        return None
    if origin == "material" and "add_material" not in done:
        return _tool("add_material", {"text": opening[len("素材：") :]})
    if "list_personas" not in done:
        return _tool("list_personas", {})
    if "read_lessons" not in done:
        return _tool("read_lessons", {})
    if origin == "report_topic" and "read_report" not in done:
        return _tool("read_report", {"job_id": job})
    if "propose_questions" not in done:
        body: dict[str, Any] = {"station": 1, "questions": station_questions(1, origin, _personas(calls))}
        if origin == "report_topic":
            topic = _WRITE_TOPIC.match(opening)
            body["origin"] = {
                "kind": origin,
                "job_id": job,
                "topic": int(topic.group(1)) if topic else 1,
                "title": "夜班值不值得",
            }
        elif origin == "idea":
            body["origin"] = {"kind": origin, "title": opening[len("想寫：") :][:40]}
        else:
            added = next(c for c in calls if c.name == "add_material")
            card = re.search(r"card_id: (\S+)", added.result)
            body["card"] = card.group(1) if card else ""
            body["origin"] = {"kind": origin, "title": "夜班的筆記"}
        return _tool("propose_questions", body)
    last = calls[-1]
    if last.error:
        return "text", "沒記：" + last.result
    return "text", "第 1 關的問題卡在畫面上了。"


def _propose(card: _Card, station: int) -> tuple[str, Any]:
    return _tool(
        "propose_questions",
        {"card": card.card_id, "station": station, "questions": station_questions(station, card.origin)},
    )


def _next_step(card: _Card) -> tuple[str, Any]:
    if card.writable:
        return "text", "編輯卡六關都有決定了。照編輯卡寫稿還沒接上，之後會加。"
    station = card.current
    if station is not None and card.statuses.get(station) == "not_reached":
        return _propose(card, station)
    return "text", f"等你回答第 {station} 關。"


def _record_typed(card: _Card, station: int, answer: str, quote: str) -> tuple[str, Any]:
    form: dict[str, Any] = {"no_example": True} if answer == "沒有例子" else {"other_text": answer}
    return _tool(
        "record_interpreted_answers",
        {
            "card": card.card_id,
            "station": station,
            "quote": quote,
            "answers": [{"question": q, **form} for q in card.questions.get(station, [])],
        },
    )


def _interview(messages: list[dict[str, Any]]) -> tuple[str, Any] | None:
    opening, calls = _turn(messages)
    if _WRITE_TOPIC.match(opening):
        return _start("report_topic", opening, messages, calls)
    if opening.startswith("想寫："):
        return _start("idea", opening, messages, calls)
    if opening.startswith("素材："):
        return _start("material", opening, messages, calls)
    typed = _TYPED.match(opening)
    notice = opening.startswith("[ef-card]")
    if not (notice or typed or opening == "你幫我選"):
        return None
    # 每一輪先讀編輯卡，不靠通知（提示詞〈How a station is asked〉）
    if not any(c.name == "read_card" for c in calls):
        known = [m.group(1) for m in map(_CARD_ID.search, map(_text, messages)) if m]
        return _tool("read_card", {"card": known[-1]} if known else {})
    last = calls[-1]
    if last.error:
        return "text", "沒記：" + last.result
    cards = [card for c in calls if not c.error and (card := _parse_card(c.result))]
    if not cards:
        listed = re.search(r"^- (\S+)", last.result, re.MULTILINE)
        if last.name == "read_card" and listed:
            return _tool("read_card", {"card": listed.group(1)})
        return "text", "這個對話還沒有編輯卡。"
    card = cards[-1]
    done = [c.name for c in calls]
    if last.name == "propose_questions":
        if typed and "record_interpreted_answers" not in done:
            return _record_typed(card, int(typed.group(1)), typed.group(2).strip(), opening)
        return "text", f"第 {last.input.get('station')} 關的問題卡在畫面上了。"
    if last.name == "record_interpreted_answers":
        station = int(last.input.get("station", 0))
        if station == 3 and card.statuses.get(3) == "pending_author":
            # 「尚待作者提供」不算有決定：只問作者這一題
            return _propose(card, 3)
        return _next_step(card)
    # 剛讀完編輯卡
    reask = re.search(r"asked to re-ask station (\d)", opening)
    if notice and reask:
        return _propose(card, int(reask.group(1)))
    if opening == "你幫我選":
        return _tool(
            "record_interpreted_answers",
            {"card": card.card_id, "station": card.current, "pick_for_me": True},
        )
    if typed:
        station = int(typed.group(1))
        if card.statuses.get(station) in _DECIDED:
            # 不蓋掉已經有決定的關：先重新提出這一關
            return _propose(card, station)
        return _record_typed(card, station, typed.group(2).strip(), opening)
    return _next_step(card)


def decide(body: dict[str, Any]) -> tuple[str, Any]:
    """回 `("text", 字串)` 或 `("tool", (名稱, 參數))`。"""
    messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    if not messages:
        return "text", "（沒有訊息）"
    tools = _tool_names(body)
    answered = _answered_tool(messages)
    last_text = _text(messages[-1])
    if "analyze" in tools:
        interview = _interview(messages)
        if interview is not None:
            return interview
        if answered == "analyze":
            return "text", "開始分析了。"
        if answered == "rewrite":
            _, calls = _turn(messages)
            if calls and calls[-1].error:
                return "text", "沒開：" + calls[-1].result
            return "text", "開始寫了。"
        if answered == "read_report":
            return "text", "報告重點：" + last_text.strip()[-60:]
        if answered == "read_article":
            return "text", "文章重點：" + last_text.strip()[-60:]
        if answered == "read_card":
            return "text", "編輯卡：" + last_text.strip()[-200:]
        if answered is not None:
            return "text", "好。"
        for message in reversed(messages):
            if message.get("role") != "user":
                break
            card = _CARD_ID.search(_text(message))
            if "[ef-card]" in _text(message) and card:
                return "tool", ("read_card", {"card": card.group(1)})
            found = _JOB_ID.search(_text(message))
            if "[ef-job]" in _text(message) and found:
                tool = "read_article" if "read_article" in _text(message) else "read_report"
                return "tool", (tool, {"job_id": found.group(1)})
        topic = _REWRITE_TOPIC.search(last_text)
        analysis = _analysis_job(messages)
        if topic and "rewrite" in tools and analysis is not None:
            return "tool", ("rewrite", {"from_job": analysis, "goal": f"題材 {topic.group(1)}"})
        revise = _REVISE.match(last_text.strip())
        if revise and "rewrite" in tools:
            return "tool", ("rewrite", {"from_job": revise.group(1), "feedback": revise.group(2)})
        url = _URL.search(last_text)
        if url:
            return "tool", ("analyze", {"url": url.group(0)})
        if last_text.startswith("分析："):
            return "tool", ("analyze", {"text": last_text[len("分析：") :]})
        return "text", "回覆：" + last_text.strip()
    if "read_file" in tools:
        if answered is None:
            return "tool", ("read_file", {"path": "input/content.md"})
        if answered == "read_file":
            prompt = "\n".join(_text(m) for m in messages if m.get("role") == "user")
            system = body.get("system")
            if isinstance(system, list):
                prompt += "\n".join(str(b.get("text", "")) for b in system if isinstance(b, dict))
            elif isinstance(system, str):
                prompt += system
            folder = _OUTPUT_DIR.search(prompt)
            path = (folder.group(0) if folder else "stages/echo/1/") + "summary.json"
            summary = json.dumps({"summary": "假的摘要：" + last_text.strip()[:40]}, ensure_ascii=False)
            return "tool", ("write_file", {"path": path, "content": summary})
        return "text", "寫好了。"
    return "text", "好。"


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events
    ).encode()


def reply(kind: str, value: Any, number: int) -> bytes:
    start = {
        "type": "message_start",
        "message": {
            "id": f"msg_fake_{number}",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "fake",
            "stop_reason": None,
            "usage": {"input_tokens": 100, "output_tokens": 1},
        },
    }
    block: dict[str, Any]
    if kind == "text":
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": value}
        stop = "end_turn"
    else:
        name, arguments = value
        block = {"type": "tool_use", "id": f"toolu_fake_{number}", "name": name, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments, ensure_ascii=False)}
        stop = "tool_use"
    return _sse(
        [
            ("message_start", start),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 20}},
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
    )


@dataclass
class FakeAnthropic:
    delay_s: float = 0.0
    requests: list[dict[str, Any]] = field(default_factory=list)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/models")
        async def models() -> dict[str, Any]:
            # 閘道的開跑前檢查拿它確認金鑰有效（不花錢）
            return {"data": [], "has_more": False}

        @app.post("/v1/messages")
        async def messages(request: Request) -> Response:
            body = await request.json()
            self.requests.append(body)
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            kind, value = decide(body)
            return Response(reply(kind, value, len(self.requests)), media_type="text/event-stream")

        return app


def create_app(delay_s: float | None = None) -> FastAPI:
    if delay_s is None:
        delay_s = float(os.environ.get("EF_FAKE_ANTHROPIC_DELAY_S", "0") or 0)
    return FakeAnthropic(delay_s=delay_s).app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="照劇本回答的假 Anthropic（只綁 127.0.0.1）")
    parser.add_argument("--port", type=int, default=9911)
    parsed = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=parsed.port, log_level="warning")
