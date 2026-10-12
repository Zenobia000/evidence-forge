"""閘道 ↔ 對話鑰匙（ADR-0021、NFR-SEC-02、chat.feature 的 @security 與「對話本身的花費也入帳」）。

- 對話只能用對話的工具（analyze、read_report、job_status），段的工具一律 403。
- 段的鑰匙不能用對話的工具。
- 對話只讀得到自己 `jobs.jsonl` 裡的工單；別的對話開的一律 403。
- 接回之後只認最新那一把；Pi 結束撤銷之後也拒絕。
- 對話本身的模型呼叫記在對話的帳本、受對話的預算管、不走模型快取。
- 每個對話同一時間最多幾張工單在跑（預設 1）。

開單用真的投入（`runner.intake.open_job`）；模型後端是本機的假 Anthropic，金鑰是假的。
"""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response

from evidence_forge import conversations
from evidence_forge.convkey import ConversationKey
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
FAKE_KEY = "sk-fake-xxxx-anthropic-chat-canary"
RECIPE = Recipe(
    name="echo",
    description="test",
    stages=(RecipeStage(id="echo", skill="echo", tier="local", output=("a.json",)),),
    on_feedback="echo",
    default_goal="用兩三句話說明這篇文章在講什麼",
)
SSE = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1",'
    '"type":"message","role":"assistant","content":[],'
    '"usage":{"input_tokens":1000,"output_tokens":1}}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    '"delta":{"text":"好"}}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":200}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
).encode()
CHAT_BODY = json.dumps(
    {
        "model": "strong",
        "max_tokens": 1024,
        "stream": True,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "嗨"}]}],
    }
).encode()


class IntakeDesk:
    """照真的投入開單；讀狀態與報告只讀 `job.md`，不跑工單。"""

    def __init__(self, data: DataDir) -> None:
        self.data = data
        self.opened: list[dict[str, Any]] = []

    def open_analysis(
        self,
        *,
        conversation_id: str,
        url: str | None,
        text: str | None,
        goal: str | None,
        before_create: Callable[[], None],
    ) -> dict[str, Any]:
        self.opened.append({"conversation_id": conversation_id, "url": url})
        if url is not None:
            return {"status": "unavailable", "reason": "付費牆"}
        intake = open_job(
            self.data,
            RECIPE,
            PastedText(text or ""),
            goal=goal,
            now=NOW,
            conversation=conversation_id,
            before_create=before_create,
        )
        return {
            "job_id": intake.job.id,
            "created": intake.created,
            "status": intake.job.status,
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = read_job(self.data.job_dir(job_id) / "job.md").job
        return {"job_id": job_id, "status": job.status}

    def report(self, job_id: str) -> dict[str, Any]:
        return {**self.status(job_id), "files": {"report.md": f"{job_id} 的報告"}}


@dataclass
class Backend:
    """假的 Anthropic：記下收到幾次，一律回同一段串流。"""

    url: str = ""
    received: int = 0

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/messages")
        async def messages(request: Request) -> Response:
            await request.body()
            self.received += 1
            return Response(SSE, media_type="text/event-stream")

        return app


Serve = Callable[[Any], AbstractContextManager[str]]


@dataclass
class Env:
    serve: Serve
    config: GatewayConfig
    data: DataDir
    stages: ActiveStages
    desk: IntakeDesk
    backend: Backend
    mine: ConversationKey
    other: ConversationKey
    url: str = ""
    exits: list[Callable[[], None]] = field(default_factory=list)

    @contextmanager
    def gateway(self, **changes: Any) -> Iterator[str]:
        """開一個閘道；同一個資料目錄可以開好幾次（模擬 `ef serve` 重開）。"""
        config = replace(self.config, **changes)
        with self.serve(create_app(config, self.stages, desk=self.desk)) as url:
            yield url

    def call(self, tool: str, key: object, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}",
            json=body,
            headers={"x-api-key": str(key)},
            timeout=10,
        )

    def message(self, key: object, url: str | None = None) -> httpx.Response:
        return httpx.post(
            f"{url or self.url}/v1/messages",
            content=CHAT_BODY,
            headers={"x-api-key": str(key), "content-type": "application/json"},
            timeout=10,
        )

    def finish(self, job_id: str) -> None:
        """把工單改成跑完（待審）：執行器會做的事，這裡直接寫 `job.md`。"""
        path = self.data.job_dir(job_id) / "job.md"
        job = read_job(path).job
        done = job.model_copy(
            update={
                "status": "review",
                "result": JobResult(outcome="complete", reason="跑完"),
            }
        )
        write_job(path, JobDocument(job=done))


