"""對話的工具（ADR-0021 §3.4、FR-CH-01、NFR-SEC-02）。

對話的 Pi 只有這幾個工具，全部帶對話鑰匙（`conv:<id>:<n>`）打 `POST /tools/<名稱>`：

- `analyze`：開一張分析工單，**開了就回**（工單 id 與狀態），不等它跑完。
  同一份素材在這個對話裡已有排隊中、執行中或完成的單就拿回那一張；帶 `again: true`（重做）就不拿。
- `read_report`：讀這個對話開過的某張工單的報告。
- `read_article`：讀這個對話開過的某張重構工單的文章（#85）。
- `job_status`：一張工單現在跑到哪。
- `list_personas`：現有的角色與它們寫過的文類（#85；創作者說「用 me 寫」時對話的 agent 才聽得懂）。
- `read_lessons`：一個角色 `lessons.md` 最近十筆（ADR-0026 §3.3：訪談開工前讀，#109）。只讀這一份，
  碰不到角色資料夾的其他檔；沒給角色就是創作者自己（`me`）。對話從不寫角色資料夾。
- `rewrite`：從這個對話的一張重構工單接著改，開一張新的重構工單（等同 `ef new --from`，#102），
  **開了就回**，跟 `analyze` 一樣。`from_job` 一定要是這個對話自己的單（跟讀報告同一條範圍，403 不說存不存在）。
  #109 起只能改已經有的文章：`from_job` 是分析（或任何不是重構的單）一律 409「新文章從訪談開始」。
  新文章要先訪談、照編輯卡寫（ADR-0025 §3.5，#110 接上），不能從分析直接花錢寫。
  記進 `jobs.jsonl` 時接上版本鏈（CONTEXT.md〈版本鏈〉、ADR-0019 §3.6）：`parent` 是 `from_job`；
  從同一個角色的上一版重構接著做是那條鏈的下一版，從分析開、或換了角色，是新的一條鏈的第 1 版。
- 編輯卡的四個（ADR-0025 §3.3，規則在 `cards.py`）：`propose_questions`（記下提出的問題卡，沒給 `card` 就開新卡）、
  `record_interpreted_answers`（解讀創作者打的字、或「你幫我選」）、`add_material`（作者素材）、
  `read_card`（讀一張卡；沒給 `card` 就列出這個對話的卡）。事件一律標 `source: agent`，不打模型、不花錢。
  寫完叫工作台的 `card_changed`，對話主機據此推 `ef_card` 給畫面。
  畫面才能叫的 `submit_station`、`keep_decision`、`reask_station` **不在這裡**：對話主機的 API 直接叫 `cards.py`，
  對話的 Pi 叫不到（`app.py` 照 `UI_ONLY_TOOLS` 一律 403）。
- `propose_lesson(persona, text, why)`：agent 提議一筆 lesson（ADR-0026 §3.3、#114），只記進這個對話自己的
  `lessons-proposed.jsonl`，**不寫角色資料夾**；規則在 `lesson_proposals.py`。寫完叫工作台的 `lesson_changed`，畫面據此放一張
  「同意／不要」的小卡。同意（`approve_lesson`）與不要（`reject_lesson`）只由畫面叫，**不在這裡**：`UI_ONLY_TOOLS` 一律 403，
  同意之後由閘道把那一行附加到 `authors/<角色>/lessons.md`。

範圍：
- 只碰得到列在這個對話 `jobs.jsonl` 裡的工單，別的對話的一律 403，而且不說那張單存不存在。
- 段的工具（讀寫檔、抓網頁、搜尋、跑腳本）拿對話鑰匙來一律 403；對話的工具拿段的鑰匙來也是（`app.py`）。
- 編輯卡只碰得到這個對話自己的：路徑從對話資料夾拼，別的對話的卡跟沒有這張卡一樣回 404。
- 每個對話同一時間最多 `chat_max_running_jobs` 張工單在跑（排隊中也算，預設 1；`analyze`、`rewrite`、
  畫面的「照這張編輯卡寫稿」（工作台的 `write_from_card`，#110）都算）：
  對話的 agent 可以一直開單，每張單各有預算，不擋的話總花費只剩月上限管得到。
  只擋「要開新單」：同一篇文章再丟一次（連按兩下）照樣拿回那一張，即使它還在跑。
  「在跑幾張」看 `job.md` 記的是哪個對話開的，不看 `jobs.jsonl`：開了單、還沒記進去就當掉的那張也算。
  已經滿了而這次不會是拿回在跑的那一張，就在抓網頁之前擋下，不對外抓、不寫投入帳。
  `rewrite` 不走去重，每次都是新單，所以滿了就直接擋。
- 工作台回的工單，閘道先讀它的 `job.md`：不是這個對話開的就不記、回 500。範圍由閘道自己把關。
- 記進 `jobs.jsonl` 之後才叫工作台的 `recorded`（它再通知對話主機）：主機靠 `jobs.jsonl` 找工單是哪個對話的，
  先通知再記的話，開了馬上跑完的單會漏掉結束通知。

真正開單、讀結果的事交給 `Desk`（對話主機注入，知道配方與 harness）；閘道只管鑰匙、範圍與記帳。
"""

