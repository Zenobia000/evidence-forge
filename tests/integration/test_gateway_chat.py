"""閘道 ↔ 對話鑰匙（ADR-0021、NFR-SEC-02、chat.feature 的 @security 與「對話本身的花費也入帳」）。

- 對話只能用對話的工具（analyze、read_report、read_article、job_status、list_personas、rewrite），段的工具一律 403。
- 段的鑰匙不能用對話的工具。
- 對話只讀得到自己 `jobs.jsonl` 裡的工單；別的對話開的一律 403。
- 接回之後只認最新那一把；Pi 結束撤銷之後也拒絕。
- 對話本身的模型呼叫記在對話的帳本、受對話的預算管、不走模型快取。
- 每個對話同一時間最多幾張工單在跑（預設 1）。
- `rewrite` 只從這個對話自己的工單接著做；從分析開是新的版本鏈，同一個角色再改接在同一條鏈上，換角色另開一條（#102）。

開單用真的投入（`runner.intake.open_job`、`open_job_from`）；模型後端是本機的假 Anthropic，金鑰是假的。
"""

import json
import threading
import time
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
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.convkey import ConversationKey
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.gateway.active import StaleKey
from evidence_forge.runner.intake import PastedText, open_job, open_job_from

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
FAKE_KEY = "sk-fake-xxxx-anthropic-chat-canary"
RECIPE = Recipe(
    name="echo",
    description="test",
    stages=(RecipeStage(id="echo", skill="echo", tier="local", output=("a.json",)),),
    on_feedback="echo",
    default_goal="用兩三句話說明這篇文章在講什麼",
)
REWRITE = Recipe(
    name="rewrite",
    description="test",
    stages=(RecipeStage(id="rewrite", skill="rewrite", tier="local", output=("article.md",)),),
    on_feedback="rewrite",
    needs=("report.md",),
    default_goal="寫一篇",
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
        self.rewrites: list[dict[str, Any]] = []
        # 閘道通知「記好了」時：(對話, 工單, 那時這張單在不在對話的 jobs.jsonl 裡)
        self.recorded_calls: list[tuple[str, str, bool]] = []
        # 開了單就馬上當成跑完（很快的執行器）
        self.finish_at_once = False
        self.recipe_name = RECIPE.name
        # 開單前故意等一下：讓兩個同時送來的請求撞在一起
        self.delay_s = 0.0
        # 不照實回：開完單卻回另一張單的 id（閘道不能相信工作台）
        self.forged_job_id: str | None = None

    def open_analysis(
        self,
        *,
        conversation_id: str,
        url: str | None,
        text: str | None,
        goal: str | None,
        again: bool,
        before_create: Callable[[], None],
    ) -> dict[str, Any]:
        self.opened.append({"conversation_id": conversation_id, "url": url})
        if url is not None:
            return {"status": "unavailable", "reason": "付費牆"}
        time.sleep(self.delay_s)
        intake = open_job(
            self.data,
            RECIPE,
            PastedText(text or ""),
            goal=goal,
            now=NOW,
            conversation=conversation_id,
            again=again,
            before_create=before_create,
        )
        if self.finish_at_once:
            _mark_done(self.data, intake.job.id)
        return {
            "job_id": self.forged_job_id or intake.job.id,
            "created": intake.created,
            "status": intake.job.status,
        }

    def recorded(self, conversation_id: str, job_id: str) -> None:
        listed = conversations.owns_job(self.data, conversation_id, job_id)
        self.recorded_calls.append((conversation_id, job_id, listed))

    def open_rewrite(
        self,
        *,
        conversation_id: str,
        from_job: str,
        persona: str | None,
        genre: str | None,
        suggested_genre: str | None,
        goal: str | None,
        feedback: str | None,
    ) -> dict[str, Any]:
        self.rewrites.append(
            {
                "conversation_id": conversation_id,
                "from_job": from_job,
                "genre": genre,
                "suggested_genre": suggested_genre,
            }
        )
        try:
            job = open_job_from(
                self.data,
                REWRITE,
                from_job,
                goal=goal,
                now=NOW,
                persona=persona,
                genre=genre,
                feedback=feedback,
                conversation=conversation_id,
            )
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
        if self.finish_at_once:
            _mark_done(self.data, job.id)
        return {
            "job_id": self.forged_job_id or job.id,
            "created": True,
            "status": job.status,
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = read_job(self.data.job_dir(job_id) / "job.md").job
        return {"job_id": job_id, "status": job.status}

    def report(self, job_id: str) -> dict[str, Any]:
        return {**self.status(job_id), "files": {"report.md": f"{job_id} 的報告"}}

    def article(self, job_id: str) -> dict[str, Any]:
        return {**self.status(job_id), "files": {"article.md": f"{job_id} 的文章"}}

    def personas(self) -> dict[str, Any]:
        return {"personas": [{"name": "me", "default_genre": "opinion"}]}

    def card_changed(self, conversation_id: str, card_id: str, events: list[dict[str, Any]]) -> None:
        # 編輯卡的工具另外測（test_gateway_cards.py）
        pass


def _mark_done(data: DataDir, job_id: str) -> None:
    path = data.job_dir(job_id) / "job.md"
    job = read_job(path).job
    write_job(
        path,
        JobDocument(job=job.model_copy(update={"status": "done", "result": JobResult(outcome="complete")})),
    )


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

    def finish(
        self,
        job_id: str,
        outcome: str = "complete",
        reason: str = "跑完",
        *,
        stage: str = "echo",
        files: dict[str, str] | None = None,
    ) -> None:
        """把工單改成跑完（結束）：執行器會做的事，這裡直接寫 `job.md`。

        給了 `files` 就連同那一段的產出與過了的檢查結果一起寫（重構要從它接著做）。
        """
        job_dir = self.data.job_dir(job_id)
        if files:
            attempt = job_dir / "stages" / stage / "1"
            attempt.mkdir(parents=True)
            for name, text in files.items():
                (attempt / name).write_text(text, encoding="utf-8")
            write_checks_report(
                job_dir,
                ChecksReport(
                    stage=stage,
                    attempt=1,
                    files=tuple(f"stages/{stage}/1/{name}" for name in files),
                    created_at=NOW,
                    entries=(CheckEntry(check="C5", status="pass", reason="測試"),),
                ),
            )
        path = job_dir / "job.md"
        job = read_job(path).job
        done = job.model_copy(
            update={
                "status": "done",
                "result": JobResult.model_validate(
                    {"outcome": outcome, "reason": reason}
                ),
            }
        )
        write_job(path, JobDocument(job=done))


@pytest.fixture
def env(tmp_path: Path, serve: Serve) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    # 重構要有角色資料夾：me 是創作者自己，ally 是另一個角色
    for persona in ("me", "ally"):
        (data.authors / persona).mkdir(parents=True)
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


@pytest.mark.parametrize(
    ("outcome", "reason"), [("failed", "模型後端連不上"), ("incomplete", "預算用完")]
)
def test_失敗或未完成的單不拿回_再丟一次開新單(
    env: Env, outcome: str, reason: str
) -> None:
    first = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    env.finish(first, outcome=outcome, reason=reason)
    again = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    assert again != first


def test_帶_again_就重做_不拿回完成的那張(env: Env) -> None:
    first = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    env.finish(first)
    redo = env.call("analyze", env.mine, text="# 一篇\n\n內容", again=True)
    assert redo.json()["created"] is True
    assert _opened(redo) != first
    assert len(conversations.jobs(env.data, env.mine.conversation_id)) == 2


def test_again_要是布林值(env: Env) -> None:
    response = env.call("analyze", env.mine, text="# 一篇\n\n內容", again="yes")
    assert response.status_code == 400


# --- 範圍 -------------------------------------------------------------------


def test_讀得到自己開的報告(env: Env) -> None:
    job_id = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    response = env.call("read_report", env.mine, job_id=job_id)
    assert response.status_code == 200
    assert response.json()["files"] == {"report.md": f"{job_id} 的報告"}


def test_讀得到自己開的文章_列得出角色(env: Env) -> None:
    job_id = _opened(env.call("analyze", env.mine, text="# 一篇\n\n內容"))
    response = env.call("read_article", env.mine, job_id=job_id)
    assert response.status_code == 200
    assert response.json()["files"] == {"article.md": f"{job_id} 的文章"}
    personas = env.call("list_personas", env.mine)
    assert personas.status_code == 200
    assert personas.json()["personas"][0]["name"] == "me"


@pytest.mark.promise("FR-RW-04")
def test_read_lessons_讀角色最近十筆_只讀不寫(env: Env) -> None:
    rows = "\n".join(f"- 規則 {n}" for n in range(1, 13))
    lessons = env.data.authors / "me" / "lessons.md"
    lessons.write_text(f"# 規則\n\n{rows}\n", encoding="utf-8")
    before = lessons.read_bytes()

    response = env.call("read_lessons", env.mine, persona="me")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "persona": "me",
        "lessons": [f"規則 {n}" for n in range(3, 13)],
    }
    assert lessons.read_bytes() == before
    # 沒給角色就是創作者自己；還沒有 lessons.md 的角色是空的
    assert env.call("read_lessons", env.mine).json()["persona"] == "me"
    assert env.call("read_lessons", env.mine, persona="ally").json()["lessons"] == []


@pytest.mark.parametrize(("persona", "status"), [("nobody", 404), ("../me", 400), (3, 400)])
def test_read_lessons_沒有的角色或不合格的名稱被拒(env: Env, persona: object, status: int) -> None:
    response = env.call("read_lessons", env.mine, persona=persona)
    assert response.status_code == status


# --- rewrite ----------------------------------------------------------------


def _analysed(env: Env, key: ConversationKey | None = None, text: str = "# 一篇\n\n內容") -> str:
    """這個對話開一張分析、跑完，交得出過了檢查的報告。"""
    job_id = _opened(env.call("analyze", key or env.mine, text=text))
    env.finish(job_id, files={"report.md": "報告"})
    return job_id


def _written(env: Env, from_job: str, **body: object) -> str:
    """從 `from_job` 開一張重構、跑完，交得出過了檢查的文章。"""
    job_id = _opened(env.call("rewrite", env.mine, from_job=from_job, **body))
    # 執行器開工前會把上一張的報告放進 input/；再改從這一版拿報告
    (env.data.job_dir(job_id) / "input").mkdir()
    (env.data.job_dir(job_id) / "input" / "report.md").write_text("報告", encoding="utf-8")
    env.finish(job_id, stage="rewrite", files={"article.md": "# 一篇文章"})
    return job_id


def _chain(env: Env) -> list[tuple[str, int, str | None]]:
    return [(r.job_id, r.version, r.parent) for r in conversations.jobs(env.data, env.mine.conversation_id)]


def test_rewrite_從這個對話的分析開_新的一條版本鏈(env: Env) -> None:
    analysis = _analysed(env)
    response = env.call("rewrite", env.mine, from_job=analysis, goal="題材 1：夜班的人力。角度：成本")
    job_id = _opened(response)
    assert response.json()["created"] is True
    assert _chain(env) == [(analysis, 1, None), (job_id, 1, analysis)]
    job = read_job(env.data.job_dir(job_id) / "job.md").job
    assert (job.conversation, job.parent, job.persona, job.goal) == (
        env.mine.conversation_id,
        analysis,
        "me",
        "題材 1：夜班的人力。角度：成本",
    )


@pytest.mark.promise("NFR-SEC-02")
def test_rewrite_拿別的對話的工單當_from_job_被拒_不說那張單存不存在(env: Env) -> None:
    theirs = _analysed(env, env.other, "# 別人的\n\n內容")
    response = env.call("rewrite", env.mine, from_job=theirs)
    assert response.status_code == 403
    assert response.json() == env.call("rewrite", env.mine, from_job="no-such-job").json()
    assert env.desk.rewrites == []
    assert conversations.jobs(env.data, env.mine.conversation_id) == []


def test_rewrite_創作者指定的文類與報告建議的文類分開交給工作台(env: Env) -> None:
    analysis = _analysed(env)
    _opened(env.call("rewrite", env.mine, from_job=analysis, genre="opinion", suggested_genre="評論"))
    assert env.desk.rewrites[-1]["genre"] == "opinion"
    assert env.desk.rewrites[-1]["suggested_genre"] == "評論"


def test_rewrite_帶意見再改_同一個角色接在同一條鏈上(env: Env) -> None:
    analysis = _analysed(env)
    first = _written(env, analysis)
    second = _opened(env.call("rewrite", env.mine, from_job=first, feedback="開頭太長，直接講結論"))
    assert _chain(env)[1:] == [(first, 1, analysis), (second, 2, first)]
    assert read_job(env.data.job_dir(second) / "job.md").job.feedback == "開頭太長，直接講結論"


def test_rewrite_換角色_另開一條鏈(env: Env) -> None:
    analysis = _analysed(env)
    first = _written(env, analysis)
    other = _opened(env.call("rewrite", env.mine, from_job=first, persona="ally"))
    assert _chain(env)[-1] == (other, 1, first)
    assert read_job(env.data.job_dir(other) / "job.md").job.persona == "ally"


def test_rewrite_對話已有工單在跑_被拒_不開單(env: Env) -> None:
    analysis = _analysed(env)
    running = _opened(env.call("analyze", env.mine, text="# 另一篇\n\n內容"))
    response = env.call("rewrite", env.mine, from_job=analysis)
    assert response.status_code == 409
    assert running in response.json()["error"]
    assert env.desk.rewrites == []


def test_rewrite_工作台不開單_原因交回對話(env: Env) -> None:
    analysis = _analysed(env)
    response = env.call("rewrite", env.mine, from_job=analysis, persona="nobody")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert "沒有角色 nobody" in response.json()["reason"]
    assert _chain(env) == [(analysis, 1, None)]


@pytest.mark.parametrize(
    "body",
    [
        {"from_job": None},
        {"from_job": 3},
        {"persona": 1},
        {"genre": ["opinion"]},
        {"suggested_genre": 5},
        {"goal": 2},
        {"feedback": True},
    ],
    ids=["沒有_from_job", "from_job_不是字串", "persona", "genre", "suggested_genre", "goal", "feedback"],
)
def test_rewrite_欄位不對_400_不開單(env: Env, body: dict[str, object]) -> None:
    analysis = _analysed(env)
    # None 表示不帶這個欄位
    merged = {k: v for k, v in ({"from_job": analysis} | body).items() if v is not None}
    response = env.call("rewrite", env.mine, **merged)
    assert response.status_code == 400
    assert env.desk.rewrites == []


def test_一開就跑完的單_記進_jobs_jsonl_之後才通知工作台(env: Env) -> None:
    # 主機靠 jobs.jsonl 找「這張單是哪個對話的、要不要通知」：先通知再記，跑得快的單通知就丟了
    env.desk.finish_at_once = True
    analysis = _opened(env.call("analyze", env.mine, text="# 很快的\n\n內容"))
    env.finish(analysis, files={"report.md": "報告"})
    rewrite = _opened(env.call("rewrite", env.mine, from_job=analysis))
    mine = env.mine.conversation_id
    assert env.desk.recorded_calls == [(mine, analysis, True), (mine, rewrite, True)]


def test_有重構在跑時丟同一篇_不會被當成拿回在跑的那張_抓之前就擋下(env: Env) -> None:
    # 重構單跟分析共用同一份素材項目：「同一份素材在跑」只看分析配方的單
    analysis = _analysed(env, text="# 第一篇\n\n內容")
    running = _opened(env.call("rewrite", env.mine, from_job=analysis))
    response = env.call("analyze", env.mine, text="# 第一篇\n\n內容")
    assert response.status_code == 409
    assert running in response.json()["error"]
    # 工作台沒被叫到：沒有對外抓取，也沒有寫投入帳
    assert len(env.desk.opened) == 1


def test_rewrite_工作台回別的對話的單_不記進來(env: Env) -> None:
    theirs = _analysed(env, env.other, "# 別人的\n\n內容")
    analysis = _analysed(env)
    env.desk.forged_job_id = theirs
    response = env.call("rewrite", env.mine, from_job=analysis)
    assert response.status_code == 500
    assert not conversations.owns_job(env.data, env.mine.conversation_id, theirs)


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize("tool", ["read_report", "read_article", "job_status"])
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
        ("read_article", {"job_id": "x"}),
        ("job_status", {"job_id": "x"}),
        ("list_personas", {}),
        ("read_lessons", {"persona": "me"}),
        ("rewrite", {"from_job": "x"}),
    ],
)
def test_段的鑰匙不能用對話的工具(env: Env, tool: str, body: dict[str, object]) -> None:
    key = JobKey("20261013-000000-aaaa1111", "echo", 1)
    env.stages.activate(key)
    response = env.call(tool, key, **body)
    assert response.status_code == 403
    assert env.desk.opened == []
    assert env.desk.rewrites == []


