"""聊天主機：管所有開著的對話 Pi、把事件分給瀏覽器、工單跑完時通知對話（#80 spike）。

- 打開或送訊息到一個對話時，Pi 沒開就開（第一次是新的，之後接回同一份紀錄）。
- 瀏覽器用 SSE 訂閱一個對話：Pi 的事件，加上這個對話的工單進度（`ef_job` 事件）。
- 每秒看一次各對話開過的工單（`jobs.jsonl` → `job.md`）：狀態變了就送進度；
  跑完了就對那個對話的 Pi 送 `/ef-job-done <工單 id>`。ef-tools 照對話紀錄去重，
  所以 Pi 被殺掉接回、主機重開，都可以把跑完的單全部再送一次，不會重複通知。
- 創作者打的字一律用 `streamingBehavior: followUp` 送：Pi 正在回答（或正在處理工單通知）時，
  沒帶這個欄位的 prompt 會被拒（spike 實測）。以 `/` 開頭的字不送，免得被當成 Pi 的指令。
"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from evidence_forge import conversations
from evidence_forge.chat.desk import Desk
from evidence_forge.chat.pi_rpc import PiConversation, build_rpc_command
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.harness import Harness
from evidence_forge.paths import DataDir

_log = logging.getLogger(__name__)

_FINISHED = ("review", "approved", "discarded")


class ChatHub:
    def __init__(
        self,
        *,
        data: DataDir,
        harness: Harness,
        stages: ActiveStages,
        gateway_url: str,
        desk: Desk,
        clock: Callable[[], datetime],
        tier: str = "strong",
        poll_s: float = 1.0,
    ) -> None:
        self._data = data
        self._harness = harness
        self._stages = stages
        self._gateway_url = gateway_url
        self._desk = desk
        self._clock = clock
        self._tier = tier
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._live: dict[str, PiConversation] = {}
        self._subscribers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = {}
        # 每個對話每張單上次送出去的進度，與這次 Pi 開著時已經送過通知的單
        self._progress: dict[tuple[str, str], dict[str, Any]] = {}
        self._notified: dict[str, set[str]] = {}
        self._stop = threading.Event()
        self._watcher = threading.Thread(target=self._watch, daemon=True)

    # --- 生命週期 -------------------------------------------------------

    def start(self) -> None:
        self._watcher.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            live = list(self._live.values())
            self._live.clear()
        for pi in live:
            pi.stop()

    # --- 對話 -----------------------------------------------------------

    def ensure(self, conversation_id: str) -> PiConversation:
        """這個對話的 Pi；沒開（或已經死掉）就開一個新的，接回同一份紀錄。"""
        with self._lock:
            pi = self._live.get(conversation_id)
            if pi is not None and pi.alive():
                return pi
            conversation = conversations.read(self._data, conversation_id)
            conversation.launches += 1
            conversations.save(self._data, conversation)
            key = ConversationKey(conversation_id, conversation.launches)
            command = build_rpc_command(
                harness=self._harness,
                data=self._data,
                key=key,
                gateway_url=self._gateway_url,
                base_env=os.environ,
                tier=self._tier,
            )
            pi = PiConversation(
                command,
                key,
                self._stages,
                lambda event: self._emit(conversation_id, event),
                self._data.run / "chat" / f"{conversation_id}.{key.launch}.stderr",
            )
            pi.start()
            self._live[conversation_id] = pi
            # 新開的 Pi：跑完的單全部再通知一次（ef-tools 照紀錄去重）
            self._notified[conversation_id] = set()
        self._emit(conversation_id, {"type": "ef_pi_start", "key": str(key), "pid": pi.pid})
        return pi

    def send_user(self, conversation_id: str, text: str) -> str | None:
        """送創作者的一句話；不送的話回原因。"""
        message = text.strip()
        if not message:
            return "訊息是空的"
        if message.startswith("/"):
            return "以 / 開頭的訊息不送（會被當成 Pi 的指令）"
        pi = self.ensure(conversation_id)
        # 跟 ensure 改 launches 用同一把鎖：讀改寫 conversation.json 不能交錯，不然開過幾次會被蓋回去
        with self._lock:
            conversation = conversations.read(self._data, conversation_id)
            if conversation.title == "新對話":
                conversation.title = message[:40]
                conversations.save(self._data, conversation)
        pi.send({"type": "prompt", "message": message, "streamingBehavior": "followUp"})
        return None

    def history(self, conversation_id: str) -> list[dict[str, Any]]:
        """完整的對話紀錄（Pi 的 `get_messages`）。"""
        pi = self.ensure(conversation_id)
        response = pi.request({"type": "get_messages"})
        messages = response.get("data", {}).get("messages", [])
        return messages if isinstance(messages, list) else []

    def kill(self, conversation_id: str) -> bool:
        """spike 用：殺掉這個對話的 Pi（模擬當機），下一次打開時接回。"""
        with self._lock:
            pi = self._live.pop(conversation_id, None)
        if pi is None:
            return False
        pi.kill()
        return True

    def jobs_progress(self, conversation_id: str) -> list[dict[str, Any]]:
        found = []
        for job_id in conversations.jobs(self._data, conversation_id):
            try:
                found.append(self._desk.status(job_id))
            except (OSError, ValueError):
                continue
        return found

    # --- 訂閱 -----------------------------------------------------------

    def subscribe(self, conversation_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subscribers.setdefault(conversation_id, set()).add(entry)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(conversation_id, set())
            for entry in list(subscribers):
                if entry[1] is queue:
                    subscribers.discard(entry)

    def _emit(self, conversation_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(conversation_id, ()))
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # 那個瀏覽器連線的事件迴圈已經關了
                continue

    # --- 工單 -----------------------------------------------------------

    def _watch(self) -> None:
        while not self._stop.wait(self._poll_s):
            with self._lock:
                live = {cid: pi for cid, pi in self._live.items() if pi.alive()}
            for conversation_id, pi in live.items():
                try:
                    self._check_jobs(conversation_id, pi)
                except Exception:
                    _log.exception("看對話 %s 的工單時出錯", conversation_id)

    def _check_jobs(self, conversation_id: str, pi: PiConversation) -> None:
        notified = self._notified.setdefault(conversation_id, set())
        for status in self.jobs_progress(conversation_id):
            job_id = status["job_id"]
            if self._progress.get((conversation_id, job_id)) != status:
                self._progress[(conversation_id, job_id)] = status
                self._emit(conversation_id, {"type": "ef_job", "at": time.time(), **status})
            if status["status"] in _FINISHED and job_id not in notified:
                # 擴充指令：Pi 正在回答時也會馬上執行，通知本身排在回答之後（deliverAs followUp）
                pi.send({"type": "prompt", "message": f"/ef-job-done {job_id}"})
                # 送成功才算送過：Pi 剛好死掉時 send 會丟例外，下一輪再送
                notified.add(job_id)
