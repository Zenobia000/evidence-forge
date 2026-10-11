"""閘道的 HTTP 介面（架構草案 v0.3 §4）。

- `POST /tools/<名稱>`：ef-tools 的工具呼叫（`read_file`、`list_files`、`write_file`、
  `fetch_url`、`run_script`）。
- `POST /v1/messages`：Pi 的模型請求（Anthropic Messages 格式），見 `messages.py`。

兩種請求都在 `x-api-key` 帶工單鑰匙；閘道只接受登記中的那一把（`ActiveStages`）。
`fetch_url` 與 `run_script` 每呼叫一次，在這張工單的帳本記一筆（`kind: fetch`／`script`）。
"""

import json
import time
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from evidence_forge.gateway import ledger, scope, scripts
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.gateway.messages import add_message_routes
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
    script_timeout_s: float = scripts.DEFAULT_TIMEOUT_S,
) -> FastAPI:
    """`board` 要跟執行器共用（`ef serve`）；不傳就自己開一個，執行器讀不到。

    `fetcher` 不傳就照環境變數建一個（`EF_FETCH_TTL_SECONDS`）；測試傳假 DNS 的那一個。
    """
    app = FastAPI(title="evidence-forge gateway")
    fetcher = fetcher or Fetcher.from_env(config.data, clock=config.clock)
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

    add_message_routes(app, config, stages, board or JobBoard(config.clock))
    return app
