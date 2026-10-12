"""SEAM-02、SEAM-04：閘道的 `web_search`（Tavily）↔ 預算 ↔ 帳本 ↔ 快取與重播 ↔ 告示板（#69）。

假 Tavily 在 `tests/tavily_stub.py`；金鑰是假的，任何請求都只到 127.0.0.1。
"""

import asyncio
import logging
import shutil
import socket
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from tavily_stub import FAKE_KEY, Tavily, ok, status

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, JobBoard, create_app
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.board import Outage
from evidence_forge.gateway.search import Searcher
from evidence_forge.harness import default_harness
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=TAIPEI)


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
    # 閘道跟執行器共用的告示板：等限流、Tavily 故障記在這裡
    board: JobBoard

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
        sleep: Callable[[float], Any] = asyncio.sleep,
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
        board = JobBoard(config.clock)
        searcher = Searcher(config, board=board, timeout_s=timeout_s, sleep=sleep)
        with serve(create_app(config, stages, board, searcher=searcher)) as url:
            yield Gateway(url, board)

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
    # 回給 agent 的錯誤存進 blobs/：重播時照原樣回放（見〈重播〉）
    refused = entries[1].response_hash
    assert refused is not None
    stored = BlobStore(data.blobs).get(refused)
    assert stored is not None and message.encode() in stored
    # credits 用完不是後端故障：不告訴執行器停掉，這一段照樣收尾
    assert gw.board.outage(key) is None


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
def test_429_退避重試後成功_等待時間記在_queue_ms(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    # 第一次照 retry-after 等 0.1 秒；第二次沒有 retry-after，從 base 加倍：0.05 × 2 = 0.1 秒
    tavily.replies = [status(429, **{"retry-after": "0.1"}), status(429)]
    with gateway(_active(key), backoff_base_s=0.05, backoff_max_s=0.1) as gw:
        response = gw.search(key, query="限流")
    assert response.status_code == 200
    assert len(tavily.received) == 3
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("ok", 1)
    # 真的等了約 0.2 秒；排隊時間是總延遲的一部分
    assert 150 <= entry.queue_ms <= entry.latency_ms, entry


@pytest.mark.promise("NFR-PAR-03")
def test_429_重試有上限_用完記_rate_limited_不扣_credits(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(429)] * 10
    with gateway(
        _active(key), rate_limit_retries=2, backoff_base_s=0.05, backoff_max_s=0.05
    ) as gw:
        response = gw.search(key, query="一直限流")
    assert response.status_code == 429
    assert "429" in response.json()["error"]
    assert len(tavily.received) == 3
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("rate_limited", 0)
    # 退避兩次、各 0.05 秒
    assert 80 <= entry.queue_ms <= entry.latency_ms, entry


@pytest.mark.promise("NFR-OBS-02", "NFR-PAR-03")
def test_退避時告示板寫著等限流_退避完就清掉(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(429)]
    seen: list[str | None] = []
    boards: list[JobBoard] = []

    async def watch_sleep(delay: float) -> None:
        # 退避的那一刻，執行器從告示板看到的是什麼
        waiting = boards[0].waiting(job_id)
        seen.append(waiting.kind if waiting is not None else None)
        await asyncio.sleep(delay)

    with gateway(_active(key), sleep=watch_sleep) as gw:
        boards.append(gw.board)
        response = gw.search(key, query="限流")
        after = gw.board.waiting(job_id)
    assert response.status_code == 200
    assert seen == ["rate_limit"]
    assert after is None


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
    assert (entry.status, entry.credits) == ("error", 0)
    # 帳本指向回給 agent 的錯誤（重播用），不是 Tavily 的原始回應
    assert entry.response_hash is not None


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


def test_Tavily_的錯誤說明裡夾著金鑰_遮掉再交給_agent(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(400, detail=f"query too long for key {FAKE_KEY}")]
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="很長的查詢")
    assert response.status_code == 400
    error = response.json()["error"]
    assert FAKE_KEY not in error
    assert "query too long for key [redacted]" in error


