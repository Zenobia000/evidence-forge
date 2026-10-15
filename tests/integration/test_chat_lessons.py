"""畫面 ↔ lessons 的提議（`chat/api.py`、`chat/host.py`、`ui/`，ADR-0026 §3.3、決定 G、FR-RW-04、#114）。

- agent 提議（閘道的 `propose_lesson`，這裡直接叫 `lesson_proposals.propose`），畫面在對話裡放一張「同意／不要」的小卡。
- 創作者按「同意」：由對話主機（閘道這一側）把一行附加到 `authors/<角色>/lessons.md`；「不要」什麼都不寫。
- 按完對這個對話的 Pi 送 `/ef-lesson`（只放通知、不開一輪）；同意兩次只寫一次、只通知一次。
- 只碰得到這個對話自己的提議；別的對話的、不存在的一律 403。
- 角色頁（#116）讀的就是同一份 `lessons.md`，同意過的會出現在最近十筆。

真的 API 開在 127.0.0.1 的空埠上；Pi 是假 Pi（`tests/chat_world.py`）。不打模型。角色是虛構的 `nightowl`。
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_folder import RECIPES, ChatFolder, at
from chat_world import fake_chat_harness, wait_until

from evidence_forge import lesson_proposals
from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.chat.pi_rpc import session_dir
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.lessons import latest_lessons

# 提議的日子（假的時鐘 `at(1)`）：寫進 lessons.md 的日期是提議的那天
DAY = at(1).date().isoformat()
RULE = "第 2 篇｜開頭用了問句 → 開頭直接講結論"
WHY = "創作者說：我不要問句開頭"


@dataclass
class World:
    url: str
    folder: ChatFolder
    host: ConversationHost

    @property
    def lessons(self) -> Path:
        return self.folder.data.authors / "nightowl" / "lessons.md"

    def conversation(self) -> str:
        return self.folder.conversation()

    def propose(self, cid: str, text: str = RULE) -> str:
        """agent 提議一筆（閘道的 `propose_lesson` 就是叫這個）；回提議編號。"""
        written = lesson_proposals.propose(self.folder.data, cid, "nightowl", text, WHY, now=at(1))
        self.host.lesson_changed(cid, written.events)
        return written.proposal.id

    def decide(self, cid: str, proposal: str, action: str) -> httpx.Response:
        return httpx.post(f"{self.url}/api/conversations/{cid}/lessons/{proposal}/{action}", json={}, timeout=10)

    def notices(self, cid: str) -> list[str]:
        path = session_dir(self.folder.data, cid) / "commands.jsonl"
        if not path.is_file():
            return []
        sent = [json.loads(line).get("message", "") for line in path.read_text(encoding="utf-8").splitlines() if line]
        return [m for m in sent if m.startswith("/ef-lesson ")]

    def thread(self, cid: str) -> str:
        return httpx.get(f"{self.url}/ui/c/{cid}/thread", timeout=15).text


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[World]:
    folder = ChatFolder(tmp_path / "data")
    persona = folder.data.authors / "nightowl"
    persona.mkdir(parents=True)
    (persona / "voice.md").write_text("夜貓的聲音\n", encoding="utf-8")
    host = ConversationHost(
        data=folder.data,
        harness=fake_chat_harness(tmp_path / "harness"),
        stages=ActiveStages(),
        gateway_url="http://127.0.0.1:9",
        recipes=RECIPES,
        clock=lambda: at(30),
    )
    host.start()
    try:
        with serve(create_app(host, data=folder.data, recipes=RECIPES)) as url:
            yield World(url, folder, host)
    finally:
        host.close()


def _stream(
    url: str, cid: str, done: Callable[[list[dict[str, Any]]], bool], after_open: Callable[[], None] | None = None
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


@pytest.mark.promise("FR-RW-04")
def test_按同意_閘道才把那一行寫進角色的_lessons_再通知對話(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)
    assert not world.lessons.exists()

    response = world.decide(cid, proposal, "approve")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposal"]["status"] == "approved"
    assert [(e["type"], e["source"]) for e in body["events"]] == [("approved", "ui")]
    assert world.lessons.read_text(encoding="utf-8") == f"- {DAY}｜" + RULE + "\n"
    assert latest_lessons(world.folder.data, "nightowl") == [f"{DAY}｜" + RULE]
    wait_until(lambda: world.notices(cid) == [f"/ef-lesson {proposal} approved nightowl"], what="對話的 Pi 收到 /ef-lesson")


@pytest.mark.promise("FR-RW-04")
def test_連按兩次同意_只寫一次_也只通知一次(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)
    world.decide(cid, proposal, "approve")
    wait_until(lambda: len(world.notices(cid)) == 1, what="第一則 /ef-lesson")

    again = world.decide(cid, proposal, "approve")

    assert again.status_code == 200 and again.json()["events"] == []
    assert world.lessons.read_text(encoding="utf-8").count(RULE) == 1
    assert len(world.notices(cid)) == 1


@pytest.mark.promise("FR-RW-04")
def test_按不要_什麼都不寫_通知對話_之後不能再同意(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)

    response = world.decide(cid, proposal, "reject")

    assert response.status_code == 200 and response.json()["proposal"]["status"] == "rejected"
    assert not world.lessons.exists()
    wait_until(lambda: world.notices(cid) == [f"/ef-lesson {proposal} rejected nightowl"], what="對話的 Pi 收到 /ef-lesson")
    assert world.decide(cid, proposal, "approve").status_code == 409
    assert not world.lessons.exists()


@pytest.mark.promise("FR-RW-04")
def test_別的對話的提議與不存在的提議_畫面也是_403_什麼都不寫(world: World) -> None:
    mine = world.conversation()
    other = world.conversation()
    proposal = world.propose(mine)

    cross = world.decide(other, proposal, "approve")
    cross_reject = world.decide(other, proposal, "reject")
    missing = world.decide(mine, "L99", "approve")
    no_conversation = world.decide("c-nope", proposal, "approve")
    odd_action = world.decide(mine, proposal, "delete")

    assert (cross.status_code, cross_reject.status_code, missing.status_code) == (403, 403, 403)
    assert cross.json()["error"] == missing.json()["error"]
    assert no_conversation.status_code == 404
    assert odd_action.status_code == 404
    assert not world.lessons.exists()
    assert world.notices(mine) == [] and world.notices(other) == []
    assert lesson_proposals.read_proposals(world.folder.data, mine)[0].status == "pending"


def test_角色在提議之後不見了_同意回錯誤_提議留著_沒通知(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)
    (world.folder.data.authors / "nightowl" / "voice.md").unlink()
    (world.folder.data.authors / "nightowl").rmdir()

    response = world.decide(cid, proposal, "approve")

    assert response.status_code == 404
    assert not world.lessons.exists()
    assert lesson_proposals.read_proposals(world.folder.data, cid)[0].status == "pending"
    assert world.notices(cid) == []


def test_列出這個對話的提議_帶同意的話會寫進去的那一行(world: World) -> None:
    cid = world.conversation()
    world.propose(cid)

    listed = httpx.get(f"{world.url}/api/conversations/{cid}/lessons", timeout=10).json()

    assert [(p["id"], p["status"], p["persona"], p["text"], p["why"]) for p in listed["proposals"]] == [
        ("L1", "pending", "nightowl", RULE, WHY)
    ]
    assert listed["proposals"][0]["preview"] == f"- {DAY}｜" + RULE
    other = httpx.get(f"{world.url}/api/conversations/{world.conversation()}/lessons", timeout=10).json()
    assert other == {"proposals": []}


def test_對話裡有一張小卡_兩顆按鈕_說清楚按同意才會寫(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)

    html = world.thread(cid)

    assert f'data-lesson="{proposal}"' in html
    assert RULE in html and WHY in html
    assert 'data-lesson-action="approve"' in html and 'data-lesson-action="reject"' in html
    assert "按同意才會寫進 nightowl 的 lessons" in html and "agent 不能替你按" in html


@pytest.mark.promise("FR-RW-04")
def test_決定之後小卡變成一行系統小字_同意的在角色頁看得到(world: World) -> None:
    cid = world.conversation()
    approved = world.propose(cid)
    dismissed = world.propose(cid, "第 3 篇｜結尾寫了口號 → 結尾收在一個動作")
    world.decide(cid, approved, "approve")
    world.decide(cid, dismissed, "reject")

    html = world.thread(cid)
    page = httpx.get(f"{world.url}/personas/nightowl", timeout=15)

    assert "data-lesson=" not in html
    assert "你同意了這筆 lesson，已寫進「nightowl」的 lessons" in html
    assert "你不要這筆 lesson，沒有寫任何東西" in html
    assert page.status_code == 200
    assert RULE in page.text
    assert "結尾收在一個動作" not in page.text


def test_訂閱時先送現況_提議與決定都推_ef_lesson(world: World) -> None:
    cid = world.conversation()
    proposal = world.propose(cid)

    def approve() -> None:
        world.decide(cid, proposal, "approve")

    got = _stream(
        world.url,
        cid,
        lambda events: any(e["type"] == "ef_lesson" and e["events"] for e in events),
        after_open=approve,
    )

    lesson_events = [e for e in got if e["type"] == "ef_lesson"]
    # 現況：還在等；之後：同意
    assert lesson_events[0]["events"] == [] and lesson_events[0]["proposals"][0]["status"] == "pending"
    assert [e["type"] for e in lesson_events[-1]["events"]] == ["approved"]
    assert lesson_events[-1]["proposals"][0]["status"] == "approved"
    assert lesson_events[-1]["seq"] > lesson_events[0]["seq"]
