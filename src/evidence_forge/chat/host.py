"""對話主機（#85、ADR-0019、ADR-0021 §3.7）：在 `ef serve` 裡管所有對話的 Pi，把工單的進度與結果帶回對話。

**開、接回、閒置關掉**
- 送訊息或拿紀錄時，這個對話的 Pi 沒開就開（`--session-id` 是對話 id：第一次是新的，之後接回同一份紀錄）。
- 接回的順序照 ADR-0021：先收掉舊的 Pi（連它的程序群組、撤銷它的鑰匙），`launches` 加一，才登記新鑰匙開新的。
- 閒置 `idle_s` 秒（預設 10 分鐘）沒有任何動靜、也不在回答中，就關 stdin 請它收掉；下次有事再接回。
  「回答中」從送出 prompt 那一刻算起（Pi 接受了但 `agent_start` 還沒來也算），到 `agent_settled`、
  被拒或只是被擴充指令處理掉為止。通知指令處理完之後再留 `NOTICE_GRACE_S`（30 秒）寬限，
  給它用 triggerTurn 開的那一輪等 `agent_start`；`agent_settled` 一來就收回寬限。計時器只在「下一個會到期的時間」醒來，不輪詢。
- 關掉時拿不到對話鎖（有人卡在寫給這個 Pi）就直接殺 Pi，卡住的寫入跟著失敗放手：關機不會卡住。
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
  開 Pi 時先用 `get_messages` 看紀錄：已經有那則通知、只是沒記到的，直接補記，不再送。
- 送出去但沒送成（ef-tools 的指令丟錯，Pi 發 `extension_error` 卻照樣回成功；或指令被拒）：
  過 `notice_retry_s` 秒再送，同一個 Pi 最多重試 `MAX_NOTICE_RETRIES` 次，之後等下次接回。

**編輯卡（ADR-0025 §3.3）**
- 創作者在畫面上點的（送出一關、沿用、重問）由這裡直接叫 `cards.py` 寫進決策紀錄（`source: ui`），不經 agent 轉述；
  閘道的 `/tools/` 沒有這三個，對話的 Pi 叫不到。
- 寫完排一件事給工作執行緒：對這個對話的 Pi 送 `/ef-card <卡> <最後一筆的 n> <submit|keep|reask> <關>`
  （Pi 沒開就先開）。ef-tools 放一則 `ef-card` 通知、觸發一輪，agent 讀編輯卡接著往下；同一筆照紀錄去重。
  沒送成只推一則 `ef_error`，不重試：點選已經記下了，創作者在聊天裡說一句 agent 一樣讀得到。
- 不管誰寫的（畫面，或閘道替 agent 寫完叫 `card_changed`），都推一則 `ef_card` 給畫面。
- lessons 的提議（#114、ADR-0026 §3.3）同一個樣子：agent 提議（閘道的 `propose_lesson`）→ `lesson_changed` 推 `ef_lesson`
  給畫面，畫面放「同意／不要」的小卡；創作者按了，這裡直接叫 `lesson_proposals.py`（同意由閘道寫角色的 `lessons.md`，
  不要什麼都不寫），推 `ef_lesson`，再排一則 `/ef-lesson <提議> <approved|rejected> <角色>` 給 Pi：ef-tools 只在對話裡
  放一則 `ef-lesson` 通知，**不開新的一輪**（不為了一個按鈕多花一次模型）；agent 下一輪就讀得到。
  同意與不要的入口只在這裡（畫面的 API），閘道的 `/tools/` 拿任何鑰匙來都 403。
  通知不能只排在記憶體裡：看到 `ef-lesson` 訊息的 `message_end`（已經進了 Pi 的紀錄）才在 `lessons-proposed.jsonl` 記
  `notified`；每次開 Pi、主機每次啟動，都把「決定了、還沒有 `notified`」的全部再送一次（先用 `get_messages` 看紀錄，
  已經在裡面的直接補記）。送出去但沒送成（`ef-lesson` 丟錯、被拒）過 `notice_retry_s` 秒再送，同一個 Pi 最多
  `MAX_NOTICE_RETRIES` 次，之後推一則 `ef_error`、等下次接回。
- 「照這張編輯卡寫稿」（#110）也是畫面按的：交給工作台的 `write_from_card` 開寫稿單（`use_desk` 接上），
  開了的單照一般工單推進度、結束時送 `/ef-job-done`。對話的 agent 叫不到它。

**進度**
- 每次工單有變，算一次 `ef_job` 事件（`progress.py`），跟上次不一樣才推給訂閱這個對話的畫面。
- 訂閱時先送一份現況（Pi 開著沒有、每張工單的進度），之後只送變化。現況與變化排同一個順序：
  每則事件帶主機的流水號 `seq`，畫面看到同一張工單 `seq` 比較小的就丟掉。
- 記憶體有上限：佇列同一件事只排一次；每條 SSE 連線同一張工單的進度只留最新的一筆，
  其他事件積到 `SUBSCRIBER_BACKLOG` 則還沒讀走就斷開那條連線（畫面重連、用完整紀錄補）。

事件格式（給 #86 的畫面；SSE 每則 `data:` 一個 JSON 物件，看 `type`；每則都有 `seq`，主機重開從 1 重算）：
- Pi 的事件原樣轉送（`agent_start`、`message_update`、`message_end`、`tool_execution_*`、`queue_update`、`agent_settled`…；
  型別見 Pi 的 `docs/json.md`）。有人在等的 `response` 不轉送；被拒的指令變成 `ef_error`。
- `ef_job`：一張工單的進度，欄位見 `progress.job_progress`。
- `ef_pi`：`{"state": "starting"|"running"|"stopped", "launch", "reason", "busy"}`；`reason` 是
  `idle`（閒置關掉）、`shutdown`（主機關機）、`exited`（Pi 自己結束，帶 `returncode`）。
- `ef_conversation`：標題變了（`{"title"}`）。
- `ef_card`：`{"card_id", "events": [這次寫的事件], "card": 編輯卡}`；`events` 的欄位見 `cards.py`，`card` 是 `card.json`
  的樣子（`cards.CardState`）。訂閱時每張卡先送一則 `events: []` 的現況。畫面照 `card` 整張換掉就好。
- `ef_lesson`：`{"events": [這次寫的事件], "proposals": [這個對話所有 lesson 提議現在的樣子]}`（欄位見
  `lesson_proposals.Proposal`）。訂閱時先送一則 `events: []` 的現況（有提議才送）。畫面照 `proposals` 整份換掉就好。
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
from functools import partial
from datetime import datetime
from typing import Any, Protocol

from evidence_forge import cards, conversations, lesson_proposals
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
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.harness import Harness
from evidence_forge.models import Item, Recipe
from evidence_forge.paths import DataDir

_log = logging.getLogger(__name__)

# 閒置多久關掉：讀一份報告、想下一句要幾分鐘；一個開著的對話常駐約 115 MB（spike 量的）
DEFAULT_IDLE_S = 600.0
# 每條 SSE 連線最多積幾則沒送出去的事件；積滿表示那個畫面卡住了，斷掉讓它重連、用完整紀錄補回來
SUBSCRIBER_BACKLOG = 2000
# 結束通知沒送成時，同一個 Pi 最多再送幾次；之後等下次開 Pi 或主機重開
MAX_NOTICE_RETRIES = 3
# 關 Pi 時等對話鎖多久；等不到表示有人卡在寫給這個 Pi，直接殺
STOP_LOCK_WAIT_S = 1.0
# 通知指令處理完之後多久之內不准閒置關閉。ef-tools 用 triggerTurn 開的那一輪，在 agent_start 之前
# 沒有任何訊號；同一張單已經通知過時根本不開新的一輪，Pi 也不會說。所以給寬限，不等一個可能不來的訊號
NOTICE_GRACE_S = 30.0

Event = dict[str, Any]


class ConversationNotFound(LookupError):
    pass


class WritingDesk(Protocol):
    """開寫稿工單的那一層（`chat/desk.py` 的 `Desk`）。"""

    def write_from_card(
        self, conversation_id: str, card_id: str, confirmed_events: object
    ) -> dict[str, Any]: ...


class PromptRejected(RuntimeError):
    """Pi 拒絕了這則訊息（`response` 的 `success: false`）。"""


class Subscription:
    """一條 SSE 連線。事件從哪條執行緒來都可以，送進那條連線的事件迴圈。

    有上限：排進事件迴圈、還沒被讀走的事件超過 `backlog` 則就結束這條連線。
    同一張工單的進度（`ef_job`）還沒送出時再來一筆，只留最新的那一筆，不多佔位置。
    `get()` 回 `None` 表示串流該結束了（積太多、或主機關機）。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, backlog: int = SUBSCRIBER_BACKLOG) -> None:
        self.loop = loop
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._backlog = backlog
        self._lock = threading.Lock()
        # 已經排進事件迴圈（或在佇列裡）、還沒被 get 讀走的則數
        self._outstanding = 0
        # 同一張工單還沒送出的最新進度
        self._latest_jobs: dict[str, Event] = {}
        self._ended = False

    def put(self, event: Event | None) -> None:
        with self._lock:
            if self._ended:
                return
            if event is None or self._outstanding >= self._backlog:
                self._ended = True
                item: tuple[str, Any] = ("end", None)
            elif event.get("type") == "ef_job":
                job_id = str(event.get("job_id"))
                already = job_id in self._latest_jobs
                self._latest_jobs[job_id] = event
                if already:
                    # 已經排了一則，送出時拿最新的
                    return
                self._outstanding += 1
                item = ("job", job_id)
            else:
                self._outstanding += 1
                item = ("event", event)
        try:
            self.loop.call_soon_threadsafe(self._deliver, item)
        except RuntimeError:
            # 那條連線的事件迴圈已經關了
            pass

    def _deliver(self, item: tuple[str, Any]) -> None:
        kind, value = item
        if kind == "end":
            self._queue.put_nowait(None)
            return
        if kind == "job":
            with self._lock:
                event = self._latest_jobs.pop(value, None)
            if event is not None:
                self._queue.put_nowait(event)
            return
        self._queue.put_nowait(value)

    async def get(self) -> Event | None:
        event = await self._queue.get()
        if event is not None:
            with self._lock:
                self._outstanding -= 1
        return event


