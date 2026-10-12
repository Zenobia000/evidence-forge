"""對話主機（#85、ADR-0019、ADR-0021 §3.7）：在 `ef serve` 裡管所有對話的 Pi，把工單的進度與結果帶回對話。

**開、接回、閒置關掉**
- 送訊息或拿紀錄時，這個對話的 Pi 沒開就開（`--session-id` 是對話 id：第一次是新的，之後接回同一份紀錄）。
- 接回的順序照 ADR-0021：先收掉舊的 Pi（連它的程序群組、撤銷它的鑰匙），`launches` 加一，才登記新鑰匙開新的。
- 閒置 `idle_s` 秒（預設 10 分鐘）沒有任何動靜、也不在回答中，就關 stdin 請它收掉；下次有事再接回。
  計時器只在「下一個會到期的時間」醒來，不輪詢。
- 創作者的訊息一律帶 `streamingBehavior: followUp`：Pi 正在回答時，沒帶的 prompt 會被拒（spike 實測）。
  以 `/` 開頭的字不送，免得被當成 Pi 的指令（例如假冒 `/ef-job-done`）。

**工單跑完的通知**
- 執行器每改寫一次 `job.md`、閘道的告示板每變一次，都把工單 id 丟進這裡的佇列（`job_changed`）；
  工作台開了單也丟一次（`job_opened`）。一條工作執行緒從佇列拿出來處理，**沒有輪詢**。
- 工單結束而且列在對話的 `jobs.jsonl`：對那個對話的 Pi 送 `/ef-job-done <工單>`（Pi 沒開就先開）。
  ef-tools 照對話紀錄去重，同一張單只會有一則 `ef-job` 通知。
- 主機看到 `ef-job` 訊息的 `message_end`（已經進了 Pi 的紀錄）才把那張單記進 `conversation.json` 的
  `notified_jobs`。每次開 Pi、主機每次啟動，都把「結束了、還沒記進去的」全部再送一次：
  主機在通知半路被硬殺、Pi 回答到一半被關掉而排著的通知沒送到，重開後都補得回來。

**進度**
- 每次工單有變，算一次 `ef_job` 事件（`progress.py`），跟上次不一樣才推給訂閱這個對話的畫面。
- 訂閱時先送一份現況（Pi 開著沒有、每張工單的進度），之後只送變化。

事件格式（給 #86 的畫面；SSE 每則 `data:` 一個 JSON 物件，看 `type`）：
- Pi 的事件原樣轉送（`agent_start`、`message_update`、`message_end`、`tool_execution_*`、`queue_update`、`agent_settled`…；
  型別見 Pi 的 `docs/json.md`）。有人在等的 `response` 不轉送；被拒的指令變成 `ef_error`。
- `ef_job`：一張工單的進度，欄位見 `progress.job_progress`。
- `ef_pi`：`{"state": "starting"|"running"|"stopped", "launch", "reason", "busy"}`；`reason` 是
  `idle`（閒置關掉）、`shutdown`（主機關機）、`exited`（Pi 自己結束，帶 `returncode`）。
- `ef_conversation`：標題變了（`{"title"}`）。
- `ef_error`：`{"message"}`，例如 Pi 開不起來、指令被 Pi 拒絕。
"""

import asyncio
import contextlib
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from evidence_forge import conversations
from evidence_forge.budget import micros_to_usd
from evidence_forge.chat.pi_rpc import (
    PiExited,
    PiProcess,
    build_rpc_command,
    kill_leftover,
    pid_file,
    session_dir,
)
from evidence_forge.chat.progress import UNFINISHED, job_progress, same_progress
from evidence_forge.convkey import PREFIX, ConversationKey
from evidence_forge.documents import read_job
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import SpendTally
from evidence_forge.harness import Harness
from evidence_forge.models import Item, Recipe
from evidence_forge.paths import DataDir

_log = logging.getLogger(__name__)

# 閒置多久關掉：讀一份報告、想下一句要幾分鐘；一個開著的對話常駐約 115 MB（spike 量的）
DEFAULT_IDLE_S = 600.0
# 每條 SSE 連線最多積幾則沒送出去的事件；積滿表示那個畫面卡住了，斷掉讓它重連、用完整紀錄補回來
SUBSCRIBER_BACKLOG = 2000

Event = dict[str, Any]


class ConversationNotFound(LookupError):
    pass


