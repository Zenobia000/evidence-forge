"""閘道 ↔ lessons 的提議（ADR-0026 §3.3、ADR-0021 §3.4、決定 G、FR-RW-04、#114）。

- 對話的 agent 只能叫 `propose_lesson`：記進這個對話自己的 `lessons-proposed.jsonl`，不寫角色資料夾。
- `approve_lesson`、`reject_lesson` 只有畫面能叫：閘道的 `/tools/` 拿對話鑰匙、工單鑰匙來都 403。
- 提議是這個對話自己的：agent 指不到別的對話；沒有同意之前，角色的 `lessons.md` 一個字都沒動。
- 寫完叫工作台的 `lesson_changed`，畫面據此放小卡。

工作台是真的 `chat/desk.py`（不開單），閘道是真的；不打模型、不連網路。角色是虛構的 `nightowl`。
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evidence_forge import conversations, lesson_proposals
from evidence_forge.chat.desk import Desk
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.gateway.chat_tools import CHAT_TOOLS, UI_ONLY_TOOLS
from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
RULE = "第 2 篇｜開頭用了問句 → 開頭直接講結論"
WHY = "創作者說：我不要問句開頭"


@dataclass
class Env:
    url: str
    data: DataDir
    mine: ConversationKey
    other: ConversationKey
    stage: JobKey
    changed: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)

    @property
    def lessons(self) -> Path:
        return self.data.authors / "nightowl" / "lessons.md"

    def call(self, tool: str, key: object, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}", json=body, headers={"x-api-key": str(key)}, timeout=10
        )

    def propose(
        self, overrides: dict[str, object] | None = None, *, key: ConversationKey | None = None
    ) -> httpx.Response:
        body: dict[str, object] = {"persona": "nightowl", "text": RULE, "why": WHY, **(overrides or {})}
        return self.call("propose_lesson", key or self.mine, **body)


@pytest.fixture
def env(tmp_path: Path, serve: Any) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.authors / "nightowl").mkdir(parents=True)
    (data.authors / "nightowl" / "voice.md").write_text("夜貓的聲音\n", encoding="utf-8")
    mine = ConversationKey(conversations.create(data, NOW).id, 1)
    other = ConversationKey(conversations.create(data, NOW).id, 1)
    stage = JobKey("20261016-job", "echo", 1)
    (data.jobs / stage.job_id).mkdir()
    stages = ActiveStages()
    for key in (mine, other, stage):
        stages.activate(key)
    changed: list[tuple[str, list[dict[str, Any]]]] = []
    desk = Desk(
        data,
        {},
        "analyze",
        clock=lambda: NOW,
        on_lesson=lambda c, events: changed.append((c, events)),
    )
    config = GatewayConfig(
        data=data, mode="live", anthropic_url="http://127.0.0.1:9", secrets_dir=tmp_path, clock=lambda: NOW
    )
    with serve(create_app(config, stages, desk=desk)) as url:
        yield Env(url, data, mine, other, stage, changed)


def _ok(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


@pytest.mark.promise("FR-RW-04")
def test_agent_提議_只記進這個對話的檔_角色資料夾沒動_畫面收到變化(env: Env) -> None:
    body = _ok(env.propose())

    assert (body["proposal"], body["persona"], body["status"], body["created"]) == ("L1", "nightowl", "pending", True)
    # 同意的話會寫進去的那一行：agent 照實轉告，不是「已經存了」
    assert body["line"] == "- 2026-10-16｜" + RULE
    path = env.data.conversation_dir(env.mine.conversation_id) / lesson_proposals.PROPOSED_FILE
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(e["type"], e["source"]) for e in events] == [("proposed", "agent")]
    assert not env.lessons.exists()
    assert [(c, [e["type"] for e in evs]) for c, evs in env.changed] == [(env.mine.conversation_id, ["proposed"])]


def test_同一筆再提議_拿回同一筆_畫面不再收到變化(env: Env) -> None:
    _ok(env.propose())
    again = _ok(env.propose())

    assert (again["proposal"], again["created"]) == ("L1", False)
    assert len(env.changed) == 1


@pytest.mark.promise("FR-RW-04")
@pytest.mark.parametrize("tool", ["approve_lesson", "reject_lesson"])
def test_agent_不能同意也不能不要_對話鑰匙與工單鑰匙都_403_什麼都不寫(env: Env, tool: str) -> None:
    _ok(env.propose())

    by_conversation = env.call(tool, env.mine, proposal="L1")
    by_stage = env.call(tool, env.stage, proposal="L1")
    by_other = env.call(tool, env.other, proposal="L1")

    for response in (by_conversation, by_stage, by_other):
        assert response.status_code == 403, response.text
        assert "只有畫面能叫" in response.json()["error"]
    assert not env.lessons.exists()
    path = env.data.conversation_dir(env.mine.conversation_id) / lesson_proposals.PROPOSED_FILE
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_同意與不要不在對話的工具清單_只在畫面專用的名單裡() -> None:
    assert {"approve_lesson", "reject_lesson"} <= UI_ONLY_TOOLS
    assert not ({"approve_lesson", "reject_lesson"} & CHAT_TOOLS)
    assert "propose_lesson" in CHAT_TOOLS and "propose_lesson" not in UI_ONLY_TOOLS


def test_工單鑰匙不能提議(env: Env) -> None:
    response = env.call("propose_lesson", env.stage, persona="nightowl", text=RULE, why=WHY)

    assert response.status_code == 403
    assert not env.lessons.exists()


@pytest.mark.promise("FR-RW-04")
def test_提議各記在自己的對話_兩個對話的提議互不相干(env: Env) -> None:
    _ok(env.propose(key=env.mine))
    theirs = _ok(env.propose(key=env.other))

    assert theirs["proposal"] == "L1"
    assert [p.id for p in lesson_proposals.read_proposals(env.data, env.mine.conversation_id)] == ["L1"]
    assert [p.id for p in lesson_proposals.read_proposals(env.data, env.other.conversation_id)] == ["L1"]


@pytest.mark.promise("FR-RW-04")
@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"persona": "nobody"}, 404),
        ({"persona": "me"}, 404),
        ({"persona": "../nightowl"}, 400),
        ({"persona": 5}, 400),
        ({"text": "兩行\n第二行"}, 400),
        ({"text": "規則 <!-- 註解 -->"}, 400),
        ({"text": "規" * (lesson_proposals.MAX_TEXT + 1)}, 400),
        ({"text": ""}, 400),
        ({"why": "兩行\n第二行"}, 400),
        ({"why": 3}, 400),
    ],
)
def test_壞的角色_換行_太長都被拒_不記提議也不寫(env: Env, overrides: dict[str, object], status: int) -> None:
    response = env.propose(overrides)

    assert response.status_code == status, response.text
    assert "error" in response.json()
    assert not (env.data.conversation_dir(env.mine.conversation_id) / lesson_proposals.PROPOSED_FILE).exists()
    assert not env.lessons.exists()
    assert env.changed == []


def test_缺欄位_400(env: Env) -> None:
    response = env.call("propose_lesson", env.mine, persona="nightowl", text=RULE)

    assert response.status_code == 400