# --- 鑰匙換代與撤銷 ---------------------------------------------------------


def test_接回之後舊鑰匙被拒_新鑰匙可以用(env: Env) -> None:
    relaunched = ConversationKey(env.mine.conversation_id, 2)
    env.stages.activate(relaunched)
    assert env.call("analyze", env.mine, text="# a\n\nb").status_code == 403
    assert env.message(env.mine).status_code == 403
    assert env.call("analyze", relaunched, text="# a\n\nb").status_code == 200


def test_倒過來的順序登記_舊鑰匙不會復活(env: Env) -> None:
    # 第 2 次開先登記好，延遲的第 1 次開才來登記：拒絕，而且第 1 把照樣被拒
    relaunched = ConversationKey(env.mine.conversation_id, 2)
    env.stages.activate(relaunched)
    with pytest.raises(StaleKey):
        env.stages.activate(env.mine)
    assert env.call("analyze", env.mine, text="# a\n\nb").status_code == 403
    assert env.message(env.mine).status_code == 403
    assert env.call("analyze", relaunched, text="# a\n\nb").status_code == 200


def test_同一把鑰匙不能登記兩次_撤銷之後也不能再登記(env: Env) -> None:
    with pytest.raises(StaleKey):
        env.stages.activate(env.mine)
    env.stages.deactivate(env.mine)
    with pytest.raises(StaleKey):
        env.stages.activate(env.mine)
    assert env.call("analyze", env.mine, text="# a\n\nb").status_code == 403


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