@pytest.fixture
def env(tmp_path: Path, serve: Serve) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "anthropic").write_text(FAKE_KEY + "\n", encoding="utf-8")
    mine = ConversationKey(conversations.create(data, NOW).id, 1)
    other = ConversationKey(conversations.create(data, NOW).id, 1)
    stages = ActiveStages()
    stages.activate(mine)
    stages.activate(other)
    backend = Backend()
    with serve(backend.app()) as backend_url:
        backend.url = backend_url
        config = GatewayConfig(
            data=data,
            mode="live",
            anthropic_url=backend_url,
            secrets_dir=secrets,
            clock=lambda: NOW,
        )
        built = Env(serve, config, data, stages, IntakeDesk(data), backend, mine, other)
        with built.gateway() as url:
            built.url = url
            yield built


def _opened(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert isinstance(job_id, str)
    return job_id


def _conversation_ledger(data: DataDir, key: ConversationKey) -> list[LedgerEntry]:
    path = data.conversation_dir(key.conversation_id) / "ledger.jsonl"
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line) for line in path.read_text().splitlines()
    ]


# --- analyze ---------------------------------------------------------------


def test_analyze_開了就回_並記在這個對話的_jobs_jsonl(env: Env) -> None:
    job_id = _opened(env.call("analyze", env.mine, text="# 一篇文章\n\n內容"))
    rows = conversations.jobs(env.data, env.mine.conversation_id)
    assert [(r.job_id, r.version, r.parent) for r in rows] == [(job_id, 1, None)]
    assert conversations.jobs(env.data, env.other.conversation_id) == []
    assert env.desk.opened == [
        {"conversation_id": env.mine.conversation_id, "url": None}
    ]


def test_讀不到的網址不開工_原因交回對話(env: Env) -> None:
    response = env.call("analyze", env.mine, url="https://paywalled.example/")
    assert response.json() == {"status": "unavailable", "reason": "付費牆"}
    assert conversations.jobs(env.data, env.mine.conversation_id) == []


@pytest.mark.promise("NFR-SEC-02")
def test_兩個對話丟同一篇文章_各開各的單_各自讀不到對方的(env: Env) -> None:
    article = "# 同一篇\n\n一模一樣的內容"
    mine = _opened(env.call("analyze", env.mine, text=article))
    other = _opened(env.call("analyze", env.other, text=article))
    assert mine != other
    assert env.call("read_report", env.mine, job_id=other).status_code == 403
    assert env.call("read_report", env.other, job_id=mine).status_code == 403


@pytest.mark.parametrize("finished", [True, False], ids=["跑完了", "還在跑"])
def test_同一個對話再丟同一篇_拿回同一張_jobs_jsonl_不重複記(
    env: Env, finished: bool
) -> None:
    first = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    if finished:
        env.finish(first)
    # 還在跑也一樣拿回那一張：同時在跑的上限只擋「要開新單」，不擋去重（連按兩下）
    again = env.call("analyze", env.mine, text="# 一篇\n\n內容")
    assert again.status_code == 200, again.text
    assert again.json()["job_id"] == first and again.json()["created"] is False
    assert len(conversations.jobs(env.data, env.mine.conversation_id)) == 1


def test_沒有對話資料夾的鑰匙不能開單(env: Env) -> None:
    ghost = ConversationKey("c-no-such-conversation", 1)
    env.stages.activate(ghost)
    response = env.call("analyze", ghost, text="# 一篇\n\n內容")
    assert response.status_code == 403
    assert env.desk.opened == []
    assert not env.data.conversation_dir(ghost.conversation_id).exists()


# --- 範圍 -------------------------------------------------------------------


def test_讀得到自己開的報告(env: Env) -> None:
    job_id = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    response = env.call("read_report", env.mine, job_id=job_id)
    assert response.status_code == 200
    assert response.json()["files"] == {"report.md": f"{job_id} 的報告"}


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize("tool", ["read_report", "job_status"])
def test_讀別的對話的工單被拒(env: Env, tool: str) -> None:
    theirs = _opened(env.call("analyze", env.other, text="# 別人的\n\n內容"))
    response = env.call(tool, env.mine, job_id=theirs)
    assert response.status_code == 403
    # 不說那張單存不存在
    assert response.json() == env.call(tool, env.mine, job_id="no-such-job").json()


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    ("tool", "body"),
    [
        ("read_file", {"path": "job.md"}),
        ("list_files", {"path": "."}),
        ("write_file", {"path": "stages/x/1/a.md", "content": "x"}),
        ("fetch_url", {"url": "https://example.com/"}),
        ("web_search", {"query": "x"}),
        ("run_script", {"script": "x.py"}),
        ("no_such_tool", {}),
    ],
)
def test_對話不能用段的工具(env: Env, tool: str, body: dict[str, object]) -> None:
    response = env.call(tool, env.mine, **body)
    assert response.status_code == 403
    assert "對話不能用" in response.json()["error"]


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    ("tool", "body"),
    [
        ("analyze", {"text": "# 一篇\n\n內容"}),
        ("read_report", {"job_id": "x"}),
        ("job_status", {"job_id": "x"}),
    ],
)
def test_段的鑰匙不能用對話的工具(env: Env, tool: str, body: dict[str, object]) -> None:
    key = JobKey("20261013-000000-aaaa1111", "echo", 1)
    env.stages.activate(key)
    response = env.call(tool, key, **body)
    assert response.status_code == 403
    assert env.desk.opened == []


