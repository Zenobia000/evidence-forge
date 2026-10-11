"""`POST /v1/messages`：Pi 的模型請求（SEAM-03、SEAM-04、v0.3 §4.1～§4.5）。

一個請求依序經過：

1. 鑰匙不是正在跑的那一把：拒絕，不轉送。
2. `model` 是等級別名；不認得就拒絕。請求原文依 hash 存進 `blobs/`。
   **請求 hash 一律照 Pi 送來的原始本文算**，不是改寫後的本文：快取與重播的鍵要跟 Pi 的請求
   逐位元組對應（Pi 對同樣的輸入送出一樣的請求，#20），改了 `models.yaml` 也不影響對得上錄音。
   改寫後真的送出去的本文另外存進 `blobs/`，帳本不記它的 hash。
3. 有存好的回應就回放，不轉送也不花錢：
   - 重播模式：只看錄音；沒錄過就報錯，從不轉送（NFR-COST-06）。
   - 一般模式：同一張單、同一段、同一次嘗試裡，第 n 次送出同一個請求，回放帳本裡第 n 份成功的回應
     （`cache_hit`，金額 0）。換了嘗試編號不會命中，重試才不會一直拿到同一份壞答案（NFR-COST-02）。
4. 雲端模型先預留：工單剩下的預算或本月剩下的額度不夠，就回 `budget_exceeded`，不轉送（NFR-COST-01）。
5. 本機模型一次只服務一個請求，其餘排隊；排隊時間記在 `queue_ms`（NFR-PAR-02）。
6. 用 `models.yaml` 的值改寫 `model`、`max_tokens`、`thinking` 後轉送；雲端 429 退避重試，
   等待時間也算進 `queue_ms`（NFR-PAR-03）。Ollama 連不上就報錯，不改送付費模型（NFR-RUN-05）。
7. 串流逐位元組原樣回傳。結束時（不管是完整收到、後端斷線、Pi 斷線）**先寫帳本**，
   再放掉預留與 GPU、最後才關後端連線：關連線可能被取消打斷，帳本不能跟著漏記。
   完整收到的回應存進 `blobs/`（跟狀態碼無關）；中途斷掉的不存。
   狀態碼 200 但不是一則完整的訊息（串流裡有 error 事件、沒送到結尾、讀不到用量）記 `error`、
   照預留的金額入帳：不知道後端實際算了多少，而 `error` 的回應不會被回放。
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from evidence_forge.budget import micros_to_usd, usd_to_micros
from evidence_forge.documents import read_job
from evidence_forge.gateway import ledger
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import Alias, rewrite_request
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import BudgetExceeded, BudgetGuard, Reservation
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.credentials import MissingCredential, read_anthropic_key
from evidence_forge.gateway.ledger import Usage
from evidence_forge.ledger_models import LedgerEntry, LedgerStatus
from evidence_forge.gateway.prices import Price, cost_micros, reservation_micros
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import JobKey
from evidence_forge.models import TIER_ORDER

_log = logging.getLogger(__name__)

_ERROR_TYPES = {
    400: "invalid_request_error",
    403: "permission_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
}

# 故障注入 bad_output 回的內容：格式是正常的串流，內容不是任何 skill 要的產出
BAD_OUTPUT_SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_fault",'
    b'"type":"message","role":"assistant","model":"fault","content":[],'
    b'"stop_reason":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"(fault injection: not a valid output)"}}\n\n'
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":'
    b'{"stop_reason":"end_turn"},"usage":{"output_tokens":0}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def anthropic_error(
    status: int, message: str, error_type: str | None = None
) -> JSONResponse:
    """回一個 Anthropic 格式的錯誤，Pi 看得懂、會結束這一段。"""
    return JSONResponse(
        {
            "type": "error",
            "error": {
                "type": error_type or _ERROR_TYPES.get(status, "api_error"),
                "message": message,
            },
        },
        status_code=status,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class _StreamCut(Exception):
    """故障注入 stream_cut：回應送到一半就斷掉。"""


class _Relay(StreamingResponse):
    """串流回應；不管怎麼結束（送完、出錯、Pi 斷線被取消），最後一定呼叫 `on_close`。

    把收尾放在這裡而不是 generator 的 finally：generator 還沒開始跑就被取消時，
    它的 finally 不會執行，帳本就漏了。
    """

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        on_close: Callable[[], Awaitable[None]],
        status_code: int,
        media_type: str,
    ) -> None:
        super().__init__(content, status_code=status_code, media_type=media_type)
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._on_close()


@dataclass
class _Call:
    """一個請求從進來到寫完帳本的狀態。"""

    key: JobKey
    tier: str
    alias: Alias
    request_hash: str
    started: float
    queue_s: float = 0.0
    ttft_ms: int | None = None
    collected: bytearray = field(default_factory=bytearray)
    complete: bool = False
    timed_out: bool = False
    status_code: int = 200
    upstream: httpx.Response | None = None
    reservation: Reservation | None = None
    price: Price | None = None
    semaphore: asyncio.Semaphore | None = None
    gpu_started: float | None = None
    finished: bool = False


def add_message_routes(
    app: FastAPI, config: GatewayConfig, stages: ActiveStages, board: JobBoard
) -> None:
    for name, alias in config.aliases.items():
        if alias.backend == "anthropic" and alias.model not in config.prices:
            raise ValueError(
                f"models.yaml 的 {name}（{alias.model}）在 prices.yaml 裡沒有價目"
            )
    blobs = BlobStore(config.data.blobs)
    recorded_blobs: BlobStore | None = None
    recorded: dict[ledger.CallKey, list[str]] = {}
    if config.mode == "replay":
        if config.replay_dir is None:
            raise ValueError("重播模式要指定錄音的位置（replay_dir）")
        recorded_blobs = BlobStore(config.replay_dir / "blobs")
        recorded = ledger.responses_by_call(
            ledger.read(config.replay_dir / "ledger.jsonl"), ("ok", "cache_hit")
        )
    guard = BudgetGuard(
        config.data,
        monthly_cap_micros=usd_to_micros(config.monthly_cap_usd),
        clock=config.clock,
    )
    # 同一張單、同一段、同一次、同一個請求，這個程序裡已經送過幾次（第 n 次回放第 n 份）
    seen: dict[tuple[str, str, int, str], int] = {}
    semaphores: dict[str, asyncio.Semaphore] = {}
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.backend_timeout_s, connect=5.0)
    )

    def record(
        call: _Call,
        status: LedgerStatus,
        *,
        response_hash: str | None = None,
        usage: Usage | None = None,
        cost: int = 0,
    ) -> None:
        usage = usage or Usage()
        gpu_s = (
            time.monotonic() - call.gpu_started if call.gpu_started is not None else 0.0
        )
        ledger.append(
            config.data.job_dir(call.key.job_id),
            LedgerEntry.model_validate(
                {
                    "ts": config.clock(),
                    "job_id": call.key.job_id,
                    "stage": call.key.stage,
                    "attempt": call.key.attempt,
                    "kind": "llm",
                    "tier": call.tier,
                    "model": call.alias.model,
                    "escalated_from": escalated_from(call),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "cost_usd": micros_to_usd(cost),
                    "gpu_seconds": round(gpu_s, 3),
                    "ttft_ms": call.ttft_ms,
                    "latency_ms": _elapsed_ms(call.started),
                    "queue_ms": int(call.queue_s * 1000),
                    "status": status,
                    "request_hash": call.request_hash,
                    "response_hash": response_hash,
                }
            ),
        )

    def escalated_from(call: _Call) -> str | None:
        """執行器登記的升級前等級（ADR-0011 §3.3）。只收比這次低的等級：Pi 送來的等級
        跟執行器想的不一樣時不記，免得帳本那一行驗不過、整筆帳漏記。"""
        source = stages.escalated_from(call.key)
        if source not in TIER_ORDER or call.tier not in TIER_ORDER:
            return None
        if TIER_ORDER.index(source) >= TIER_ORDER.index(call.tier):
            return None
        return source

    def release(call: _Call, unbooked: int = 0) -> None:
        """放掉預留、GPU、等待狀態。可以重複呼叫；收尾一律經過 `finish`。

        `unbooked` 是帳本沒寫進去的實際金額，交給預算照樣算進上限（`BudgetGuard.release`）。
        """
        if call.reservation is not None:
            guard.release(call.reservation, unbooked_micros=unbooked)
            call.reservation = None
        if call.semaphore is not None:
            call.semaphore.release()
            call.semaphore = None
        board.done(call.key.job_id)

    def finish(
        call: _Call,
        status: LedgerStatus,
        *,
        response: bytes | None = None,
        usage: Usage | None = None,
        cost: int = 0,
    ) -> None:
        """收尾：先記一行帳，再放掉預留與 GPU。只做一次；這裡沒有 await，取消打不斷。

        記帳丟例外（例如磁碟滿）也一定放資源：不然這筆預留一直佔著工單預算與本月額度，
        本機等級的 GPU 也不放，之後的本機請求全部排隊到逾時。可是錢已經花了：
        這次的實際金額（`cost`）留給預算照樣算進上限，寫不進帳本的期間上限才擋得住。
        `response` 有給就存進 `blobs/`。
        """
        if call.finished:
            return
        call.finished = True
        unbooked = cost
        try:
            record(call, status, response_hash=keep(response), usage=usage, cost=cost)
            unbooked = 0
        finally:
            release(call, unbooked)

    def keep(response: bytes | None) -> str | None:
        """回應原文存進 `blobs/`，回傳 hash；存不進去就記一筆警告、回傳 None。

        原文是事後查帳用的，帳本那一行才是錢：不能因為原文存不進去就連帳都不記。
        沒有 `response_hash` 的那一行不會被回放。
        """
        if response is None:
            return None
        try:
            return blobs.put(response)
        except OSError:
            _log.warning(
                "回應原文存不進 blobs/，帳本這一行不帶 response_hash", exc_info=True
            )
            return None

    def down(call: _Call, message: str) -> None:
        """後端故障告訴執行器：它據此直接標失敗，不重試、不升級（NFR-RUN-05）。"""
        board.report_outage(call.key, call.alias.backend, message)

    def fail(
        call: _Call, status: LedgerStatus, http_status: int, message: str
    ) -> JSONResponse:
        """沒拿到回應就結束：記一行、放掉資源、回 Anthropic 格式的錯誤。"""
        finish(call, status)
        return anthropic_error(http_status, message)

    async def body(
        call: _Call, source: AsyncIterator[bytes], success: LedgerStatus
    ) -> AsyncIterator[bytes]:
        try:
            async for chunk in source:
                if call.ttft_ms is None:
                    call.ttft_ms = _elapsed_ms(call.started)
                if config.fault == "stream_cut":
                    half = chunk[: max(1, len(chunk) // 2)]
                    call.collected.extend(half)
                    yield half
                    raise _StreamCut("故障注入：串流中途斷掉")
                call.collected.extend(chunk)
                yield chunk
            call.complete = True
        except httpx.TimeoutException:
            call.timed_out = True
            down(call, "模型後端回應到一半沒有下文")
            raise
        except httpx.HTTPError as exc:
            down(call, f"模型後端回應到一半斷線：{type(exc).__name__}")
            raise
        finally:
            # 送出最後一段之前就記好帳：Pi 看到回應結束時，帳本已經有這一行。
            # 這裡沒有 await，被取消也會跑完
            settle(call, success)

    def relay(
        call: _Call,
        source: AsyncIterator[bytes],
        *,
        success: LedgerStatus,
        media_type: str = "text/event-stream",
    ) -> _Relay:
        async def close() -> None:
            # generator 沒開始跑就被取消時，它的 finally 不會執行；這裡補記。
            # 補記丟例外（寫不進帳本）也要關後端連線
            try:
                settle(call, success)
            finally:
                if call.upstream is not None:
                    await call.upstream.aclose()

        return _Relay(
            body(call, source, success),
            on_close=close,
            status_code=call.status_code,
            media_type=media_type,
        )

    def settle(call: _Call, success: LedgerStatus) -> None:
        """結算：照實際用量記帳，再放掉預留（`finish`）。只做一次；這裡沒有 await，取消打不斷。"""
        if call.finished:
            return
        response = bytes(call.collected)
        reply = ledger.read_reply(response)
        # 狀態碼 200 不代表拿到一則完整的訊息：串流中途可能送 error 事件、沒送到結尾就結束，
        # 也可能根本讀不到用量。那種回應記 error，重播索引只收成功的，所以不會被回放
        succeeded = call.complete and call.status_code == 200 and reply.whole
        if succeeded:
            status: LedgerStatus = success
            # Pi 自己重試成功：先前的 5xx 或斷線是暫時的，執行器不該當成故障
            board.recovered(call.key)
        elif call.complete and call.status_code == 429:
            status = "rate_limited"
        elif call.timed_out:
            status = "timeout"
        else:
            status = "error"
        cost = 0
        if call.price is not None and call.reservation is not None:
            if succeeded:
                cost = cost_micros(call.price, reply.usage)
            elif not call.complete or call.status_code == 200:
                # 中途斷掉、200 但串流裡報錯、讀不到用量：不知道後端實際算了多少，
                # 照預留的金額算（寧可多算，不少算）。其餘狀態碼是後端拒收，不算錢
                cost = call.reservation.micros
        finish(
            call,
            status,
            # 完整收到就存，跟狀態碼無關：出錯的那次最需要回頭看原文。
            # 中途斷掉的不存，免得半截回應被當成完整的。
            response=response if call.complete else None,
            usage=reply.usage,
            cost=cost,
        )

    async def single(data: bytes) -> AsyncIterator[bytes]:
        yield data

    async def back_off(call: _Call, delay: float) -> None:
        board.wait(call.key.job_id, "rate_limit")
        waited = time.monotonic()
        await asyncio.sleep(delay)
        call.queue_s += time.monotonic() - waited

    def backoff_delay(retry_after: str | None, attempt: int) -> float:
        try:
            delay = float(retry_after) if retry_after is not None else -1.0
        except ValueError:
            delay = -1.0
        if delay < 0:
            delay = config.backoff_base_s * (2**attempt)
        return min(delay, config.backoff_max_s)

    def replayable(store: BlobStore, hashes: list[str], nth: int) -> bytes | None:
        """第 nth 份回應；不是一則完整的訊息就當作沒有。

        帳本記 ok 不保證回應完整：#54 合併後到修好之前，200 加 error 事件的回應也記成 ok。
        回放之前再讀一次，壞的那份照常轉送，不會一直回放同一份壞答案（NFR-COST-02）。
        """
        response = store.get(hashes[nth]) if nth < len(hashes) else None
        if response is None or not ledger.read_reply(response).whole:
            return None
        return response

    def stored_response(call: _Call) -> bytes | None:
        """有沒有存好的回應可以回放（見模組說明第 3 點）；這次是第幾次同樣的請求由 `seen` 算。"""
        key = call.key
        counter = (key.job_id, key.stage, key.attempt, call.request_hash)
        nth = seen.get(counter, 0)
        seen[counter] = nth + 1
        if recorded_blobs is not None:
            hashes = recorded.get((key.stage, key.attempt, call.request_hash), [])
            return replayable(recorded_blobs, hashes, nth)
        # 讀不懂的行（寫到一半被砍的半行）`ledger.read` 會跳過：那一次的回應不在清單裡，
        # 同一個請求後面的回應往前遞補。回放的仍然都是這個請求真的拿到過的回應
        entries = ledger.read(config.data.job_dir(key.job_id) / "ledger.jsonl")
        # 只認真的轉送拿到的成功回應；cache_hit 那幾行是回放，不能再算成一份
        hashes = ledger.responses_by_call(entries, ("ok",)).get(
            (key.stage, key.attempt, call.request_hash), []
        )
        return replayable(blobs, hashes, nth)

    def job_budget_micros(job_id: str) -> int:
        try:
            budget = read_job(config.data.job_dir(job_id) / "job.md").job.budget
        except (OSError, ValueError):
            # 讀不到工單的預算就當成 0：雲端呼叫一律不放行
            return 0
        return usd_to_micros(budget.usd)

    async def take_gpu(call: _Call, tier: str) -> None:
        limit = call.alias.concurrency
        if limit is None:
            return
        semaphore = semaphores.setdefault(tier, asyncio.Semaphore(limit))
        if semaphore.locked():
            board.wait(call.key.job_id, "gpu")
        waited = time.monotonic()
        await semaphore.acquire()
        call.queue_s += time.monotonic() - waited
        call.semaphore = semaphore

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        started = time.monotonic()
        try:
            key = require_active_key(request.headers.get("x-api-key"), stages)
        except ToolRejected as exc:
            return anthropic_error(exc.status, exc.message)
        raw = await request.body()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return anthropic_error(400, "請求內容要是 JSON")
        if not isinstance(parsed, dict):
            return anthropic_error(400, "請求內容要是 JSON 物件")
        tier = parsed.get("model")
        alias = config.aliases.get(tier) if isinstance(tier, str) else None
        if alias is None or not isinstance(tier, str):
            return anthropic_error(400, f"不認得的等級別名：{tier!r}")
        call = _Call(
            key=key,
            tier=tier,
            alias=alias,
            request_hash=blobs.put(raw),
            started=started,
        )

        if config.fault == "rate_limit":
            # 故障注入：第一次先當成收到 429，退避一次再照常（重播模式也一樣）
            await back_off(call, backoff_delay(None, 0))

        # 故障注入蓋過存好的回應：重播模式下也要看得到故障（UAT、重試與升級的測試）
        if config.fault == "backend_down":
            # 基礎設施故障：停下標失敗，不改用別的模型（NFR-RUN-05）
            down(call, "模型後端連不上（故障注入 backend_down）")
            return fail(call, "error", 502, "模型後端連不上（故障注入 backend_down）")
        if config.fault == "bad_output":
            return relay(call, single(BAD_OUTPUT_SSE), success="ok")
        stored = stored_response(call)
        if stored is not None:
            blobs.put(stored)
            return relay(call, single(stored), success="cache_hit")
        if recorded_blobs is not None:
            return fail(
                call,
                "error",
                500,
                f"重播模式：沒有錄過這個請求（{key.stage} 第 {key.attempt} 次）",
            )
        return await forward(call, parsed, request)

    async def forward(
        call: _Call, parsed: dict[str, Any], request: Request
    ) -> Response:
        alias = call.alias
        headers = {
            "content-type": "application/json",
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            # 要原樣轉送，不能讓後端壓縮
            "accept-encoding": "identity",
        }
        if alias.backend == "anthropic":
            try:
                headers["x-api-key"] = read_anthropic_key(config.secrets_dir)
            except MissingCredential as exc:
                down(call, str(exc))
                return fail(call, "error", 502, str(exc))
            beta = request.headers.get("anthropic-beta")
            if beta:
                headers["anthropic-beta"] = beta
            url = f"{config.anthropic_url}/v1/messages"
            call.price = config.prices[alias.model]
        else:
            url = f"{config.ollama_url}/v1/messages"
        content = json.dumps(rewrite_request(parsed, alias), ensure_ascii=False).encode(
            "utf-8"
        )
        # 改寫後真的送出去的本文也存一份（回頭查「後端到底收到什麼」）；
        # 帳本的 request_hash 仍是 Pi 送來的原文，見模組說明
        blobs.put(content)

        if call.price is not None:
            try:
                call.reservation = guard.reserve(
                    call.key.job_id,
                    job_budget_micros=job_budget_micros(call.key.job_id),
                    micros=reservation_micros(call.price, content, alias.max_tokens),
                )
            except BudgetExceeded as exc:
                board.stop_for_budget(call.key, exc.limit)
                finish(call, "budget_exceeded")
                return anthropic_error(400, exc.message, "budget_exceeded")

        # 預留之後到交給 relay 之前，任何沒接住的例外（讀 429 本文時斷線、關機時被取消）
        # 都要記一行帳、放掉預留與 GPU；不然本月額度被吃掉、本機等級從此卡死
        # 已經開了、還沒關也還沒交給 relay 的後端回應
        pending: httpx.Response | None = None
        try:
            await take_gpu(call, call.tier)
            for attempt in range(config.rate_limit_retries + 1):
                board.wait(call.key.job_id, "model")
                if alias.backend == "ollama" and call.gpu_started is None:
                    call.gpu_started = time.monotonic()
                try:
                    upstream = await client.send(
                        client.build_request(
                            "POST", url, content=content, headers=headers
                        ),
                        stream=True,
                    )
                    pending = upstream
                except httpx.TimeoutException as exc:
                    down(call, f"模型後端沒有回應：{type(exc).__name__}")
                    return fail(
                        call, "timeout", 502, f"模型後端沒有回應：{type(exc).__name__}"
                    )
                except httpx.HTTPError as exc:
                    # 只帶例外的種類：例外訊息可能含請求細節
                    down(call, f"模型後端連不上：{type(exc).__name__}")
                    return fail(
                        call, "error", 502, f"模型後端連不上：{type(exc).__name__}"
                    )
                if upstream.status_code != 429 or attempt == config.rate_limit_retries:
                    break
                retry_after = upstream.headers.get("retry-after")
                await upstream.aread()
                await upstream.aclose()
                pending = None
                await back_off(call, backoff_delay(retry_after, attempt))
            call.upstream = upstream
            call.status_code = upstream.status_code
            if upstream.status_code >= 500:
                # 後端自己出錯（模型載不起來、過載）：換一級也不會好，算故障
                down(call, f"模型後端回 HTTP {upstream.status_code}")
            return relay(
                call,
                upstream.aiter_raw(),
                success="ok",
                media_type=upstream.headers.get("content-type", "text/event-stream"),
            )
        except BaseException:
            try:
                # 這裡沒有 await，被取消也會跑完；已經收尾過的不會再記一次
                finish(call, "error")
            finally:
                if pending is not None and call.upstream is None:
                    await pending.aclose()
            raise