@dataclass
class _Live:
    pi: PiProcess
    last_active: float
    # 這次開的 Pi 已經送過 /ef-job-done 的工單；每次開都重來（舊的 Pi 排著沒送到的會丟）
    notified: set[str] = field(default_factory=set)
    # 送出去、還沒收到回應的通知指令：指令 id → 工單
    notice_ids: dict[str, str] = field(default_factory=dict)
    # 送出去、還沒收到回應的 /ef-card 指令 id
    card_notice_ids: set[str] = field(default_factory=set)
    # 這次開的 Pi 已經送過 /ef-lesson 的提議（`<編號> <決定>`）；每次開都重來
    lessons_sent: set[str] = field(default_factory=set)
    # 重試用完、放棄的 `<編號> <決定>`：同一個 Pi 不再送（下一個決定來也不會把它帶出去），下次開 Pi 才重來
    lessons_given_up: set[str] = field(default_factory=set)
    # 送出去、還沒收到回應的 /ef-lesson 指令：指令 id → `<編號> <決定>`
    lesson_notice_ids: dict[str, str] = field(default_factory=dict)
    # 這次開的 Pi 每張單（或每筆 lesson 提議）重送過幾次
    retries: dict[str, int] = field(default_factory=dict)
    # Pi 正在回答（agent_start 到 agent_settled）
    busy: bool = False
    # 送出去、Pi 還沒開始也還沒拒絕的 prompt：這段時間也不能閒置關掉
    pending: int = 0
    # 這個時間（monotonic）之前不准閒置關閉：通知指令處理完、它開的那一輪還沒 agent_start 的空檔
    hold_until: float = 0.0
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
        tier: str = "fast",
        base_env: Mapping[str, str] | None = None,
        request_timeout_s: float = 30.0,
        notice_retry_s: float = 5.0,
        notice_grace_s: float = NOTICE_GRACE_S,
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
        self._notice_retry_s = notice_retry_s
        self._notice_grace_s = notice_grace_s
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
        # 佇列裡已經排著的事：同一件事只排一次，佇列的長度就不會超過工單與對話的數目
        self._queued: set[tuple[str, str]] = set()
        # 推給畫面的事件排同一個順序（訂閱時的現況也是）；流水號給畫面丟掉較舊的
        self._emit_lock = threading.RLock()
        self._seq = 0
        self._reaper_wake = threading.Event()
        self._closing = threading.Event()
        self._threads: list[threading.Thread] = []
        # 「照這張編輯卡寫稿」交給它開單；`ef serve` 建好工作台之後接上（工作台要拿主機的回呼，所以晚一步）
        self._desk: WritingDesk | None = None

    def use_desk(self, desk: WritingDesk) -> None:
        """接上開寫稿單的工作台（`ef serve` 啟動時）。"""
        self._desk = desk

    # --- 生命週期 ---------------------------------------------------------------

    def start(self) -> None:
        """殺掉前一個主機留下的 Pi，開工作與閒置計時兩條執行緒，補送沒送到的通知。"""
        pids = self._data.run / "chat-pids"
        if pids.is_dir():
            for record in sorted(pids.glob("*.pid")):
                conversation_id = record.stem
                with self._conversation_lock(conversation_id):
                    with self._lock:
                        mine = conversation_id in self._live
                    if mine:
                        # 這個主機自己開的（start 之前就有訊息送到）：不是孤兒
                        continue
                    try:
                        kill_leftover(self._data, conversation_id)
                    except (RuntimeError, ValueError):
                        _log.warning("對話 %s 前一次的 Pi 收不掉", conversation_id, exc_info=True)
        for target, name in ((self._work, "chat-host-work"), (self._reap, "chat-host-idle")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        self._enqueue(("sweep", ""))

    def close(self) -> None:
        """關掉所有對話的 Pi（關 stdin、等它收掉，不行就殺），結束串流與兩條執行緒。"""
        self._closing.set()
        self._inbox.put(None)
        self._reaper_wake.set()
        # 先收 Pi：工作執行緒若卡在寫給某個 Pi，殺掉它才放得開
        self._stop_all()
        for thread in self._threads:
            thread.join(10)
        # 執行緒停下之前剛好開出來的那一個（`_closing` 之後就不會再開）
        self._stop_all()
        with self._lock:
            subscribers = [s for group in self._subscribers.values() for s in group]
        for subscription in subscribers:
            subscription.put(None)

    def _stop_all(self) -> None:
        with self._lock:
            ids = list(self._live)
        for conversation_id in ids:
            self._stop(conversation_id, "shutdown")

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
            "seen_job": conversation.seen_job,
        }

    def mark_seen(self, conversation_id: str, job_id: str) -> None:
        """創作者看過這張工單的結果了（畫面打開對話時回報）；清單據此不再標「有新結果」。

        工單不是這個對話開的丟 `ValueError`（不讓畫面把別的對話的單記進來）。
        """
        self._read(conversation_id)
        if not conversations.owns_job(self._data, conversation_id, job_id):
            raise ValueError("這張工單不是這個對話開的")
        with self._json_lock:
            conversation = conversations.read(self._data, conversation_id)
            if conversation.seen_job == job_id:
                return
            conversation.seen_job = job_id
            conversations.save(self._data, conversation)

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
            # 從送出這一刻起就算「有事在做」：Pi 接受了、agent_start 還沒來的空檔不能被閒置關掉
            with self._lock:
                live.pending += 1
        started = False
        try:
            response = live.pi.request(
                {"type": "prompt", "message": message, "streamingBehavior": "followUp"},
                timeout_s=self._request_timeout_s,
            )
            if not response.get("success"):
                raise PromptRejected(str(response.get("error") or "Pi 拒絕了這則訊息"))
            data = response.get("data")
            found = data if isinstance(data, dict) else {}
            # started／queued：要等 agent_settled 才算做完；handled 表示沒有要跑的
            started = found.get("disposition") in ("started", "queued")
            return found
        finally:
            if not started:
                self._settle_pending(live)

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
        # 現況與之後的變化排同一個順序：拿著推事件的鎖組現況、登記訂閱，
        # 這段時間沒有別的事件能插進來，也不會有比現況舊的事件排在後面
        with self._emit_lock:
            with self._lock:
                live = self._live.get(conversation_id)
                state: Event = {
                    "type": "ef_pi",
                    "state": "running" if live is not None and live.pi.alive() else "stopped",
                    "launch": live.pi.key.launch if live is not None else None,
                    "reason": None,
                    "busy": live.busy if live is not None else False,
                }
            snapshot = [
                state,
                *self.jobs(conversation_id),
                *(
                    _card_event(card.card_id, [], card)
                    for card in cards.list_cards(self._data, conversation_id)
                ),
                *self._lesson_snapshot(conversation_id),
            ]
            for event in snapshot:
                subscription.put(self._stamp(event))
            with self._lock:
                self._subscribers.setdefault(conversation_id, set()).add(subscription)
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

    # --- 編輯卡（ADR-0025 §3.3） -----------------------------------------------------------

    def list_cards(self, conversation_id: str) -> list[dict[str, Any]]:
        self._read(conversation_id)
        return [c.model_dump(mode="json") for c in cards.list_cards(self._data, conversation_id)]

    def card(self, conversation_id: str, card_id: str) -> dict[str, Any]:
        """一張卡的編輯卡與完整的決策紀錄（決策分頁要列問過的、沒選的）。"""
        self._read(conversation_id)
        return {
            "card": cards.read_card(self._data, conversation_id, card_id).model_dump(mode="json"),
            "events": cards.read_events(self._data, conversation_id, card_id),
        }

    def submit_station(
        self, conversation_id: str, card_id: str, station: object, answers: object
    ) -> dict[str, Any]:
        """畫面的「確認並送出這一關」。錯誤丟 `ToolRejected`（帶 HTTP 狀態碼）。"""
        self._read(conversation_id)
        written = cards.submit_station(
            self._data, conversation_id, card_id, station, answers, now=self._clock()
        )
        return self._clicked(conversation_id, written, "submit", station)

    def keep_decision(self, conversation_id: str, card_id: str, station: object) -> dict[str, Any]:
        """畫面的「沿用」。"""
        self._read(conversation_id)
        written = cards.keep_decision(self._data, conversation_id, card_id, station, now=self._clock())
        return self._clicked(conversation_id, written, "keep", station)

    def reask_station(self, conversation_id: str, card_id: str, station: object) -> dict[str, Any]:
        """畫面的「重問這一關」。"""
        self._read(conversation_id)
        written = cards.reask_station(self._data, conversation_id, card_id, station, now=self._clock())
        return self._clicked(conversation_id, written, "reask", station)

    def write_from_card(
        self, conversation_id: str, card_id: str, confirmed_events: object
    ) -> dict[str, Any]:
        """畫面的「照這張編輯卡寫稿」（`confirmed_events` 是確認時看到的事件筆數）。錯誤丟 `ToolRejected`。

        開了單由工作台記進 `jobs.jsonl` 並通知這裡（`job_opened`），之後照一般工單推進度。
        """
        self._read(conversation_id)
        if self._desk is None:
            raise ToolRejected(503, "這個 ef serve 沒有接上寫稿的工作台")
        return self._desk.write_from_card(conversation_id, card_id, confirmed_events)

    def card_changed(
        self, conversation_id: str, card_id: str, events: list[dict[str, Any]]
    ) -> None:
        """閘道替對話的 agent 寫了編輯卡（工作台的 `on_card`）：推給畫面。可能在閘道的執行緒上，不做慢的事。"""
        try:
            card = cards.read_card(self._data, conversation_id, card_id)
        except ToolRejected:
            return
        self._emit(conversation_id, _card_event(card_id, events, card))

    # --- lessons 的提議（ADR-0026 §3.3、#114） -----------------------------------------------

    def list_lessons(self, conversation_id: str) -> dict[str, Any]:
        """這個對話所有的 lesson 提議（含已同意、不要的）。"""
        self._read(conversation_id)
        return {"proposals": self._proposals(conversation_id)}

    def approve_lesson(self, conversation_id: str, proposal_id: object) -> dict[str, Any]:
        """畫面的「同意」：由閘道（這裡）把那一行附加到角色的 `lessons.md`。錯誤丟 `ToolRejected`。"""
        self._read(conversation_id)
        written = lesson_proposals.approve(self._data, conversation_id, proposal_id, now=self._clock())
        return self._lesson_decided(conversation_id, written, "approved")

    def reject_lesson(self, conversation_id: str, proposal_id: object) -> dict[str, Any]:
        """畫面的「不要」：什麼都不寫。"""
        self._read(conversation_id)
        written = lesson_proposals.reject(self._data, conversation_id, proposal_id, now=self._clock())
        return self._lesson_decided(conversation_id, written, "rejected")

    def lesson_changed(self, conversation_id: str, events: list[dict[str, Any]]) -> None:
        """閘道替對話的 agent 記了 lesson 提議（工作台的 `on_lesson`）：推給畫面。可能在閘道的執行緒上，不做慢的事。"""
        try:
            proposals = self._proposals(conversation_id)
        except ToolRejected:
            return
        self._emit(conversation_id, _lesson_event(events, proposals))

    def _proposals(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {**p.model_dump(mode="json"), "preview": p.preview}
            for p in lesson_proposals.read_proposals(self._data, conversation_id)
        ]

    def _lesson_snapshot(self, conversation_id: str) -> list[Event]:
        try:
            proposals = self._proposals(conversation_id)
        except (ToolRejected, OSError, ValueError):
            return []
        return [_lesson_event([], proposals)] if proposals else []

    def _lesson_decided(
        self, conversation_id: str, written: lesson_proposals.Written, decision: str
    ) -> dict[str, Any]:
        """畫面按完：推給畫面，再排一則 `/ef-lesson` 給這個對話的 Pi（工作執行緒送，不讓畫面等 Pi 開起來）。
        冪等的重按（沒有新事件）不再通知 agent。"""
        self.lesson_changed(conversation_id, written.events)
        proposal = written.proposal
        # 重按（沒有新事件）但上次的通知還沒確定送到：也排一次，已經送過的不會重送
        if written.events or not proposal.notified:
            self._enqueue(("lesson", conversation_id))
        return {
            "proposal": {**proposal.model_dump(mode="json"), "preview": proposal.preview},
            "events": written.events,
        }

    def _lessons_unnotified(self, conversation_id: str) -> list[lesson_proposals.Proposal]:
        try:
            return lesson_proposals.unnotified(self._data, conversation_id)
        except (ToolRejected, OSError, ValueError):
            return []

    def _notify_lessons(self, conversation_id: str, live: _Live) -> None:
        """決定了、還沒確定通知到的 lesson 提議：送 `/ef-lesson`（這個 Pi 送過的不重送）。呼叫的人拿著對話鎖。"""
        for proposal in self._lessons_unnotified(conversation_id):
            sent = f"{proposal.id} {proposal.status}"
            with self._lock:
                if sent in live.lessons_sent or sent in live.lessons_given_up:
                    continue
                live.lessons_sent.add(sent)
                live.pending += 1
                live.last_active = self._monotonic()
            # 回應可能在 send 回傳之前就到：先登記再寫（`PiProcess.send` 的 `on_id`）
            register = partial(self._register_lesson_notice, live, sent)
            try:
                live.pi.send(
                    {"type": "prompt", "message": f"/ef-lesson {proposal.id} {proposal.status} {proposal.persona}"},
                    on_id=register,
                )
            except BaseException:
                with self._lock:
                    live.lessons_sent.discard(sent)
                    for request_id in [r for r, key in live.lesson_notice_ids.items() if key == sent]:
                        del live.lesson_notice_ids[request_id]
                self._settle_pending(live)
                raise

    def _register_lesson_notice(self, live: _Live, sent: str, request_id: str) -> None:
        with self._lock:
            live.lesson_notice_ids[request_id] = sent

    def _deliver_lessons(self, conversation_id: str) -> None:
        """排著的 lesson 通知：送進這個對話的 Pi（沒開就接回，接回時會一起補送）；沒送成告訴畫面。"""
        try:
            with self._conversation_lock(conversation_id):
                live = self._ensure(conversation_id)
                self._notify_lessons(conversation_id, live)
        except Exception as exc:
            _log.warning("lesson 的決定沒通知到對話 %s", conversation_id, exc_info=True)
            self._emit(
                conversation_id,
                {"type": "ef_error", "message": f"lesson 的決定已經記下，但沒通知到對話：{exc}"},
            )

    def _record_lesson_notified(self, conversation_id: str, delivered: tuple[str, str]) -> None:
        lesson_proposals.mark_notified(
            self._data, conversation_id, delivered[0], delivered[1], now=self._clock()
        )

    def _retry_lessons(self, conversation_id: str, live: _Live, sent: list[str]) -> None:
        """沒送成的 lesson 通知：從「送過」拿掉（它還沒有 `notified`），過一下再排一次；重試太多次就等下次接回。"""
        retry = False
        given_up: list[str] = []
        with self._lock:
            for key in sent:
                live.lessons_sent.discard(key)
                tries = live.retries.get(f"lesson {key}", 0) + 1
                live.retries[f"lesson {key}"] = tries
                if tries <= MAX_NOTICE_RETRIES:
                    retry = True
                else:
                    live.lessons_given_up.add(key)
                    given_up.append(key.split(" ")[0])
        for proposal in given_up:
            self._emit(
                conversation_id,
                {"type": "ef_error", "message": f"lesson {proposal} 的決定送不進對話，下次接回時再送"},
            )
        if retry and not self._closing.is_set():
            timer = threading.Timer(
                self._notice_retry_s, self._enqueue, args=(("lesson", conversation_id),)
            )
            timer.daemon = True
            timer.start()

    def _clicked(
        self, conversation_id: str, written: cards.Written, action: str, station: object
    ) -> dict[str, Any]:
        """畫面寫完：推給畫面，再排一則 `/ef-card` 給這個對話的 Pi（工作執行緒送，不讓畫面等 Pi 開起來）。

        推給畫面的卡在推的當下重讀（`card_changed`），不用寫入時算的那一份：agent 剛好接著寫了一筆的話，
        較舊的那一份不會晚到、蓋掉較新的。畫面另外可以照 `card.events` 丟掉筆數比手上少的。
        """
        self.card_changed(conversation_id, written.card_id, written.events)
        if written.events:
            last = written.events[-1]["n"]
            self._enqueue(("card", f"{conversation_id} {written.card_id} {last} {action} {station}"))
        return {"card": written.card.model_dump(mode="json"), "events": written.events}

    def _register_card_notice(self, live: _Live, registered: list[str], request_id: str) -> None:
        with self._lock:
            live.card_notice_ids.add(request_id)
        registered.append(request_id)

    def _deliver_card(self, conversation_id: str, command: str) -> None:
        """把 `/ef-card …` 送進這個對話的 Pi（沒開就接回）；沒送成告訴畫面，不重試。"""
        try:
            with self._conversation_lock(conversation_id):
                live = self._ensure(conversation_id)
                with self._lock:
                    live.pending += 1
                    live.last_active = self._monotonic()
                registered: list[str] = []
                try:
                    # 回應可能在 send 回傳之前就到：先登記再寫
                    live.pi.send(
                        {"type": "prompt", "message": command},
                        on_id=partial(self._register_card_notice, live, registered),
                    )
                except BaseException:
                    # 寫不出去：剛登記的 id 不會有回應了
                    with self._lock:
                        live.card_notice_ids.difference_update(registered)
                    self._settle_pending(live)
                    raise
        except Exception as exc:
            _log.warning("編輯卡的通知沒送到對話 %s", conversation_id, exc_info=True)
            self._emit(
                conversation_id,
                {"type": "ef_error", "message": f"編輯卡的變化已經記下，但沒通知到對話：{exc}"},
            )

    # --- 事件的入口（執行器、告示板、工作台） --------------------------------------------

    def job_changed(self, owner_id: str) -> None:
        """執行器改寫了 `job.md`、告示板變了。只丟進佇列，不做事：呼叫的人可能在閘道的事件迴圈上。"""
        if owner_id.startswith(PREFIX):
            # 對話自己的 Pi 在等模型：畫面從 Pi 的事件就看得到，不是工單進度
            return
        self._enqueue(("job", owner_id))

    def job_opened(self, conversation_id: str, job_id: str) -> None:
        """工作台替這個對話開了單（或拿回舊單）：馬上推一次進度，不用等執行器認領。"""
        self._enqueue(("job", job_id))

    def _enqueue(self, item: tuple[str, str]) -> None:
        """同一件事已經排著就不再排：之後處理時讀的是當下的狀態，排一次就夠。"""
        with self._lock:
            if item in self._queued:
                return
            self._queued.add(item)
        self._inbox.put(item)

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
        # 新開的 Pi：結束了、還沒確定送到的通知全部再送一次（ef-tools 照紀錄去重）。
        # 送之前先看紀錄：通知已經在裡面、只是主機沒記到的（記之前被硬殺），補記就好
        pending = self._unnotified(conversation_id)
        lessons_pending = self._lessons_unnotified(conversation_id)
        if pending or lessons_pending:
            self._backfill_notified(conversation_id, pi)
            pending = self._unnotified(conversation_id)
        for job_id in pending:
            self._notify(conversation_id, live, job_id)
        # 決定了、還沒確定通知到的 lesson：主機在送到之前當掉也補得回來（紀錄裡已經有的，上面補記過了）
        self._notify_lessons(conversation_id, live)
        return live

    def _backfill_notified(self, conversation_id: str, pi: PiProcess) -> None:
        try:
            response = pi.request({"type": "get_messages"}, timeout_s=self._request_timeout_s)
        except (PiExited, TimeoutError):
            _log.warning("對話 %s 讀不到紀錄，通知照樣重送（ef-tools 會去重）", conversation_id)
            return
        data = response.get("data")
        messages = data.get("messages") if isinstance(data, dict) else None
        for message in messages if isinstance(messages, list) else []:
            if not isinstance(message, dict):
                continue
            job_id = _notice_job(message)
            if job_id is not None:
                self._record_notified(conversation_id, job_id)
            lesson = _notice_lesson(message)
            if lesson is not None:
                self._record_lesson_notified(conversation_id, lesson)

    def _stop(self, conversation_id: str, reason: str) -> None:
        lock = self._lock_for(conversation_id)
        if not lock.acquire(timeout=STOP_LOCK_WAIT_S):
            if reason == "idle":
                # 有人正拿著這個對話在做事：不是閒置
                return
            # 有人拿著對話鎖卡住（多半卡在寫給這個 Pi）：先殺 Pi，卡住的寫入跟著失敗、放開鎖
            with self._lock:
                stuck = self._live.get(conversation_id)
                if stuck is not None:
                    stuck.stop_reason = reason
            if stuck is not None:
                stuck.pi.kill()
            lock.acquire()
        try:
            with self._lock:
                live = self._live.get(conversation_id)
                if live is None:
                    return
                if reason == "idle" and not self._idle(live):
                    # 拿到鎖之前剛好有人送了訊息：不關
                    return
                live.stop_reason = reason
            live.pi.stop()
            with self._lock:
                if self._live.get(conversation_id) is live:
                    del self._live[conversation_id]
        finally:
            lock.release()

    def _idle(self, live: _Live) -> bool:
        """閒置：沒在回答、沒有送出去還沒開始的 prompt、而且安靜夠久了。拿著 `_lock` 呼叫。"""
        if live.busy or live.pending > 0:
            return False
        now = self._monotonic()
        return now >= live.hold_until and now - live.last_active >= self._idle_s

    def _settle_pending(self, live: _Live) -> None:
        with self._lock:
            live.pending = max(0, live.pending - 1)
        self._reaper_wake.set()

    def _on_pi_event(self, conversation_id: str, key: ConversationKey, record: Event) -> None:
        """Pi 的讀取執行緒上：記下忙不忙、通知送到了沒有，再轉給畫面。"""
        kind = record.get("type")
        notice: str | None = None
        lesson_notice: str | None = None
        with self._lock:
            live = self._live.get(conversation_id)
            if live is None or live.pi.key != key:
                live = None
            else:
                live.last_active = self._monotonic()
                if kind == "agent_start":
                    live.busy = True
                elif kind == "agent_settled":
                    # 答完了，排著的也都做完了：送出去的 prompt 全部算數
                    live.busy = False
                    live.pending = 0
                    # 通知開的那一輪也做完了（或排著的都處理完、根本沒開）：寬限不用再留
                    live.hold_until = 0.0
                elif kind == "response":
                    notice = live.notice_ids.pop(str(record.get("id")), None)
                    card_notice = str(record.get("id")) in live.card_notice_ids
                    live.card_notice_ids.discard(str(record.get("id")))
                    lesson_notice = live.lesson_notice_ids.pop(str(record.get("id")), None)
                    if notice is not None or card_notice or lesson_notice is not None:
                        # 擴充指令的回應在處理函式跑完之後才來
                        live.pending = max(0, live.pending - 1)
                        if record.get("success"):
                            # 它可能開了一輪（triggerTurn），agent_start 還沒到：寬限內不准閒置關閉
                            live.hold_until = max(
                                live.hold_until, self._monotonic() + self._notice_grace_s
                            )
        if kind in ("agent_settled", "response"):
            # 閒置計時從答完、或指令處理完這一刻算起
            self._reaper_wake.set()
        if kind == "response":
            # 沒有人在等的回應（通知指令的）：成功的不轉送，被拒的告訴畫面、通知再排一次
            if not record.get("success"):
                if live is not None and notice is not None:
                    self._retry_notices(conversation_id, live, [notice])
                if live is not None and lesson_notice is not None:
                    self._retry_lessons(conversation_id, live, [lesson_notice])
                self._emit(
                    conversation_id,
                    {"type": "ef_error", "message": str(record.get("error") or "Pi 拒絕了指令")},
                )
            return
        if (
            kind == "extension_error"
            and record.get("extensionPath") == "command:ef-job-done"
            and live is not None
        ):
            # ef-tools 處理通知時丟錯（例如問閘道失敗）：Pi 照樣回成功，所以看這個事件。
            # 不知道是哪一張，就把這次送過、還沒確定進紀錄的都再排一次（ef-tools 會照紀錄去重）
            self._retry_notices(conversation_id, live, self._unconfirmed(conversation_id, live))
        if (
            kind == "extension_error"
            and record.get("extensionPath") == "command:ef-lesson"
            and live is not None
        ):
            # 同上：ef-lesson 丟錯、Pi 照樣回成功。這次送過、還沒確定進紀錄（沒有 `notified`）的都再排一次
            waiting = {f"{p.id} {p.status}" for p in self._lessons_unnotified(conversation_id)}
            with self._lock:
                unconfirmed = sorted(live.lessons_sent & waiting)
            self._retry_lessons(conversation_id, live, unconfirmed)
        if kind == "message_end":
            delivered = _delivered_job(record)
            if delivered is not None:
                self._record_notified(conversation_id, delivered)
            lesson = _delivered_lesson(record)
            if lesson is not None:
                self._record_lesson_notified(conversation_id, lesson)
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
        """從佇列拿事情做。同一件事排著時不會再排（`_enqueue`），拿出來的當下就可以再排。"""
        while True:
            item = self._inbox.get()
            if item is None or self._closing.is_set():
                return
            with self._lock:
                self._queued.discard(item)
            kind, value = item
            try:
                if kind == "job":
                    self._on_job(value)
                elif kind == "sweep":
                    self._sweep()
                elif kind == "retry":
                    for job_id in self._unnotified(value):
                        self._deliver(value, job_id)
                elif kind == "card":
                    conversation_id, command = value.split(" ", 1)
                    self._deliver_card(conversation_id, f"/ef-card {command}")
                elif kind == "lesson":
                    self._deliver_lessons(value)
            except Exception:
                _log.exception("對話主機處理 %s %s 時出錯", kind, value)

    def _on_job(self, job_id: str) -> None:
        # 算進度與推出去排在同一個順序裡：訂閱時的現況不會被一則較舊的進度蓋掉
        with self._emit_lock:
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
        if changed:
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
                # 剛開的 Pi 可能從紀錄補記了這一張：再看一次
                if job_id in self._unnotified(conversation_id):
                    self._notify(conversation_id, live, job_id)
        except Exception as exc:
            _log.warning("工單 %s 的結束通知沒送到對話 %s", job_id, conversation_id, exc_info=True)
            self._emit(conversation_id, {"type": "ef_error", "message": f"工單 {job_id} 的結束通知沒送到：{exc}"})

    def _notify(self, conversation_id: str, live: _Live, job_id: str) -> None:
        with self._lock:
            if job_id in live.notified:
                return
            live.notified.add(job_id)
            live.pending += 1
            live.last_active = self._monotonic()
        # 擴充指令：Pi 正在回答時也會馬上執行；通知本身排在回答之後（ef-tools 的 deliverAs followUp）
        # 回應可能在 send 回傳之前就到：先登記再寫
        try:
            live.pi.send(
                {"type": "prompt", "message": f"/ef-job-done {job_id}"},
                on_id=partial(self._register_job_notice, live, job_id),
            )
        except BaseException:
            with self._lock:
                live.notified.discard(job_id)
                for request_id in [r for r, j in live.notice_ids.items() if j == job_id]:
                    del live.notice_ids[request_id]
            self._settle_pending(live)
            raise

    def _register_job_notice(self, live: _Live, job_id: str, request_id: str) -> None:
        with self._lock:
            live.notice_ids[request_id] = job_id

    def _unconfirmed(self, conversation_id: str, live: _Live) -> list[str]:
        """這次開的 Pi 送過、但還沒確定進了紀錄（不在 `notified_jobs`）的工單。"""
        try:
            done = set(conversations.read(self._data, conversation_id).notified_jobs)
        except (OSError, ValueError):
            done = set()
        with self._lock:
            return sorted(live.notified - done)

    def _retry_notices(self, conversation_id: str, live: _Live, jobs: list[str]) -> None:
        """沒送成的通知：從「送過」拿掉，過一下再排一次；同一個 Pi 重試太多次就等下次接回。"""
        retry: list[str] = []
        given_up: list[str] = []
        with self._lock:
            for job_id in jobs:
                live.notified.discard(job_id)
                live.retries[job_id] = live.retries.get(job_id, 0) + 1
                (retry if live.retries[job_id] <= MAX_NOTICE_RETRIES else given_up).append(job_id)
        for job_id in given_up:
            self._emit(
                conversation_id,
                {"type": "ef_error", "message": f"工單 {job_id} 的結束通知送不進對話，下次接回時再送"},
            )
        if retry and not self._closing.is_set():
            timer = threading.Timer(
                self._notice_retry_s, self._enqueue, args=(("retry", conversation_id),)
            )
            timer.daemon = True
            timer.start()

    def _sweep(self) -> None:
        """主機啟動時：每個對話結束了、還沒確定送到的通知（工單、lesson 的決定）補送一次。"""
        for conversation in conversations.list_all(self._data):
            for job_id in self._unnotified(conversation.id):
                self._deliver(conversation.id, job_id)
            # 決定了、還沒確定通知到的 lesson 也補送（`_ensure` 開 Pi 時一起送）
            if self._lessons_unnotified(conversation.id):
                self._deliver_lessons(conversation.id)

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
        candidates = [job_dir / "input" / "item.json"]
        if job.feed is not None:
            candidates.append(self._data.root / job.feed.item / "item.json")
        for path in candidates:
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
                    if live.busy or live.pending > 0:
                        continue
                    deadline = max(live.last_active + self._idle_s, live.hold_until)
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

    def _lock_for(self, conversation_id: str) -> threading.Lock:
        with self._lock:
            return self._conversation_locks.setdefault(conversation_id, threading.Lock())

    @contextlib.contextmanager
    def _conversation_lock(self, conversation_id: str) -> Iterator[None]:
        with self._lock_for(conversation_id):
            yield

    def _read(self, conversation_id: str) -> conversations.Conversation:
        try:
            return conversations.read(self._data, conversation_id)
        except (OSError, ValueError):
            raise ConversationNotFound(conversation_id) from None

    def _stamp(self, event: Event) -> Event:
        """加上流水號；拿著 `_emit_lock` 呼叫。"""
        self._seq += 1
        return {**event, "seq": self._seq}

    def _emit(self, conversation_id: str, event: Event) -> None:
        with self._emit_lock:
            stamped = self._stamp(event)
            with self._lock:
                subscribers = list(self._subscribers.get(conversation_id, ()))
            for subscription in subscribers:
                subscription.put(stamped)

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
    return _notice_job(message) if isinstance(message, dict) else None


