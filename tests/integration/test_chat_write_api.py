"""畫面的「照這張編輯卡寫稿」（`POST /api/conversations/<id>/cards/<卡>/write`，ADR-0025 §3.3、#110）。

創作者看過最後的確認卡按下去：畫面送確認時看到的事件筆數，對話主機交給工作台開寫稿單。
對話的 agent 叫不到（閘道的 `/tools/` 沒有它），只有畫面能叫。

真的 API 開在 127.0.0.1 的空埠上；Pi 是假 Pi。不打模型、不跑工單。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from card_world import NOW, decided_card
from chat_world import fake_chat_harness

from evidence_forge import cards, conversations
from evidence_forge.chat.api import create_app
from evidence_forge.chat.desk import Desk
from evidence_forge.chat.host import ConversationHost
from evidence_forge.documents import read_job
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import default_harness
from evidence_forge.models import TIER_ORDER
from evidence_forge.paths import DataDir
from evidence_forge.runner.recipes import load_recipes

RECIPES = load_recipes(default_harness(), TIER_ORDER)


@dataclass
class World:
    url: str
    data: DataDir

    def write(self, cid: str, card_id: str, body: Any) -> httpx.Response:
        return httpx.post(f"{self.url}/api/conversations/{cid}/cards/{card_id}/write", json=body, timeout=10)


def _world(tmp_path: Path, serve: Any, *, desk: bool) -> Iterator[World]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.authors / "me").mkdir(parents=True)
    (data.authors / "me" / "voice.md").write_text("---\nid: me\n---\n\n聲音。\n", encoding="utf-8")
    host = ConversationHost(
        data=data,
        harness=fake_chat_harness(tmp_path / "harness"),
        stages=ActiveStages(),
        gateway_url="http://127.0.0.1:9",
        recipes=RECIPES,
        clock=lambda: NOW,
    )
    if desk:
        host.use_desk(Desk(data, RECIPES, "analyze", clock=lambda: NOW, budget_usd=0.5, on_opened=host.job_opened))
    host.start()
    try:
        with serve(create_app(host)) as url:
            yield World(url, data)
    finally:
        host.close()


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[World]:
    yield from _world(tmp_path, serve, desk=True)


@pytest.mark.promise("FR-RW-01", "FR-UI-13")
def test_畫面按照這張編輯卡寫稿_開一張寫稿單_記進對話(world: World) -> None:
    cid = conversations.create(world.data, NOW).id
    card_id = decided_card(world.data, cid, genre="explainer", theme="tools")
    shown = cards.read_card(world.data, cid, card_id).events

    response = world.write(cid, card_id, {"confirmed_events": shown})

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["persona"], body["genre"], body["theme"], body["card_id"]) == ("me", "explainer", "tools", card_id)
    job = read_job(world.data.job_dir(body["job_id"]) / "job.md").job
    assert (job.recipe, job.card) == ("rewrite", card_id)
    assert [row.job_id for row in conversations.jobs(world.data, cid)] == [body["job_id"]]


def test_確認之後卡又改過_409_不開單(world: World) -> None:
    cid = conversations.create(world.data, NOW).id
    card_id = decided_card(world.data, cid)
    shown = cards.read_card(world.data, cid, card_id).events
    cards.reask_station(world.data, cid, card_id, 2, now=NOW)

    response = world.write(cid, card_id, {"confirmed_events": shown})

    assert response.status_code == 409
    assert conversations.jobs(world.data, cid) == []


def test_別的對話的卡_404_不說存不存在(world: World) -> None:
    cid = conversations.create(world.data, NOW).id
    other = conversations.create(world.data, NOW).id
    card_id = decided_card(world.data, cid)

    response = world.write(other, card_id, {"confirmed_events": 1})

    assert response.status_code == 404


@pytest.mark.parametrize("body", [{}, {"confirmed_events": "15"}, [1]])
def test_沒給確認時的筆數_400(world: World, body: Any) -> None:
    cid = conversations.create(world.data, NOW).id
    card_id = decided_card(world.data, cid)

    assert world.write(cid, card_id, body).status_code == 400


def test_沒有接上工作台_503(tmp_path: Path, serve: Any) -> None:
    for world in _world(tmp_path, serve, desk=False):
        cid = conversations.create(world.data, NOW).id
        card_id = decided_card(world.data, cid)
        assert world.write(cid, card_id, {"confirmed_events": 1}).status_code == 503
