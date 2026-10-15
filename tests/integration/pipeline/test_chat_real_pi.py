"""真的 Pi（RPC 模式）＋ ef-tools 的對話工具＋閘道＋對話主機，模型是照劇本回答的假 Anthropic（#85）。

證明假 Pi 證明不了的那幾件：
- 對話的 Pi 只拿到對話的工具（模型看得到的工具清單）。
- `analyze` 經閘道開單、開了就回；工單結束時主機送 `/ef-job-done`，ef-tools 放一則 `ef-job` 通知、
  觸發一輪回答，模型叫 `read_report` 再整理。
- ef-tools 照對話紀錄去重：接回之後再送一次同一張單，還是一則通知。
- Pi 正在回答時送訊息（`followUp`）排在後面，兩句都答。
- 創作者在畫面上送出一關（`submit_station`，#107）：主機送 `/ef-card`，ef-tools 放一則 `ef-card` 通知、
  觸發一輪，模型叫 `read_card` 讀到創作者點的回答；畫面才能叫的入口不在模型的工具清單裡。
- `rewrite` 的接線：從分析開被閘道擋下（新文章從訪談開始，#109）；改已經有的文章才經閘道開單，
  它跑完時的通知叫模型讀文章（#102）。照編輯卡寫稿是 #110。
- 訪談（#109）：三種起點（報告的題材、一句想法、貼作者素材）都開一張編輯卡、先讀角色的 lessons；
  六關一關一關走到可以寫稿，中間有「你幫我選」（第 1 關選不需要作者經驗的文體、第 3 關只記「尚待作者提供」）、
  在主輸入框打字回答、第 3 關被擋（要作者經驗的文體不能沒有例子；不是從報告開始的隨筆也不能）、
  畫面重問、改已經有決定的關要先重新提出。模型是照 `fake_anthropic._interview` 劇本走的假模型：
  這裡證明的是工具、通知與閘道的規則接得起來，不是真模型會不會照提示詞做。

要先用 nvm 的 Node 在 `harness/pi/` 跑 `npm ci`；沒裝就跳過。不打任何付費 API。
"""

import json
import os
import shutil
import signal
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from chat_world import wait_until
from fake_anthropic import FakeAnthropic, station_questions

from evidence_forge import cards, conversations
from evidence_forge.chat.desk import Desk
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.chat.host import ConversationHost
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.app import create_app
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.harness import default_harness
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import open_job_from
from evidence_forge.runner.pi_launch import prepare_agent_dir

PI_BIN = default_harness().pi.pi_bin
FAKE_KEY = "sk-fake-xxxx-anthropic-chat-pi"
NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
RECIPE = Recipe(
    name="analyze",
    description="test",
    stages=(RecipeStage(id="investigate", skill="investigate", tier="strong", output=("report.md",)),),
    on_feedback="investigate",
    default_goal="讀懂這篇",
)
REWRITE = Recipe(
    name="rewrite",
    description="test",
    stages=(RecipeStage(id="rewrite", skill="rewrite", tier="strong", output=("article.md",)),),
    on_feedback="rewrite",
    needs=("report.md",),
    default_goal="寫一篇",
)
RECIPES = {"analyze": RECIPE, "rewrite": REWRITE}
CHAT_TOOLS = {
    "analyze",
    "read_report",
    "read_article",
    "job_status",
    "list_personas",
    "read_lessons",
    "rewrite",
    "propose_questions",
    "record_interpreted_answers",
    "add_material",
    "read_card",
}

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(
        not PI_BIN.exists() or shutil.which("node") is None,
        reason="沒有鎖版的 Pi：先用 nvm 的 Node 在 harness/pi/ 跑 npm ci",
    ),
]

Serve = Callable[[Any], AbstractContextManager[str]]


@dataclass
class Chat:
    data: DataDir
    host: ConversationHost
    model: FakeAnthropic

    def messages(self, cid: str) -> list[dict[str, Any]]:
        found = self.host.history(cid)["messages"]
        assert isinstance(found, list)
        return found

    def texts(self, cid: str) -> list[str]:
        return [
            block["text"]
            for m in self.messages(cid)
            if m.get("role") == "assistant"
            for block in m.get("content", [])
            if block.get("type") == "text"
        ]

    def notices(self, cid: str, job_id: str) -> int:
        return sum(
            1
            for m in self.messages(cid)
            if m.get("role") == "custom"
            and m.get("customType") == "ef-job"
            and (m.get("details") or {}).get("job_id") == job_id
        )


