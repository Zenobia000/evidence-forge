"""對話主機（`chat/host.py`，#85 的做完的定義）：

- 接回同一份紀錄；閒置關掉後再打開；Pi 正在回答時送訊息（followUp）。
- 工單結束通知不重複；主機被硬殺、重開後補通知。
- Pi 程序不留孤兒：主機關機收掉全部，主機被硬殺時 Pi 自己收掉。

Pi 是講 RPC 協定的假 Pi（`tests/chat_world.py`）；工單由真的投入開、測試直接改 `job.md` 當成執行器跑完，
再呼叫 `job_changed`（執行器寫 `job.md` 後會呼叫的那一個）。
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from functools import partial
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from chat_world import fake_chat_harness, pid_alive, wait_until

from evidence_forge import conversations
from evidence_forge.chat.host import ConversationHost, ConversationNotFound
from evidence_forge.chat.pi_rpc import session_dir
from evidence_forge.convkey import ConversationKey
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import Harness
from evidence_forge.models import JobResult, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
RECIPE = Recipe(
    name="analyze",
    description="test",
    stages=(RecipeStage(id="investigate", skill="investigate", tier="strong", output=("report.md",)),),
    on_feedback="investigate",
    default_goal="讀懂這篇",
)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.data = DataDir(tmp_path / "data")
        self.data.ensure()
        self.stages = ActiveStages()
        self.hosts: list[ConversationHost] = []

    def harness(self, **settings: Any) -> Harness:
        return fake_chat_harness(self.tmp_path / "harness", settings)

    def host(self, harness: Harness | None = None, **options: Any) -> ConversationHost:
        host = ConversationHost(
            data=self.data,
            harness=harness or self.harness(),
            stages=self.stages,
            gateway_url="http://127.0.0.1:9",
            recipes={"analyze": RECIPE},
            clock=lambda: NOW,
            **options,
        )
        host.start()
        self.hosts.append(host)
        return host

    def conversation(self) -> str:
        return conversations.create(self.data, NOW).id

    def job(self, conversation_id: str, text: str = "# 一篇文章\n\n內容") -> str:
        job = open_job(
            self.data, RECIPE, PastedText(text), goal=None, now=NOW, conversation=conversation_id
        ).job
        conversations.add_job(
            self.data, conversation_id, conversations.ConversationJob(job_id=job.id, ts=NOW)
        )
        return job.id

    def finish(self, job_id: str) -> None:
        path = self.data.job_dir(job_id) / "job.md"
        job = read_job(path).job
        done = job.model_copy(
            update={"status": "review", "result": JobResult(outcome="complete", reason="跑完")}
        )
        write_job(path, JobDocument(job=done))

    def session(self, conversation_id: str) -> list[dict[str, Any]]:
        path = session_dir(self.data, conversation_id) / f"{conversation_id}.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def commands(self, conversation_id: str) -> list[dict[str, Any]]:
        path = session_dir(self.data, conversation_id) / "commands.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def notices(self, conversation_id: str, job_id: str) -> int:
        return sum(
            1
            for m in self.session(conversation_id)
            if m.get("customType") == "ef-job" and m["details"]["job_id"] == job_id
        )

    def replies(self, conversation_id: str) -> list[str]:
        return [
            m["content"][0]["text"]
            for m in self.session(conversation_id)
            if m.get("role") == "assistant"
        ]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = World(tmp_path)
    try:
        yield built
    finally:
        for host in built.hosts:
            host.close()


def test_送訊息_開_Pi_帶_followUp_拿得到完整紀錄(world: World) -> None:
    host = world.host()
    cid = world.conversation()

    assert host.send(cid, "嗨") == {"disposition": "started"}
    wait_until(lambda: world.replies(cid) == ["回覆：嗨"], what="Pi 回答")

    sent = [c for c in world.commands(cid) if c["type"] == "prompt"]
    assert sent[0]["streamingBehavior"] == "followUp"
    history = host.history(cid)
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["jobs"] == []
    assert world.stages.is_active(ConversationKey(cid, 1))


def test_沒有這個對話_空訊息_斜線開頭都不送(world: World) -> None:
    host = world.host()
    cid = world.conversation()
    with pytest.raises(ConversationNotFound):
        host.send("c-no-such", "嗨")
    with pytest.raises(ValueError, match="空的"):
        host.send(cid, "   ")
    with pytest.raises(ValueError, match="/ 開頭"):
        host.send(cid, "/ef-job-done 123")
    assert host.live_pid(cid) is None


@pytest.mark.promise("FR-CH-03", "NFR-RUN-04")
def test_Pi_死掉後_接回同一份紀錄_舊鑰匙撤銷_新鑰匙加一(world: World) -> None:
    host = world.host()
    cid = world.conversation()
    host.send(cid, "第一句")
    wait_until(lambda: len(world.replies(cid)) == 1, what="第一句的回答")
    old_pid = host.live_pid(cid)
    assert old_pid is not None

    os.killpg(old_pid, signal.SIGKILL)
    wait_until(lambda: host.live_pid(cid) is None, what="主機發現 Pi 死了")

    history = host.history(cid)
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert host.live_pid(cid) not in (None, old_pid)
    assert conversations.read(world.data, cid).launches == 2
    assert not world.stages.is_active(ConversationKey(cid, 1))
    assert world.stages.is_active(ConversationKey(cid, 2))


@pytest.mark.promise("FR-CH-03")
def test_閒置關掉_再打字就接回_紀錄還在(world: World) -> None:
    host = world.host(idle_s=0.3)
    cid = world.conversation()
    host.send(cid, "第一句")
    wait_until(lambda: len(world.replies(cid)) == 1, what="第一句的回答")
    pid = host.live_pid(cid)
    assert pid is not None

    # 閒置計時器到期就關，不用任何人來問
    wait_until(lambda: host.live_pid(cid) is None, what="閒置關掉")
    wait_until(lambda: not pid_alive(pid), what="閒置的 Pi 結束")
    assert not world.stages.is_active(ConversationKey(cid, 1))

    host.send(cid, "第二句")
    wait_until(lambda: len(world.replies(cid)) == 2, what="接回後的回答")
    assert world.replies(cid) == ["回覆：第一句", "回覆：第二句"]
    assert conversations.read(world.data, cid).launches == 2


def test_正在回答時不閒置關掉_答完才開始算(world: World) -> None:
    # 一輪要答 1 秒，中間沒有任何事件；閒置 0.2 秒就關的話，回答會被砍掉、永遠等不到
    host = world.host(world.harness(turn_s=1.0), idle_s=0.2)
    cid = world.conversation()
    host.send(cid, "慢慢想")
    wait_until(lambda: world.replies(cid) == ["回覆：慢慢想"], what="回答完")
    wait_until(lambda: host.live_pid(cid) is None, what="答完之後閒置關掉")


@pytest.mark.promise("FR-CH-01")
def test_Pi_正在回答時送訊息_排在後面_兩句都答(world: World) -> None:
    host = world.host(world.harness(turn_s=0.5))
    cid = world.conversation()

    assert host.send(cid, "第一句")["disposition"] == "started"
    assert host.send(cid, "第二句")["disposition"] == "queued"

    wait_until(lambda: len(world.replies(cid)) == 2, what="兩句都答完")
    assert world.replies(cid) == ["回覆：第一句", "回覆：第二句"]
    prompts = [c for c in world.commands(cid) if c["type"] == "prompt"]
    assert all(c["streamingBehavior"] == "followUp" for c in prompts)


def test_工單跑完_通知對話一次_重複的變動與接回都不會多一則(world: World) -> None:
    host = world.host()
    cid = world.conversation()
    job_id = world.job(cid)
    host.job_changed(job_id)

    world.finish(job_id)
    host.job_changed(job_id)
    host.job_changed(job_id)
    wait_until(lambda: world.notices(cid, job_id) == 1, what="一則通知")
    wait_until(lambda: conversations.read(world.data, cid).notified_jobs == [job_id], what="記下通知送到了")
    wait_until(lambda: f"收到工單 {job_id}" in world.replies(cid), what="通知觸發一輪回答")

    # Pi 死掉再接回：已經記下送到的不再送
    pid = host.live_pid(cid)
    assert pid is not None
    os.killpg(pid, signal.SIGKILL)
    wait_until(lambda: host.live_pid(cid) is None, what="Pi 死了")
    host.history(cid)
    host.job_changed(job_id)
    wait_until(lambda: host.live_pid(cid) is not None)
    done = [c for c in world.commands(cid) if c.get("message", "").startswith("/ef-job-done")]
    assert len(done) == 1
    assert world.notices(cid, job_id) == 1


def test_沒記下送到就再送一次_ef_tools_照紀錄去重_還是一則(world: World) -> None:
    host = world.host()
    cid = world.conversation()
    job_id = world.job(cid)
    world.finish(job_id)
    host.job_changed(job_id)
    wait_until(lambda: world.notices(cid, job_id) == 1, what="一則通知")
    wait_until(lambda: conversations.read(world.data, cid).notified_jobs == [job_id])
    host.close()

    # 模擬「通知進了紀錄、主機還沒記下就死了」：拿掉記錄，重開主機
    conversation = conversations.read(world.data, cid)
    conversation.notified_jobs = []
    conversations.save(world.data, conversation)
    world.host()

    wait_until(
        lambda: sum(c.get("message", "").startswith("/ef-job-done") for c in world.commands(cid)) == 2,
        what="重開後補送一次",
    )
    assert world.notices(cid, job_id) == 1


def test_別的對話的工單結束_不通知這個對話(world: World) -> None:
    host = world.host()
    mine = world.conversation()
    theirs = world.conversation()
    job_id = world.job(theirs)
    world.finish(job_id)
    host.job_changed(job_id)
    wait_until(lambda: world.notices(theirs, job_id) == 1)
    assert host.live_pid(mine) is None
    assert world.session(mine) == []


_HOST_PROCESS = r'''
import sys, threading
from datetime import UTC, datetime
from evidence_forge.chat.host import ConversationHost
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import Harness
from evidence_forge.paths import DataDir
from pathlib import Path

data, harness, cid = DataDir(Path(sys.argv[1])), Harness.at(Path(sys.argv[2])), sys.argv[3]
host = ConversationHost(
    data=data, harness=harness, stages=ActiveStages(), gateway_url="http://127.0.0.1:9",
    recipes={}, clock=lambda: datetime.now(UTC),
)
host.start()
host.send(cid, "被殺之前")
print(host.live_pid(cid), flush=True)
threading.Event().wait()
'''


@pytest.mark.promise("NFR-RUN-04")
def test_主機被硬殺_Pi_自己收掉不留孤兒_重開後補送這段期間結束的工單(world: World) -> None:
    harness = world.harness()
    cid = world.conversation()
    job_id = world.job(cid)
    process = subprocess.Popen(
        [sys.executable, "-c", _HOST_PROCESS, str(world.data.root), str(harness.root), cid],
        stdout=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        pi_pid = int(process.stdout.readline())
        assert pid_alive(pi_pid)
        process.kill()
        process.wait()
        # stdin 被關，Pi 自己收掉（spike 實測約 1 秒）
        wait_until(lambda: not pid_alive(pi_pid), timeout_s=10, what="Pi 跟著結束")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    # 主機死掉的期間工單跑完了（執行器重開後會接著跑完；這裡直接改 job.md）
    world.finish(job_id)
    host = world.host(harness)

    wait_until(lambda: world.notices(cid, job_id) == 1, what="重開後補送通知")
    wait_until(lambda: f"收到工單 {job_id}" in world.replies(cid))
    assert world.replies(cid)[0] == "回覆：被殺之前"
    assert conversations.read(world.data, cid).launches == 2
    assert host.live_pid(cid) is not None


def test_啟動時殺的只有前一個主機留下的_不殺自己剛開的(world: World) -> None:
    # 回歸：API 先起來、主機才 start，第一句話剛好在中間送到時，start 殺孤兒會把剛開的 Pi 殺掉
    host = ConversationHost(
        data=world.data,
        harness=world.harness(),
        stages=world.stages,
        gateway_url="http://127.0.0.1:9",
        recipes={"analyze": RECIPE},
        clock=lambda: NOW,
    )
    world.hosts.append(host)
    cid = world.conversation()
    host.send(cid, "搶在 start 之前")
    pid = host.live_pid(cid)
    assert pid is not None

    host.start()

    assert pid_alive(pid)
    wait_until(lambda: world.replies(cid) == ["回覆：搶在 start 之前"])


def test_主機關機時收掉所有對話的_Pi(world: World) -> None:
    host = world.host()
    first, second = world.conversation(), world.conversation()
    host.send(first, "一")
    host.send(second, "二")
    pids = [host.live_pid(first), host.live_pid(second)]
    assert all(pids)

    host.close()

    for pid in pids:
        assert pid is not None
        wait_until(partial(lambda p: not pid_alive(p), pid), what="Pi 結束")
    assert not world.stages.is_active(ConversationKey(first, 1))
    assert not list((world.data.run / "chat-pids").glob("*.pid"))


def test_訂閱先送現況_之後推進度_標題換成素材標題(world: World) -> None:
    host = world.host()
    cid = world.conversation()
    job_id = world.job(cid, "# 圖書館的夜班\n\n內容")

    async def collect() -> list[dict[str, Any]]:
        subscription = host.subscribe(cid, asyncio.get_running_loop())
        got: list[dict[str, Any]] = []
        try:
            while not any(e.get("type") == "ef_conversation" for e in got):
                event = await asyncio.wait_for(subscription.queue.get(), 10)
                assert event is not None
                got.append(event)
                if len(got) == 2:
                    # 現況送完了：工單有變（執行器改寫 job.md 會呼叫這個）
                    host.job_changed(job_id)
        finally:
            host.unsubscribe(cid, subscription)
        return got

    events = asyncio.run(collect())
    assert events[0] == {"type": "ef_pi", "state": "stopped", "launch": None, "reason": None, "busy": False}
    assert events[1]["type"] == "ef_job" and events[1]["job_id"] == job_id
    assert events[-1] == {"type": "ef_conversation", "title": "圖書館的夜班"}
    assert conversations.read(world.data, cid).title == "圖書館的夜班"
    summary = host.summary(cid)
    assert summary["title"] == "圖書館的夜班"
    assert summary["job_count"] == 1
    assert summary["latest_job"]["status"] == "queued"
