"""lessons 的提議與同意（ADR-0026 §3.3、創作者 2026-10-15 決定 G、FR-RW-04、#114）。

為什麼這樣分：lessons 是之後每一次訪談與寫稿都要照著做的規則，寫進去等於改了創作者的寫作系統。
所以**對話的 agent 只能提議，創作者在畫面上按同意，由閘道寫進角色的 `lessons.md`**；
對話與它的 agent 從來不直接寫角色資料夾。

- 提議記在這個對話自己的 `$EF_DATA/conversations/<對話 id>/lessons-proposed.jsonl`：一行一個事件，只增不改，
  只有閘道寫（跟編輯卡的決策紀錄同一個樣子）。事件三種：
  - `proposed`：agent 提議（`source: agent`）：哪個角色、一行規則 `text`、為什麼 `why`（給創作者判斷用，不寫進 lessons.md）。
  - `approved`：創作者同意（`source: ui`）：記下實際寫進去的那一行 `line`。
  - `rejected`：創作者不要（`source: ui`）：什麼都不寫。
  - `notified`：對話主機確定把決定通知到對話的 Pi 了（`source: system`）。同意或不要之後、通知送到之前主機當掉，
    下次開 Pi 時把「決定了、還沒有 `notified`」的全部補送（`unnotified`、`mark_notified`）。
- 同意與不要都是畫面叫的（`approve_lesson`、`reject_lesson`，閘道的 `/tools/` 拿任何鑰匙來都 403，
  見 `gateway/chat_tools.py` 的 `UI_ONLY_TOOLS`），不是 agent 的工具。
- 只碰得到這個對話自己的提議：提議 id 不在這個對話的檔裡（別的對話的、不存在的）一律 403，不說存不存在。

寫進 `lessons.md` 的格式（跟 `harness/templates/author-lessons.md`、`lessons.latest_lessons` 對得上）：

    - <提議的日期>｜<text>

`text` 由 agent 寫成「哪一篇｜發生什麼 → 改成什麼」，一行；日期由閘道照提議的時間加，用台北時間的日期
（跟畫面上其他時間同一個時區：台北凌晨提議的，不會變成前一天）。

同意是冪等的（兩次不會附加兩次）：
- 同一筆提議已經同意過，再按一次什麼都不寫、不多記事件。
- `lessons.md` 裡已經有一模一樣的那一行（同一個角色、同一天、同一句規則：例如兩個對話各提議了一次，或寫進去之後、
  記 `approved` 之前當機重來），就不再附加，只記 `approved` 並標 `already_present`；畫面上說「已經有一樣的一條」。
- `lessons.md` 是一般檔案卻讀不懂（不是 UTF-8、超過 5 MB）：沒辦法確定有沒有一樣的一行，409、什麼都不寫。
- 已經不要的不能再同意，已經同意的不能再不要（409）。

角色資料夾用 `confined`：不跟隨符號連結、檢查與寫入同一個 fd；角色一定要真的存在（資料夾名稱完全相符），
沒有就報錯、什麼都不寫（連創作者自己 `me` 也一樣：還沒有資料夾就先去「角色」頁建）。
"""

import fcntl
import threading
import unicodedata
from zoneinfo import ZoneInfo
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt, TypeAdapter, ValidationError

from evidence_forge import confined
from evidence_forge.figures.brand import BrandError, persona_dir
from evidence_forge.gateway.ledger import append_line
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.persona_spec import LESSONS_FILE
from evidence_forge.runner.stage_inputs import personas

PROPOSED_FILE = "lessons-proposed.jsonl"
# 一筆 lesson 一行：日期加「哪一篇｜發生什麼 → 改成什麼」，兩百字夠寫一條規則，也不會把訪談與寫稿的上下文塞爆
MAX_TEXT = 200
MAX_WHY = 300
# 一個對話同時最多幾筆還沒決定的提議：agent 不該一直提議，堆到這個數就請創作者先處理
MAX_PENDING = 10
# HTML 註解標記：`lessons.latest_lessons` 先把 `<!-- … -->` 整段拿掉再讀，text 帶這個會把後面的規則吃掉
_COMMENT_MARKS = ("<!--", "-->")