def test_同時送來兩篇_只開得了一張(env: Env) -> None:
    env.desk.delay_s = 0.3
    start = threading.Barrier(2)
    statuses: list[int] = []

    def send(article: str) -> None:
        start.wait()
        statuses.append(env.call("analyze", env.mine, text=article).status_code)

    threads = [
        threading.Thread(target=send, args=(f"# 第 {n} 篇\n\n內容 {n}",))
        for n in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 409]
    assert len(conversations.jobs(env.data, env.mine.conversation_id)) == 1


def test_開了單卻沒記進_jobs_jsonl_的單也算在跑(env: Env) -> None:
    # 開單之後、記進 jobs.jsonl 之前當掉：那張單已經在排隊，`job.md` 記著是這個對話開的
    open_job(
        env.data,
        RECIPE,
        PastedText("# 當掉前開的\n\n內容"),
        goal=None,
        now=NOW,
        conversation=env.mine.conversation_id,
    )
    assert conversations.jobs(env.data, env.mine.conversation_id) == []
    assert env.call("analyze", env.mine, text="# 另一篇\n\n內容").status_code == 409


def test_有單在跑時換別的網址_抓之前就擋下(env: Env) -> None:
    _opened(env.call("analyze", env.mine, text="# 第一篇\n\n內容"))
    response = env.call("analyze", env.mine, url="https://example.com/other")
    assert response.status_code == 409
    # 工作台沒被叫到：沒有對外抓取，也沒有寫投入帳
    assert len(env.desk.opened) == 1


