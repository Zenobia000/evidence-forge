"""SEAM-03、SEAM-04：閘道 ↔ 兩個後端（Ollama、Anthropic）、預算 ↔ 帳本 ↔ 快取與重播。

兩個本機 stub 扮演 Ollama 與 Anthropic 的 `/v1/messages`。金鑰是假的（`sk-fake-xxxx-…`），
放在測試自己的 `EF_SECRETS` 資料夾；任何請求都只到 127.0.0.1（NFR-COST-06）。
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, JobBoard, create_app
from evidence_forge.gateway.config import Fault
from evidence_forge.gateway.ledger import append_intake
from evidence_forge.ledger_models import IntakeEntry, LedgerEntry
from evidence_forge.harness import default_harness
from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=TAIPEI)
# 假金鑰：帶 xxxx，guard-secrets 認得是假的；整串出現在任何輸出裡就是外洩
FAKE_KEY = "sk-fake-xxxx-anthropic-canary-7f3a"


def _sse(text: str, input_tokens: int = 1000, output_tokens: int = 200) -> bytes:
    """照 Anthropic Messages 串流格式寫的一段回應，用量放在 message_start 與 message_delta。"""
    start = {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [],
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }
    delta = {"type": "content_block_delta", "index": 0, "delta": {"text": text}}
    end = {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": output_tokens},
    }
    return (
        f"event: message_start\ndata: {json.dumps(start)}\n\n"
        f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n"
        f"event: message_delta\ndata: {json.dumps(end)}\n\n"
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    ).encode()


SSE = _sse("好")

# Pi 送來的樣子：自己決定的 max_tokens 與 thinking（strong 會自己開 8192 的思考，#10）
PI_BODY: dict[str, Any] = {
    "model": "strong",
    "max_tokens": 16384,
    "stream": True,
    "thinking": {"type": "enabled", "budget_tokens": 8192},
    "system": [{"type": "text", "text": "test"}],
    "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
}


def _body(tier: str = "strong", text: str = "go") -> bytes:
    body = {**PI_BODY, "model": tier}
    body["messages"] = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    return json.dumps(body).encode()


Reply = Callable[[], Response]


@dataclass
class Backend:
    """一個假的模型後端。`replies` 依序用完之後回 `SSE`；`gate` 沒開之前回應先卡住。"""

    url: str = ""
    received: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)
    gate: threading.Event | None = None
    # 卡在 gate 的時候是先送一半再卡（串流中途），還是整個回應都還沒開始
    stall_midstream: bool = False
    active: int = 0
    max_active: int = 0

    async def _wait_gate(self) -> None:
        deadline = time.monotonic() + 5
        while self.gate is not None and not self.gate.is_set():
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.01)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/messages")
        async def messages(request: Request) -> Response:
            self.received.append(json.loads(await request.body()))
            self.headers.append(dict(request.headers))
            if self.replies:
                return self.replies.pop(0)()
            if self.stall_midstream:

                async def halves() -> AsyncIterator[bytes]:
                    yield SSE[:40]
                    await self._wait_gate()
                    yield SSE[40:]

                return StreamingResponse(halves(), media_type="text/event-stream")
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await self._wait_gate()
            finally:
                self.active -= 1
            return Response(SSE, media_type="text/event-stream")

        return app


Serve = Callable[[Any], AbstractContextManager[str]]


@pytest.fixture
def ollama(serve: Serve) -> Iterator[Backend]:
    backend = Backend()
    with serve(backend.app()) as url:
        backend.url = url
        yield backend
        if backend.gate is not None:
            backend.gate.set()


@pytest.fixture
def anthropic(serve: Serve) -> Iterator[Backend]:
    backend = Backend()
    with serve(backend.app()) as url:
        backend.url = url
        yield backend
        if backend.gate is not None:
            backend.gate.set()


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    return data


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    folder = tmp_path / "secrets"
    folder.mkdir()
    (folder / "anthropic").write_text(FAKE_KEY + "\n", encoding="utf-8")
    return folder


@dataclass
class Gateway:
    url: str
    board: JobBoard


@pytest.fixture
def gateway(
    serve: Serve,
    data: DataDir,
    ollama: Backend,
    anthropic: Backend,
    secrets: Path,
) -> Callable[..., AbstractContextManager[Gateway]]:
    """`with gateway(stages, **設定) as gw:` 開一個閘道；同一個資料目錄可以開好幾次（模擬重開）。"""
    from contextlib import contextmanager

    @contextmanager
    def start(stages: ActiveStages, **changes: Any) -> Iterator[Gateway]:
        settings: dict[str, Any] = {
            "data": data,
            "mode": "live",
            "ollama_url": ollama.url,
            "anthropic_url": anthropic.url,
            "secrets_dir": secrets,
            "backoff_base_s": 0.01,
            "backoff_max_s": 0.05,
            **changes,
        }
        config = GatewayConfig(**settings)
        board = JobBoard(config.clock)
        with serve(create_app(config, stages, board)) as url:
            yield Gateway(url, board)

    return start


def _job(data: DataDir, budget_usd: float = 1.0, goal: str = "測試") -> str:
    harness = default_harness()
    recipe = load_recipes(harness, tiers=["local", "strong", "max"])["echo"]
    return open_job(
        data,
        recipe,
        PastedText(f"素材 {goal}"),
        goal=goal,
        now=NOW,
        budget_usd=budget_usd,
    ).job.id


def _active(*keys: JobKey) -> ActiveStages:
    stages = ActiveStages()
    for key in keys:
        stages.activate(key)
    return stages


def _post(url: str, key: JobKey, raw: bytes) -> httpx.Response:
    return httpx.post(
        f"{url}/v1/messages?beta=true",
        content=raw,
        headers={"x-api-key": str(key), "content-type": "application/json"},
        timeout=10,
    )


def _ledger(data: DataDir, job_id: str) -> list[LedgerEntry]:
    path = data.job_dir(job_id) / "ledger.jsonl"
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line) for line in path.read_text().splitlines()
    ]


def _until(condition: Callable[[], bool], what: str) -> None:
    """等一個條件成立（等的是狀態，不是拿時間當斷言），最多 5 秒。"""
    deadline = time.monotonic() + 5
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"等不到：{what}")
        time.sleep(0.01)


# --- 別名對照與 Anthropic 後端 ------------------------------------------------


@pytest.mark.promise("FR-MD-01", "NFR-SEC-04")
def test_strong_改寫_max_tokens_與_thinking_帶金鑰轉送到_Anthropic(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    anthropic: Backend,
) -> None:
    key = JobKey(_job(data), "echo", 1)
    with gateway(_active(key)) as gw:
        response = _post(gw.url, key, _body("strong"))
    assert response.status_code == 200
    assert response.content == SSE
    assert ollama.received == []
    [forwarded] = anthropic.received
    assert forwarded["model"] == "claude-sonnet-5-5"
    # 不採用 Pi 送來的 16384 與 budget_tokens：Sonnet 5.5 不收 budget_tokens
    assert forwarded["max_tokens"] == 16000
    assert forwarded["thinking"] == {"type": "adaptive"}
    assert forwarded["messages"] == PI_BODY["messages"]
    [headers] = anthropic.headers
    assert headers["x-api-key"] == FAKE_KEY
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.promise("FR-MD-01")
def test_max_對到_Opus_local_對到_Qwen_而且明確關掉思考(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    anthropic: Backend,
) -> None:
    key = JobKey(_job(data), "echo", 1)
    with gateway(_active(key)) as gw:
        _post(gw.url, key, _body("max"))
        _post(gw.url, key, _body("local"))
    [opus] = anthropic.received
    assert (opus["model"], opus["thinking"]) == (
        "claude-opus-5-5",
        {"type": "adaptive"},
    )
    [qwen] = ollama.received
    assert qwen["model"] == "qwen3.5:9b"
    assert qwen["max_tokens"] == 16384
    assert qwen["thinking"] == {"type": "disabled"}
    # 本機的呼叫沒有金鑰
    assert "x-api-key" not in ollama.headers[0]


@pytest.mark.promise("NFR-OBS-01", "NFR-COST-01")
def test_雲端呼叫依實際用量入帳_欄位齊全(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        _post(gw.url, key, _body("strong"))
    [entry] = _ledger(data, job_id)
    assert (entry.kind, entry.tier, entry.model) == (
        "llm",
        "strong",
        "claude-sonnet-5-5",
    )
    assert (entry.input_tokens, entry.output_tokens) == (1000, 200)
    # 1000×$2 + 200×$10（每百萬 token）＝ 4000 微美元
    assert entry.cost_usd == 0.004
    assert entry.status == "ok"
    assert entry.ttft_ms is not None and entry.latency_ms >= entry.ttft_ms
    assert entry.response_hash == hashlib.sha256(SSE).hexdigest()


# --- 預算 -------------------------------------------------------------------


@pytest.mark.promise("NFR-COST-01")
def test_工單預算不夠回_budget_exceeded_不轉送(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    # 一次 strong 呼叫要預留約 US$0.16（16000 個輸出 token × $10／百萬）
    job_id = _job(data, budget_usd=0.1)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        response = _post(gw.url, key, _body("strong"))
        stop = gw.board.budget_stop(key)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert anthropic.received == []
    assert stop == "job"
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.cost_usd, entry.response_hash) == (
        "budget_exceeded",
        0.0,
        None,
    )


@pytest.mark.promise("NFR-COST-01", "FR-LG-01")
def test_投入帳本的花費讓本月額度不夠_回_budget_exceeded_原因是本月額度(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    # 投入時的抓取（S0.5 用同一個函式寫）有金額，月上限要把它算進去
    append_intake(
        data,
        IntakeEntry(
            ts=NOW,
            kind="fetch",
            url="https://example.com/a",
            cost_usd=9.9,
            latency_ms=10,
            status="ok",
        ),
    )
    job_id = _job(data, budget_usd=5.0)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), clock=lambda: NOW) as gw:
        response = _post(gw.url, key, _body("strong"))
        stop = gw.board.budget_stop(key)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert "本月" in response.json()["error"]["message"]
    assert stop == "month"
    assert anthropic.received == []


@pytest.mark.promise("NFR-COST-01")
def test_回應後依實際用量結算_多預留的退回(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    # 預算只夠預留一次（約 0.16）；第一次只花 0.004，退回之後第二次照樣放行
    job_id = _job(data, budget_usd=0.2)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        first = _post(gw.url, key, _body("strong", "one"))
        second = _post(gw.url, key, _body("strong", "two"))
    assert (first.status_code, second.status_code) == (200, 200)
    assert len(anthropic.received) == 2
    assert [entry.cost_usd for entry in _ledger(data, job_id)] == [0.004, 0.004]


@pytest.mark.promise("NFR-COST-01")
def test_本機模型不預留金額_預算是零也照常(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
) -> None:
    job_id = _job(data, budget_usd=0.0)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        response = _post(gw.url, key, _body("local"))
    assert response.status_code == 200
    [entry] = _ledger(data, job_id)
    assert entry.cost_usd == 0.0
    assert entry.gpu_seconds >= 0.0


# --- 快取：同一段同一次回放，換了嘗試就不命中 ---------------------------------


@pytest.mark.promise("NFR-COST-02")
def test_重開之後同一段同一次的相同請求回放_記_cache_hit_金額_0(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        first = _post(gw.url, key, _body("strong"))
    # 模擬 ef serve 重開：新的閘道、同一個資料目錄，Pi 送出一模一樣的請求
    with gateway(_active(key)) as gw:
        again = _post(gw.url, key, _body("strong"))
    assert again.content == first.content == SSE
    assert len(anthropic.received) == 1
    entries = _ledger(data, job_id)
    assert [entry.status for entry in entries] == ["ok", "cache_hit"]
    assert entries[1].cost_usd == 0.0
    assert entries[1].response_hash == entries[0].response_hash


@pytest.mark.promise("NFR-COST-02")
def test_同一段第_2_次嘗試送出一樣的請求_要真的轉送_不命中快取(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    job_id = _job(data)
    first_try = JobKey(job_id, "echo", 1)
    with gateway(_active(first_try)) as gw:
        _post(gw.url, first_try, _body("strong"))
    second_try = JobKey(job_id, "echo", 2)
    with gateway(_active(second_try)) as gw:
        _post(gw.url, second_try, _body("strong"))
    assert len(anthropic.received) == 2
    assert [entry.status for entry in _ledger(data, job_id)] == ["ok", "ok"]


@pytest.mark.promise("NFR-COST-02")
def test_別張工單送出一樣的請求不命中_快取只看這張工單自己的帳本(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    first = JobKey(_job(data, goal="一"), "echo", 1)
    other = JobKey(_job(data, goal="二"), "echo", 1)
    with gateway(_active(first)) as gw:
        _post(gw.url, first, _body("strong"))
    # blobs/ 裡已經有這個請求與回應，但那是別張單的
    with gateway(_active(other)) as gw:
        _post(gw.url, other, _body("strong"))
    assert len(anthropic.received) == 2
    assert [e.status for e in _ledger(data, other.job_id)] == ["ok"]


@pytest.mark.promise("NFR-KEEP-01")
def test_請求_hash_照_Pi_送來的原文算_改寫後的本文另外存(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    raw = _body("strong")
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        _post(gw.url, key, raw)
    [entry] = _ledger(data, job_id)
    assert entry.request_hash == hashlib.sha256(raw).hexdigest()
    forwarded = json.dumps(anthropic.received[0], ensure_ascii=False).encode()
    assert (data.blobs / hashlib.sha256(forwarded).hexdigest()).is_file()


@pytest.mark.promise("NFR-COST-02")
def test_同一個程序裡第二次送出同樣的請求_帳本裡只有一份就轉送(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    # 第 n 次相同的請求回放第 n 份；第 2 次時帳本只有 1 份，所以照常轉送
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        _post(gw.url, key, _body("strong"))
        _post(gw.url, key, _body("strong"))
    assert len(anthropic.received) == 2


# --- 中斷的串流 ---------------------------------------------------------------


def _read_cut(url: str, key: JobKey, raw: bytes) -> bytes:
    """讀一個會被斷掉的串流；斷掉時 httpx 會丟例外，回傳讀到的部分。"""
    received = bytearray()
    try:
        with httpx.stream(
            "POST",
            f"{url}/v1/messages",
            content=raw,
            headers={"x-api-key": str(key)},
            timeout=10,
        ) as response:
            for chunk in response.iter_raw():
                received.extend(chunk)
    except httpx.HTTPError:
        pass
    return bytes(received)


@pytest.mark.promise("NFR-COST-02", "NFR-KEEP-01")
def test_串流中途斷掉的回應不存進_blobs_下一次同樣的請求重新轉送(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), fault="stream_cut") as gw:
        partial = _read_cut(gw.url, key, _body("strong"))
    assert partial != SSE
    [cut] = _ledger(data, job_id)
    assert cut.status == "error"
    assert cut.response_hash is None
    assert not (data.blobs / hashlib.sha256(SSE).hexdigest()).exists()
    # 斷掉的那次照預留的金額入帳（拿不到完整用量，寧可多算）
    assert cut.cost_usd > 0

    with gateway(_active(key)) as gw:
        again = _post(gw.url, key, _body("strong"))
    assert again.content == SSE
    assert len(anthropic.received) == 2
    assert [entry.status for entry in _ledger(data, job_id)] == ["error", "ok"]


@pytest.mark.promise("FR-LG-01", "NFR-COST-01")
def test_Pi_在串流中途斷線_帳本還是有一行_狀態是_error(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    # 帳本漏一行等於那次呼叫沒花錢，預算就算錯了（#44 審查）
    anthropic.gate = threading.Event()
    anthropic.stall_midstream = True
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        with httpx.stream(
            "POST",
            f"{gw.url}/v1/messages",
            content=_body("strong"),
            headers={"x-api-key": str(key)},
            timeout=10,
        ) as response:
            next(response.iter_raw())
        # 讀到第一段就關掉連線：Pi 斷線
        _until(lambda: len(_ledger(data, job_id)) == 1, "斷線之後帳本寫一行")
        anthropic.gate.set()
    [entry] = _ledger(data, job_id)
    assert entry.status == "error"
    assert entry.response_hash is None


@pytest.mark.promise("FR-LG-01")
def test_關後端連線時出錯_帳本照樣有這一行(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 帳本要在關連線之前寫：關連線丟例外（或被取消打斷）時，那一行不能跟著不見
    async def broken_aclose(self: httpx.Response) -> None:
        raise RuntimeError("aclose 壞了")

    monkeypatch.setattr(httpx.Response, "aclose", broken_aclose)
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        _read_cut(gw.url, key, _body("strong"))
        _until(lambda: len(_ledger(data, job_id)) == 1, "帳本寫一行")
    # httpx 讀完串流時自己也會關連線，所以這次算沒有完整收到：記 error、照預留的金額入帳
    [entry] = _ledger(data, job_id)
    assert entry.status == "error"
    assert entry.cost_usd > 0


# --- 重播 -------------------------------------------------------------------


def _recording(tmp_path: Path, raw: bytes, responses: list[bytes]) -> Path:
    """一份錄音：同一個請求依序拿到 `responses`，格式跟 live 模式寫出來的一樣。"""
    replay = tmp_path / "recording"
    (replay / "blobs").mkdir(parents=True)
    request_hash = hashlib.sha256(raw).hexdigest()
    (replay / "blobs" / request_hash).write_bytes(raw)
    lines = []
    for response in responses:
        response_hash = hashlib.sha256(response).hexdigest()
        (replay / "blobs" / response_hash).write_bytes(response)
        lines.append(
            json.dumps(
                {
                    "ts": "2026-10-11T12:00:00+08:00",
                    "job_id": "some-recorded-job",
                    "stage": "echo",
                    "attempt": 1,
                    "kind": "llm",
                    "tier": "local",
                    "model": "qwen3.5:9b",
                    "latency_ms": 10,
                    "status": "ok",
                    "request_hash": request_hash,
                    "response_hash": response_hash,
                }
            )
        )
    (replay / "ledger.jsonl").write_text("\n".join(lines) + "\n")
    return replay


@pytest.mark.promise("NFR-COST-06")
def test_重播時第_n_次相同的請求回放第_n_份(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    tmp_path: Path,
) -> None:
    raw = _body("local")
    first, second = _sse("第一份"), _sse("第二份")
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    replay_dir = _recording(tmp_path, raw, [first, second])
    with gateway(_active(key), mode="replay", replay_dir=replay_dir) as gw:
        responses = [_post(gw.url, key, raw) for _ in range(3)]
    assert [r.content for r in responses[:2]] == [first, second]
    # 錄音裡只有兩份，第三次沒錄過：報錯、不轉送
    assert responses[2].status_code >= 500
    assert ollama.received == []


@pytest.mark.promise("NFR-PAR-03", "NFR-COST-06")
def test_重播模式下故障注入也有效_rate_limit_退避後照常回放_等待入帳(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    tmp_path: Path,
) -> None:
    raw = _body("local")
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    replay_dir = _recording(tmp_path, raw, [SSE])
    with gateway(
        _active(key), mode="replay", replay_dir=replay_dir, fault="rate_limit"
    ) as gw:
        response = _post(gw.url, key, raw)
    assert response.content == SSE
    [entry] = _ledger(data, job_id)
    assert entry.status == "cache_hit"
    assert entry.queue_ms >= 10
    assert ollama.received == []


@pytest.mark.promise("NFR-COST-06", "NFR-RUN-05")
@pytest.mark.parametrize("fault", ["backend_down", "bad_output", "stream_cut"])
def test_重播模式下故障注入蓋過錄音(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    tmp_path: Path,
    fault: Fault,
) -> None:
    # 錄音裡有正常的回應，但設了故障就要看到故障：UAT 與重試、升級的測試靠這個
    raw = _body("local")
    key = JobKey(_job(data), "echo", 1)
    replay_dir = _recording(tmp_path, raw, [SSE])
    with gateway(_active(key), mode="replay", replay_dir=replay_dir, fault=fault) as gw:
        if fault == "stream_cut":
            assert _read_cut(gw.url, key, raw) != SSE
        else:
            response = _post(gw.url, key, raw)
            assert response.content != SSE
            if fault == "backend_down":
                assert response.status_code == 502
            else:
                assert b"fault injection" in response.content
    assert ollama.received == []


# --- 429 退避 ----------------------------------------------------------------


def _too_many() -> Response:
    return Response(
        b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}',
        status_code=429,
        media_type="application/json",
    )


@pytest.mark.promise("NFR-PAR-03")
def test_雲端_429_退避重試_等待時間記在_queue_ms(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    anthropic.replies = [_too_many, _too_many]
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        response = _post(gw.url, key, _body("strong"))
    assert response.status_code == 200
    assert len(anthropic.received) == 3
    [entry] = _ledger(data, job_id)
    assert entry.status == "ok"
    # 退避 0.01 + 0.02 秒
    assert entry.queue_ms >= 30


@pytest.mark.promise("NFR-PAR-03")
def test_一直_429_重試用完就把_429_交給_Pi_帳本記_rate_limited(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    anthropic.replies = [_too_many] * 3
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), rate_limit_retries=2) as gw:
        response = _post(gw.url, key, _body("strong"))
    assert response.status_code == 429
    assert len(anthropic.received) == 3
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.cost_usd) == ("rate_limited", 0.0)


def _broken_429() -> Response:
    # 宣告的長度比實際送的多：閘道讀 429 本文時連線斷掉（RemoteProtocolError）
    return Response(
        b'{"type":"error"}',
        status_code=429,
        media_type="application/json",
        headers={"content-length": "1000"},
    )


@pytest.mark.promise("NFR-PAR-02", "FR-LG-01")
def test_讀_429_本文時連線斷掉_帳本照樣記一行_GPU_放掉_下一個請求不卡(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
) -> None:
    ollama.replies = [_broken_429]
    first = JobKey(_job(data, goal="一"), "echo", 1)
    second = JobKey(_job(data, goal="二"), "echo", 1)
    with gateway(_active(first, second)) as gw:
        assert _post(gw.url, first, _body("local")).status_code >= 500
        assert _post(gw.url, second, _body("local")).status_code == 200
        assert gw.board.waiting(first.job_id) is None
    assert [e.status for e in _ledger(data, first.job_id)] == ["error"]


# --- 本機 GPU 一次一個 ---------------------------------------------------------


@pytest.mark.promise("NFR-PAR-02")
def test_本機模型一次只服務一個請求_其餘排隊_等待狀態是_gpu_排隊時間入帳(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
) -> None:
    ollama.gate = threading.Event()
    first = JobKey(_job(data, goal="一"), "echo", 1)
    second = JobKey(_job(data, goal="二"), "echo", 1)
    with gateway(_active(first, second)) as gw:
        threads = [
            threading.Thread(target=_post, args=(gw.url, key, _body("local")))
            for key in (first, second)
        ]
        threads[0].start()
        _until(lambda: len(ollama.received) == 1, "第一個請求到後端")
        assert gw.board.waiting(first.job_id) is not None
        threads[1].start()
        _until(
            lambda: (
                (w := gw.board.waiting(second.job_id)) is not None and w.kind == "gpu"
            ),
            "第二張單在等 GPU",
        )
        assert len(ollama.received) == 1
        ollama.gate.set()
        for thread in threads:
            thread.join(timeout=10)
        waiting_after = gw.board.waiting(second.job_id)
    assert ollama.max_active == 1
    assert len(ollama.received) == 2
    [queued] = _ledger(data, second.job_id)
    assert queued.queue_ms > 0
    assert waiting_after is None


# --- 後端故障：不改送付費模型 -----------------------------------------------------


@pytest.mark.promise("NFR-RUN-05", "FR-MD-01")
def test_Ollama_連不上回錯誤_不改送_Anthropic(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), ollama_url="http://127.0.0.1:9") as gw:
        response = _post(gw.url, key, _body("local"))
    assert response.status_code == 502
    assert response.json()["type"] == "error"
    assert anthropic.received == []
    [entry] = _ledger(data, job_id)
    assert entry.status == "error"


@pytest.mark.promise("NFR-RUN-05")
def test_Ollama_沒回應_逾時回錯誤_帳本記_timeout(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    anthropic: Backend,
) -> None:
    ollama.gate = threading.Event()
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), backend_timeout_s=0.2) as gw:
        response = _post(gw.url, key, _body("local"))
        ollama.gate.set()
    assert response.status_code == 502
    assert anthropic.received == []
    [entry] = _ledger(data, job_id)
    assert entry.status == "timeout"


@pytest.mark.promise("NFR-RUN-05")
@pytest.mark.parametrize("tier", ["local", "strong"])
def test_故障注入_backend_down_兩種後端都不送(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
    anthropic: Backend,
    tier: str,
) -> None:
    key = JobKey(_job(data), "echo", 1)
    with gateway(_active(key), fault="backend_down") as gw:
        response = _post(gw.url, key, _body(tier))
    assert response.status_code == 502
    assert (ollama.received, anthropic.received) == ([], [])


def test_故障注入_bad_output_回一份不合格的產出_不打後端(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    ollama: Backend,
) -> None:
    key = JobKey(_job(data), "echo", 1)
    with gateway(_active(key), fault="bad_output") as gw:
        response = _post(gw.url, key, _body("local"))
    assert response.status_code == 200
    assert b"fault injection" in response.content
    assert ollama.received == []


def test_故障注入的值只認四種(data: DataDir) -> None:
    from evidence_forge.gateway.config import config_from_env

    faults: list[Fault] = ["rate_limit", "bad_output", "backend_down", "stream_cut"]
    for fault in faults:
        env = {"EF_GATEWAY_MODE": "live", "EF_GATEWAY_FAULT": fault}
        assert config_from_env(data, env).fault == fault
    with pytest.raises(ValueError, match="EF_GATEWAY_FAULT"):
        config_from_env(data, {"EF_GATEWAY_FAULT": "meteor"})


def test_月上限與金鑰位置從環境變數讀(data: DataDir, tmp_path: Path) -> None:
    from evidence_forge.gateway.config import config_from_env

    config = config_from_env(
        data, {"EF_MONTHLY_BUDGET_USD": "3.5", "EF_SECRETS": str(tmp_path / "s")}
    )
    assert (config.monthly_cap_usd, config.secrets_dir) == (3.5, tmp_path / "s")
    default = config_from_env(data, {})
    assert default.monthly_cap_usd == 10.0
    assert default.secrets_dir == Path("~/.config/evidence-forge/secrets").expanduser()


# --- 金鑰不外洩 ---------------------------------------------------------------


def _everything_written(data: DataDir) -> str:
    return "\n".join(
        path.read_bytes().decode("utf-8", errors="replace")
        for path in data.root.rglob("*")
        if path.is_file()
    )


@pytest.mark.promise("NFR-SEC-04")
def test_金鑰不進回應_帳本_blobs_與日誌(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    anthropic.replies = [
        lambda: Response(
            b'{"type":"error","error":{"type":"authentication_error",'
            b'"message":"invalid x-api-key"}}',
            status_code=401,
            media_type="application/json",
        )
    ]
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key)) as gw:
        rejected = _post(gw.url, key, _body("strong", "one"))
    with gateway(_active(key), anthropic_url="http://127.0.0.1:9") as gw:
        unreachable = _post(gw.url, key, _body("strong", "two"))
    assert anthropic.headers[0]["x-api-key"] == FAKE_KEY
    assert (rejected.status_code, unreachable.status_code) == (401, 502)
    captured = capsys.readouterr()
    for text in (
        rejected.text,
        unreachable.text,
        _everything_written(data),
        caplog.text,
        captured.out,
        captured.err,
    ):
        assert FAKE_KEY not in text
    assert [entry.status for entry in _ledger(data, job_id)] == ["error", "error"]


@pytest.mark.promise("NFR-SEC-04")
def test_沒有金鑰檔就報錯_指出位置_不轉送(
    gateway: Callable[..., AbstractContextManager[Gateway]],
    data: DataDir,
    anthropic: Backend,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "no-secrets"
    empty.mkdir()
    job_id = _job(data)
    key = JobKey(job_id, "echo", 1)
    with gateway(_active(key), secrets_dir=empty) as gw:
        response = _post(gw.url, key, _body("strong"))
    assert response.status_code == 502
    assert str(empty / "anthropic") in response.json()["error"]["message"]
    assert anthropic.received == []
