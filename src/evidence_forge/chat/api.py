"""給聊天室畫面（#86）用的 HTTP API（#85）。只綁 127.0.0.1。

給 `data` 與 `recipes` 時，同一個程序也送出畫面本身（`ui/pages.py`：`/`、`/c/<id>`、`/bills`、`/static`、`/ui/…`）：
網頁跟 API 同源，這裡擋跨站的規則才不會連自己的頁面一起擋掉（#94 審查）。

| 方法與路徑 | 做什麼 |
| :--- | :--- |
| `GET /api/conversations` | 對話清單，最近有動靜的在上面：`{"conversations": [摘要…]}` |
| `POST /api/conversations` | 開一個新對話，回它的摘要（201）。不開 Pi：第一句話送來才開 |
| `GET /api/conversations/<id>` | 一個對話的摘要加每張工單的進度：`{…摘要, "jobs": [ef_job…]}`。不開 Pi |
| `GET /api/conversations/<id>/messages` | 完整紀錄（Pi 的 `get_messages`）與工單進度：`{"messages", "jobs"}`。Pi 沒開就接回 |
| `POST /api/conversations/<id>/messages` | 送一句話：`{"text": "…"}` → 202 `{"disposition": "started"｜"queued"}`。Pi 正在回答也可以送，排在後面 |
| `POST /api/conversations/<id>/seen` | 回報創作者看過這張工單的結果：`{"job_id": "…"}` → 204；清單據此不再標「有新結果」 |
| `GET /api/conversations/<id>/events` | SSE：先一則 `ef_pi`、每張工單的 `ef_job`、每張編輯卡的 `ef_card`（現況），之後是變化。格式見 `chat/host.py` |
| `GET /api/conversations/<id>/cards` | 這個對話的編輯卡：`{"cards": [編輯卡…]}`（`cards.CardState`） |
| `GET /api/conversations/<id>/cards/<卡>` | 一張卡：`{"card": 編輯卡, "events": [決策紀錄的每一筆]}` |
| `POST /api/conversations/<id>/cards/<卡>/stations/<關>/submit` | 「確認並送出這一關」：`{"answers": [{"question", "choice"｜"other_text", "note"?}]}` → `{"card", "events"}` |
| `POST /api/conversations/<id>/cards/<卡>/stations/<關>/keep` | 「沿用」（要重確認的關）：`{}` → `{"card", "events"}` |
| `POST /api/conversations/<id>/cards/<卡>/stations/<關>/reask` | 「重問這一關」：`{}` → `{"card", "events"}` |
| `GET /api/conversations/<id>/lessons` | 這個對話所有的 lesson 提議（含已同意、不要的）：`{"proposals": [提議…]}`（欄位見 `lesson_proposals.Proposal`，另有 `preview`：同意的話會寫進去的那一行） |
| `POST /api/conversations/<id>/lessons/<提議>/approve` | 「同意」：由閘道把那一行附加到 `authors/<角色>/lessons.md`：`{}` → `{"proposal", "events"}`。冪等：同意過再按不會多寫 |
| `POST /api/conversations/<id>/lessons/<提議>/reject` | 「不要」：什麼都不寫：`{}` → `{"proposal", "events"}` |
| `POST /api/conversations/<id>/cards/<卡>/write` | 「照這張編輯卡寫稿」：`{"confirmed_events": 確認時那張卡的 card.events}` → 202 `{"job_id", "status", "persona", "genre", "theme", "card_id", "events", …}` |

編輯卡的三個 POST 是**只有畫面能叫的入口**（ADR-0025 §3.3）：主機直接寫進決策紀錄（`source: ui`），再用 `/ef-card` 通知對話的 Pi；
閘道的 `/tools/` 沒有它們，對話的 agent 叫不到。卡不是這個對話的、或沒有這張卡一律 404，不說存不存在；
不合規則（沒有要重確認卻按沿用、選項不在卡上、沒回答到每一張）是 409 或 400，什麼都不寫。

lessons 的「同意」「不要」也是**只有畫面能叫的入口**（ADR-0026 §3.3、#114）：agent 只能經閘道的 `propose_lesson` 提議，
寫角色資料夾的只有這裡叫起的 `lesson_proposals.approve`。提議不是這個對話的、或根本沒有一律 403，不說存不存在；
已經不要的再同意、已經同意的再不要是 409；角色不見了、是符號連結寫不進去，什麼都不寫，提議留著。

`write` 也只有畫面能叫（#110）：工作台開寫稿單之前再查一次。卡還不能寫、確認之後又改過（筆數對不上，畫面要請創作者重新確認）、
這個對話已經有單在跑、分析交不出報告都是 409，什麼都不開；`confirmed_events` 不是 0 以上的整數 400；
`ef serve` 沒接上工作台 503。開了的單照一般工單推 `ef_job`。

摘要的欄位：`id`、`title`、`created_at`、`last_activity`、`pi_running`、`busy`、`job_count`（幾張）、
`latest_job`（最後一張的 `ef_job`，畫面據此標「分析中／有新結果／失敗」）、`spent_usd`（對話本身加它開的工單）、`budget_usd`、
`seen_job`（創作者看過結果的最後一張工單）。

錯誤一律 `{"error": "…"}`：沒有這個對話 404；訊息是空的、以 `/` 開頭 400；Pi 拒絕 409；Pi 開不起來或沒回應 503。

**只給這台機器上的瀏覽器用。** 它會花錢（每句話都打模型），所以：
- `Host` 標頭只准 127.0.0.1、localhost、[::1]：擋 DNS rebinding（別的網域解析到 127.0.0.1 來打）。
- 帶了 `Origin` 的請求（瀏覽器跨站送的）也只准這幾個主機；`Sec-Fetch-Site` 不是同源就擋（跨站的 `<img>` 不帶 Origin）。
- `POST` 一律要 `Content-Type: application/json`：別的網站不能用一個不需要預檢的表單偷送。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from evidence_forge.chat.host import (
    ConversationHost,
    ConversationNotFound,
    PromptRejected,
)
from evidence_forge.chat.pi_rpc import PiExited
from evidence_forge.gateway.active import StaleKey
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir
from evidence_forge.ui import pages

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# SSE 沒事件時多久送一行註解，讓瀏覽器與中間的代理知道連線還活著
KEEPALIVE_S = 15.0


def _hostname(netloc: str) -> str | None:
    return urlsplit(f"//{netloc}").hostname


_DEFAULT_PORT = {"http": 80, "https": 443}


def _same_origin(origin: str, request: Request) -> bool:
    """`Origin` 標頭跟這個請求自己的來源（協定、主機、埠）完全一樣，而且主機是本機。

    只比主機名稱不夠：本機另一個埠上跑著的別的網頁（例如別的開發伺服器）也是 127.0.0.1，
    它送來的請求不是這個畫面送的。
    """
    parts = urlsplit(origin)
    host = urlsplit(f"//{request.headers.get('host', '')}")
    scheme = request.url.scheme
    try:
        # 明寫的埠照字比對：`:0` 是 0，不是預設埠（所以不能用 `or`）
        origin_port = parts.port if parts.port is not None else _DEFAULT_PORT.get(parts.scheme)
        own_port = host.port if host.port is not None else _DEFAULT_PORT.get(scheme)
    except ValueError:  # 埠不是數字
        return False
    return (
        parts.hostname in LOCAL_HOSTS
        and parts.scheme == scheme
        and parts.hostname == host.hostname
        and origin_port == own_port
    )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _call(work: Callable[[], Any]) -> Any:
    """在執行緒上跑主機的方法（會開 Pi、等 Pi 回應），把例外換成 HTTP 錯誤。"""
    try:
        return await asyncio.to_thread(work)
    except ConversationNotFound:
        return _error(404, "沒有這個對話")
    except ToolRejected as exc:
        # 編輯卡的規則（`cards.py`）：狀態碼照它說的
        return _error(exc.status, exc.message)
    except PromptRejected as exc:
        return _error(409, str(exc))
    except (PiExited, TimeoutError, StaleKey, OSError, RuntimeError) as exc:
        # Pi 開不起來（執行檔不見、舊的殺不掉、鑰匙登記不上）、半路結束、沒回應：紀錄還在，可以重試
        return _error(503, f"這個對話暫時打不開：{exc}")
    except ValueError as exc:
        return _error(400, str(exc))


def create_app(
    host: ConversationHost,
    *,
    data: DataDir | None = None,
    recipes: Mapping[str, Recipe] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Evidence Forge chat", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def local_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _hostname(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return _error(403, "只接受本機的請求")
        origin = request.headers.get("origin")
        if origin is not None and not _same_origin(origin, request):
            return _error(403, "只接受本機網頁送來的請求")
        # 別的網站放一張 <img> 指到這裡，瀏覽器不帶 Origin 但會帶 Sec-Fetch-Site。
        # 讀紀錄也會開 Pi、補送通知（打模型），所以 GET 一樣擋
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return _error(403, "只接受本機網頁送來的請求")
        if request.method == "POST":
            content_type = request.headers.get("content-type", "")
            if content_type.split(";")[0].strip().lower() != "application/json":
                return _error(415, "要用 application/json")
        return await call_next(request)

    @app.get("/api/conversations")
    async def list_conversations() -> Any:
        return {"conversations": await asyncio.to_thread(host.list_conversations)}

    @app.post("/api/conversations", status_code=201)
    async def create_conversation() -> Any:
        return await asyncio.to_thread(host.create)

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: str) -> Any:
        def work() -> dict[str, Any]:
            return {**host.summary(conversation_id), "jobs": host.jobs(conversation_id)}

        return await _call(work)

    @app.get("/api/conversations/{conversation_id}/messages")
    async def history(conversation_id: str) -> Any:
        return await _call(lambda: host.history(conversation_id))

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    async def send(conversation_id: str, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "請求內容要是 JSON")
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            return _error(400, "要有字串欄位 text")
        return await _call(lambda: host.send(conversation_id, text))

    @app.post("/api/conversations/{conversation_id}/seen", status_code=204, response_class=Response)
    async def seen(conversation_id: str, request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "請求內容要是 JSON")
        job_id = body.get("job_id") if isinstance(body, dict) else None
        if not isinstance(job_id, str):
            return _error(400, "要有字串欄位 job_id")
        result = await _call(lambda: host.mark_seen(conversation_id, job_id))
        return result if isinstance(result, Response) else Response(status_code=204)

    @app.get("/api/conversations/{conversation_id}/cards")
    async def list_cards(conversation_id: str) -> Any:
        return await _call(lambda: {"cards": host.list_cards(conversation_id)})

    @app.get("/api/conversations/{conversation_id}/cards/{card_id}")
    async def card(conversation_id: str, card_id: str) -> Any:
        return await _call(lambda: host.card(conversation_id, card_id))

    @app.post("/api/conversations/{conversation_id}/cards/{card_id}/stations/{station}/{action}")
    async def card_action(
        conversation_id: str, card_id: str, station: str, action: str, request: Request
    ) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "請求內容要是 JSON")
        if not isinstance(body, dict):
            return _error(400, "請求內容要是 JSON 物件")
        # 關卡號碼不是 1～6 就原樣交給 `cards.py`，由它回 400（`isdigit` 認得「²」，int 卻轉不了，所以只認 ASCII 一位數）
        number: object = int(station) if station in ("1", "2", "3", "4", "5", "6") else station
        if action == "submit":
            answers = body.get("answers")
            return await _call(lambda: host.submit_station(conversation_id, card_id, number, answers))
        if action == "keep":
            return await _call(lambda: host.keep_decision(conversation_id, card_id, number))
        if action == "reask":
            return await _call(lambda: host.reask_station(conversation_id, card_id, number))
        return _error(404, f"沒有這個動作：{action}")

    @app.get("/api/conversations/{conversation_id}/lessons")
    async def list_lessons(conversation_id: str) -> Any:
        return await _call(lambda: host.list_lessons(conversation_id))

    @app.post("/api/conversations/{conversation_id}/lessons/{proposal_id}/{action}")
    async def lesson_action(conversation_id: str, proposal_id: str, action: str) -> Any:
        if action == "approve":
            return await _call(lambda: host.approve_lesson(conversation_id, proposal_id))
        if action == "reject":
            return await _call(lambda: host.reject_lesson(conversation_id, proposal_id))
        return _error(404, f"沒有這個動作：{action}")

    @app.post("/api/conversations/{conversation_id}/cards/{card_id}/write", status_code=202)
    async def write_from_card(conversation_id: str, card_id: str, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "請求內容要是 JSON")
        if not isinstance(body, dict):
            return _error(400, "請求內容要是 JSON 物件")
        confirmed = body.get("confirmed_events")
        return await _call(lambda: host.write_from_card(conversation_id, card_id, confirmed))

    @app.get("/api/conversations/{conversation_id}/events")
    async def events(conversation_id: str) -> Any:
        try:
            subscription = await asyncio.to_thread(
                host.subscribe, conversation_id, asyncio.get_running_loop()
            )
        except ConversationNotFound:
            return _error(404, "沒有這個對話")

        async def stream() -> AsyncIterator[bytes]:
            try:
                yield b": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(subscription.get(), KEEPALIVE_S)
                    except TimeoutError:
                        yield b": keepalive\n\n"
                        continue
                    if event is None:
                        # 積太多沒送出去，或主機關機：結束串流，畫面重連後用完整紀錄補
                        return
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            finally:
                host.unsubscribe(conversation_id, subscription)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    if data is not None:
        pages.install(app, host, data, recipes or {})
    return app