@pytest.fixture
def chat(tmp_path: Path, serve: Serve) -> Iterator[Chat]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.authors / "me").mkdir(parents=True)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "anthropic").write_text(FAKE_KEY + "\n", encoding="utf-8")
    model = FakeAnthropic()
    stages = ActiveStages()
    board = JobBoard(lambda: NOW)
    harness = default_harness()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app()))
        config = GatewayConfig(
            data=data, mode="live", anthropic_url=model_url, secrets_dir=secrets
        )
        holder: dict[str, ConversationHost] = {}
        desk = Desk(
            data,
            RECIPES,
            "analyze",
            clock=lambda: NOW,
            on_opened=lambda c, j: holder["host"].job_opened(c, j),
        )
        gateway_url = stack.enter_context(serve(create_app(config, stages, board, desk=desk)))
        prepare_agent_dir(harness.pi, data, gateway_url)
        host = ConversationHost(
            data=data,
            harness=harness,
            stages=stages,
            gateway_url=gateway_url,
            recipes=RECIPES,
            clock=lambda: NOW,
            board=board,
            request_timeout_s=60,
        )
        holder["host"] = host
        host.start()
        stack.callback(host.close)
        yield Chat(data, host, model)


def _finish(
    data: DataDir, job_id: str, report: str, *, stage: str = "investigate", name: str = "report.md"
) -> None:
    """執行器會做的事：寫產出與過了的檢查結果、把工單標成跑完。"""
    attempt = data.job_dir(job_id) / "stages" / stage / "1"
    attempt.mkdir(parents=True)
    (attempt / name).write_text(report, encoding="utf-8")
    write_checks_report(
        data.job_dir(job_id),
        ChecksReport(
            stage=stage,
            attempt=1,
            files=(f"stages/{stage}/1/{name}",),
            created_at=NOW,
            entries=(CheckEntry(check="C5", status="pass", reason="測試"),),
        ),
    )
    path = data.job_dir(job_id) / "job.md"
    job = read_job(path).job
    done = job.model_copy(
        update={"status": "done", "result": JobResult(outcome="complete", reason="跑完")}
    )
    write_job(path, JobDocument(job=done))