# --- Tavily 故障告訴執行器 -----------------------------------------------------


def _closed_port() -> str:
    """一個沒有人在聽的本機位址：連上去一定被拒。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.mark.promise("NFR-RUN-05")
@pytest.mark.parametrize(
    "case", ["401", "432", "500", "429 重試用完", "逾時", "連不上", "沒有金鑰檔"]
)
def test_Tavily_故障告訴執行器_原因跟_agent_看到的一樣(
    gateway: Start, data: DataDir, tavily: Tavily, secrets: Path, case: str
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    changes: dict[str, Any] = {"rate_limit_retries": 1}
    if case.isdigit():
        tavily.replies = [status(int(case))]
    elif case == "429 重試用完":
        tavily.replies = [status(429)] * 2
    elif case == "逾時":
        tavily.delay_s = 1.0
        changes["timeout_s"] = 0.2
    elif case == "連不上":
        changes["tavily_url"] = _closed_port()
    else:
        (secrets / "tavily").unlink()
    with gateway(_active(key), **changes) as gw:
        response = gw.search(key, query="故障")
    assert response.status_code in (429, 502, 504)
    # 執行器據此把沒交出產出的這一段標失敗，不重試、不升級（NFR-RUN-05）
    assert gw.board.outage(key) == Outage("tavily", response.json()["error"])


@pytest.mark.parametrize("case", ["400 查詢不行", "credits 用完"])
def test_查詢不行與_credits_用完不算故障(
    gateway: Start, data: DataDir, tavily: Tavily, case: str
) -> None:
    job_id = _job(data, credits=0 if case == "credits 用完" else 10)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [status(400)]
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="不算故障")
    assert response.status_code in (400, 402)
    assert gw.board.outage(key) is None


@pytest.mark.promise("NFR-RUN-05")
def test_Tavily_後來好好回了_先前的故障不算數_快取命中不算(
    gateway: Start, data: DataDir, tavily: Tavily
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        gw.search(key, query="先暖快取")
        tavily.replies = [status(503)]
        assert gw.search(key, query="故障").status_code == 502
        # 快取命中沒有碰 Tavily，說不出它好了沒有
        assert gw.search(key, query="先暖快取").json()["cache_hit"] is True
        assert gw.board.outage(key) is not None
        assert gw.search(key, query="故障").status_code == 200
        assert gw.board.outage(key) is None


# --- 結算的小地方 ---------------------------------------------------------------


def test_Tavily_算的_credits_比預留的多_記一筆警告(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    tavily.replies = [ok(credits=2)]
    with caplog.at_level(logging.WARNING), gateway(_active(key)) as gw:
        gw.search(key, query="比較貴")
    [entry] = _ledger(data, job_id)
    assert entry.credits == 2
    assert "比預留的 1 多" in caplog.text


def test_Tavily_原始回應存不進_blobs_照樣回結果(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = BlobStore.put

    def put(self: BlobStore, content: bytes) -> str:
        # 只有 Tavily 的原始回應存不進去（磁碟滿之類）
        if "Tavily 的模型答案".encode() in content:
            raise OSError("磁碟滿了")
        return original(self, content)

    monkeypatch.setattr(BlobStore, "put", put)
    job_id = _job(data)
    key = JobKey(job_id, "research", 1)
    with gateway(_active(key)) as gw:
        response = gw.search(key, query="存不進去")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    [entry] = _ledger(data, job_id)
    assert (entry.status, entry.credits) == ("ok", 1)


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


@dataclass
class Replay:
    gateway: Gateway
    data: DataDir
    key: JobKey


Replaying = Callable[[Path], AbstractContextManager[Replay]]


@pytest.fixture
def replaying(serve: Serve, tavily: Tavily, tmp_path: Path, secrets: Path) -> Replaying:
    """`with replaying(錄音) as replay:`：重播模式的閘道，用另一個資料目錄（沒有快取可用，只能靠錄音），
    工單一個 credit 都沒有，時鐘撥到 30 天後（錄音裡的結果早就過期）。"""

    @contextmanager
    def start(recording: Path) -> Iterator[Replay]:
        replay_data = DataDir(tmp_path / "replay-data")
        replay_data.ensure()
        key = JobKey(_job(replay_data, credits=0, goal="重播"), "research", 1)
        config = GatewayConfig(
            data=replay_data,
            mode="replay",
            replay_dir=recording,
            tavily_url=tavily.url,
            secrets_dir=secrets,
            clock=lambda: NOW + timedelta(days=30),
        )
        board = JobBoard(config.clock)
        with serve(create_app(config, _active(key), board)) as url:
            yield Replay(Gateway(url, board), replay_data, key)

    return start


@pytest.mark.promise("NFR-COST-06")
def test_重播模式從錄音回放_不連_Tavily_沒錄過就報錯(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    tmp_path: Path,
    replaying: Replaying,
) -> None:
    recorded_job = _job(data, goal="錄音")
    recorded_key = JobKey(recorded_job, "research", 1)
    with gateway(_active(recorded_key)) as gw:
        live = gw.search(recorded_key, query="錄過的查詢")
    recording = _record(tmp_path, data, recorded_job)

    with replaying(recording) as replay:
        replayed = replay.gateway.search(replay.key, query="  錄過的查詢")
        missing = replay.gateway.search(replay.key, query="沒錄過的查詢")

    assert len(tavily.received) == 1
    assert replayed.status_code == 200
    assert replayed.json()["results"] == live.json()["results"]
    assert missing.status_code == 500
    assert "重播" in missing.json()["error"]
    entries = _ledger(replay.data, replay.key.job_id)
    assert [(e.status, e.credits) for e in entries] == [
        ("cache_hit", 0),
        ("error", 0),
    ]


@pytest.mark.promise("NFR-COST-06")
def test_錄音裡失敗的搜尋_重播時回一模一樣的錯誤(
    gateway: Start,
    data: DataDir,
    tavily: Tavily,
    tmp_path: Path,
    replaying: Replaying,
) -> None:
    # 錄音：同一個查詢先碰到 Tavily 500、再試一次成功；另一個查詢被 credits 擋下（402）、
    # 查詢本身不行（400）。agent 收到錯誤之後照樣往下走，錯誤文字會進下一個模型請求
    recorded_job = _job(data, credits=1, goal="錄音")
    recorded_key = JobKey(recorded_job, "research", 1)
    tavily.replies = [status(400), status(500)]
    queries = ["查詢不行", "同一個", "同一個", "沒額度了"]
    with gateway(_active(recorded_key)) as gw:
        live = [gw.search(recorded_key, query=query) for query in queries]
    assert [r.status_code for r in live] == [400, 502, 200, 402]
    recording = _record(tmp_path, data, recorded_job)

    with replaying(recording) as replay:
        replayed = [replay.gateway.search(replay.key, query=query) for query in queries]
        outage = replay.gateway.board.outage(replay.key)

    assert len(tavily.received) == 3
    for before, after in zip(live, replayed, strict=True):
        assert after.status_code == before.status_code
        if before.status_code != 200:
            # agent 收到的位元組一樣：狀態碼與錯誤本文
            assert after.content == before.content
    assert replayed[2].json()["results"] == live[2].json()["results"]
    # 帳本照錄音時的狀態記，執行器才做出跟錄音時一樣的判斷；重播不花 credits
    entries = _ledger(replay.data, replay.key.job_id)
    assert [(e.status, e.credits) for e in entries] == [
        ("error", 0),
        ("error", 0),
        ("cache_hit", 0),
        ("budget_exceeded", 0),
    ]
    # 錄音裡 Tavily 500 之後又成功了：重播時告示板也一樣，沒有留下故障
    assert outage is None


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