import asyncio
import threading
from collections.abc import Callable
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from evidence_forge import cards, conversations, lesson_proposals
from evidence_forge.lessons import latest_lessons
from evidence_forge.convkey import ConversationKey
from evidence_forge.documents import read_job
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.models import Job
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, Source, WebPage, same_source
from evidence_forge.runner.stage_inputs import AUTHOR, job_persona

CARD_TOOLS = frozenset(
    {"propose_questions", "record_interpreted_answers", "add_material", "read_card"}
)
CHAT_TOOLS = (
    frozenset(
        {
            "analyze",
            "read_report",
            "read_article",
            "job_status",
            "list_personas",
            "read_lessons",
            "propose_lesson",
            "rewrite",
        }
    )
    | CARD_TOOLS
)
# 只有畫面能叫的入口（ADR-0025 §3.3）：閘道的 /tools/ 拿任何鑰匙來都拒絕，對話主機的 API 直接叫 `cards.py`
# 「照這張編輯卡寫稿」也是創作者按的（#110）：花錢開寫稿工單，只由畫面經對話主機叫
# lessons 的同意與不要也是創作者按的（ADR-0026 §3.3、決定 G）：agent 只能 `propose_lesson`，寫角色資料夾的只有閘道
UI_ONLY_TOOLS = frozenset(
    {
        "submit_station",
        "keep_decision",
        "reask_station",
        "write_from_card",
        "approve_lesson",
        "reject_lesson",
    }
)
# 還會花錢的狀態：排隊中的單等一下就會跑
_RUNNING = frozenset({"queued", "running"})


# 「數在跑的單 → 開單 → 記進 jobs.jsonl」一個對話一次一個：閘道的工具（事件迴圈上的 asyncio 鎖之外）
# 與畫面的「照這張編輯卡寫稿」（對話主機的 API，另一個伺服器的執行緒）共用這一把
_opening_guard = threading.Lock()
_opening: dict[str, threading.Lock] = {}


def opening_lock(conversation_id: str) -> threading.Lock:
    """這個對話開單用的鎖（跨執行緒；拿著它數在跑的單、開單、記進 `jobs.jsonl`）。"""
    with _opening_guard:
        return _opening.setdefault(conversation_id, threading.Lock())


def running_jobs(data: DataDir, conversation_id: str) -> list[Job]:
    """這個對話開的、還在排隊或在跑的工單。

    看每張單 `job.md` 的 `conversation`（投入時在同一把鎖裡寫的），不看 `jobs.jsonl`：
    開了單、還沒記進 `jobs.jsonl` 就當掉的那張，照樣算在跑。
    讀不到 `job.md` 的不算：執行器也跑不了它。
    """
    jobs_root = data.jobs
    if not jobs_root.is_dir():
        return []
    found: list[Job] = []
    for job_dir in sorted(jobs_root.iterdir()):
        try:
            job = read_job(job_dir / "job.md").job
        except (OSError, ValueError):
            continue
        if job.conversation == conversation_id and job.status in _RUNNING:
            found.append(job)
    return found


def too_many_running(running: list[Job], limit: int) -> ToolRejected:
    """對話的單已經滿了：409，說是哪幾張。"""
    ids = "、".join(job.id for job in running)
    return ToolRejected(
        409,
        f"這個對話已經有 {len(running)} 張工單在跑（{ids}），"
        f"同一時間最多 {limit} 張；等它跑完再開，或請創作者決定",
    )