# --- 鑰匙換代與撤銷 ---------------------------------------------------------


def test_接回之後舊鑰匙被拒_新鑰匙可以用(env: Env) -> None:
    relaunched = ConversationKey(env.mine.conversation_id, 2)
    env.stages.activate(relaunched)
    assert env.call("analyze", env.mine, text="# a\n\nb").status_code == 403
    assert env.message(env.mine).status_code == 403
    assert env.call("analyze", relaunched, text="# a\n\nb").status_code == 200


def test_Pi_結束撤銷之後鑰匙被拒(env: Env) -> None:
    env.stages.deactivate(env.mine)
    assert env.call("analyze", env.mine, text="# a\n\nb").status_code == 403
    assert env.message(env.mine).status_code == 403
    # 別的對話不受影響
    assert env.call("analyze", env.other, text="# a\n\nb").status_code == 200


# --- 同一時間在跑的工單 -----------------------------------------------------


def test_對話已有一張工單在跑_第二張被拒_跑完才能再開(env: Env) -> None:
    first = _opened(env.call("analyze", env.mine, text="# 第一篇\n\n內容"))
    busy = env.call("analyze", env.mine, text="# 第二篇\n\n內容")
    assert busy.status_code == 409
    assert first in busy.json()["error"]
    assert len(conversations.jobs(env.data, env.mine.conversation_id)) == 1
    # 別的對話不受影響
    _opened(env.call("analyze", env.other, text="# 第二篇\n\n內容"))
    env.finish(first)
    _opened(env.call("analyze", env.mine, text="# 第二篇\n\n內容"))


def test_同一時間在跑的工單上限可以設定(env: Env) -> None:
    with env.gateway(chat_max_running_jobs=2) as url:
        env.url = url
        _opened(env.call("analyze", env.mine, text="# 第一篇\n\n內容"))
        _opened(env.call("analyze", env.mine, text="# 第二篇\n\n內容"))
        assert env.call("analyze", env.mine, text="# 第三篇\n\n內容").status_code == 409


# --- 帳與預算 ---------------------------------------------------------------


@pytest.mark.promise("NFR-COST-01")
def test_對話的模型呼叫記在對話的帳本_不在_jobs_底下(env: Env) -> None:
    response = env.message(env.mine)
    assert response.status_code == 200, response.text
    rows = _conversation_ledger(env.data, env.mine)
    assert [(r.conversation_id, r.job_id, r.stage, r.attempt) for r in rows] == [
        (env.mine.conversation_id, None, "chat", 1)
    ]
    assert rows[0].cost_usd > 0
    # 冒號不進任何路徑：jobs/ 底下不會長出 conv:… 的資料夾
    assert not any(":" in p.name for p in env.data.root.rglob("*"))


@pytest.mark.promise("NFR-COST-01")
def test_對話預算用完_不轉送(env: Env) -> None:
    conversation = conversations.read(env.data, env.mine.conversation_id)
    conversations.save(env.data, conversation.model_copy(update={"budget_usd": 0.0001}))
    response = env.message(env.mine)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert env.backend.received == 0


@pytest.mark.promise("NFR-COST-01")
def test_讀不到_conversation_json_預算當成_0_不轉送(env: Env) -> None:
    path = env.data.conversation_dir(env.mine.conversation_id) / "conversation.json"
    path.write_text("{壞掉", encoding="utf-8")
    response = env.message(env.mine)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert env.backend.received == 0


def test_對話的預算預設_1_美元() -> None:
    assert conversations.DEFAULT_BUDGET_USD == 1.0


def test_對話不走模型快取_閘道重開後同樣的請求照樣轉送(env: Env) -> None:
    assert env.message(env.mine).status_code == 200
    with env.gateway() as restarted:
        assert env.message(env.mine, restarted).status_code == 200
    assert env.backend.received == 2
    statuses = [r.status for r in _conversation_ledger(env.data, env.mine)]
    assert statuses == ["ok", "ok"]