Status = Literal["pending", "approved", "rejected"]
Decision = Literal["approved", "rejected"]
# 畫面上的時間都用台北時間（`ui/format.py`）；lessons 的日期也是
TAIPEI = ZoneInfo("Asia/Taipei")


# --- 事件 -------------------------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # 這個檔裡第幾筆事件（從 1 起算，只增不改）
    n: NonNegativeInt
    ts: AwareDatetime
    # 提議的編號 `L<n>`：提議事件的 `n`
    proposal: str


class Proposed(_Base):
    type: Literal["proposed"] = "proposed"
    source: Literal["agent"] = "agent"
    persona: str
    text: str
    why: str


class Approved(_Base):
    type: Literal["approved"] = "approved"
    source: Literal["ui"] = "ui"
    # 實際寫進 lessons.md 的那一行（含 `- `）
    line: str
    # 同意的時候 lessons.md 裡已經有一模一樣的一行，這次沒有再附加：
    # 兩個對話各提議了同一個角色、同一天的同一句規則，或上次寫進去之後、記 `approved` 之前當機重來
    already_present: bool = False


class Rejected(_Base):
    type: Literal["rejected"] = "rejected"
    source: Literal["ui"] = "ui"


class Notified(_Base):
    """對話主機確定把這個決定通知到對話的 Pi 了（通知進了 Pi 的紀錄才記）。"""

    type: Literal["notified"] = "notified"
    source: Literal["system"] = "system"
    decision: Decision


_Event = Annotated[Proposed | Approved | Rejected | Notified, Field(discriminator="type")]
_Any = Proposed | Approved | Rejected | Notified
_EVENT: TypeAdapter[_Any] = TypeAdapter(_Event)