class Desk(Protocol):
    """開分析工單、讀工單狀態與結果的那一層。"""

    @property
    def recipe_name(self) -> str:
        """`analyze` 開的配方：「這次是不是拿回在跑的那一張」只看這份配方的單。"""
        ...

    @property
    def rewrite_recipe(self) -> str:
        """`rewrite` 開的配方：`rewrite` 只能從這份配方的單（已經有的文章）接著改（#109）。"""
        ...

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
        """開單（或拿回這個對話已有的那張）；回 `{"job_id", "created", "status", …}`。
        讀不到網址回 `{"status": "unavailable", "reason"}`，不開單。可以花幾秒抓網頁。
        `conversation_id`、`again` 與 `before_create` 原樣傳給投入（`open_job`）：去重的鍵含對話 id；
        `before_create` 只在要開新單時呼叫，它丟的 `ToolRejected` 要原樣往外丟。"""
        ...

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
        """從 `from_job` 接著開一張重構工單；`genre` 是創作者明說的文類（對不上就不開），
        `suggested_genre` 是報告建議的文類（對不上用預設文類、附 `note`）；回 `{"job_id", "created": True, "status", …}`。
        開不了（角色不存在、上一張交不出報告…）回 `{"status": "rejected", "reason"}`。
        `from_job` 是不是這個對話的，閘道呼叫之前已經查過。"""
        ...

    def recorded(self, conversation_id: str, job_id: str) -> None:
        """閘道把開出來（或拿回）的單記進對話的 `jobs.jsonl` 之後呼叫。
        對話主機靠 `jobs.jsonl` 決定要不要送結束通知，所以通知主機要等到這時候。"""
        ...

    def status(self, job_id: str) -> dict[str, Any]: ...

    def report(self, job_id: str) -> dict[str, Any]: ...

    def article(self, job_id: str) -> dict[str, Any]: ...

    def personas(self) -> dict[str, Any]: ...

    def card_changed(
        self, conversation_id: str, card_id: str, events: list[dict[str, Any]]
    ) -> None:
        """閘道替對話的 agent 寫了編輯卡的事件之後呼叫：對話主機據此推 `ef_card` 給畫面。"""
        ...

    def lesson_changed(self, conversation_id: str, events: list[dict[str, Any]]) -> None:
        """閘道替對話的 agent 記了一筆 lesson 提議之後呼叫：對話主機據此推 `ef_lesson` 給畫面。"""
        ...


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
        if name == "read_article":
            return await run_in_threadpool(desk.article, self._owned_job(key, body))
        if name == "job_status":
            return await run_in_threadpool(desk.status, self._owned_job(key, body))
        if name == "list_personas":
            return await run_in_threadpool(desk.personas)
        if name == "read_lessons":
            return await run_in_threadpool(self._lessons, body)
        if name == "propose_lesson":
            return await run_in_threadpool(self._propose_lesson, desk, key, body)
        if name == "rewrite":
            return await self._rewrite(desk, key, body)
        if name in CARD_TOOLS:
            return await run_in_threadpool(self._card_tool, desk, key, name, body)
        raise ToolRejected(404, f"沒有這個工具：{name}")

    def _lessons(self, body: dict[str, Any]) -> dict[str, Any]:
        """一個角色最近十筆 lessons（`lessons.py`）；沒給角色就是創作者自己。"""
        persona = _optional(body, "persona") or AUTHOR
        return {"persona": persona, "lessons": latest_lessons(self._config.data, persona)}

    def _propose_lesson(self, desk: Desk, key: ConversationKey, body: dict[str, Any]) -> dict[str, Any]:
        """agent 提議一筆 lesson：只記提議，不寫角色資料夾（`lesson_proposals.propose`）。

        範圍從鑰匙來：只記進這個對話自己的 `lessons-proposed.jsonl`，agent 指不到別的對話。
        """
        written = lesson_proposals.propose(
            self._config.data,
            key.conversation_id,
            body.get("persona"),
            body.get("text"),
            body.get("why"),
            now=self._config.clock(),
        )
        if written.events:
            desk.lesson_changed(key.conversation_id, written.events)
        proposal = written.proposal
        return {
            "proposal": proposal.id,
            "persona": proposal.persona,
            "status": proposal.status,
            "created": written.created,
            # 同意的話實際會寫進去的那一行：agent 照實轉告，不要說成已經存了
            "line": proposal.preview,
        }

    def _card_tool(
        self, desk: Desk, key: ConversationKey, name: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """編輯卡的四個工具（`cards.py`）；寫了事件就通知工作台。"""
        data = self._config.data
        conversation_id = key.conversation_id
        card_id = _optional(body, "card")
        if name == "read_card":
            if card_id is None:
                return {
                    "cards": [
                        c.model_dump(mode="json") for c in cards.list_cards(data, conversation_id)
                    ]
                }
            return {"card": cards.read_card(data, conversation_id, card_id).model_dump(mode="json")}
        now = self._config.clock()
        if name == "propose_questions":
            written = cards.propose_questions(
                data,
                conversation_id,
                card_id,
                body.get("station"),
                body.get("questions"),
                now=now,
                origin=body.get("origin"),
                claims=body.get("claims"),
            )
        elif name == "add_material":
            written = cards.add_material(
                data, conversation_id, card_id, body.get("text"), now=now, filename=body.get("filename")
            )
        else:
            if card_id is None:
                raise ToolRejected(400, "缺少字串欄位 card")
            written = cards.record_interpreted_answers(
                data,
                conversation_id,
                card_id,
                body.get("station"),
                body.get("answers"),
                now=now,
                pick_for_me=body.get("pick_for_me", False),
                quote=body.get("quote"),
            )
        if written.events:
            desk.card_changed(conversation_id, written.card_id, written.events)
        result: dict[str, Any] = {
            "card_id": written.card_id,
            "events": written.events,
            "card": written.card.model_dump(mode="json"),
        }
        if written.material is not None:
            result["material"] = written.material
            result["created"] = written.created
        return result

    async def _analyze(
        self, desk: Desk, key: ConversationKey, body: dict[str, Any]
    ) -> dict[str, Any]:
        url = _optional(body, "url")
        text = _optional(body, "text")
        if (url is None) == (text is None):
            raise ToolRejected(400, "url 與 text 要給其中一個")
        goal = _optional(body, "goal")
        again = body.get("again", False)
        if not isinstance(again, bool):
            raise ToolRejected(400, "again 要是 true 或 false")
        source: Source = WebPage(url) if url is not None else PastedText(text or "")
        data = self._config.data
        conversation_id = key.conversation_id
        if not (data.conversation_dir(conversation_id) / "conversation.json").is_file():
            raise ToolRejected(403, f"沒有這個對話：{conversation_id}")

        limit = self._config.chat_max_running_jobs

        def room_for_new_job() -> None:
            # 在投入的鎖裡、確定要開新單時才數：去重拿回舊單不受上限影響
            running = self._running_jobs(conversation_id)
            if len(running) >= limit:
                raise self._full(running)

        def open_and_record() -> dict[str, Any]:
            with opening_lock(conversation_id):
                return open_and_record_locked()

        def open_and_record_locked() -> dict[str, Any]:
            # 已經滿了，而這次也不會是拿回在跑的那一張（換了素材，或要重做）：
            # 抓網頁之前就擋下，不然每次都先對外抓、寫投入帳，最後才拒絕
            # 只看分析的單：重構單跟分析共用同一份素材項目，但丟同一篇文章不會拿回重構單
            running = self._running_jobs(conversation_id)
            if len(running) >= limit and (
                again
                or not any(
                    job.recipe == desk.recipe_name and same_source(data, job, source)
                    for job in running
                )
            ):
                raise self._full(running)
            # 開單與記進 jobs.jsonl 在同一個執行緒裡做完：請求中途被取消（關機、斷線）時，
            # 不會留下開了單卻沒記在對話底下的工單
            opened = desk.open_analysis(
                conversation_id=conversation_id,
                url=url,
                text=text,
                goal=goal,
                again=again,
                before_create=room_for_new_job,
            )
            job_id = opened.get("job_id")
            if isinstance(job_id, str):
                self._check_opened_here(conversation_id, job_id)
                conversations.add_job(
                    data,
                    conversation_id,
                    conversations.ConversationJob(
                        job_id=job_id, ts=self._config.clock()
                    ),
                )
                desk.recorded(conversation_id, job_id)
            return opened

        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return await run_in_threadpool(open_and_record)

    async def _rewrite(
        self, desk: Desk, key: ConversationKey, body: dict[str, Any]
    ) -> dict[str, Any]:
        # 跟讀報告同一條範圍：只從這個對話自己的單接著做，別的對話的不說存不存在（ADR-0021 §3.4）
        from_job = self._owned_job(key, body, "from_job")
        self._require_article(desk, from_job)
        persona = _optional(body, "persona")
        genre = _optional(body, "genre")
        suggested_genre = _optional(body, "suggested_genre")
        goal = _optional(body, "goal")
        feedback = _optional(body, "feedback")
        data = self._config.data
        conversation_id = key.conversation_id

        def open_and_record() -> dict[str, Any]:
            with opening_lock(conversation_id):
                return open_and_record_locked()

        def open_and_record_locked() -> dict[str, Any]:
            # 重構不走去重，每次都是新單：滿了就擋，不叫工作台
            running = self._running_jobs(conversation_id)
            if len(running) >= self._config.chat_max_running_jobs:
                raise self._full(running)
            opened = desk.open_rewrite(
                conversation_id=conversation_id,
                from_job=from_job,
                persona=persona,
                genre=genre,
                suggested_genre=suggested_genre,
                goal=goal,
                feedback=feedback,
            )
            job_id = opened.get("job_id")
            if isinstance(job_id, str):
                job = self._check_opened_here(conversation_id, job_id)
                conversations.add_job(
                    data,
                    conversation_id,
                    conversations.ConversationJob(
                        job_id=job_id,
                        ts=self._config.clock(),
                        version=self._next_version(conversation_id, from_job, job),
                        parent=from_job,
                    ),
                )
                desk.recorded(conversation_id, job_id)
            return opened

        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return await run_in_threadpool(open_and_record)

    def _require_article(self, desk: Desk, from_job: str) -> None:
        """`rewrite` 只改已經有的文章：`from_job` 要是重構的單（#109、ADR-0025 §3.5）。

        從分析直接開重構等於跳過訪談、直接花錢寫；新文章從訪談開始，照編輯卡寫稿是 #110。
        讀不到 `job.md` 的也擋：工作台也開不了。
        """
        try:
            parent = read_job(self._config.data.job_dir(from_job) / "job.md").job
        except (OSError, ValueError):
            parent = None
        if parent is None or parent.recipe != desk.rewrite_recipe:
            raise ToolRejected(
                409,
                "新文章從訪談開始：rewrite 只能改這個對話已經有的文章（from_job 要是重構的單）；"
                "要寫新的，用 propose_questions 從第 1 關開始",
            )

    def _next_version(self, conversation_id: str, from_job: str, job: Job) -> int:
        """新的重構在版本鏈上是第幾版（CONTEXT.md〈版本鏈〉、ADR-0019 §3.6）。

        上一張是同一份配方、同一個角色的單：那條鏈的下一版。從分析開、或換了角色：新的一條鏈的第 1 版。
        """
        try:
            parent = read_job(self._config.data.job_dir(from_job) / "job.md").job
        except (OSError, ValueError):
            return 1
        same_chain = parent.recipe == job.recipe and job_persona(parent) == job_persona(job)
        if not same_chain:
            return 1
        rows = conversations.jobs(self._config.data, conversation_id)
        previous = next((row for row in rows if row.job_id == from_job), None)
        return (previous.version if previous is not None else 1) + 1

    def _full(self, running: list[Job]) -> ToolRejected:
        return too_many_running(running, self._config.chat_max_running_jobs)

    def _running_jobs(self, conversation_id: str) -> list[Job]:
        return running_jobs(self._config.data, conversation_id)

    def _check_opened_here(self, conversation_id: str, job_id: str) -> Job:
        """工作台回的工單真的是這個對話開的；不是就不記、回 500（ADR-0021 §3.4）。

        記進 `jobs.jsonl` 就讀得到，所以不能只聽工作台的：工作台寫錯，別的對話的單就漏進來。
        """
        try:
            job = read_job(self._config.data.job_dir(job_id) / "job.md").job
        except (OSError, ValueError):
            job = None
        if job is None or job.conversation != conversation_id:
            raise ToolRejected(
                500, f"工作台回的工單 {job_id} 不是這個對話開的，沒有記下"
            )
        return job

    def _owned_job(
        self, key: ConversationKey, body: dict[str, Any], field: str = "job_id"
    ) -> str:
        job_id = body.get(field)
        if not isinstance(job_id, str) or not job_id:
            raise ToolRejected(400, f"缺少字串欄位 {field}")
        if not conversations.owns_job(self._config.data, key.conversation_id, job_id):
            # 不說那張單存不存在：對話不該知道別的對話開了什麼
            raise ToolRejected(403, "這張工單不是這個對話開的")
        return job_id
