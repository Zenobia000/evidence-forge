"""閘道 ↔ 編輯卡的對話工具（ADR-0025 §3.3、ADR-0021 §3.4、FR-IW-03、#107）。

- 對話的 agent 能叫的四個：`propose_questions`、`record_interpreted_answers`、`add_material`、`read_card`，事件標 `source: agent`。
- 只有畫面能叫的三個（`submit_station`、`keep_decision`、`reask_station`）不在閘道的工具裡：
  對話鑰匙、工單鑰匙拿來都 403。
- 只碰得到這個對話自己的卡：別的對話的卡一律 404，不說存不存在。
- 寫完一筆，工作台（對話主機）收到「這張卡變了」，畫面據此更新。

工作台是真的 `chat/desk.py`（不開單），閘道是真的；不打模型、不連網。
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evidence_forge import cards, conversations
from evidence_forge.chat.desk import Desk
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
STATION_1 = [
    {
        "header": "文體",
        "question": "這篇的主證據是什麼？",
        "options": [
            {"label": "explainer", "description": "把計價方式講清楚", "value": "explainer"},
            {"label": "trend", "description": "兩年內這筆帳會怎麼變", "value": "trend"},
        ],
    },
    # 第 1 關也一定有一張主題卡（ADR-0026 §3.2、#109）
    {
        "kind": "theme",
        "header": "主題",
        "question": "讀者做決定時最在意哪一個？",
        "options": [
            {"label": "工具選型", "description": "讀者要挑工具", "value": "tools"},
            {"label": "落地導入", "description": "讀者要導入", "value": "adoption"},
        ],
    },
]


@dataclass
class Env:
    url: str
    data: DataDir
    mine: ConversationKey
    other: ConversationKey
    stage: JobKey
    changed: list[tuple[str, str, list[dict[str, Any]]]] = field(default_factory=list)

    def call(self, tool: str, key: object, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}", json=body, headers={"x-api-key": str(key)}, timeout=10
        )


@pytest.fixture
def env(tmp_path: Path, serve: Any) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    mine = ConversationKey(conversations.create(data, NOW).id, 1)
    other = ConversationKey(conversations.create(data, NOW).id, 1)
    stage = JobKey("20261015-job", "echo", 1)
    (data.jobs / stage.job_id).mkdir()
    stages = ActiveStages()
    for key in (mine, other, stage):
        stages.activate(key)
    changed: list[tuple[str, str, list[dict[str, Any]]]] = []
    desk = Desk(
        data,
        {},
        "analyze",
        clock=lambda: NOW,
        on_card=lambda c, k, events: changed.append((c, k, events)),
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


@pytest.mark.promise("FR-IW-03")
def test_agent_提出問題卡_開一張新卡_事件標_agent_畫面收到變化(env: Env) -> None:
    body = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))

    card_id = body["card_id"]
    assert body["card"]["stations"][0]["status"] == "asking"
    events = cards.read_events(env.data, env.mine.conversation_id, card_id)
    assert [(e["type"], e["source"]) for e in events] == [("questions_proposed", "agent")]
    assert [(c, k, [e["type"] for e in evs]) for c, k, evs in env.changed] == [
        (env.mine.conversation_id, card_id, ["questions_proposed"])
    ]


def test_讀卡_沒給卡就列出這個對話的卡(env: Env) -> None:
    card_id = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))["card_id"]

    one = _ok(env.call("read_card", env.mine, card=card_id))
    listed = _ok(env.call("read_card", env.mine))

    assert one["card"]["card_id"] == card_id
    assert [c["card_id"] for c in listed["cards"]] == [card_id]
    assert _ok(env.call("read_card", env.other))["cards"] == []


def test_打字回答與你幫我選都經閘道記_來源是_agent(env: Env) -> None:
    card_id = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))["card_id"]
    genre, theme = _ok(env.call("read_card", env.mine, card=card_id))["card"]["stations"][0]["questions"]

    typed = _ok(
        env.call(
            "record_interpreted_answers",
            env.mine,
            card=card_id,
            station=1,
            answers=[
                {"question": genre["id"], "choice": "trend"},
                {"question": theme["id"], "choice": "工具選型"},
            ],
            quote="寫成趨勢吧，講工具",
        )
    )

    assert [(e["source"], e["mode"], e["choice"]) for e in typed["events"]] == [
        ("agent", "typed", "trend"),
        ("agent", "typed", "工具選型"),
    ]
    assert (typed["card"]["genre"], typed["card"]["theme"]) == ("trend", "tools")
    assert typed["card"]["stations"][0]["decided"] is True


def test_作者素材經閘道記_同一份內容拿回原本的編號(env: Env) -> None:
    first = _ok(env.call("add_material", env.mine, text="我的帳單：三千元"))
    again = _ok(env.call("add_material", env.mine, card=first["card_id"], text="我的帳單：三千元"))

    assert (first["material"], first["created"]) == ("M1", True)
    assert (again["material"], again["created"]) == ("M1", False)


@pytest.mark.promise("FR-IW-03", "NFR-SEC-02")
@pytest.mark.parametrize(
    "tool", ["submit_station", "keep_decision", "reask_station", "write_from_card"]
)
def test_畫面的入口對話的_agent_叫不到_段也叫不到(env: Env, tool: str) -> None:
    card_id = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))["card_id"]

    for key in (env.mine, env.stage):
        response = env.call(tool, key, card=card_id, station=1, answers=[])
        assert response.status_code == 403, response.text
    assert [e["type"] for e in cards.read_events(env.data, env.mine.conversation_id, card_id)] == [
        "questions_proposed"
    ]


@pytest.mark.parametrize(
    "tool", ["propose_questions", "record_interpreted_answers", "add_material", "read_card"]
)
def test_段的鑰匙不能用編輯卡的工具(env: Env, tool: str) -> None:
    assert env.call(tool, env.stage, station=1, questions=STATION_1).status_code == 403


@pytest.mark.promise("FR-IW-03")
def test_別的對話的卡碰不到_跟沒有這張卡一樣(env: Env) -> None:
    card_id = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))["card_id"]

    responses = {
        "read_card": env.call("read_card", env.other, card=card_id),
        "add_material": env.call("add_material", env.other, card=card_id, text="偷塞"),
        "propose_questions": env.call(
            "propose_questions", env.other, card=card_id, station=1, questions=STATION_1
        ),
        "record_interpreted_answers": env.call(
            "record_interpreted_answers", env.other, card=card_id, station=1, pick_for_me=True
        ),
    }
    missing = env.call("read_card", env.other, card="card-20261015-000000-000000")

    for name, response in responses.items():
        assert response.status_code == missing.status_code == 404, (name, response.text)
    assert [e["type"] for e in cards.read_events(env.data, env.mine.conversation_id, card_id)] == [
        "questions_proposed"
    ]


def test_卡_id_拼不出這個對話的資料夾就擋下(env: Env) -> None:
    response = env.call("read_card", env.mine, card="../" + env.other.conversation_id)
    assert response.status_code == 400


def test_問題卡不合格_回_400_說原因(env: Env) -> None:
    bad = [{**STATION_1[0], "options": STATION_1[0]["options"][:1]}]
    response = env.call("propose_questions", env.mine, station=1, questions=bad)
    assert response.status_code == 400
    assert "選項" in response.json()["error"]


@pytest.mark.promise("FR-IW-02")
def test_第_6_關的主張清單經閘道記在提出問題那一筆(env: Env) -> None:
    card_id = _ok(env.call("propose_questions", env.mine, station=1, questions=STATION_1))["card_id"]
    folder = env.data.conversation_dir(env.mine.conversation_id)
    assert folder.is_dir()
    for station in range(1, 6):
        state = cards.read_card(env.data, env.mine.conversation_id, card_id).stations[station - 1]
        answers = [
            {"question": q.id, "choice": q.options[0].label, "note": "作者自己的例子"}
            for q in state.questions
            if q.current
        ]
        cards.submit_station(env.data, env.mine.conversation_id, card_id, station, answers, now=NOW)
        if station < 5:
            _ok(
                env.call(
                    "propose_questions",
                    env.mine,
                    card=card_id,
                    station=station + 1,
                    questions=[
                        {
                            "header": f"第{station + 1}關",
                            "question": "選哪個？",
                            "options": [
                                {"label": "甲", "description": "說明"},
                                {"label": "乙", "description": "說明"},
                            ],
                        }
                    ],
                )
            )
    claims = [{"claim": "每月三千元", "wrong_if": "帳單不是這個數"}]
    storyboard = {
        "kind": "storyboard",
        "header": "圖文分鏡",
        "question": "每節配不配圖？",
        "options": [
            {"label": "一張數據圖", "description": "第 2 節", "figures": [{"section": "帳單", "kind": "chart"}]},
            {"label": "不配圖", "description": "只有文字", "figures": []},
        ],
    }

    body = _ok(
        env.call("propose_questions", env.mine, card=card_id, station=6, questions=[storyboard], claims=claims)
    )

    [event] = body["events"]
    assert event["claims"] == claims
    missing = env.call("propose_questions", env.mine, card=card_id, station=6, questions=[storyboard])
    assert missing.status_code == 400 and "claims" in missing.json()["error"]
