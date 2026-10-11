"""`POST /v1/messages`：Pi 的模型請求（SEAM-03、v0.3 §4.1、§4.3）。

- 路徑後面的 query string（Pi 送 `?beta=true`，#10 第 1 題）照收不誤。
- 鑰匙不是正在跑的那一把：拒絕，不轉送。
- `model` 是等級別名，換成 `models.yaml` 對到的實際模型再轉送；其他欄位原樣。
- 串流逐位元組原樣回傳；回應完整收到才存進 `blobs/`，中途斷掉的不存。
- 重播模式：用（段, 次, 請求 hash）查錄音，有就回放，沒有就報錯，從不轉送。

`max_tokens` 與 thinking 的改寫、預算、GPU 排隊在 S0.4。
"""

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from evidence_forge.gateway import ledger
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import JobKey
from evidence_forge.models import LedgerEntry, LedgerStatus

_ERROR_TYPES = {
    400: "invalid_request_error",
    403: "permission_error",
    502: "api_error",
    500: "api_error",
}


def anthropic_error(status: int, message: str) -> JSONResponse:
    """回一個 Anthropic 格式的錯誤，Pi 看得懂、會結束這一段。"""
    return JSONResponse(
        {
            "type": "error",
            "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message},
        },
        status_code=status,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def add_message_routes(app: FastAPI, config: GatewayConfig, stages: ActiveStages) -> None:
    blobs = BlobStore(config.data.blobs)
    recorded_blobs: BlobStore | None = None
    recorded: dict[tuple[str, int, str], str] = {}
    if config.mode == "replay":
        if config.replay_dir is None:
            raise ValueError("重播模式要指定錄音的位置（replay_dir）")
        recorded_blobs = BlobStore(config.replay_dir / "blobs")
        recorded = ledger.index_by_call(ledger.read(config.replay_dir / "ledger.jsonl"))
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.backend_timeout_s, connect=5.0)
    )

    def record(
        key: JobKey,
        *,
        tier: str | None,
        model: str | None,
        status: LedgerStatus,
        started: float,
        request_hash: str,
        response_hash: str | None = None,
        tokens: tuple[int, int] = (0, 0),
        ttft_ms: int | None = None,
    ) -> None:
        ledger.append(
            config.data.job_dir(key.job_id),
            LedgerEntry.model_validate(
                {
                    "ts": datetime.now(UTC),
                    "job_id": key.job_id,
                    "stage": key.stage,
                    "attempt": key.attempt,
                    "kind": "llm",
                    "tier": tier,
                    "model": model,
                    "input_tokens": tokens[0],
                    "output_tokens": tokens[1],
                    "ttft_ms": ttft_ms,
                    "latency_ms": _elapsed_ms(started),
                    "status": status,
                    "request_hash": request_hash,
                    "response_hash": response_hash,
                }
            ),
        )

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        started = time.monotonic()
        try:
            key = require_active_key(request.headers.get("x-api-key"), stages)
        except ToolRejected as exc:
            return anthropic_error(exc.status, exc.message)
        raw = await request.body()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return anthropic_error(400, "請求內容要是 JSON")
        tier = body.get("model") if isinstance(body, dict) else None
        alias = config.aliases.get(tier) if isinstance(tier, str) else None
        if alias is None:
            return anthropic_error(400, f"不認得的等級別名：{tier!r}")
        request_hash = blobs.put(raw)

        if recorded_blobs is not None:
            response_hash = recorded.get((key.stage, key.attempt, request_hash))
            replayed = recorded_blobs.get(response_hash) if response_hash else None
            if replayed is None:
                record(
                    key, tier=tier, model=alias.model, status="error",
                    started=started, request_hash=request_hash,
                )
                return anthropic_error(
                    500, f"重播模式：沒有錄過這個請求（{key.stage} 第 {key.attempt} 次）"
                )
            blobs.put(replayed)
            record(
                key, tier=tier, model=alias.model, status="cache_hit",
                started=started, request_hash=request_hash,
                response_hash=response_hash, tokens=ledger.usage_from_sse(replayed),
            )
            return Response(replayed, media_type="text/event-stream")

        forwarded = json.dumps({**body, "model": alias.model}, ensure_ascii=False)
        upstream_request = client.build_request(
            "POST",
            f"{config.ollama_url}/v1/messages",
            content=forwarded.encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": request.headers.get(
                    "anthropic-version", "2023-06-01"
                ),
                # 要原樣轉送，不能讓後端壓縮
                "accept-encoding": "identity",
            },
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            record(
                key, tier=tier, model=alias.model, status="error",
                started=started, request_hash=request_hash,
            )
            # 基礎設施故障：停下標失敗，不改用別的模型（NFR-RUN-05）
            return anthropic_error(502, f"模型後端連不上：{type(exc).__name__}")

        async def relay() -> AsyncIterator[bytes]:
            collected = bytearray()
            ttft_ms: int | None = None
            complete = False
            try:
                async for chunk in upstream.aiter_raw():
                    if ttft_ms is None:
                        ttft_ms = _elapsed_ms(started)
                    collected.extend(chunk)
                    yield chunk
                complete = True
            finally:
                await upstream.aclose()
                ok = complete and upstream.status_code == 200
                response = bytes(collected)
                record(
                    key, tier=tier, model=alias.model,
                    status="ok" if ok else "error",
                    started=started, request_hash=request_hash,
                    response_hash=blobs.put(response) if ok else None,
                    tokens=ledger.usage_from_sse(response) if ok else (0, 0),
                    ttft_ms=ttft_ms,
                )

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )
