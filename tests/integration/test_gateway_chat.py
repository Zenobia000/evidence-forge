"""#80 spike：對話鑰匙在閘道的範圍（NFR-SEC-02、chat.feature 的 @security）。

- 對話只能用對話的工具（analyze、read_report、job_status），段的工具一律 403。
- 段的鑰匙不能用對話的工具。
- 對話只讀得到自己 `jobs.jsonl` 裡的工單；別的對話開的一律 403。
- 接回之後只認最新那一把，前一次開的 Pi 拿舊鑰匙來一律 403。
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evidence_forge import conversations
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.gateway.budget import SpendTally, taipei_month
from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


class FakeDesk:
    """不開真的工單：記下被叫了什麼，回固定的工單 id。"""

    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []

    def open_analysis(
        self, *, url: str | None, text: str | None, goal: str | None
    ) -> dict[str, Any]:
        self.opened.append({"url": url, "text": text, "goal": goal})
        if url == "https://paywalled.example/":
            return {"status": "unavailable", "reason": "付費牆"}
        return {"job_id": f"J{len(self.opened)}", "created": True, "status": "queued"}

    def status(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "review", "outcome": "complete"}

    def report(self, job_id: str) -> dict[str, Any]:
        return {**self.status(job_id), "files": {"report.md": f"{job_id} 的報告"}}


@dataclass
class Env:
    url: str
    data: DataDir
    stages: ActiveStages
    desk: FakeDesk
    mine: ConversationKey
    other: ConversationKey

    def call(self, tool: str, key: object, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}",
            json=body,
            headers={"x-api-key": str(key)},
            timeout=10,
        )


@pytest.fixture
def env(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    mine = ConversationKey(conversations.create(data, NOW).id, 1)
    other = ConversationKey(conversations.create(data, NOW).id, 1)
    stages = ActiveStages()
    stages.activate(mine)
    stages.activate(other)
    desk = FakeDesk()
    config = GatewayConfig(
        data=data, mode="replay", replay_dir=tmp_path / "no-recording", clock=lambda: NOW
    )
    with serve(create_app(config, stages, desk=desk)) as url:
        yield Env(url, data, stages, desk, mine, other)


def test_analyze_開了就回_並記在這個對話底下(env: Env) -> None:
    response = env.call("analyze", env.mine, url="https://example.com/a")
    assert response.status_code == 200
    assert response.json() == {"job_id": "J1", "created": True, "status": "queued"}
    assert conversations.jobs(env.data, env.mine.conversation_id) == ["J1"]
    assert conversations.jobs(env.data, env.other.conversation_id) == []


def test_讀不到的網址不開工_原因交回對話(env: Env) -> None:
    response = env.call("analyze", env.mine, url="https://paywalled.example/")
    assert response.json() == {"status": "unavailable", "reason": "付費牆"}
    assert conversations.jobs(env.data, env.mine.conversation_id) == []


def test_讀得到自己開的報告(env: Env) -> None:
    env.call("analyze", env.mine, url="https://example.com/a")
    response = env.call("read_report", env.mine, job_id="J1")
    assert response.status_code == 200
    assert response.json()["files"] == {"report.md": "J1 的報告"}


@pytest.mark.promise("NFR-SEC-02")
def test_讀別的對話的報告被拒(env: Env) -> None:
    env.call("analyze", env.other, url="https://example.com/b")
    for tool in ("read_report", "job_status"):
        response = env.call(tool, env.mine, job_id="J1")
        assert response.status_code == 403, tool


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
    ],
)
def test_對話不能用段的工具(env: Env, tool: str, body: dict[str, object]) -> None:
    response = env.call(tool, env.mine, **body)
    assert response.status_code == 403
    assert "對話不能用" in response.json()["error"]


@pytest.mark.promise("NFR-SEC-02")
def test_段的鑰匙不能開分析(env: Env) -> None:
    key = JobKey("20261013-000000-aaaa1111", "echo", 1)
    env.stages.activate(key)
    response = env.call("analyze", key, url="https://example.com/a")
    assert response.status_code == 403
    assert env.desk.opened == []


def test_接回之後舊鑰匙被拒(env: Env) -> None:
    relaunched = ConversationKey(env.mine.conversation_id, 2)
    env.stages.activate(relaunched)
    old = env.call("analyze", env.mine, url="https://example.com/a")
    assert old.status_code == 403
    assert env.call("analyze", relaunched, url="https://example.com/a").status_code == 200


def test_對話本身的花費算進本月(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    conversation = conversations.create(data, NOW)
    ledger = data.conversation_dir(conversation.id) / "ledger.jsonl"
    ledger.write_text(
        f'{{"ts": "{NOW.isoformat()}", "cost_usd": 0.25}}\n', encoding="utf-8"
    )
    tally = SpendTally(data)
    assert tally.month_micros(taipei_month(NOW)) == 250_000
    assert tally.job_micros(f"conv:{conversation.id}") == 250_000