@pytest.mark.promise("FR-CH-01", "FR-CH-03", "NFR-SEC-02")
def test_丟文章_開單就回_跑完通知一次_讀報告整理_接回後不重複(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id

    chat.host.send(cid, "分析：# 圖書館的夜班\n\n夜班的人力成本與使用率。")
    wait_until(lambda: "開始分析了。" in chat.texts(cid), timeout_s=60, what="analyze 開了單就回")
    # 模型看得到的只有對話的工具（段的讀寫檔、抓網頁、搜尋、跑腳本都沒有）
    assert {t["name"] for t in chat.model.requests[0]["tools"]} == CHAT_TOOLS
    jobs = conversations.jobs(chat.data, cid)
    assert len(jobs) == 1
    job_id = jobs[0].job_id
    assert conversations.read(chat.data, cid).title == "圖書館的夜班"

    _finish(chat.data, job_id, "夜班使用率三成，主要是學生。")
    chat.host.job_changed(job_id)
    wait_until(
        lambda: any(t.startswith("報告重點：") for t in chat.texts(cid)),
        timeout_s=60,
        what="通知觸發一輪：讀報告、整理",
    )
    assert chat.notices(cid, job_id) == 1
    assert "夜班使用率三成" in next(t for t in chat.texts(cid) if t.startswith("報告重點："))
    wait_until(lambda: conversations.read(chat.data, cid).notified_jobs == [job_id])

    # 主機沒記下送到（例如記之前就被硬殺）：接回之後主機會再送；ef-tools 照紀錄去重
    conversation = conversations.read(chat.data, cid)
    conversation.notified_jobs = []
    conversations.save(chat.data, conversation)
    pid = chat.host.live_pid(cid)
    assert pid is not None
    before = len(chat.messages(cid))
    os.killpg(pid, signal.SIGKILL)
    wait_until(lambda: chat.host.live_pid(cid) is None)

    chat.host.send(cid, "還在嗎")
    wait_until(lambda: "回覆：還在嗎" in chat.texts(cid), timeout_s=60, what="接回後回答")
    after = chat.messages(cid)
    # 同一份紀錄接回來，只多了這一問一答，沒有第二則通知
    assert len(after) == before + 2
    assert chat.notices(cid, job_id) == 1
    assert conversations.read(chat.data, cid).launches == 2


def test_Pi_正在回答時送訊息_排在後面_兩句都答(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
    chat.model.delay_s = 1.0

    assert chat.host.send(cid, "第一句")["disposition"] == "started"
    assert chat.host.send(cid, "第二句")["disposition"] == "queued"

    wait_until(
        lambda: chat.texts(cid) == ["回覆：第一句", "回覆：第二句"],
        timeout_s=60,
        what="兩句照順序都答",
    )


def _analysed(chat: Chat, cid: str) -> str:
    """丟一篇文章、分析跑完、通知讀完報告；回分析的工單 id。"""
    chat.host.send(cid, "分析：# 圖書館的夜班\n\n夜班的人力成本與使用率。")
    wait_until(lambda: "開始分析了。" in chat.texts(cid), timeout_s=60, what="analyze 開了單就回")
    analysis = conversations.jobs(chat.data, cid)[0].job_id
    _finish(chat.data, analysis, "## 可以寫的題材\n\n### 1. 夜班值不值得\n")
    chat.host.job_changed(analysis)
    wait_until(
        lambda: any(t.startswith("報告重點：") for t in chat.texts(cid)),
        timeout_s=60,
        what="分析的通知：讀報告、整理",
    )
    return analysis


@pytest.mark.promise("FR-CH-01", "FR-RW-01")
def test_rewrite_從分析開被閘道擋下_改已經有的文章才開單_跑完通知讀文章(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
    analysis = _analysed(chat, cid)

    # 模型不照提示詞、直接從分析寫：閘道擋下，新文章從訪談開始（#109）
    chat.host.send(cid, "直接重構第 1 個")
    wait_until(lambda: any(t.startswith("沒開：") for t in chat.texts(cid)), timeout_s=60, what="rewrite 被擋")
    assert "新文章從訪談開始" in next(t for t in chat.texts(cid) if t.startswith("沒開："))
    assert [r.job_id for r in conversations.jobs(chat.data, cid)] == [analysis]

    # 第一版文章（照編輯卡寫稿是 #110；這裡直接用投入開、記進對話）
    first = open_job_from(chat.data, REWRITE, analysis, goal="題材 1", now=NOW, conversation=cid)
    conversations.add_job(
        chat.data, cid, conversations.ConversationJob(job_id=first.id, ts=NOW, version=1, parent=analysis)
    )
    (chat.data.job_dir(first.id) / "input").mkdir()
    (chat.data.job_dir(first.id) / "input" / "report.md").write_text("報告", encoding="utf-8")
    _finish(chat.data, first.id, "# 夜班\n\n第一版。", stage="rewrite", name="article.md")

    chat.host.send(cid, f"改寫 {first.id}：開頭短一點")
    wait_until(lambda: "開始寫了。" in chat.texts(cid), timeout_s=60, what="rewrite 開了單就回")
    rows = conversations.jobs(chat.data, cid)
    assert [(r.version, r.parent) for r in rows] == [(1, None), (1, analysis), (2, first.id)]
    rewrite = rows[2].job_id
    job = read_job(chat.data.job_dir(rewrite) / "job.md").job
    assert (job.recipe, job.parent, job.conversation, job.persona) == ("rewrite", first.id, cid, "me")
    assert job.feedback == "開頭短一點"

    _finish(chat.data, rewrite, "# 夜班值不值得\n\n人力成本算給你看。", stage="rewrite", name="article.md")
    chat.host.job_changed(rewrite)
    wait_until(
        lambda: any(t.startswith("文章重點：") for t in chat.texts(cid)),
        timeout_s=60,
        what="重構的通知：讀文章、整理",
    )
    assert chat.notices(cid, rewrite) == 1
    assert "人力成本算給你看" in next(t for t in chat.texts(cid) if t.startswith("文章重點："))
    # 畫面的結果卡照這份進度畫：重構那張跑完了
    progress = {p["job_id"]: p for p in chat.host.jobs(cid)}
    assert progress[rewrite]["finished"] and progress[rewrite]["outcome"] == "complete"


@pytest.mark.promise("FR-IW-03", "FR-UI-13")
def test_畫面送出一關_ef_card_通知觸發一輪_模型讀編輯卡(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
    question = {
        "header": "文體",
        "question": "這篇的主證據是什麼？",
        "options": [
            {"label": "explainer", "description": "把計價方式講清楚", "value": "explainer"},
            {"label": "trend", "description": "兩年內這筆帳會怎麼變", "value": "trend"},
        ],
    }
    card_id = cards.propose_questions(chat.data, cid, None, 1, [question], now=NOW).card_id

    chat.host.submit_station(
        cid, card_id, 1, [{"question": "q1", "choice": "trend", "note": "看兩年"}]
    )

    _wait_said(chat, cid, "第 2 關的問題卡在畫面上了。", what="ef-card 通知觸發一輪：讀編輯卡、提出下一關")
    # 模型這一輪先叫 read_card，讀到的是創作者點的回答
    assert "-> trend (note: 看兩年)" in json.dumps(chat.model.requests[-1], ensure_ascii=False)
    notices = [m for m in chat.messages(cid) if m.get("customType") == "ef-card"]
    assert [(m["details"]["card_id"], m["details"]["action"]) for m in notices] == [(card_id, "submit")]
    # 模型看得到編輯卡的四個工具，看不到畫面才能叫的三個
    tools = {t["name"] for t in chat.model.requests[0]["tools"]}
    assert tools == CHAT_TOOLS
    assert not tools & {"submit_station", "keep_decision", "reask_station"}


# --- 訪談（#109）-----------------------------------------------------------------------------


def _wait_said(chat: Chat, cid: str, text: str, times: int = 1, *, what: str | None = None) -> None:
    wait_until(
        lambda: sum(1 for t in chat.texts(cid) if t.startswith(text)) >= times,
        timeout_s=60,
        what=what or f"模型說第 {times} 次「{text}」",
    )


def _said(chat: Chat, cid: str, text: str) -> list[str]:
    return [t for t in chat.texts(cid) if t.startswith(text)]


def _the_card(chat: Chat, cid: str) -> cards.CardState:
    [card] = cards.list_cards(chat.data, cid)
    return card


def _current(card: cards.CardState, station: int) -> list[cards.StationQuestion]:
    return [q for q in card.stations[station - 1].questions if q.current]


def _submit(chat: Chat, cid: str, station: int, picks: dict[str, str] | None = None) -> None:
    """創作者在畫面上回答這一關每一張現在的問題卡（沒指定就選第一個），按「確認並送出這一關」。"""
    card = _the_card(chat, cid)
    answers: list[dict[str, Any]] = []
    for question in _current(card, station):
        answer: dict[str, Any] = {
            "question": question.id,
            "choice": (picks or {}).get(question.header, question.options[0].label),
        }
        if station == 3:
            # 第 3 關只選提示不算：例子要作者自己寫
            answer["note"] = "我自己夜裡去過，十點前只剩我一個"
        answers.append(answer)
    chat.host.submit_station(cid, card.card_id, station, answers)


def _answered(chat: Chat, cid: str, station: int) -> list[dict[str, Any]]:
    events = cards.read_events(chat.data, cid, _the_card(chat, cid).card_id)
    return [e for e in events if e["type"] == "answered" and e["station"] == station]


def _proposed(chat: Chat, cid: str, station: int) -> int:
    events = cards.read_events(chat.data, cid, _the_card(chat, cid).card_id)
    return sum(1 for e in events if e["type"] == "questions_proposed" and e["station"] == station)


@pytest.mark.promise("FR-IW-01", "FR-IW-02", "FR-IW-03", "FR-RW-04")
def test_寫第_1_個_開一張編輯卡_六關訪談到可以寫稿_不開重構單(chat: Chat) -> None:
    (chat.data.authors / "ally").mkdir()
    (chat.data.authors / "me" / "lessons.md").write_text(
        "# 退件與指正累積的規則\n\n- 2026-10-01｜第 3 篇｜標題不要用問句\n", encoding="utf-8"
    )
    cid = conversations.create(chat.data, NOW).id
    analysis = _analysed(chat, cid)

    # 起點 1：報告的第 N 個題材 → 讀角色與 lessons、讀報告，開卡問第 1 關
    chat.host.send(cid, "寫第 1 個")
    _wait_said(chat, cid, "第 1 關的問題卡在畫面上了。")
    card = _the_card(chat, cid)
    assert card.origin is not None
    assert (card.origin.kind, card.origin.job_id, card.origin.topic) == ("report_topic", analysis, 1)
    station_1 = _current(card, 1)
    assert [(q.header, q.kind) for q in station_1] == [
        ("文體", "genre"),
        ("主題", "theme"),
        ("角色", "persona"),
    ]
    assert {o.value for o in station_1[0].options} <= cards.GENRES
    # 訪談開工前讀了角色最近的 lessons
    assert "標題不要用問句" in json.dumps(chat.model.requests, ensure_ascii=False)
    # 「寫第 N 個」不再直接開重構單
    assert [r.job_id for r in conversations.jobs(chat.data, cid)] == [analysis]

    # 第 1 關「你幫我選」：只選不需要作者經驗的文體，記成 agent 選的
    chat.host.send(cid, "你幫我選")
    _wait_said(chat, cid, "第 2 關的問題卡在畫面上了。")
    card = _the_card(chat, cid)
    assert card.stations[0].decided
    # 文體只挑不需要作者經驗的；主題與角色選排第一的，都以結構化的值記下
    assert (card.genre, card.theme, card.persona) == ("research-review", "tools", "me")
    assert {(e["source"], e["mode"]) for e in _answered(chat, cid, 1)} == {("agent", "pick_for_me")}

    _submit(chat, cid, 2)
    _wait_said(chat, cid, "第 3 關的問題卡在畫面上了。")

    # 第 3 關「你幫我選」：只記「尚待作者提供」，不算有決定，接著只問作者這一題
    chat.host.send(cid, "你幫我選")
    _wait_said(chat, cid, "第 3 關的問題卡在畫面上了。", 2)
    assert not _the_card(chat, cid).stations[2].decided
    assert [e["pending"] for e in _answered(chat, cid, 3)] == [True]
    assert _proposed(chat, cid, 3) == 2

    # 在主輸入框打字回答：模型解讀後記下，附原話
    typed = "回答第 3 關：上個月我自己夜裡去了三次，每次都不到十個人"
    chat.host.send(cid, typed)
    _wait_said(chat, cid, "第 4 關的問題卡在畫面上了。")
    assert _the_card(chat, cid).stations[2].decided
    last = _answered(chat, cid, 3)[-1]
    assert (last["source"], last["mode"], last["quote"]) == ("agent", "typed", typed)
    assert last["other_text"] == "上個月我自己夜裡去了三次，每次都不到十個人"

    for station in (4, 5):
        _submit(chat, cid, station)
        _wait_said(chat, cid, f"第 {station + 1} 關的問題卡在畫面上了。")
    assert [q.header for q in _current(_the_card(chat, cid), 6)] == ["框架", "大綱", "圖文分鏡", "主張"]
    _submit(chat, cid, 6)
    _wait_said(chat, cid, "編輯卡六關都有決定了。")

    assert _the_card(chat, cid).writable
    # 照編輯卡寫稿是 #110：訪談完也不叫 rewrite
    assert [r.job_id for r in conversations.jobs(chat.data, cid)] == [analysis]
    called = {
        block["name"]
        for request in chat.model.requests
        for message in request["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_use"
    }
    assert "rewrite" not in called


@pytest.mark.promise("FR-IW-01", "FR-IW-02")
def test_從一句想法開始_要作者經驗的文體不替他選_第_3_關沒有例子停住(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id

    # 起點 2：一句想法，不用分析
    chat.host.send(cid, "想寫：圖書館夜班值不值得")
    _wait_said(chat, cid, "第 1 關的問題卡在畫面上了。")
    card = _the_card(chat, cid)
    assert card.origin is not None and card.origin.kind == "idea"
    # 只有 me 一個角色：不問角色
    assert [q.header for q in _current(card, 1)] == ["文體", "主題"]
    assert conversations.jobs(chat.data, cid) == []

    # 列的文體全要作者自己的經驗：「你幫我選」被拒，什麼都沒記
    chat.host.send(cid, "你幫我選")
    _wait_said(chat, cid, "沒記：")
    assert "請創作者自己挑文體" in _said(chat, cid, "沒記：")[0]
    assert not _the_card(chat, cid).stations[0].decided
    assert _answered(chat, cid, 1) == []

    _submit(chat, cid, 1, {"文體": "產品個案"})
    _wait_said(chat, cid, "第 2 關的問題卡在畫面上了。")
    _submit(chat, cid, 2)
    _wait_said(chat, cid, "第 3 關的問題卡在畫面上了。")

    # 產品個案要作者自己用過：「沒有例子」停在第 3 關
    chat.host.send(cid, "回答第 3 關：沒有例子")
    _wait_said(chat, cid, "沒記：", 2)
    assert "product-case" in _said(chat, cid, "沒記：")[1]
    assert not _the_card(chat, cid).stations[2].decided

    # 回第 1 關換成隨筆：畫面重問 → 模型重新提出 → 送出；第 2 關要重確認，按「沿用」
    card_id = _the_card(chat, cid).card_id
    chat.host.reask_station(cid, card_id, 1)
    _wait_said(chat, cid, "第 1 關的問題卡在畫面上了。", 2)
    _submit(chat, cid, 1, {"文體": "隨筆"})
    _wait_said(chat, cid, "等你回答第 2 關。")
    assert _the_card(chat, cid).stations[1].needs_reconfirm
    chat.host.keep_decision(cid, card_id, 2)
    _wait_said(chat, cid, "等你回答第 3 關。")

    # 不是從報告開始的隨筆，沒有作者的經驗也寫不了
    chat.host.send(cid, "回答第 3 關：沒有例子")
    _wait_said(chat, cid, "沒記：", 3)
    assert "不是從報告開始" in _said(chat, cid, "沒記：")[2]
    card = _the_card(chat, cid)
    assert card.genre == "essay" and not card.stations[2].decided


@pytest.mark.promise("FR-IW-01")
def test_貼自己的素材_記成作者素材_從第_1_關開始(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
    notes = "上週我自己量的夜班人數：週一 8 人、週五 15 人"

    # 起點 3：作者自己的素材，不當別人的文章去分析
    chat.host.send(cid, f"素材：{notes}")
    _wait_said(chat, cid, "第 1 關的問題卡在畫面上了。")
    card = _the_card(chat, cid)
    assert card.origin is not None and card.origin.kind == "material"
    assert [m.id for m in card.materials] == ["M1"]
    path = cards.material_path(chat.data, cid, card.card_id, "M1")
    assert path.read_text(encoding="utf-8") == notes
    assert conversations.jobs(chat.data, cid) == []


def _decided_card(chat: Chat, cid: str) -> str:
    """六關都由畫面送出過的一張卡（直接寫決策紀錄，不經模型）。"""
    origin = {"kind": "idea", "title": "夜班"}
    card_id = cards.propose_questions(
        chat.data, cid, None, 1, station_questions(1, "idea"), now=NOW, origin=origin
    ).card_id
    for station in range(1, 7):
        if station > 1:
            cards.propose_questions(
                chat.data, cid, card_id, station, station_questions(station, "idea"), now=NOW
            )
        card = cards.read_card(chat.data, cid, card_id)
        answers: list[dict[str, Any]] = [
            {"question": q.id, "choice": q.options[0].label} for q in _current(card, station)
        ]
        if station == 3:
            answers = [{**a, "note": "我自己用過三個月"} for a in answers]
        cards.submit_station(chat.data, cid, card_id, station, answers, now=NOW)
    assert cards.read_card(chat.data, cid, card_id).writable
    return card_id


@pytest.mark.promise("FR-IW-02", "FR-IW-03")
def test_改已經有決定的關_先重新提出再記_畫面重問時模型重新提出(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
    card_id = _decided_card(chat, cid)

    # 打字改第 5 關：已經有決定，不直接蓋掉，先重新提出這一關再記
    typed = "回答第 5 關：換成「夜裡的圖書館」"
    chat.host.send(cid, typed)
    _wait_said(chat, cid, "等你回答第 6 關。")
    card = _the_card(chat, cid)
    assert _proposed(chat, cid, 5) == 2
    assert card.stations[4].status == "changed"
    last = _answered(chat, cid, 5)[-1]
    assert (last["source"], last["other_text"]) == ("agent", "換成「夜裡的圖書館」")
    # 後面已經有決定的關標要重確認，還不能寫
    assert card.stations[5].needs_reconfirm and not card.writable

    # 畫面「重問這一關」：通知送進來，模型重新提出那一關的問題卡
    chat.host.reask_station(cid, card_id, 2)
    _wait_said(chat, cid, "第 2 關的問題卡在畫面上了。")
    assert _proposed(chat, cid, 2) == 2
    assert _the_card(chat, cid).stations[1].status == "asking"
