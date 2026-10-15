"""真的 Pi（RPC 模式）＋ ef-tools 的對話工具＋閘道＋對話主機，模型是照劇本回答的假 Anthropic（#85）。

證明假 Pi 證明不了的那幾件：
- 對話的 Pi 只拿到對話的工具（模型看得到的工具清單）。
- `analyze` 經閘道開單、開了就回；工單結束時主機送 `/ef-job-done`，ef-tools 放一則 `ef-job` 通知、
  觸發一輪回答，模型叫 `read_report` 再整理。
- ef-tools 照對話紀錄去重：接回之後再送一次同一張單，還是一則通知。
- Pi 正在回答時送訊息（`followUp`）排在後面，兩句都答。
- 分析跑完之後說「寫第 1 個」：模型叫 `rewrite`，經閘道開一張接在那張分析後面的重構單；
  它跑完時的通知叫模型讀文章（#102）。

要先用 nvm 的 Node 在 `harness/pi/` 跑 `npm ci`；沒裝就跳過。不打任何付費 API。
"""

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
from fake_anthropic import FakeAnthropic

from evidence_forge import conversations
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
CHAT_TOOLS = {"analyze", "read_report", "read_article", "job_status", "list_personas", "rewrite"}

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


@pytest.mark.promise("FR-CH-01", "FR-RW-01")
def test_分析跑完說寫第_1_個_開出重構單_跑完通知讀文章(chat: Chat) -> None:
    cid = conversations.create(chat.data, NOW).id
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

    chat.host.send(cid, "寫第 1 個")
    wait_until(lambda: "開始寫了。" in chat.texts(cid), timeout_s=60, what="rewrite 開了單就回")
    rows = conversations.jobs(chat.data, cid)
    assert [(r.version, r.parent) for r in rows] == [(1, None), (1, analysis)]
    rewrite = rows[1].job_id
    job = read_job(chat.data.job_dir(rewrite) / "job.md").job
    assert (job.recipe, job.parent, job.conversation, job.persona) == ("rewrite", analysis, cid, "me")
    assert job.goal == "題材 1"

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
