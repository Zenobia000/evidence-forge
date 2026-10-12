"""SEAM-02、SEAM-04：閘道的 `web_search`（Tavily）↔ 預算 ↔ 帳本 ↔ 快取與重播（#69）。

一個本機 stub 扮演 Tavily 的 `POST /search`。金鑰是假的（`tvly-fake-xxxx-…`），放在測試自己的
`EF_SECRETS` 資料夾；任何請求都只到 127.0.0.1（`tests/conftest.py` 擋掉往外的連線）。
"""

import asyncio
import json
import logging
import shutil
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.gateway.search import Searcher
from evidence_forge.harness import default_harness
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=TAIPEI)
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


def _tavily_body(
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
    return lambda: JSONResponse(_tavily_body(credits=credits))


def status(code: int, **headers: str) -> Reply:
    return lambda: JSONResponse(
        {"detail": {"error": f"stub {code}"}}, status_code=code, headers=headers
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


Serve = Callable[[Any], AbstractContextManager[str]]


@pytest.fixture
def tavily(serve: Serve) -> Iterator[Tavily]:
    stub = Tavily()
    with serve(stub.app()) as url:
        stub.url = url
        yield stub


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    return data


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    folder = tmp_path / "secrets"
    folder.mkdir()
    (folder / "tavily").write_text(FAKE_KEY + "\n", encoding="utf-8")
    return folder


@dataclass
class Gateway:
    url: str

    def search(self, key: JobKey, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/web_search",
            json=body,
            headers={"x-api-key": str(key)},
            timeout=10,
        )


Start = Callable[..., AbstractContextManager[Gateway]]


@pytest.fixture
def gateway(serve: Serve, data: DataDir, tavily: Tavily, secrets: Path) -> Start:
    """`with gateway(stages, **設定) as gw:`；`now` 是可以往後撥的時鐘。"""

    @contextmanager
    def start(
        stages: ActiveStages,
        *,
        clock: Callable[[], datetime] = lambda: NOW,
        timeout_s: float = 5.0,
        **changes: Any,
    ) -> Iterator[Gateway]:
        settings: dict[str, Any] = {
            "data": data,
            "mode": "live",
            "tavily_url": tavily.url,
            "secrets_dir": secrets,
            "backoff_base_s": 0.01,
            "backoff_max_s": 0.05,
            "clock": clock,
            **changes,
        }
        config = GatewayConfig(**settings)
        searcher = Searcher(config, timeout_s=timeout_s)
        with serve(create_app(config, stages, searcher=searcher)) as url:
            yield Gateway(url)

    return start


def _job(data: DataDir, credits: int | None = 10, goal: str = "測試") -> str:
    recipe = load_recipes(default_harness(), tiers=["local", "strong", "max"])["echo"]
    return open_job(
        data,
        recipe,
        PastedText(f"素材 {goal}"),
        goal=goal,
        now=NOW,
        budget_credits=credits,
    ).job.id


def _active(*keys: JobKey) -> ActiveStages:
    stages = ActiveStages()
    for key in keys:
        stages.activate(key)
    return stages


def _ledger(data: DataDir, job_id: str) -> list[LedgerEntry]:
    path = data.job_dir(job_id) / "ledger.jsonl"
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line) for line in path.read_text().splitlines()
    ]


# --- 搜尋本身 -----------------------------------------------------------------


@pytest.mark.promise("FR-RS-01", "NFR-SEC-04", "FR-LG-01")
def test_搜尋回結果清單_不給_answer_帳本記一筆_search_1_credit(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="  台灣   PM\n觀點  ", max_results=2)

    assert response.status_code == 200
    body = response.json()
    assert "answer" not in body
    assert body["query"] == "台灣 PM 觀點"
    assert body["cache_hit"] is False
    assert body["results"] == [
        {
            "url": "https://example.org/a",
            "title": "第一篇",
            "content": "第一篇的摘要",
            "score": 0.91,
            "published_date": "2026-10-01",
        },
        # 沒有發布日期的就不給這個欄位
        {
            "url": "https://example.org/b",
            "title": "第二篇",
            "content": "第二篇的摘要",
            "score": 0.5,
        },
    ]
    [sent] = tavily.received
    assert sent["query"] == "台灣 PM 觀點"
    assert (sent["max_results"], sent["search_depth"]) == (2, "basic")
    assert sent["include_answer"] is False
    assert sent["include_raw_content"] is False
    # 金鑰放在標頭，不放在請求本文（本文會存進 blobs/）
    assert "api_key" not in sent
    assert tavily.headers[0]["authorization"] == f"Bearer {FAKE_KEY}"

    [entry] = _ledger(data, job_id)
    assert (entry.kind, entry.status, entry.credits, entry.cost_usd) == (
        "search",
        "ok",
        1,
        0.0,
    )
    assert (entry.stage, entry.attempt, entry.tier) == ("research", 1, None)
    assert entry.response_hash is not None


