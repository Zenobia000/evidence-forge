"""給畫面的 API（`chat/api.py`）：對話清單、送訊息、SSE 事件串流、完整紀錄，只接受本機的請求。

真的開在 127.0.0.1 的空埠上；Pi 是假 Pi（`tests/chat_world.py`）。
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_world import fake_chat_harness

from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


@pytest.fixture
def api(tmp_path: Path, serve: Any) -> Iterator[str]:
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
            yield url
    finally:
        # 先收主機：SSE 串流收到結束，伺服器才關得掉
        host.close()


def _events(url: str, conversation_id: str, until: str, after_open: Any = None) -> list[dict[str, Any]]:
    """讀 SSE，直到收到 `type == until` 的事件。"""
    got: list[dict[str, Any]] = []
    with httpx.stream("GET", f"{url}/api/conversations/{conversation_id}/events", timeout=10) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            got.append(event)
            if len(got) == 1 and after_open is not None:
                after_open()
            if event.get("type") == until:
                return got
    raise AssertionError("串流先結束了")


def test_開對話_送訊息_串流收得到回答_完整紀錄拿得到(api: str) -> None:
    created = httpx.post(f"{api}/api/conversations", json={})
    assert created.status_code == 201
    cid = created.json()["id"]
    assert created.json()["title"] == "新對話"
    assert created.json()["pi_running"] is False

    def send() -> None:
        sent = httpx.post(f"{api}/api/conversations/{cid}/messages", json={"text": "嗨"}, timeout=10)
        assert sent.status_code == 202, sent.text
        assert sent.json() == {"disposition": "started"}

    events = _events(api, cid, "agent_settled", after_open=send)
    types = [e["type"] for e in events]
    assert types[0] == "ef_pi" and events[0]["state"] == "stopped"
    assert {"type": "ef_pi", "state": "running", "launch": 1, "reason": None, "busy": False} in events
    ends = [e["message"] for e in events if e["type"] == "message_end"]
    assert [m["role"] for m in ends] == ["user", "assistant"]

    history = httpx.get(f"{api}/api/conversations/{cid}/messages", timeout=10).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    listed = httpx.get(f"{api}/api/conversations").json()["conversations"]
    assert [c["id"] for c in listed] == [cid]
    assert listed[0]["pi_running"] is True
    detail = httpx.get(f"{api}/api/conversations/{cid}").json()
    assert detail["jobs"] == [] and detail["job_count"] == 0


def test_錯誤都說清楚(api: str) -> None:
    cid = httpx.post(f"{api}/api/conversations", json={}).json()["id"]
    missing = httpx.post(f"{api}/api/conversations/c-nope/messages", json={"text": "嗨"})
    assert missing.status_code == 404
    assert httpx.get(f"{api}/api/conversations/c-nope/events").status_code == 404
    assert httpx.get(f"{api}/api/conversations/..%2F..%2Fetc").status_code == 404
    slash = httpx.post(f"{api}/api/conversations/{cid}/messages", json={"text": "/ef-job-done x"})
    assert slash.status_code == 400 and "/ 開頭" in slash.json()["error"]
    no_text = httpx.post(f"{api}/api/conversations/{cid}/messages", json={"msg": "嗨"})
    assert no_text.status_code == 400


@pytest.mark.promise("NFR-SEC-02")
def test_只接受本機的請求_擋跨站與_DNS_rebinding(api: str) -> None:
    cid = httpx.post(f"{api}/api/conversations", json={}).json()["id"]
    target = f"{api}/api/conversations/{cid}/messages"
    # 別的網站用不需要預檢的表單偷送
    form = httpx.post(target, content="text=嗨", headers={"content-type": "text/plain"})
    assert form.status_code == 415
    # 瀏覽器帶著別的網站的 Origin
    cross = httpx.post(target, json={"text": "嗨"}, headers={"origin": "https://evil.example"})
    assert cross.status_code == 403
    # 別的網域解析到 127.0.0.1（DNS rebinding）：Host 不是本機
    rebound = httpx.get(f"{api}/api/conversations", headers={"host": "evil.example"})
    assert rebound.status_code == 403
    # 同一台機器的網頁可以
    same = httpx.get(f"{api}/api/conversations", headers={"origin": "http://127.0.0.1:8790"})
    assert same.status_code == 200
    assert httpx.get(f"{api}/api/conversations/{cid}").json()["pi_running"] is False
