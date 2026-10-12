"""測試共用的假 Tavily（`POST /search`）：閘道的搜尋測試與執行器的整合測試都用它。

金鑰是假的（`tvly-fake-xxxx-…`），放在測試自己的 `EF_SECRETS` 資料夾；
任何請求都只到 127.0.0.1（`tests/conftest.py` 擋掉往外的連線）。
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

# 假金鑰：帶 xxxx，guard-secrets 認得是假的；整串出現在任何輸出裡就是外洩
FAKE_KEY = "tvly-fake-xxxx-tavily-canary-41c9"

RESULTS: list[dict[str, Any]] = [
    {
        "url": "https://example.org/a",
        "title": "第一篇",
        "content": "第一篇的摘要",
        "score": 0.91,
        "published_date": "2026-10-01",
        "raw_content": None,
    },
    {
        "url": "https://example.org/b",
        "title": "第二篇",
        "content": "第二篇的摘要",
        "score": 0.5,
    },
]


def tavily_body(
    results: list[dict[str, Any]] | None = None, credits: int | None = 1
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": "ignored",
        # Tavily 自己用模型寫的答案：不是來源，閘道不能交給 agent
        "answer": "Tavily 的模型答案",
        "images": [],
        "results": RESULTS if results is None else results,
        "response_time": 0.42,
        "request_id": "req-1",
    }
    if credits is not None:
        body["usage"] = {"credits": credits}
    return body


Reply = Callable[[], Response]


def ok(credits: int | None = 1) -> Reply:
    return lambda: JSONResponse(tavily_body(credits=credits))


def status(code: int, detail: str | None = None, **headers: str) -> Reply:
    """回 `code`；`detail` 是 Tavily 錯誤說明的內容（沒給就是 `stub <code>`）。"""
    return lambda: JSONResponse(
        {"detail": {"error": detail if detail is not None else f"stub {code}"}},
        status_code=code,
        headers=headers,
    )


@dataclass
class Tavily:
    """假的 Tavily：`replies` 依序用完之後回 `ok()`；`delay_s` 讓回應卡一下（逾時用）。"""

    url: str = ""
    received: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)
    delay_s: float = 0.0

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/search")
        async def search(request: Request) -> Response:
            self.received.append(json.loads(await request.body()))
            self.headers.append(dict(request.headers))
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.replies:
                return self.replies.pop(0)()
            return ok()()

        return app
