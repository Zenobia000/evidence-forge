"""閘道的 HTTP 介面（架構草案 v0.3 §4）。

- `POST /tools/<名稱>`：ef-tools 的工具呼叫。段（工單鑰匙）用 `read_file`、`list_files`、
  `write_file`、`fetch_url`、`web_search`、`run_script`；對話（對話鑰匙）只能用 `analyze`、
  `read_report`、`read_article`、`job_status`、`list_personas`（`rewrite` 先回 501）
  （`chat_tools.py`、ADR-0021）。兩邊互相拿對方的工具一律 403。
- `POST /v1/messages`：Pi 的模型請求（Anthropic Messages 格式），見 `messages.py`。
- `GET /health`、`GET /health/anthropic`：執行器的開跑前檢查用（閘道活著、金鑰有效）。

兩種請求都在 `x-api-key` 帶工單鑰匙或對話鑰匙；閘道只接受登記中的那一把（`ActiveStages`）。
`fetch_url`、`web_search` 與 `run_script` 每呼叫一次，在這張工單的帳本記一筆
（`kind: fetch`／`search`／`script`）。搜尋的規則在 `search.py`。
重播模式下 `fetch_url` 與 `web_search` 只回放錄音（`fetch.RecordedFetches`、`search.py`），不連網。
"""

import json
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway import ledger, scope, scripts
from evidence_forge.gateway.chat_tools import CHAT_TOOLS, ChatTools, Desk
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.credentials import MissingCredential, read_anthropic_key
from evidence_forge.gateway.fetch import Fetcher, RecordedFetches, request_bytes
from evidence_forge.gateway.messages import add_message_routes
from evidence_forge.gateway.search import Searcher, parse_query
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry, LedgerKind, LedgerStatus
from evidence_forge.models import FetchRecord

# 要上網的工具：只有登記成能上網的段（配方給了搜尋額度）才准用
ONLINE_TOOLS = frozenset({"fetch_url", "web_search"})

__all__ = ["ActiveStages", "GatewayConfig", "JobBoard", "create_app"]


def _string(body: dict[str, Any], name: str, default: str | None = None) -> str:
    value = body.get(name, default)
    if not isinstance(value, str):
        raise scope.ToolRejected(400, f"缺少字串欄位 {name}")
    return value