@pytest.mark.promise("FR-RS-01")
def test_max_results_預設_5_上限_10_查詢不能是空的(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    key = JobKey(_job(data), "research", 1)
    with gateway(_active(key)) as gw:
        assert gw.search(key, query="預設").status_code == 200
        too_many = gw.search(key, query="太多", max_results=11)
        zero = gw.search(key, query="零", max_results=0)
        not_int = gw.search(key, query="小數", max_results=2.5)
        empty = gw.search(key, query="   ")
        missing = gw.search(key)
    assert tavily.received[0]["max_results"] == 5
    for refused in (too_many, zero, not_int, empty, missing):
        assert refused.status_code == 400
    assert len(tavily.received) == 1


def test_鑰匙不是正在跑的那一把_拒絕_不搜尋(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    key = JobKey(_job(data), "research", 1)
    with gateway(_active()) as gw:
        response = gw.search(key, query="x")
    assert response.status_code == 403
    assert tavily.received == []


# --- 預算 -------------------------------------------------------------------


@pytest.mark.promise("NFR-COST-01", "FR-RS-01")
def test_credits_不夠就拒絕_不送出_帳本記_budget_exceeded(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data, credits=1)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        first = gw.search(key, query="第一次")
        second = gw.search(key, query="第二次")
    assert first.status_code == 200
    assert second.status_code == 402
    message = second.json()["error"]
    assert message.startswith("budget_exceeded")
    # agent 看得懂下一步：不要再搜尋，用已經找到的資料
    assert "已經找到的資料" in message
    assert len(tavily.received) == 1
    entries = _ledger(data, job_id)
    assert [(e.status, e.credits) for e in entries] == [
        ("ok", 1),
        ("budget_exceeded", 0),
    ]
    assert entries[1].response_hash is None


@pytest.mark.promise("NFR-COST-01")
def test_預設的工單沒有_credits_不能搜尋(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    # echo 配方沒有一段給 credits：開單預設 0
    job_id = _job(data, credits=None)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="x")
    assert response.status_code == 402
    assert tavily.received == []


@pytest.mark.promise("NFR-COST-01")
def test_結算照_Tavily_回報的用量(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data, credits=3)
    key = JobKey(job_id, "research", 1)
    # 回報 2 credits 的那次照 2 算；沒回報用量的照 basic 的 1 算
    tavily.replies = [ok(credits=2), ok(credits=None)]
    with gateway(_active(key)) as gw:
        gw.search(key, query="一")
        gw.search(key, query="二")
        third = gw.search(key, query="三")
    assert third.status_code == 402
    assert [e.credits for e in _ledger(data, job_id)] == [2, 1, 0]


# --- 快取 -------------------------------------------------------------------


@pytest.mark.promise("FR-RS-01", "NFR-COST-02")
def test_同一個查詢跨工單共用快取_不扣_credits_記_cache_hit(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    first_job = _job(data, goal="一")
    # 第二張單一個 credit 都沒有：快取命中不需要預算
    second_job = _job(data, credits=0, goal="二")
    first = JobKey(first_job, "research", 1)
    second = JobKey(second_job, "research", 1)
    with gateway(_active(first, second)) as gw:
        live = gw.search(first, query="同一個 查詢", max_results=3)
        # 前後空白、連續空白不一樣，正規化之後是同一個查詢
        cached = gw.search(second, query=" 同一個   查詢 ", max_results=3)
        # 參數不一樣就是另一個查詢
        other = gw.search(first, query="同一個 查詢", max_results=4)
    assert len(tavily.received) == 2
    assert cached.status_code == 200
    assert cached.json()["cache_hit"] is True
    assert cached.json()["results"] == live.json()["results"]
    assert other.json()["cache_hit"] is False
    [hit] = _ledger(data, second_job)
    assert (hit.status, hit.credits) == ("cache_hit", 0)
    [ok_entry, _] = _ledger(data, first_job)
    assert hit.request_hash == ok_entry.request_hash
    assert hit.response_hash == ok_entry.response_hash


@pytest.mark.promise("NFR-COST-02")
def test_快取過了有效期限就重新搜尋(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    key = JobKey(_job(data), "research", 1)
    now = [NOW]
    with gateway(_active(key), clock=lambda: now[0]) as gw:
        gw.search(key, query="會過期")
        now[0] = NOW + timedelta(days=6)
        assert gw.search(key, query="會過期").json()["cache_hit"] is True
        now[0] = NOW + timedelta(days=8)
        assert gw.search(key, query="會過期").json()["cache_hit"] is False
    assert len(tavily.received) == 2


# --- Tavily 回錯 --------------------------------------------------------------


@pytest.mark.promise("NFR-PAR-03")
def test_429_退避重試後成功(gateway: Start, data: DataDir, tavily: Tavily) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(429, **{"retry-after": "0"}), status(429)]
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="限流")
    assert response.status_code == 200
    assert len(tavily.received) == 3
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("ok", 1)


@pytest.mark.promise("NFR-PAR-03")
def test_429_重試有上限_用完記_rate_limited_不扣_credits(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(429)] * 10
    with gateway(_active(key), rate_limit_retries=2) as gw:
        response = gw.search(key, query="一直限流")
    assert response.status_code == 429
    assert "429" in response.json()["error"]
    assert len(tavily.received) == 3
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("rate_limited", 0)


@pytest.mark.parametrize(
    ("code", "words"),
    [(401, "金鑰"), (432, "額度"), (500, "HTTP 500"), (400, "查詢")],
)
def test_Tavily_回錯_agent_看得懂_不扣_credits(
    gateway: Start, data: DataDir, tavily: Tavily, code: int, words: str
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(code)]
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="出錯")
    assert response.status_code >= 400
    assert words in response.json()["error"]
    assert len(tavily.received) == 1
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits, entry.response_hash) == ("error", 0, None)


