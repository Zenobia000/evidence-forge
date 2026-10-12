"""閘道的 HTTP 介面（架構草案 v0.3 §4）。

- `POST /tools/<名稱>`：ef-tools 的工具呼叫（`read_file`、`list_files`、`write_file`、
  `fetch_url`、`web_search`、`run_script`）。
- `POST /v1/messages`：Pi 的模型請求（Anthropic Messages 格式），見 `messages.py`。
- `GET /health`、`GET /health/anthropic`：執行器的開跑前檢查用（閘道活著、金鑰有效）。

兩種請求都在 `x-api-key` 帶工單鑰匙；閘道只接受登記中的那一把（`ActiveStages`）。
`fetch_url`、`web_search` 與 `run_script` 每呼叫一次，在這張工單的帳本記一筆
（`kind: fetch`／`search`／`script`）。搜尋的規則在 `search.py`。
"""

import json
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from evidence_forge.gateway import ledger, scope, scripts
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.credentials import MissingCredential, read_anthropic_key
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.gateway.messages import add_message_routes
from evidence_forge.gateway.search import Searcher, parse_query
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry, LedgerKind, LedgerStatus

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
) -> FastAPI:
    """`board` 要跟執行器共用（`ef serve`）；不傳就自己開一個，執行器讀不到。

    `fetcher` 不傳就照環境變數建一個（`EF_FETCH_TTL_SECONDS`）；測試傳假 DNS 的那一個。
    `searcher` 不傳就照設定建一個（`EF_SEARCH_TTL_SECONDS`）；測試傳逾時短的那一個。
    """
    app = FastAPI(title="evidence-forge gateway")
    fetcher = fetcher or Fetcher.from_env(config.data, clock=config.clock)
    searcher = searcher or Searcher(config)
    blobs = fetcher.blobs

    def record(
        key: JobKey,
        kind: LedgerKind,
        status: LedgerStatus,
        *,
        ts: datetime,
        latency_ms: int,
        request_hash: str,
        response_hash: str,
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
        job_dir = config.data.job_dir(key.job_id)
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
        found = outcome.record
        return {
            "status": found.status,
            "reason": found.reason,
            "url": found.url,
            "final_url": found.final_url,
            "title": found.title,
            "author": found.author,
            "text": found.text,
            "fetched_at": found.fetched_at.isoformat(),
            "cache_hit": outcome.cache_hit,
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

    add_message_routes(app, config, stages, board or JobBoard(config.clock))
    return app