def _strings(body: dict[str, Any], name: str) -> list[str]:
    value = body.get(name, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise scope.ToolRejected(400, f"{name} 要是字串清單")
    return value


def create_app(
    config: GatewayConfig,
    stages: ActiveStages,
    board: JobBoard | None = None,
    *,
    fetcher: Fetcher | None = None,
    searcher: Searcher | None = None,
    script_timeout_s: float = scripts.DEFAULT_TIMEOUT_S,
    desk: Desk | None = None,
) -> FastAPI:
    """`board` 要跟執行器共用（`ef serve`）；不傳就自己開一個，執行器讀不到。

    `fetcher` 不傳就照環境變數建一個（`EF_FETCH_TTL_SECONDS`）；測試傳假 DNS 的那一個。
    `searcher` 不傳就照設定建一個（`EF_SEARCH_TTL_SECONDS`），跟模型呼叫共用 `board`；
    測試傳逾時短的那一個，要看告示板的話建它時傳同一個 `board`。
    `desk` 是對話的工具開單、讀結果用的那一層（對話主機注入）；不傳的話對話的工具回 503。
    """
    app = FastAPI(title="evidence-forge gateway")
    board = board or JobBoard(config.clock)
    fetcher = fetcher or Fetcher.from_env(config.data, clock=config.clock)
    searcher = searcher or Searcher(config, board=board)
    blobs = fetcher.blobs
    chat = ChatTools(config, desk)
    # 重播模式的抓取只看錄音；正式執行是 None
    recorded: RecordedFetches | None = None
    if config.mode == "replay":
        if config.replay_dir is None:
            raise ValueError("重播模式要指定錄音的位置（replay_dir）")
        recorded = RecordedFetches(config.replay_dir)

    def record(
        key: JobKey,
        kind: LedgerKind,
        status: LedgerStatus,
        *,
        ts: datetime,
        latency_ms: int,
        request_hash: str,
        response_hash: str | None,
    ) -> None:
        ledger.append(
            config.data.job_dir(key.job_id),
            LedgerEntry(
                ts=ts,
                job_id=key.job_id,
                stage=key.stage,
                attempt=key.attempt,
                kind=kind,
                latency_ms=latency_ms,
                status=status,
                request_hash=request_hash,
                response_hash=response_hash,
            ),
        )

    @app.exception_handler(scope.ToolRejected)
    async def _rejected(_: Request, exc: scope.ToolRejected) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.post("/tools/{name}")
    async def tool(name: str, request: Request) -> dict[str, Any]:
        key = require_active_key(request.headers.get("x-api-key"), stages)
        try:
            body = await request.json()
        except ValueError:
            raise scope.ToolRejected(400, "請求內容要是 JSON") from None
        if not isinstance(body, dict):
            raise scope.ToolRejected(400, "請求內容要是 JSON 物件")
        # 對話鑰匙只能用對話的工具；段的工具（讀寫檔、抓網頁、搜尋、跑腳本）只給工單鑰匙（NFR-SEC-02）
        if isinstance(key, ConversationKey):
            if name not in CHAT_TOOLS:
                raise scope.ToolRejected(403, f"對話不能用 {name}")
            return await chat.handle(key, name, body)
        if name in CHAT_TOOLS:
            # 段不能自己開新工單、也不能讀別張單的結果
            raise scope.ToolRejected(403, f"段不能用 {name}")
        job_dir = config.data.job_dir(key.job_id)
        if name in ONLINE_TOOLS and not stages.online(key):
            # 配方沒給這一段搜尋額度：不准上網（例：重構段不准把原文抓回來，#92 審查）。
            # Pi 那邊的工具清單不動（動了錄好的請求就對不上），由閘道擋
            raise scope.ToolRejected(
                403,
                f"這一段不能上網：配方沒有給 {key.stage} 段搜尋額度（tavily_credits），"
                "fetch_url 與 web_search 都不能用，只用工單資料夾裡的檔",
            )
        if name == "read_file":
            return {"content": scope.read_file(job_dir, _string(body, "path"))}
        if name == "list_files":
            return {"entries": scope.list_files(job_dir, _string(body, "path", "."))}
        if name == "write_file":
            written = scope.write_file(
                job_dir, key, _string(body, "path"), _string(body, "content")
            )
            return {"written": written}
        if name == "fetch_url":
            return await _fetch_url(key, _string(body, "url"))
        if name == "web_search":
            return await searcher.search(key, parse_query(body))
        if name == "run_script":
            return await _run_script(key, body)
        raise scope.ToolRejected(404, f"沒有這個工具：{name}")

    async def _fetch_url(key: JobKey, url: str) -> dict[str, Any]:
        if recorded is not None:
            return _replay_fetch(key, recorded, url)
        outcome = await run_in_threadpool(fetcher.fetch, url)
        request_hash, response_hash = outcome.store_blobs(blobs)
        record(
            key,
            "fetch",
            outcome.ledger_status,
            ts=outcome.ts,
            latency_ms=outcome.latency_ms,
            request_hash=request_hash,
            response_hash=response_hash,
        )
        return _fetched(outcome.record, cache_hit=outcome.cache_hit)

    def _replay_fetch(
        key: JobKey, recorded: RecordedFetches, url: str
    ) -> dict[str, Any]:
        """重播模式：只回放錄音裡的抓取，沒錄過就報錯，從不連網（NFR-COST-06）。"""
        started = time.monotonic()
        ts = config.clock()
        request_hash = blobs.put(request_bytes(url))
        replayed = recorded.get(key, request_hash)
        response_hash = (
            blobs.put(replayed.record.model_dump_json().encode("utf-8"))
            if replayed is not None
            else None
        )
        record(
            key,
            "fetch",
            replayed.status if replayed is not None else "error",
            ts=ts,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_hash=request_hash,
            response_hash=response_hash,
        )
        if replayed is None:
            raise scope.ToolRejected(
                500, f"重播模式：沒有錄過這個網址的抓取（{url.strip()}）"
            )
        return _fetched(replayed.record, cache_hit=replayed.status == "cache_hit")

    def _fetched(found: FetchRecord, *, cache_hit: bool) -> dict[str, Any]:
        return {
            "status": found.status,
            "reason": found.reason,
            "url": found.url,
            "final_url": found.final_url,
            "title": found.title,
            "author": found.author,
            "text": found.text,
            "fetched_at": found.fetched_at.isoformat(),
            "cache_hit": cache_hit,
        }

    async def _run_script(key: JobKey, body: dict[str, Any]) -> dict[str, Any]:
        name = _string(body, "script")
        args = _strings(body, "args")
        stdin = _string(body, "stdin", "")
        script = scripts.resolve_script(stages.skill_dir(key), name)
        started = time.monotonic()
        ts = config.clock()
        result = await run_in_threadpool(
            scripts.run_script,
            script,
            args,
            stdin,
            config.data.job_dir(key.job_id),
            script_timeout_s,
        )
        status: LedgerStatus = (
            "timeout"
            if result.timed_out
            else "ok"
            if result.exit_code == 0
            else "error"
        )
        request = {"tool": "run_script", "script": name, "args": args, "stdin": stdin}
        record(
            key,
            "script",
            status,
            ts=ts,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_hash=blobs.put(json.dumps(request, ensure_ascii=False).encode()),
            response_hash=blobs.put(result.to_json()),
        )
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        """開跑前檢查問「閘道活著嗎」（NFR-RUN-01）。"""
        return {"status": "ok", "mode": config.mode}

    @app.get("/health/anthropic")
    async def anthropic_health() -> JSONResponse:
        """開跑前檢查問「Anthropic 金鑰有效嗎」：閘道拿自己保管的金鑰打 `/v1/models`（不花錢）。

        金鑰只有閘道讀（NFR-SEC-04）；回應只說有效或哪裡不對，不帶金鑰內容。
        """
        try:
            key = read_anthropic_key(config.secrets_dir)
        except MissingCredential as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{config.anthropic_url}/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": f"連不上 Anthropic（{type(exc).__name__}）"}, status_code=503
            )
        if response.status_code != 200:
            return JSONResponse(
                {
                    "error": f"金鑰無效或不能用（Anthropic 回 HTTP {response.status_code}）"
                },
                status_code=503,
            )
        return JSONResponse({"status": "ok"})

    add_message_routes(app, config, stages, board)
    return app