def test_Tavily_逾時記_timeout_照預留的_credits_入帳(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.delay_s = 1.0
    with gateway(_active(key), timeout_s=0.2) as gw:
        response = gw.search(key, query="很慢")
    assert response.status_code == 504
    assert "Tavily" in response.json()["error"]
    [entry] = _ledger(data, job_id)
    # 請求已經送出：不知道 Tavily 算了沒有，寧可多算
    assert (entry.status, entry.credits) == ("timeout", 1)


def test_沒有金鑰檔_回錯_不扣_credits(
    gateway: Start, data: DataDir, tavily: Tavily, secrets: Path
) -> None:
    (secrets / "tavily").unlink()
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="沒金鑰")
    assert response.status_code == 502
    assert "Tavily 金鑰" in response.json()["error"]
    assert tavily.received == []
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("error", 0)


# --- 重播 -------------------------------------------------------------------


def _record(tmp_path: Path, data: DataDir, job_id: str) -> Path:
    """照錄音的做法：把一張工單的 ledger.jsonl 與它指到的 blobs/ 複製出來。"""
    recording = tmp_path / "recording"
    (recording / "blobs").mkdir(parents=True)
    shutil.copy(data.job_dir(job_id) / "ledger.jsonl", recording / "ledger.jsonl")
    for entry in _ledger(data, job_id):
        for digest in (entry.request_hash, entry.response_hash):
            if digest is not None:
                shutil.copy(data.blobs / digest, recording / "blobs" / digest)
    return recording


@pytest.mark.promise("NFR-COST-06")
def test_重播模式從錄音回放_不連_Tavily_沒錄過就報錯(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    tmp_path: Path,
    serve: Serve,
    secrets: Path,
) -> None:
    recorded_job = _job(data, goal="錄音")
    recorded_key = JobKey(recorded_job, "research", 1)
    with gateway(_active(recorded_key)) as gw:
        live = gw.search(recorded_key, query="錄過的查詢")
    recording = _record(tmp_path, data, recorded_job)

    # 重播用另一個資料目錄：沒有快取可用，只能靠錄音
    replay_data = DataDir(tmp_path / "replay-data")
    replay_data.ensure()
    job_id = _job(replay_data, credits=0, goal="重播")
    key = JobKey(job_id, "research", 1)
    config = GatewayConfig(
        data=replay_data,
        mode="replay",
        replay_dir=recording,
        tavily_url=tavily.url,
        secrets_dir=secrets,
        clock=lambda: NOW + timedelta(days=30),
    )
    with serve(create_app(config, _active(key))) as url:
        gw = Gateway(url)
        replayed = gw.search(key, query="  錄過的查詢")
        missing = gw.search(key, query="沒錄過的查詢")

    assert len(tavily.received) == 1
    assert replayed.status_code == 200
    assert replayed.json()["results"] == live.json()["results"]
    assert missing.status_code == 500
    assert "重播" in missing.json()["error"]
    entries = _ledger(replay_data, job_id)
    assert [(e.status, e.credits) for e in entries] == [
        ("cache_hit", 0),
        ("error", 0),
    ]


# --- 金鑰不外洩 ---------------------------------------------------------------


@pytest.mark.promise("NFR-SEC-04")
def test_金鑰不出現在帳本_blobs_快取_回應與日誌裡(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    job_id = _job(data, credits=2)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [ok(), status(401), status(400), status(429)]
    responses: list[httpx.Response] = []
    with gateway(_active(key), rate_limit_retries=0) as gw:
        for query in ("一", "二", "三", "四", "一", "五", "六"):
            responses.append(gw.search(key, query=query))

    assert len(responses) == 7
    for response in responses:
        assert FAKE_KEY not in response.text
    assert FAKE_KEY not in caplog.text
    for path in data.root.rglob("*"):
        if path.is_file():
            assert FAKE_KEY.encode() not in path.read_bytes(), path


def test_Tavily_的位址可以用環境變數換成本機的假後端(data: DataDir) -> None:
    # 錄重播 fixture 時用（`tests/fixtures/web/`）：搜尋回的是為測試寫的文章，不是真的網路
    from evidence_forge.gateway.config import config_from_env

    custom = config_from_env(data, {"EF_TAVILY_URL": " http://127.0.0.1:8765/ "})
    assert custom.tavily_url == "http://127.0.0.1:8765"
    assert config_from_env(data, {}).tavily_url == "https://api.tavily.com"
