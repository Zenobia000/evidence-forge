"""畫面 ↔ 編輯卡（`chat/api.py`、`chat/host.py`，ADR-0025 §3.3、FR-UI-13、FR-IW-03、#107）。

- 創作者點的東西（送出一關、沿用、重問）由畫面經對話主機直接寫進決策紀錄（`source: ui`），不經 agent 轉述。
- 寫完對這個對話的 Pi 送 `/ef-card`（像 `/ef-job-done`），ef-tools 放一則 `[ef-card]` 通知叫 agent 接著往下。
- 卡一變就推 `ef_card` 給訂閱的畫面；訂閱時先送每張卡的現況。
- 只碰得到這個對話自己的卡。

真的 API 開在 127.0.0.1 的空埠上；Pi 是假 Pi（`tests/chat_world.py`）。不打模型。
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_world import fake_chat_harness, wait_until

from evidence_forge import cards, conversations
from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.chat.pi_rpc import session_dir
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
QUESTIONS = [
    {
        "header": "文體",
        "question": "這篇的主證據是什麼？",
        "options": [
            {"label": "explainer", "description": "把計價方式講清楚", "value": "explainer"},
            {"label": "trend", "description": "兩年內這筆帳會怎麼變", "value": "trend"},
        ],
    }
]

# 第 2 關以後的問題卡：選項不帶文體的 value
VIEWPOINT = [
    {
        "header": "觀點",
        "question": "讀者看完要記住哪一件事？",
        "options": [
            {"label": "explainer", "description": "成本是架構問題"},
            {"label": "trend", "description": "價目一直在降"},
        ],
    }
]


@dataclass
class World:
    url: str
    data: DataDir
    host: ConversationHost

    def conversation(self) -> str:
        return conversations.create(self.data, NOW).id

    def card(self, cid: str, station_count: int = 1) -> str:
        """agent 開一張卡，第 1 關問一張卡；`station_count` > 1 就一路送出到那一關再問下一關。"""
        card_id = cards.propose_questions(self.data, cid, None, 1, QUESTIONS, now=NOW).card_id
        for station in range(1, station_count):
            self.submit(cid, card_id, station)
            cards.propose_questions(self.data, cid, card_id, station + 1, VIEWPOINT, now=NOW)
        return card_id

    def submit(self, cid: str, card_id: str, station: int, choice: str = "explainer") -> httpx.Response:
        state = cards.read_card(self.data, cid, card_id).stations[station - 1]
        question = next(q.id for q in state.questions if q.current)
        return httpx.post(
            f"{self.url}/api/conversations/{cid}/cards/{card_id}/stations/{station}/submit",
            json={"answers": [{"question": question, "choice": choice, "note": "就這個"}]},
            timeout=10,
        )

    def action(self, cid: str, card_id: str, station: int, name: str) -> httpx.Response:
        return httpx.post(
            f"{self.url}/api/conversations/{cid}/cards/{card_id}/stations/{station}/{name}",
            json={},
            timeout=10,
        )

    def commands(self, cid: str) -> list[str]:
        path = session_dir(self.data, cid) / "commands.jsonl"
        if not path.is_file():
            return []
        return [
            str(json.loads(line).get("message", ""))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def card_notices(self, cid: str) -> list[str]:
        return [m for m in self.commands(cid) if m.startswith("/ef-card ")]


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[World]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    host = ConversationHost(
        data=data,
        harness=fake_chat_harness(tmp_path / "harness"),
        stages=ActiveStages(),
        gateway_url="http://127.0.0.1:9",
        recipes={},
        clock=lambda: NOW,
    )
    host.start()
    try:
        with serve(create_app(host)) as url:
            yield World(url, data, host)
    finally:
        host.close()


def _stream(
    url: str, cid: str, done: Callable[[list[dict[str, Any]]], bool], after_open: Any = None
) -> list[dict[str, Any]]:
    """讀 SSE，直到 `done(收到的事件)` 成立。"""
    got: list[dict[str, Any]] = []
    with httpx.stream("GET", f"{url}/api/conversations/{cid}/events", timeout=10) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            got.append(json.loads(line[len("data: ") :]))
            if len(got) == 1 and after_open is not None:
                after_open()
            if done(got):
                return got
    raise AssertionError("串流先結束了")


@pytest.mark.promise("FR-UI-13", "FR-IW-03")
def test_畫面送出一關_直接寫進決策紀錄_來源是畫面_再通知對話(world: World) -> None:
    cid = world.conversation()
    card_id = world.card(cid)

    response = world.submit(cid, card_id, 1)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(e["type"], e["source"], e["choice"], e["note"]) for e in body["events"]] == [
        ("answered", "ui", "explainer", "就這個")
    ]
    assert body["card"]["stations"][0]["status"] == "decided"
    last = body["events"][-1]["n"]
    wait_until(
        lambda: world.card_notices(cid) == [f"/ef-card {card_id} {last} submit 1"],
        what="對話的 Pi 收到 /ef-card",
    )


def test_重問與沿用也由畫面直接記_各通知一次(world: World) -> None:
    cid = world.conversation()
    card_id = world.card(cid, station_count=2)
    world.submit(cid, card_id, 2)

    reask = world.action(cid, card_id, 1, "reask")
    assert reask.status_code == 200, reask.text
    assert [(e["type"], e["source"]) for e in reask.json()["events"]] == [("reasked", "ui")]
    world.submit(cid, card_id, 1, "trend")
    keep = world.action(cid, card_id, 2, "keep")
    assert keep.status_code == 200, keep.text
    assert [(e["type"], e["source"]) for e in keep.json()["events"]] == [("kept", "ui")]
    assert keep.json()["card"]["stations"][1]["status"] == "kept"

    wait_until(lambda: len(world.card_notices(cid)) == 5, what="五次點選各通知一次")
    actions = [notice.split()[3:] for notice in world.card_notices(cid)]
    assert actions[-3:] == [["reask", "1"], ["submit", "1"], ["keep", "2"]]


def test_畫面點不合規則的_回錯誤_什麼都不寫_也不通知(world: World) -> None:
    cid = world.conversation()
    card_id = world.card(cid)

    keep = world.action(cid, card_id, 1, "keep")
    bad_station = world.action(cid, card_id, 9, "reask")
    wrong_choice = httpx.post(
        f"{world.url}/api/conversations/{cid}/cards/{card_id}/stations/1/submit",
        json={"answers": [{"question": "q1", "choice": "沒有這個"}]},
        timeout=10,
    )

    assert (keep.status_code, bad_station.status_code, wrong_choice.status_code) == (409, 400, 400)
    assert "error" in keep.json()
    assert [e["type"] for e in cards.read_events(world.data, cid, card_id)] == ["questions_proposed"]
    assert world.card_notices(cid) == []


@pytest.mark.promise("FR-IW-03")
def test_別的對話的卡_畫面也碰不到(world: World) -> None:
    mine = world.conversation()
    other = world.conversation()
    card_id = world.card(mine)

    detail = httpx.get(f"{world.url}/api/conversations/{other}/cards/{card_id}", timeout=10)
    submit = httpx.post(
        f"{world.url}/api/conversations/{other}/cards/{card_id}/stations/1/submit",
        json={"answers": [{"question": "q1", "choice": "explainer"}]},
        timeout=10,
    )
    # 編碼過的 `..`：httpx 不會先替它正規化，要靠卡 id 的字元規則擋
    traversal = httpx.get(f"{world.url}/api/conversations/{other}/cards/%2E%2E", timeout=10)

    assert (detail.status_code, submit.status_code) == (404, 404)
    assert traversal.status_code == 400, traversal.text
    assert [e["type"] for e in cards.read_events(world.data, mine, card_id)] == ["questions_proposed"]


def test_讀卡_列出這個對話的卡_單張帶完整決策紀錄(world: World) -> None:
    cid = world.conversation()
    card_id = world.card(cid)
    world.submit(cid, card_id, 1)

    listed = httpx.get(f"{world.url}/api/conversations/{cid}/cards", timeout=10).json()
    detail = httpx.get(f"{world.url}/api/conversations/{cid}/cards/{card_id}", timeout=10).json()

    assert [c["card_id"] for c in listed["cards"]] == [card_id]
    assert detail["card"]["stations"][0]["decided"] is True
    assert [e["type"] for e in detail["events"]] == ["questions_proposed", "answered"]


@pytest.mark.promise("FR-UI-13")
def test_訂閱先拿到每張卡的現況_之後每次變化推_ef_card(world: World) -> None:
    cid = world.conversation()
    card_id = world.card(cid)

    def has_submit(got: list[dict[str, Any]]) -> bool:
        return any(e["type"] == "ef_card" and e["events"] for e in got)

    got = _stream(world.url, cid, has_submit, after_open=lambda: world.submit(cid, card_id, 1))

    snapshots = [e for e in got if e["type"] == "ef_card" and not e["events"]]
    assert [(e["card_id"], e["card"]["stations"][0]["status"]) for e in snapshots] == [(card_id, "asking")]
    change = next(e for e in got if e["type"] == "ef_card" and e["events"])
    assert change["card_id"] == card_id
    assert [(e["type"], e["source"]) for e in change["events"]] == [("answered", "ui")]
    assert change["card"]["stations"][0]["status"] == "decided"
    assert isinstance(change["seq"], int)


def test_agent_經閘道寫的變化也推給畫面(world: World) -> None:
    cid = world.conversation()

    def proposed(got: list[dict[str, Any]]) -> bool:
        return any(e["type"] == "ef_card" and e["events"] for e in got)

    def agent_writes() -> None:
        # 閘道替 agent 寫完之後，工作台叫的就是這一個（`Desk(on_card=host.card_changed)`）
        written = cards.propose_questions(world.data, cid, None, 1, QUESTIONS, now=NOW)
        world.host.card_changed(cid, written.card_id, written.events)

    got = _stream(world.url, cid, proposed, after_open=agent_writes)

    change = next(e for e in got if e["type"] == "ef_card" and e["events"])
    assert [(e["type"], e["source"]) for e in change["events"]] == [("questions_proposed", "agent")]
    assert change["card"]["stations"][0]["questions"][0]["header"] == "文體"
    # agent 自己寫的不用再通知它自己
    assert world.card_notices(cid) == []
