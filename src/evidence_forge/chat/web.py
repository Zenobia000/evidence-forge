"""最小的聊天網頁（#80 spike）：對話清單、送訊息、用 SSE 收 Pi 的事件與工單進度。

P3 的正式 UI 會照 FR-UI-11 重做（左清單、中串流、右面板）；這頁只證明資料走得通。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from evidence_forge import conversations
from evidence_forge.chat.hub import ChatHub
from evidence_forge.paths import DataDir

_DIR = Path(__file__).resolve().parent
# SSE 沒事件時多久送一行註解，讓瀏覽器與中間的代理知道連線還活著
KEEPALIVE_S = 15.0


def _known(data: DataDir, conversation_id: str) -> bool:
    try:
        conversations.read(data, conversation_id)
    except (OSError, ValueError):
        return False
    return True


def create_app(hub: ChatHub, data: DataDir, clock: Callable[[], datetime]) -> FastAPI:
    app = FastAPI(title="Evidence Forge chat", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=_DIR / "templates")

    def missing() -> JSONResponse:
        return JSONResponse({"error": "沒有這個對話"}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "chat.html",
            {"conversations": conversations.list_all(data), "current": None},
        )

    @app.post("/new")
    async def new() -> RedirectResponse:
        conversation = conversations.create(data, clock())
        return RedirectResponse(f"/c/{conversation.id}", status_code=303)

    @app.get("/c/{conversation_id}", response_class=HTMLResponse)
    async def page(request: Request, conversation_id: str) -> HTMLResponse:
        if not _known(data, conversation_id):
            return HTMLResponse("沒有這個對話", status_code=404)
        return templates.TemplateResponse(
            request,
            "chat.html",
            {
                "conversations": conversations.list_all(data),
                "current": conversations.read(data, conversation_id),
            },
        )

    @app.get("/c/{conversation_id}/history")
    async def history(conversation_id: str) -> Any:
        if not _known(data, conversation_id):
            return missing()
        messages = await asyncio.to_thread(hub.history, conversation_id)
        jobs = await asyncio.to_thread(hub.jobs_progress, conversation_id)
        return {"messages": messages, "jobs": jobs}

    @app.post("/c/{conversation_id}/send")
    async def send(conversation_id: str, request: Request) -> Any:
        if not _known(data, conversation_id):
            return missing()
        body = await request.json()
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            return JSONResponse({"error": "要有 text"}, status_code=400)
        problem = await asyncio.to_thread(hub.send_user, conversation_id, text)
        if problem is not None:
            return JSONResponse({"error": problem}, status_code=400)
        return {"ok": True}

    @app.post("/c/{conversation_id}/kill")
    async def kill(conversation_id: str) -> Any:
        """spike 用：殺掉這個對話的 Pi，驗證接回。"""
        return {"killed": await asyncio.to_thread(hub.kill, conversation_id)}

    @app.get("/c/{conversation_id}/events")
    async def events(conversation_id: str, request: Request) -> Any:
        if not _known(data, conversation_id):
            return missing()
        queue = hub.subscribe(conversation_id)

        async def stream() -> AsyncIterator[bytes]:
            try:
                yield b": connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                    except TimeoutError:
                        yield b": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            finally:
                hub.unsubscribe(conversation_id, queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache"},
        )

    return app