def _notice_job(message: dict[str, Any]) -> str | None:
    """一則訊息是 `ef-job` 通知的話，回它的工單 id。"""
    if message.get("role") != "custom":
        return None
    if message.get("customType") != "ef-job":
        return None
    details = message.get("details")
    job_id = details.get("job_id") if isinstance(details, dict) else None
    return job_id if isinstance(job_id, str) else None


def _delivered_lesson(record: Event) -> tuple[str, str] | None:
    """`message_end` 是 ef-tools 放的 `ef-lesson` 通知的話，回（提議編號, 決定）。"""
    message = record.get("message")
    return _notice_lesson(message) if isinstance(message, dict) else None


def _notice_lesson(message: dict[str, Any]) -> tuple[str, str] | None:
    """一則訊息是 `ef-lesson` 通知的話，回（提議編號, 決定）。"""
    if message.get("role") != "custom" or message.get("customType") != "ef-lesson":
        return None
    details = message.get("details")
    if not isinstance(details, dict):
        return None
    proposal, decision = details.get("proposal"), details.get("decision")
    if isinstance(proposal, str) and isinstance(decision, str):
        return proposal, decision
    return None


def _card_event(card_id: str, events: list[dict[str, Any]], card: cards.CardState) -> Event:
    return {"type": "ef_card", "card_id": card_id, "events": events, "card": card.model_dump(mode="json")}


def _lesson_event(events: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> Event:
    return {"type": "ef_lesson", "events": events, "proposals": proposals}