class PromptRejected(RuntimeError):
    """Pi 拒絕了這則訊息（`response` 的 `success: false`）。"""


class Subscription:
    """一條 SSE 連線。事件從哪條執行緒來都可以，送進那條連線的事件迴圈的佇列。

    佇列裡的 `None` 表示串流該結束了（積太多、或主機關機）。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, backlog: int = SUBSCRIBER_BACKLOG) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(backlog)
        self._ended = False

    def put(self, event: Event | None) -> None:
        try:
            self.loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            # 那條連線的事件迴圈已經關了
            pass

    def _deliver(self, event: Event | None) -> None:
        if self._ended:
            return
        if event is None:
            self._end()
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self._end()

    def _end(self) -> None:
        self._ended = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


@dataclass
class _Live:
    pi: PiProcess
    last_active: float
    # 這次開的 Pi 已經送過 /ef-job-done 的工單；每次開都重來（舊的 Pi 排著沒送到的會丟）
    notified: set[str] = field(default_factory=set)
    busy: bool = False
    stop_reason: str = "exited"


class ConversationHost:
    def __init__(
        self,
        *,
        data: DataDir,
        harness: Harness,
        stages: ActiveStages,
        gateway_url: str,
        recipes: Mapping[str, Recipe],
        clock: Callable[[], datetime],
        board: JobBoard | None = None,
        idle_s: float = DEFAULT_IDLE_S,
        tier: str = "strong",
        base_env: Mapping[str, str] | None = None,
        request_timeout_s: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data = data
        self._harness = harness
        self._stages = stages
        self._gateway_url = gateway_url
        self._recipes = recipes
        self._clock = clock
        self._board = board
        self._idle_s = idle_s
        self._tier = tier
        self._base_env = os.environ if base_env is None else base_env
        self._request_timeout_s = request_timeout_s
        self._monotonic = monotonic
        # 保護下面幾個表；拿著它不做慢的事、不拿別的鎖
        self._lock = threading.Lock()
        self._live: dict[str, _Live] = {}
        self._subscribers: dict[str, set[Subscription]] = {}
        self._progress: dict[str, Event] = {}
        self._conversation_locks: dict[str, threading.Lock] = {}
        # conversation.json 的讀改寫（launches、title、notified_jobs）一次一個
        self._json_lock = threading.Lock()
        self._tally_lock = threading.Lock()
        self._tally = SpendTally(data)
        self._inbox: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._reaper_wake = threading.Event()
        self._closing = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- 生命週期 ---------------------------------------------------------------

    def start(self) -> None:
        """殺掉前一個主機留下的 Pi，開工作與閒置計時兩條執行緒，補送沒送到的通知。"""
        pids = self._data.run / "chat-pids"
        if pids.is_dir():
            for record in sorted(pids.glob("*.pid")):
                try:
                    kill_leftover(self._data, record.stem)
                except (RuntimeError, ValueError):
                    _log.warning("對話 %s 前一次的 Pi 收不掉", record.stem, exc_info=True)
        for target, name in ((self._work, "chat-host-work"), (self._reap, "chat-host-idle")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        self._inbox.put(("sweep", ""))

    def close(self) -> None:
        """關掉所有對話的 Pi（關 stdin、等它收掉，不行就殺），結束串流與兩條執行緒。"""
        self._closing.set()
        self._inbox.put(None)
        self._reaper_wake.set()
        for thread in self._threads:
            thread.join(10)
        # 執行緒都停了才收 Pi：工作執行緒不會在收完之後又開一個
        with self._lock:
            ids = list(self._live)
        for conversation_id in ids:
            self._stop(conversation_id, "shutdown")
        with self._lock:
            subscribers = [s for group in self._subscribers.values() for s in group]
        for subscription in subscribers:
            subscription.put(None)

    # --- 給畫面的 API 用 ----------------------------------------------------------

    def create(self) -> dict[str, Any]:
        conversation = conversations.create(self._data, self._clock())
        return self.summary(conversation.id)

    def list_conversations(self) -> list[dict[str, Any]]:
        """所有對話，最近有動靜的在上面。"""
        found = [self.summary(c.id) for c in conversations.list_all(self._data)]
        return sorted(found, key=lambda s: s["last_activity"], reverse=True)

    def summary(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._read(conversation_id)
        jobs = self.jobs(conversation_id)
        with self._tally_lock:
            spent = self._tally.job_micros(f"{PREFIX}{conversation_id}") + sum(
                self._tally.job_micros(job["job_id"]) for job in jobs
            )
        with self._lock:
            live = self._live.get(conversation_id)
        running = live is not None and live.pi.alive()
        return {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at.isoformat(),
            "last_activity": self._last_activity(conversation_id).isoformat(),
            "pi_running": running,
            "busy": running and live is not None and live.busy,
            "job_count": len(jobs),
            "latest_job": jobs[-1] if jobs else None,
            "spent_usd": micros_to_usd(spent),
            "budget_usd": conversation.budget_usd,
        }

    def jobs(self, conversation_id: str) -> list[Event]:
        """這個對話開過的工單的進度，照開的順序。"""
        found: list[Event] = []
        for row in conversations.jobs(self._data, conversation_id):
            progress = self._build(row.job_id)
            if progress is not None:
                found.append(progress)
        return found

    def send(self, conversation_id: str, text: str) -> dict[str, Any]:
        """送創作者的一句話；回 Pi 的回應的 `data`（`disposition`：started／queued）。

        錯誤：沒有這個對話丟 `ConversationNotFound`；訊息是空的或以 `/` 開頭丟 `ValueError`；
        Pi 拒絕丟 `PromptRejected`；Pi 開不起來或半路結束丟 `PiExited`。
        """
        message = text.strip()
        if not message:
            raise ValueError("訊息是空的")
        if message.startswith("/"):
            raise ValueError("以 / 開頭的訊息不送（會被當成 Pi 的指令）；前面加個字再送")
        self._read(conversation_id)
        with self._conversation_lock(conversation_id):
            live = self._ensure(conversation_id)
        response = live.pi.request(
            {"type": "prompt", "message": message, "streamingBehavior": "followUp"},
            timeout_s=self._request_timeout_s,
        )
        if not response.get("success"):
            raise PromptRejected(str(response.get("error") or "Pi 拒絕了這則訊息"))
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    def history(self, conversation_id: str) -> dict[str, Any]:
        """完整的對話紀錄（Pi 的 `get_messages`）與每張工單的進度；Pi 沒開就接回。"""
        self._read(conversation_id)
        with self._conversation_lock(conversation_id):
            live = self._ensure(conversation_id)
        response = live.pi.request({"type": "get_messages"}, timeout_s=self._request_timeout_s)
        data = response.get("data")
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return {
            "messages": messages if isinstance(messages, list) else [],
            "jobs": self.jobs(conversation_id),
        }

    def subscribe(
        self, conversation_id: str, loop: asyncio.AbstractEventLoop
    ) -> Subscription:
        """訂閱一個對話的事件；先送一份現況（Pi 的狀態、每張工單的進度）。"""
        self._read(conversation_id)
        subscription = Subscription(loop)
        with self._lock:
            self._subscribers.setdefault(conversation_id, set()).add(subscription)
            live = self._live.get(conversation_id)
            state: Event = {
                "type": "ef_pi",
                "state": "running" if live is not None and live.pi.alive() else "stopped",
                "launch": live.pi.key.launch if live is not None else None,
                "reason": None,
                "busy": live.busy if live is not None else False,
            }
        subscription.put(state)
        for progress in self.jobs(conversation_id):
            subscription.put(progress)
        return subscription

    def unsubscribe(self, conversation_id: str, subscription: Subscription) -> None:
        with self._lock:
            self._subscribers.get(conversation_id, set()).discard(subscription)

    def live_pid(self, conversation_id: str) -> int | None:
        """這個對話開著的 Pi 的程序編號；沒開是 None（測試與除錯用）。"""
        with self._lock:
            live = self._live.get(conversation_id)
        return live.pi.pid if live is not None and live.pi.alive() else None

    def stop_conversation(self, conversation_id: str, reason: str = "idle") -> None:
        """關掉這個對話的 Pi（閒置計時器用；測試也用它模擬閒置關掉）。"""
        self._stop(conversation_id, reason)

    # --- 事件的入口（執行器、告示板、工作台） --------------------------------------------

    def job_changed(self, owner_id: str) -> None:
        """執行器改寫了 `job.md`、告示板變了。只丟進佇列，不做事：呼叫的人可能在閘道的事件迴圈上。"""
        if owner_id.startswith(PREFIX):
            # 對話自己的 Pi 在等模型：畫面從 Pi 的事件就看得到，不是工單進度
            return
        self._inbox.put(("job", owner_id))

    def job_opened(self, conversation_id: str, job_id: str) -> None:
        """工作台替這個對話開了單（或拿回舊單）：馬上推一次進度，不用等執行器認領。"""
        self._inbox.put(("job", job_id))

    # --- Pi 的開與關 -------------------------------------------------------------------

    def _ensure(self, conversation_id: str) -> _Live:
        """這個對話開著的 Pi；沒開（或已經死了）就開一個新的接回同一份紀錄。呼叫的人拿著對話鎖。"""
        now = self._monotonic()
        with self._lock:
            live = self._live.get(conversation_id)
            if live is not None and live.pi.alive():
                live.last_active = now
                return live
        if live is not None:
            # 死掉的 Pi：等它的讀取執行緒收完尾（撤銷鑰匙）再開新的
            live.pi.join()
            with self._lock:
                if self._live.get(conversation_id) is live:
                    del self._live[conversation_id]
        if self._closing.is_set():
            raise PiExited("主機正在關機")
        # 先收掉前一個主機留下的（ADR-0021：先收舊的，再登記新鑰匙）
        kill_leftover(self._data, conversation_id)
        with self._json_lock:
            conversation = conversations.read(self._data, conversation_id)
            conversation.launches += 1
            conversations.save(self._data, conversation)
        key = ConversationKey(conversation_id, conversation.launches)
        command = build_rpc_command(
            harness=self._harness,
            data=self._data,
            key=key,
            gateway_url=self._gateway_url,
            base_env=self._base_env,
            tier=self._tier,
        )
        pi = PiProcess(
            command,
            key,
            self._stages,
            on_event=lambda record: self._on_pi_event(conversation_id, key, record),
            on_exit=lambda code: self._on_pi_exit(conversation_id, key, code),
            stderr_path=self._data.run / "chat" / f"{conversation_id}.stderr",
            pid_path=pid_file(self._data, conversation_id),
        )
        live = _Live(pi=pi, last_active=now)
        # 先放進表再開：Pi 一開就會送事件，事件要找得到它
        with self._lock:
            self._live[conversation_id] = live
        self._emit(
            conversation_id,
            {"type": "ef_pi", "state": "starting", "launch": key.launch, "reason": None, "busy": False},
        )
        try:
            pi.start()
        except BaseException as exc:
            with self._lock:
                if self._live.get(conversation_id) is live:
                    del self._live[conversation_id]
            self._emit(conversation_id, {"type": "ef_error", "message": f"對話的 Pi 開不起來：{exc}"})
            raise
        self._emit(
            conversation_id,
            {"type": "ef_pi", "state": "running", "launch": key.launch, "reason": None, "busy": False},
        )
        self._reaper_wake.set()
        # 新開的 Pi：結束了、還沒確定送到的通知全部再送一次（ef-tools 照紀錄去重）
        for job_id in self._unnotified(conversation_id):
            self._notify(conversation_id, live, job_id)
        return live

    def _stop(self, conversation_id: str, reason: str) -> None:
        with self._conversation_lock(conversation_id):
            with self._lock:
                live = self._live.get(conversation_id)
                if live is None:
                    return
                if reason == "idle" and (live.busy or not self._idle(live)):
                    # 拿到鎖之前剛好有人送了訊息：不關
                    return
                live.stop_reason = reason
            live.pi.stop()
            with self._lock:
                if self._live.get(conversation_id) is live:
                    del self._live[conversation_id]

    def _idle(self, live: _Live) -> bool:
        return self._monotonic() - live.last_active >= self._idle_s

    def _on_pi_event(self, conversation_id: str, key: ConversationKey, record: Event) -> None:
        """Pi 的讀取執行緒上：記下忙不忙、通知送到了沒有，再轉給畫面。"""
        kind = record.get("type")
        with self._lock:
            live = self._live.get(conversation_id)
            if live is not None and live.pi.key == key:
                live.last_active = self._monotonic()
                if kind == "agent_start":
                    live.busy = True
                elif kind == "agent_settled":
                    live.busy = False
        if kind == "agent_settled":
            # 閒置計時從答完這一刻算起
            self._reaper_wake.set()
        if kind == "response":
            # 沒有人在等的回應（通知指令的）：成功的不轉送，被拒的告訴畫面
            if not record.get("success"):
                self._emit(
                    conversation_id,
                    {"type": "ef_error", "message": str(record.get("error") or "Pi 拒絕了指令")},
                )
            return
        if kind == "message_end":
            delivered = _delivered_job(record)
            if delivered is not None:
                self._record_notified(conversation_id, delivered)
        self._emit(conversation_id, record)

    def _on_pi_exit(self, conversation_id: str, key: ConversationKey, returncode: int) -> None:
        with self._lock:
            live = self._live.get(conversation_id)
            reason = "exited"
            if live is not None and live.pi.key == key:
                reason = live.stop_reason
                if reason == "exited":
                    # 自己結束（當掉、被殺）：從表上拿掉，下次有事再接回
                    del self._live[conversation_id]
        self._emit(
            conversation_id,
            {
                "type": "ef_pi",
                "state": "stopped",
                "launch": key.launch,
                "reason": reason,
                "returncode": returncode,
                "busy": False,
            },
        )

    # --- 工單 -------------------------------------------------------------------------

    def _work(self) -> None:
        """從佇列拿事情做；一次把積著的都拿出來，同一張工單只處理一次。"""
        while True:
            item = self._inbox.get()
            if item is None:
                return
            batch = [item]
            with contextlib.suppress(queue.Empty):
                while True:
                    more = self._inbox.get_nowait()
                    if more is None:
                        return
                    batch.append(more)
            for kind, value in dict.fromkeys(batch):
                if self._closing.is_set():
                    return
                try:
                    if kind == "job":
                        self._on_job(value)
                    elif kind == "sweep":
                        self._sweep()
                except Exception:
                    _log.exception("對話主機處理 %s %s 時出錯", kind, value)

    def _on_job(self, job_id: str) -> None:
        progress = self._build(job_id)
        if progress is None:
            return
        conversation_id = progress.get("conversation_id")
        if not isinstance(conversation_id, str):
            return
        with self._lock:
            changed = not same_progress(self._progress.get(job_id), progress)
            if changed:
                self._progress[job_id] = progress
        if changed:
            self._emit(conversation_id, progress)
            self._maybe_title(conversation_id, job_id)
        if progress["finished"]:
            self._deliver(conversation_id, job_id)

    def _deliver(self, conversation_id: str, job_id: str) -> None:
        """工單結束：通知那個對話的 Pi（沒開就接回，接回時會一起補送）。"""
        if job_id not in self._unnotified(conversation_id):
            return
        try:
            with self._conversation_lock(conversation_id):
                live = self._ensure(conversation_id)
                self._notify(conversation_id, live, job_id)
        except Exception as exc:
            _log.warning("工單 %s 的結束通知沒送到對話 %s", job_id, conversation_id, exc_info=True)
            self._emit(conversation_id, {"type": "ef_error", "message": f"工單 {job_id} 的結束通知沒送到：{exc}"})

    def _notify(self, conversation_id: str, live: _Live, job_id: str) -> None:
        if job_id in live.notified:
            return
        # 擴充指令：Pi 正在回答時也會馬上執行；通知本身排在回答之後（ef-tools 的 deliverAs followUp）
        live.pi.send({"type": "prompt", "message": f"/ef-job-done {job_id}"})
        live.notified.add(job_id)
        live.last_active = self._monotonic()

    def _sweep(self) -> None:
        """主機啟動時：每個對話結束了、還沒確定送到的通知補送一次。"""
        for conversation in conversations.list_all(self._data):
            for job_id in self._unnotified(conversation.id):
                self._deliver(conversation.id, job_id)

    def _unnotified(self, conversation_id: str) -> list[str]:
        """列在 `jobs.jsonl`、已經結束、還沒記進 `notified_jobs` 的工單。"""
        try:
            done = set(conversations.read(self._data, conversation_id).notified_jobs)
        except (OSError, ValueError):
            return []
        found: list[str] = []
        for row in conversations.jobs(self._data, conversation_id):
            if row.job_id in done:
                continue
            try:
                status = read_job(self._data.job_dir(row.job_id) / "job.md").job.status
            except (OSError, ValueError):
                continue
            if status not in UNFINISHED:
                found.append(row.job_id)
        return found

    def _record_notified(self, conversation_id: str, job_id: str) -> None:
        with self._json_lock:
            try:
                conversation = conversations.read(self._data, conversation_id)
            except (OSError, ValueError):
                return
            if job_id in conversation.notified_jobs:
                return
            conversation.notified_jobs.append(job_id)
            conversations.save(self._data, conversation)

    def _maybe_title(self, conversation_id: str, job_id: str) -> None:
        """還叫「新對話」的對話，標題換成第一份素材的標題（IA §3）。"""
        with self._json_lock:
            try:
                conversation = conversations.read(self._data, conversation_id)
            except (OSError, ValueError):
                return
            if conversation.title != conversations.DEFAULT_TITLE:
                return
            title = self._item_title(job_id)
            if not title:
                return
            conversation.title = title[:80]
            conversations.save(self._data, conversation)
        self._emit(conversation_id, {"type": "ef_conversation", "title": conversation.title})

    def _item_title(self, job_id: str) -> str | None:
        job_dir = self._data.job_dir(job_id)
        try:
            job = read_job(job_dir / "job.md").job
        except (OSError, ValueError):
            return None
        root = self._data.root.resolve()
        for path in (job_dir / "input" / "item.json", self._data.root / job.feed.item / "item.json"):
            # feed.item 是 job.md 裡的字串：跳出資料目錄的不讀
            if not path.resolve().is_relative_to(root) or not path.is_file():
                continue
            try:
                return Item.model_validate_json(path.read_text(encoding="utf-8")).title
            except (OSError, ValueError):
                continue
        return None

    def _build(self, job_id: str) -> Event | None:
        try:
            with self._tally_lock:
                return job_progress(
                    self._data,
                    job_id,
                    recipes=self._recipes,
                    board=self._board,
                    tally=self._tally,
                    clock=self._clock,
                )
        except (OSError, ValueError):
            return None

    # --- 閒置計時 -----------------------------------------------------------------------

    def _reap(self) -> None:
        """睡到下一個對話會閒置到期的時間；到了就關掉。有新動靜時被叫醒重算。"""
        while not self._closing.is_set():
            now = self._monotonic()
            expired: list[str] = []
            next_deadline: float | None = None
            with self._lock:
                for conversation_id, live in self._live.items():
                    if live.busy:
                        continue
                    deadline = live.last_active + self._idle_s
                    if deadline <= now:
                        expired.append(conversation_id)
                    elif next_deadline is None or deadline < next_deadline:
                        next_deadline = deadline
            for conversation_id in expired:
                try:
                    self._stop(conversation_id, "idle")
                except Exception:
                    _log.exception("關掉閒置的對話 %s 時出錯", conversation_id)
            if expired:
                continue
            self._reaper_wake.wait(None if next_deadline is None else max(0.0, next_deadline - now))
            self._reaper_wake.clear()

    # --- 小工具 -------------------------------------------------------------------------

    @contextlib.contextmanager
    def _conversation_lock(self, conversation_id: str) -> Iterator[None]:
        with self._lock:
            lock = self._conversation_locks.setdefault(conversation_id, threading.Lock())
        with lock:
            yield

    def _read(self, conversation_id: str) -> conversations.Conversation:
        try:
            return conversations.read(self._data, conversation_id)
        except (OSError, ValueError):
            raise ConversationNotFound(conversation_id) from None

    def _emit(self, conversation_id: str, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(conversation_id, ()))
        for subscription in subscribers:
            subscription.put(event)

    def _last_activity(self, conversation_id: str) -> datetime:
        folder = self._data.conversation_dir(conversation_id)
        paths = [folder / "conversation.json", folder / "jobs.jsonl", folder / "ledger.jsonl"]
        sessions = session_dir(self._data, conversation_id)
        if sessions.is_dir():
            paths.extend(sessions.glob("*.jsonl"))
        stamps = [p.stat().st_mtime for p in paths if p.is_file()]
        moment = max(stamps) if stamps else 0.0
        return datetime.fromtimestamp(moment).astimezone()


def _delivered_job(record: Event) -> str | None:
    """`message_end` 是 ef-tools 放的 `ef-job` 通知的話，回它的工單 id。"""
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "custom":
        return None
    if message.get("customType") != "ef-job":
        return None
    details = message.get("details")
    job_id = details.get("job_id") if isinstance(details, dict) else None
    return job_id if isinstance(job_id, str) else None
