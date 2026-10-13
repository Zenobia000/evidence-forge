"""SEAM-03：閘道 ↔ 模型後端（S0.3 只有 Ollama）。

一個本機 stub 扮演 Ollama 的 `/v1/messages`：記下收到的請求、回一段固定的 SSE。
斷言閘道接受 `?beta=true`、從鑰匙讀出工單／段／次、把別名換成實際模型、
串流逐位元組原樣回傳，並把請求與回應依 hash 存進 `blobs/`。
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response

from evidence_forge.gateway.app import (
    ActiveStages,
    GatewayConfig,
    JobBoard,
    create_app,
)
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir

JOB = "20261011-120000-aaaa1111"
KEY = JobKey(JOB, "echo", 1)

# 照 Anthropic Messages 串流格式寫的一段固定回應（Ollama 也回這個格式）
SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1",'
    b'"type":"message","role":"assistant","model":"qwen3.5:9b","content":[],'
    b'"stop_reason":null,"usage":{"input_tokens":42,"output_tokens":0}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"\xe5\xa5\xbd"}}\n\n'
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":'
    b'{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)

PI_BODY = {
    "model": "local",
    "max_tokens": 16384,
    "stream": True,
    "system": [{"type": "text", "text": "test"}],
    "thinking": {"type": "disabled"},
    "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
}


@dataclass
class Stub:
    url: str
    received: list[dict[str, Any]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


@pytest.fixture
def ollama(serve: Callable[[Any], AbstractContextManager[str]]) -> Iterator[Stub]:
    stub_app = FastAPI()
    stub = Stub(url="")

    @stub_app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        stub.received.append(json.loads(await request.body()))
        stub.paths.append(str(request.url.path))
        return Response(SSE, media_type="text/event-stream")

    with serve(stub_app) as url:
        stub.url = url
        yield stub


def _gateway(
    serve: Callable[[Any], AbstractContextManager[str]],
    config: GatewayConfig,
    stages: ActiveStages,
) -> AbstractContextManager[str]:
    return serve(create_app(config, stages))


def _post(url: str, key: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.post(
        f"{url}/v1/messages?beta=true",
        content=json.dumps(body).encode(),
        headers={"x-api-key": key, "content-type": "application/json"},
        timeout=10,
    )


def _ledger(data: DataDir) -> list[LedgerEntry]:
    path = data.job_dir(JOB) / "ledger.jsonl"
    return [
        LedgerEntry.model_validate_json(line) for line in path.read_text().splitlines()
    ]


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    data.job_dir(JOB).mkdir()
    return data


@pytest.fixture
def stages() -> ActiveStages:
    stages = ActiveStages()
    stages.activate(KEY)
    return stages


def test_接受_beta_query_把別名換成實際模型轉送_串流原樣回傳(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
) -> None:
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    with _gateway(serve, config, stages) as url:
        response = _post(url, str(KEY), PI_BODY)
    assert response.status_code == 200
    assert response.content == SSE
    assert ollama.paths == ["/v1/messages"]
    forwarded = ollama.received[0]
    assert forwarded["model"] == "qwen3.5:9b"
    # 別名以外的欄位原樣轉送
    assert {k: v for k, v in forwarded.items() if k != "model"} == {
        k: v for k, v in PI_BODY.items() if k != "model"
    }


@pytest.mark.promise("NFR-KEEP-01")
def test_請求與回應依_hash_存進_blobs_帳本記下是哪一段哪一次(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
) -> None:
    raw = json.dumps(PI_BODY).encode()
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    with _gateway(serve, config, stages) as url:
        httpx.post(
            f"{url}/v1/messages?beta=true",
            content=raw,
            headers={"x-api-key": str(KEY)},
            timeout=10,
        )
    request_hash = hashlib.sha256(raw).hexdigest()
    response_hash = hashlib.sha256(SSE).hexdigest()
    assert (data.blobs / request_hash).read_bytes() == raw
    assert (data.blobs / response_hash).read_bytes() == SSE
    [entry] = _ledger(data)
    assert (entry.job_id, entry.stage, entry.attempt) == (JOB, "echo", 1)
    assert (entry.kind, entry.tier, entry.model) == ("llm", "local", "qwen3.5:9b")
    assert (entry.request_hash, entry.response_hash) == (request_hash, response_hash)
    assert (entry.input_tokens, entry.output_tokens) == (42, 7)
    assert entry.status == "ok"


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "key",
    [
        str(JobKey(JOB, "echo", 2)),
        str(JobKey("other-job", "echo", 1)),
        "",
        "sk-fake-xxxx",
    ],
)
def test_不是正在跑的鑰匙不轉送(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
    key: str,
) -> None:
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    with _gateway(serve, config, stages) as url:
        response = _post(url, key, PI_BODY)
    assert response.status_code == 403
    assert response.json()["type"] == "error"
    assert ollama.received == []


def test_不認得的等級別名不轉送(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
) -> None:
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    with _gateway(serve, config, stages) as url:
        response = _post(url, str(KEY), {**PI_BODY, "model": "claude-opus"})
    assert response.status_code == 400
    assert ollama.received == []


def _recording(tmp_path: Path, raw: bytes, status: str = "ok") -> Path:
    """做一份錄音：一行帳本加上兩個 blob，跟 live 模式寫出來的格式一樣。"""
    replay = tmp_path / "recording"
    (replay / "blobs").mkdir(parents=True)
    request_hash = hashlib.sha256(raw).hexdigest()
    response_hash = hashlib.sha256(SSE).hexdigest()
    (replay / "blobs" / request_hash).write_bytes(raw)
    (replay / "blobs" / response_hash).write_bytes(SSE)
    entry = {
        "ts": "2026-10-11T12:00:00+08:00",
        "job_id": "some-recorded-job",
        "stage": "echo",
        "attempt": 1,
        "kind": "llm",
        "tier": "local",
        "model": "qwen3.5:9b",
        "latency_ms": 10,
        "status": status,
        "request_hash": request_hash,
        "response_hash": response_hash,
    }
    (replay / "ledger.jsonl").write_text(json.dumps(entry) + "\n")
    return replay


@pytest.mark.promise("NFR-COST-06")
def test_重播模式回放錄好的回應_不打後端(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
    tmp_path: Path,
) -> None:
    raw = json.dumps(PI_BODY).encode()
    config = GatewayConfig(
        data=data,
        mode="replay",
        replay_dir=_recording(tmp_path, raw),
        ollama_url=ollama.url,
    )
    with _gateway(serve, config, stages) as url:
        response = httpx.post(
            f"{url}/v1/messages?beta=true",
            content=raw,
            headers={"x-api-key": str(KEY)},
            timeout=10,
        )
    assert response.status_code == 200
    assert response.content == SSE
    assert ollama.received == []
    [entry] = _ledger(data)
    assert entry.status == "cache_hit"


@pytest.mark.promise("NFR-COST-06")
def test_重播模式遇到沒錄過的請求報錯_不轉送(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        data=data,
        mode="replay",
        replay_dir=_recording(tmp_path, b"{}"),
        ollama_url=ollama.url,
    )
    with _gateway(serve, config, stages) as url:
        response = _post(url, str(KEY), PI_BODY)
    assert response.status_code >= 500
    assert response.json()["type"] == "error"
    assert ollama.received == []


# 後端出錯時回的完整本文（Anthropic 的錯誤格式）
UPSTREAM_ERROR = b'{"type":"error","error":{"type":"api_error","message":"boom"}}'


@pytest.mark.promise("NFR-KEEP-01")
def test_後端回_500_回應本文照樣存進_blobs_帳本記_error(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    stages: ActiveStages,
) -> None:
    failing = FastAPI()

    @failing.post("/v1/messages")
    async def messages() -> Response:
        return Response(UPSTREAM_ERROR, status_code=500, media_type="application/json")

    with serve(failing) as backend:
        config = GatewayConfig(data=data, mode="live", ollama_url=backend)
        with _gateway(serve, config, stages) as url:
            response = _post(url, str(KEY), PI_BODY)
    # 狀態碼與本文原樣交還給 Pi
    assert response.status_code == 500
    assert response.content == UPSTREAM_ERROR
    [entry] = _ledger(data)
    assert entry.status == "error"
    # 出錯的那次最需要回頭看原文：完整收到就存，跟狀態碼無關
    assert entry.response_hash == hashlib.sha256(UPSTREAM_ERROR).hexdigest()
    assert (data.blobs / entry.response_hash).read_bytes() == UPSTREAM_ERROR
    assert (entry.input_tokens, entry.output_tokens) == (0, 0)


@pytest.mark.promise("NFR-COST-06")
def test_重播模式不回放錄音裡出錯的那次(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
    tmp_path: Path,
) -> None:
    # 錄音時後端回了錯誤：帳本那行有 response_hash，但不能當成功的回應交給 Pi
    raw = json.dumps(PI_BODY).encode()
    config = GatewayConfig(
        data=data,
        mode="replay",
        replay_dir=_recording(tmp_path, raw, status="error"),
        ollama_url=ollama.url,
    )
    with _gateway(serve, config, stages) as url:
        response = httpx.post(
            f"{url}/v1/messages?beta=true",
            content=raw,
            headers={"x-api-key": str(KEY)},
            timeout=10,
        )
    assert response.status_code >= 500
    assert response.content != SSE
    assert ollama.received == []


# --- 後端故障告訴執行器（告示板）：基礎設施故障不升級（NFR-RUN-05、ADR-0011 §3.3） ------


@pytest.mark.promise("NFR-RUN-05")
def test_Ollama_連不上_告示板記下這一段這一次碰到後端故障(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    stages: ActiveStages,
) -> None:
    config = GatewayConfig(data=data, mode="live", ollama_url="http://127.0.0.1:9")
    board = JobBoard(config.clock)
    with serve(create_app(config, stages, board)) as url:
        response = _post(url, str(KEY), PI_BODY)
    assert response.status_code == 502
    outage = board.outage(KEY)
    assert outage is not None and outage.backend == "ollama"
    assert "連不上" in outage.message


@pytest.mark.promise("NFR-RUN-05")
def test_後端回_5xx_也算後端故障(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    stages: ActiveStages,
) -> None:
    failing = FastAPI()

    @failing.post("/v1/messages")
    async def messages() -> Response:
        return Response(UPSTREAM_ERROR, status_code=503, media_type="application/json")

    with serve(failing) as backend:
        config = GatewayConfig(data=data, mode="live", ollama_url=backend)
        board = JobBoard(config.clock)
        with serve(create_app(config, stages, board)) as url:
            _post(url, str(KEY), PI_BODY)
    outage = board.outage(KEY)
    assert outage is not None and "503" in outage.message


def test_後端正常回應_告示板沒有故障(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    stages: ActiveStages,
    ollama: Stub,
) -> None:
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    board = JobBoard(config.clock)
    with serve(create_app(config, stages, board)) as url:
        assert _post(url, str(KEY), PI_BODY).status_code == 200
    assert board.outage(KEY) is None


@pytest.mark.promise("FR-MD-01")
def test_執行器登記這一次是升級上來的_帳本記下_escalated_from(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    ollama: Stub,
) -> None:
    stages = ActiveStages()
    stages.activate(KEY, escalated_from="local")
    secrets = data.root.parent / "secrets"
    secrets.mkdir()
    (secrets / "anthropic").write_text("sk-fake-xxxx-escalate", encoding="utf-8")
    config = GatewayConfig(
        data=data, mode="live", anthropic_url=ollama.url, secrets_dir=secrets
    )
    # 這張單沒有 job.md，預算當成 0：被擋下的那一筆也照樣記等級與升級來源
    with _gateway(serve, config, stages) as url:
        _post(url, str(KEY), {**PI_BODY, "model": "strong"})
    [entry] = _ledger(data)
    assert (entry.tier, entry.escalated_from) == ("strong", "local")


@pytest.mark.promise("NFR-RUN-05")
def test_後端先回_503_同一把鑰匙之後成功_告示板的故障清掉(
    serve: Callable[[Any], AbstractContextManager[str]],
    data: DataDir,
    stages: ActiveStages,
) -> None:
    replies = [Response(UPSTREAM_ERROR, status_code=503, media_type="application/json")]
    flaky = FastAPI()

    @flaky.post("/v1/messages")
    async def messages() -> Response:
        if replies:
            return replies.pop()
        return Response(SSE, media_type="text/event-stream")

    with serve(flaky) as backend:
        config = GatewayConfig(data=data, mode="live", ollama_url=backend)
        board = JobBoard(config.clock)
        with serve(create_app(config, stages, board)) as url:
            assert _post(url, str(KEY), PI_BODY).status_code == 503
            assert board.outage(KEY) is not None
            assert _post(url, str(KEY), PI_BODY).status_code == 200
    assert board.outage(KEY) is None


# --- 本機模型的上下文放不下：擋下這一次，讓執行器升級，不讓 Ollama 默默截掉（ADR-0022） ------


def _local_body(text: str) -> dict[str, Any]:
    return {
        **PI_BODY,
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
    }


@pytest.mark.promise("FR-MD-01")
def test_提示大到本機模型的上下文放不下_不轉送_回請求錯誤_不算後端故障(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
) -> None:
    # 本機的上下文 32,768 token、輸出留 16,384：提示估值（位元組 ÷ 3）超過 16,384 就放不下。
    # 20,000 個中文字是 60,000 個位元組，估 20,000 token
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    board = JobBoard(config.clock)
    with serve(create_app(config, stages, board)) as url:
        response = _post(url, str(KEY), _local_body("長" * 20_000))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "上下文" in error["message"]
    # Ollama 一個請求都沒收到；不是後端故障，執行器照「產出不合格」重試、升級
    assert ollama.received == []
    assert board.outage(KEY) is None
    [entry] = _ledger(data)
    assert (entry.tier, entry.status, entry.cost_usd) == ("local", "error", 0)


def test_放得下的長提示照常轉送給本機模型(
    serve: Callable[[Any], AbstractContextManager[str]],
    ollama: Stub,
    data: DataDir,
    stages: ActiveStages,
) -> None:
    # 10,000 個中文字：30,000 個位元組，估 10,000 token，加上輸出 16,384 放得進 32,768
    config = GatewayConfig(data=data, mode="live", ollama_url=ollama.url)
    with _gateway(serve, config, stages) as url:
        response = _post(url, str(KEY), _local_body("長" * 10_000))
    assert response.status_code == 200
    assert len(ollama.received) == 1