class Proposal(BaseModel):
    """一筆提議現在的狀態（照事件算出來；畫面與 agent 讀的就是這個）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    persona: str
    text: str
    why: str
    proposed_at: AwareDatetime
    status: Status
    # 同意才有：寫進 lessons.md 的那一行
    line: str | None = None
    decided_at: AwareDatetime | None = None
    # 同意時 lessons.md 已經有一模一樣的一行，沒有再附加
    already_present: bool = False
    # 決定已經通知到對話的 Pi（畫面不用看這個；對話主機補送通知用）
    notified: bool = False

    @property
    def preview(self) -> str:
        """同意的話會寫進去的那一行（還沒同意也給，畫面讓創作者看到實際寫什麼）。"""
        return self.line or lesson_line(self.proposed_at, self.text)


@dataclass(frozen=True)
class Written:
    """一次寫入的結果：這次新記的事件（可能是空的：冪等或重複的提議）與那筆提議現在的樣子。"""

    proposal: Proposal
    events: list[dict[str, Any]]
    # 提議才有：這次是不是新記了一筆（同一個角色、同一行字還在等的話拿回舊的）
    created: bool = False


def lesson_line(when: datetime, text: str) -> str:
    """寫進 `lessons.md` 的一行。"""
    return f"- {when.astimezone(TAIPEI).date().isoformat()}｜{text}"


# --- 檢查 -------------------------------------------------------------------------------


def _clean(value: object, name: str, limit: int, *, comments_allowed: bool) -> str:
    """一行字：去頭尾空白；不准空的、換行與其他控制字元、超過上限；text 另外不准 HTML 註解標記。"""
    if not isinstance(value, str):
        raise ToolRejected(400, f"{name} 要是字串")
    text = value.strip()
    if not text:
        raise ToolRejected(400, f"{name} 是空的")
    # 換行、Tab、Unicode 的行與段落分隔（U+2028、U+2029）、零寬字元等：lessons.md 是一筆一行，不准多行
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text):
        raise ToolRejected(400, f"{name} 要寫成一行：不能有換行或控制字元")
    if len(text) > limit:
        raise ToolRejected(400, f"{name} 超過 {limit} 字（{len(text)} 字）；一行規則寫短一點")
    if not comments_allowed and any(mark in text for mark in _COMMENT_MARKS):
        raise ToolRejected(400, f"{name} 不能有 HTML 註解標記（<!-- 與 -->）：lessons.md 會把整段當成註解拿掉")
    return text


def _persona_folder(data: DataDir, persona: object) -> tuple[str, Path]:
    """要寫的角色資料夾：名稱合格、資料夾真的存在（完全相符）、本身不是符號連結。"""
    if not isinstance(persona, str):
        raise ToolRejected(400, "persona 要是字串")
    try:
        check_name("角色", persona)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    existing = personas(data)
    if persona not in existing:
        listed = "、".join(existing) or "（還沒有任何角色）"
        raise ToolRejected(404, f"沒有這個角色：{persona}；現有的角色：{listed}。沒有寫任何東西")
    try:
        return persona, persona_dir(data, persona)
    except (InvalidJobKey, BrandError):
        raise ToolRejected(403, f"角色 {persona} 的資料夾是符號連結，不寫") from None


# --- 檔案與鎖 ---------------------------------------------------------------------------

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
# 寫角色的 lessons.md 一次一個：兩個對話同時同意同一個角色的提議，不會兩邊都讀到同一個檔尾
_persona_lock = threading.Lock()


def _folder(data: DataDir, conversation_id: str) -> Path:
    """這個對話的資料夾；對話 id 不合格 400、沒有這個對話 404。"""
    try:
        folder = data.conversation_dir(conversation_id)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    if not (folder / "conversation.json").is_file():
        raise ToolRejected(404, f"沒有這個對話：{conversation_id}")
    return folder


@contextmanager
def _lock(folder: Path) -> Iterator[None]:
    """同一個對話的「讀事件 → 檢查 → 寫事件」一次一個（程序內的鎖加檔案鎖，另一個 `ef serve` 也排隊）。"""
    with _guard:
        mutex = _locks.setdefault(str(folder.resolve()), threading.Lock())
    with mutex, (folder / ".lessons.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(folder: Path) -> list[_Any]:
    """讀得懂的每一筆事件；寫到一半被砍的殘行、手改壞的行跳過。"""
    path = folder / PROPOSED_FILE
    if not path.is_file():
        return []
    found: list[_Any] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            found.append(_EVENT.validate_json(line))
        except ValidationError:
            continue
    return found


def _fold(events: list[_Any]) -> dict[str, Proposal]:
    """事件算成每筆提議現在的狀態，照提議的順序。決定只認第一筆：已經同意或不要的，後面再來的不改它。"""
    found: dict[str, Proposal] = {}
    for event in events:
        if isinstance(event, Proposed):
            found.setdefault(
                event.proposal,
                Proposal(
                    id=event.proposal,
                    persona=event.persona,
                    text=event.text,
                    why=event.why,
                    proposed_at=event.ts,
                    status="pending",
                ),
            )
            continue
        known = found.get(event.proposal)
        if known is None:
            continue
        if isinstance(event, Notified):
            # 通知只對已經決定、而且決定對得上的提議算數
            if known.status == event.decision and not known.notified:
                found[event.proposal] = known.model_copy(update={"notified": True})
            continue
        if known.status != "pending":
            continue
        found[event.proposal] = known.model_copy(
            update={
                "status": "approved" if isinstance(event, Approved) else "rejected",
                "line": event.line if isinstance(event, Approved) else None,
                "decided_at": event.ts,
                "already_present": isinstance(event, Approved) and event.already_present,
            }
        )
    return found


def _next_n(events: list[_Any]) -> int:
    return max((e.n for e in events), default=0) + 1


def _append(folder: Path, event: _Any) -> dict[str, Any]:
    append_line(folder / PROPOSED_FILE, event.model_dump_json() + "\n")
    return event.model_dump(mode="json")


# --- 讀 ---------------------------------------------------------------------------------


def read_proposals(data: DataDir, conversation_id: str) -> list[Proposal]:
    """這個對話的所有提議（含已經同意、不要的），照提議的順序。"""
    folder = _folder(data, conversation_id)
    return list(_fold(_read(folder)).values())


def pending(data: DataDir, conversation_id: str) -> list[Proposal]:
    return [p for p in read_proposals(data, conversation_id) if p.status == "pending"]


# --- 寫 ---------------------------------------------------------------------------------


def propose(
    data: DataDir,
    conversation_id: str,
    persona: object,
    text: object,
    why: object,
    *,
    now: datetime,
) -> Written:
    """agent 提議一筆 lesson：只記提議，**不寫角色資料夾**。

    - 角色不合格、不存在（連 `me` 沒資料夾也是）、是符號連結，text 或 why 不合格：丟 `ToolRejected`，什麼都不記。
    - 這個對話已經有同一個角色、同一行字還在等：拿回那一筆（`created: False`），不多記一筆。
    - 還沒決定的提議已經有 `MAX_PENDING` 筆：409，請 agent 先請創作者處理。
    """
    folder = _folder(data, conversation_id)
    name, _ = _persona_folder(data, persona)
    one_line = _clean(text, "text", MAX_TEXT, comments_allowed=False)
    reason = _clean(why, "why", MAX_WHY, comments_allowed=True)
    with _lock(folder):
        events = _read(folder)
        proposals = _fold(events)
        waiting = [p for p in proposals.values() if p.status == "pending"]
        same = next((p for p in waiting if p.persona == name and p.text == one_line), None)
        if same is not None:
            return Written(same, [], created=False)
        if len(waiting) >= MAX_PENDING:
            raise ToolRejected(
                409,
                f"這個對話已經有 {len(waiting)} 筆 lesson 提議在等創作者決定（最多 {MAX_PENDING} 筆）；"
                "請創作者先在畫面上同意或不要，再提議新的",
            )
        n = _next_n(events)
        event = Proposed(n=n, ts=now, proposal=f"L{n}", persona=name, text=one_line, why=reason)
        written = _append(folder, event)
    return Written(_fold([event])[event.proposal], [written], created=True)


def _decide_target(
    data: DataDir, conversation_id: str, proposal_id: object
) -> tuple[Path, str]:
    folder = _folder(data, conversation_id)
    if not isinstance(proposal_id, str):
        raise ToolRejected(400, "proposal 要是字串")
    return folder, proposal_id


def _own(proposals: dict[str, Proposal], proposal_id: str) -> Proposal:
    """這個對話自己的那一筆提議。別的對話的、不存在的一律 403，不說存不存在。"""
    found = proposals.get(proposal_id)
    if found is None:
        raise ToolRejected(403, "這個對話沒有這筆 lesson 提議")
    return found


def approve(
    data: DataDir, conversation_id: str, proposal_id: object, *, now: datetime
) -> Written:
    """創作者同意：閘道把那一行附加到 `authors/<角色>/lessons.md`，記下 `approved`。冪等。

    - 已經同意過：回現況，什麼都不寫。已經不要了：409。
    - 角色在提議之後不見了、改成符號連結：不寫，提議留著還在等（創作者修好角色再按，或按不要）。
    - 附加是只增不改（`confined.append_text`）；先寫 lessons.md、再記事件，兩步之間當機，
      重來時 lessons.md 已經有一模一樣的一行就不再附加（事件記 `already_present`）。
    - lessons.md 讀不懂：409、什麼都不寫（見 `_has_line`）。
    """
    folder, wanted = _decide_target(data, conversation_id, proposal_id)
    with _lock(folder):
        events = _read(folder)
        proposal = _own(_fold(events), wanted)
        if proposal.status == "approved":
            return Written(proposal, [])
        if proposal.status == "rejected":
            raise ToolRejected(409, "這筆 lesson 已經被你按了「不要」，不能再同意；要的話請 agent 重新提議")
        _, persona_folder = _persona_folder(data, proposal.persona)
        line = lesson_line(proposal.proposed_at, proposal.text)
        with _persona_lock:
            present = _has_line(persona_folder, line)
            if not present:
                _append_to_lessons(persona_folder, line)
        event = Approved(
            n=_next_n(events), ts=now, proposal=proposal.id, line=line, already_present=present
        )
        written = _append(folder, event)
    return Written(_fold([*events, event])[proposal.id], [written])


def reject(
    data: DataDir, conversation_id: str, proposal_id: object, *, now: datetime
) -> Written:
    """創作者不要：記 `rejected`，什麼都不寫。冪等；已經同意的不能再不要（409）。"""
    folder, wanted = _decide_target(data, conversation_id, proposal_id)
    with _lock(folder):
        events = _read(folder)
        proposal = _own(_fold(events), wanted)
        if proposal.status == "rejected":
            return Written(proposal, [])
        if proposal.status == "approved":
            raise ToolRejected(409, "這筆 lesson 已經寫進角色的 lessons.md 了，不能再按「不要」")
        event = Rejected(n=_next_n(events), ts=now, proposal=proposal.id)
        written = _append(folder, event)
    return Written(_fold([*events, event])[proposal.id], [written])


def unnotified(data: DataDir, conversation_id: str) -> list[Proposal]:
    """已經決定（同意或不要）、還沒確定通知到對話的 Pi 的提議，照提議的順序。"""
    return [p for p in read_proposals(data, conversation_id) if p.status != "pending" and not p.notified]


def mark_notified(
    data: DataDir, conversation_id: str, proposal_id: str, decision: str, *, now: datetime
) -> bool:
    """對話主機確定通知進了 Pi 的紀錄：記 `notified`。回這次有沒有新記。

    提議不存在、還沒決定、決定對不上、已經記過：什麼都不做（冪等；主機照 Pi 紀錄補記時會重複叫）。
    """
    if decision not in ("approved", "rejected"):
        return False
    folder = _folder(data, conversation_id)
    with _lock(folder):
        events = _read(folder)
        known = _fold(events).get(proposal_id)
        if known is None or known.status != decision or known.notified:
            return False
        _append(folder, Notified(n=_next_n(events), ts=now, proposal=proposal_id, decision=decision))
    return True


# --- 角色的 lessons.md ------------------------------------------------------------------

def _has_line(persona_folder: Path, line: str) -> bool:
    """lessons.md 裡已經有一模一樣的一行（只比頭尾空白修掉之後的字）。

    檔案不存在是沒有。是一般檔案卻讀不懂（不是 UTF-8、超過上限）：不能當成沒有，不然寫進去之後當機、重來會附加第二次，
    所以 409、什麼都不寫，請創作者先修好那個檔。連結與其他種類的檔不在這裡擋，附加那一步會拒絕（403）。
    """
    kind, text = confined.read_text(persona_folder, LESSONS_FILE)
    if (kind == "file" and text is None) or kind == "large":
        raise ToolRejected(
            409,
            f"{LESSONS_FILE} 讀不懂（不是 UTF-8，或超過 {confined.MAX_TEXT_BYTES // 1_000_000} MB）：沒辦法確定有沒有一樣的一條，"
            "沒有寫；提議還在，修好那個檔再按一次",
        )
    if text is None:
        return False
    return any(row.strip() == line for row in text.splitlines())


def _append_to_lessons(persona_folder: Path, line: str) -> None:
    try:
        confined.append_text(persona_folder, LESSONS_FILE, line + "\n")
    except confined.FolderChanged:
        raise ToolRejected(409, "角色資料夾在寫入前被換掉了，沒有寫；提議還在，再按一次") from None
    except confined.EntryRefused:
        raise ToolRejected(403, f"{LESSONS_FILE} 是符號連結或不是一般檔案，不寫；提議還在") from None
    except OSError as exc:
        raise ToolRejected(500, f"寫不進 {LESSONS_FILE}：{exc.strerror or exc}；提議還在，再按一次") from None