def test_有單在跑時同一篇換目標_抓了之後開新單時擋下(env: Env) -> None:
    # 同一份素材過得了抓之前那一關（可能只是拿回在跑的那張），但目標不同要開新單，照樣擋
    _opened(env.call("analyze", env.mine, text="# 第一篇\n\n內容"))
    response = env.call("analyze", env.mine, text="# 第一篇\n\n內容", goal="另一個目標")
    assert response.status_code == 409
    assert len(env.desk.opened) == 2
    assert len(conversations.jobs(env.data, env.mine.conversation_id)) == 1


def test_有單在跑時帶_again_重做_也先擋下(env: Env) -> None:
    _opened(env.call("analyze", env.mine, text="# 第一篇\n\n內容"))
    response = env.call("analyze", env.mine, text="# 第一篇\n\n內容", again=True)
    assert response.status_code == 409
    assert len(env.desk.opened) == 1


def test_工作台回別的對話的單_不記進來(env: Env) -> None:
    theirs = _opened(env.call("analyze", env.other, text="# 別人的\n\n內容"))
    env.desk.forged_job_id = theirs
    response = env.call("analyze", env.mine, text="# 我的\n\n內容")
    assert response.status_code == 500
    assert not conversations.owns_job(env.data, env.mine.conversation_id, theirs)
    assert env.call("read_report", env.mine, job_id=theirs).status_code == 403


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
