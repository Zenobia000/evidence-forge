"""對話的工具（ADR-0021 §3.4、FR-CH-01、NFR-SEC-02）。

對話的 Pi 只有這幾個工具，全部帶對話鑰匙（`conv:<id>:<n>`）打 `POST /tools/<名稱>`：

- `analyze`：開一張分析工單，**開了就回**（工單 id 與狀態），不等它跑完。
- `read_report`：讀這個對話開過的某張工單的結果。
- `job_status`：一張工單現在跑到哪。

範圍：
- 只碰得到列在這個對話 `jobs.jsonl` 裡的工單，別的對話的一律 403，而且不說那張單存不存在。
- 段的工具（讀寫檔、抓網頁、搜尋、跑腳本）拿對話鑰匙來一律 403；對話的工具拿段的鑰匙來也是（`app.py`）。
- 每個對話同一時間最多 `chat_max_running_jobs` 張工單在跑（排隊中也算，預設 1）：
  對話的 agent 可以一直開單，每張單各有預算，不擋的話總花費只剩月上限管得到。
  只擋「要開新單」：同一篇文章再丟一次（連按兩下）照樣拿回那一張，即使它還在跑。

真正開單、讀結果的事交給 `Desk`（對話主機注入，知道配方與 harness）；閘道只管鑰匙、範圍與記帳。
"""

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from evidence_forge import conversations
from evidence_forge.convkey import ConversationKey
from evidence_forge.documents import read_job
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.gateway.scope import ToolRejected

CHAT_TOOLS = frozenset({"analyze", "read_report", "job_status"})
# 還會花錢的狀態：排隊中的單等一下就會跑
_RUNNING = frozenset({"queued", "running"})


class Desk(Protocol):
    """開分析工單、讀工單狀態與結果的那一層。"""

    def open_analysis(
        self,
        *,
        conversation_id: str,
        url: str | None,
        text: str | None,
        goal: str | None,
        before_create: Callable[[], None],
    ) -> dict[str, Any]:
        """開單（或拿回這個對話已有的那張）；回 `{"job_id", "created", "status", …}`。
        讀不到網址回 `{"status": "unavailable", "reason"}`，不開單。可以花幾秒抓網頁。
        `conversation_id` 與 `before_create` 原樣傳給投入（`open_job`）：去重的鍵含對話 id；
        `before_create` 只在要開新單時呼叫，它丟的 `ToolRejected` 要原樣往外丟。"""
        ...

    def status(self, job_id: str) -> dict[str, Any]: ...

    def report(self, job_id: str) -> dict[str, Any]: ...


def _optional(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolRejected(400, f"{name} 要是字串")
    return value


class ChatTools:
    def __init__(self, config: GatewayConfig, desk: Desk | None) -> None:
        self._config = config
        self._desk = desk
        # 同一個對話的開單一次一個：「數在跑的單 → 開單 → 記進 jobs.jsonl」中間不能插隊，
        # 不然連按兩下會一起過關。閘道的請求都在同一個事件迴圈，用 asyncio 的鎖就夠
        self._locks: dict[str, asyncio.Lock] = {}

    async def handle(
        self, key: ConversationKey, name: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        desk = self._desk
        if desk is None:
            raise ToolRejected(503, "這個閘道沒有接上對話的工作台")
        if name == "analyze":
            return await self._analyze(desk, key, body)
        if name == "read_report":
            return await run_in_threadpool(desk.report, self._owned_job(key, body))
        if name == "job_status":
            return await run_in_threadpool(desk.status, self._owned_job(key, body))
        raise ToolRejected(404, f"沒有這個工具：{name}")

    async def _analyze(
        self, desk: Desk, key: ConversationKey, body: dict[str, Any]
    ) -> dict[str, Any]:
        url = _optional(body, "url")
        text = _optional(body, "text")
        if (url is None) == (text is None):
            raise ToolRejected(400, "url 與 text 要給其中一個")
        goal = _optional(body, "goal")
        data = self._config.data
        conversation_id = key.conversation_id
        if not (data.conversation_dir(conversation_id) / "conversation.json").is_file():
            raise ToolRejected(403, f"沒有這個對話：{conversation_id}")

        def room_for_new_job() -> None:
            # 在投入的鎖裡、確定要開新單時才數：去重拿回舊單不受上限影響
            running = self._running_jobs(conversation_id)
            limit = self._config.chat_max_running_jobs
            if len(running) >= limit:
                raise ToolRejected(
                    409,
                    f"這個對話已經有 {len(running)} 張工單在跑（{'、'.join(running)}），"
                    f"同一時間最多 {limit} 張；等它跑完再開，或請創作者決定",
                )

        def open_and_record() -> dict[str, Any]:
            # 開單與記進 jobs.jsonl 在同一個執行緒裡做完：請求中途被取消（關機、斷線）時，
            # 不會留下開了單卻沒記在對話底下、也不算進「在跑」的工單
            opened = desk.open_analysis(
                conversation_id=conversation_id,
                url=url,
                text=text,
                goal=goal,
                before_create=room_for_new_job,
            )
            job_id = opened.get("job_id")
            if isinstance(job_id, str):
                conversations.add_job(
                    data,
                    conversation_id,
                    conversations.ConversationJob(
                        job_id=job_id, ts=self._config.clock()
                    ),
                )
            return opened

        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return await run_in_threadpool(open_and_record)

    def _running_jobs(self, conversation_id: str) -> list[str]:
        """這個對話還在排隊或在跑的工單。讀不到 `job.md` 的不算：執行器也跑不了它。"""
        found: list[str] = []
        for row in conversations.jobs(self._config.data, conversation_id):
            try:
                job = read_job(self._config.data.job_dir(row.job_id) / "job.md").job
            except (OSError, ValueError):
                continue
            if job.status in _RUNNING:
                found.append(row.job_id)
        return found

    def _owned_job(self, key: ConversationKey, body: dict[str, Any]) -> str:
        job_id = body.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise ToolRejected(400, "缺少字串欄位 job_id")
        if not conversations.owns_job(self._config.data, key.conversation_id, job_id):
            # 不說那張單存不存在：對話不該知道別的對話開了什麼
            raise ToolRejected(403, "這張工單不是這個對話開的")
        return job_id
