"""對話的工具（#80 spike、FR-CH-01、NFR-SEC-02）。

對話的 Pi 只有這幾個工具，全部帶對話鑰匙（`conv:<id>:<n>`）打 `POST /tools/<名稱>`：

- `analyze`：開一張分析工單，**開了就回**（工單 id 與狀態），不等它跑完。
  跑完之後由聊天主機叫 ef-tools 的 `/ef-job-done` 指令把結果帶回對話（`chat/hub.py`）。
- `read_report`：讀這個對話開過的某張工單的結果。
- `job_status`：一張工單現在跑到哪（`/ef-job-done` 組通知用）。

範圍：只碰得到列在這個對話 `jobs.jsonl` 裡的工單，別的對話的工單一律 403。
對話沒有讀寫任意檔、抓網頁、跑腳本的工具：段的工具拿對話鑰匙來也一律 403（`app.py`）。
真正開單、讀結果的事交給 `Desk`（聊天主機注入，知道配方與 harness）；閘道只管鑰匙與範圍。
"""

from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from evidence_forge import conversations
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.gateway.scope import ToolRejected

CHAT_TOOLS = frozenset({"analyze", "read_report", "job_status"})


class Desk(Protocol):
    """開分析工單、讀工單狀態與結果的那一層（實作在 `chat/desk.py`）。"""

    def open_analysis(
        self, *, url: str | None, text: str | None, goal: str | None
    ) -> dict[str, Any]:
        """開單（或拿回已有的那張）；回 `{"job_id", "created", "status"}`，
        讀不到網址回 `{"status": "unavailable", "reason"}`。可以花幾秒抓網頁。"""
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


def _owned_job(config: GatewayConfig, key: ConversationKey, body: dict[str, Any]) -> str:
    job_id = body.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ToolRejected(400, "缺少字串欄位 job_id")
    if not conversations.owns_job(config.data, key.conversation_id, job_id):
        # 不說那張單存不存在：對話不該知道別的對話開了什麼
        raise ToolRejected(403, f"工單 {job_id} 不是這個對話開的")
    return job_id


async def handle(
    config: GatewayConfig,
    desk: Desk | None,
    key: ConversationKey,
    name: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    if desk is None:
        raise ToolRejected(503, "這個閘道沒有接上對話的工作台")
    if name == "analyze":
        url = _optional(body, "url")
        text = _optional(body, "text")
        if (url is None) == (text is None):
            raise ToolRejected(400, "url 與 text 要給其中一個")
        opened = await run_in_threadpool(
            desk.open_analysis, url=url, text=text, goal=_optional(body, "goal")
        )
        job_id = opened.get("job_id")
        if isinstance(job_id, str):
            conversations.add_job(
                config.data, key.conversation_id, job_id, config.clock()
            )
        return opened
    if name == "read_report":
        return await run_in_threadpool(desk.report, _owned_job(config, key, body))
    if name == "job_status":
        return await run_in_threadpool(desk.status, _owned_job(config, key, body))
    raise ToolRejected(404, f"沒有這個工具：{name}")
