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
  - 「寫第 N 個」而且有 `rewrite` → 叫 `rewrite(from_job=最近一張分析的工單, goal="題材 N")`
    （分析的工單從「叫你讀報告」的 `[ef-job]` 通知拿）。
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
_WRITE_TOPIC = re.compile(r"寫第\s*(\d+)\s*個")


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


def decide(body: dict[str, Any]) -> tuple[str, Any]:
    """回 `("text", 字串)` 或 `("tool", (名稱, 參數))`。"""
    messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    if not messages:
        return "text", "（沒有訊息）"
    tools = _tool_names(body)
    answered = _answered_tool(messages)
    last_text = _text(messages[-1])
    if "analyze" in tools:
        if answered == "analyze":
            return "text", "開始分析了。"
        if answered == "rewrite":
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
        topic = _WRITE_TOPIC.search(last_text)
        if topic and "rewrite" in tools:
            analyses = [
                found.group(1)
                for message in messages
                if "[ef-job]" in _text(message)
                and "read_report" in _text(message)
                and (found := _JOB_ID.search(_text(message)))
            ]
            if analyses:
                return "tool", ("rewrite", {"from_job": analyses[-1], "goal": f"題材 {topic.group(1)}"})
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
