"""編輯卡與決策紀錄（ADR-0025 §3.3、FR-IW-03、CONTEXT.md〈決策紀錄〉〈編輯卡〉〈作者素材〉）。

```
$EF_DATA/conversations/<對話 id>/cards/<卡 id>/
  decisions.jsonl     決策紀錄：一行一個事件，只增不改，只經這個模組寫
  card.json           編輯卡：由事件從頭算出來的當前狀態，只是快取，對不上以事件為準
  materials/<sha256>  作者素材的內容：寫一次就不再改，事件只記參照
```

**誰會叫這裡**（範圍照 ADR-0021：路徑一律從對話資料夾拼，別的對話的卡根本拼不到）。
ADR-0025 說的「閘道直接記」，在 `ef serve` 裡就是這個模組：閘道與對話主機在同一個程序，
畫面的入口由對話主機直接叫這裡，不再繞一趟閘道的 HTTP（那樣要另外發一把「畫面」的鑰匙）；
同一張卡的寫入用程序內的鎖排隊（`_card_lock`）。
- 對話的 agent 經閘道（`gateway/chat_tools.py`）：`propose_questions`、`record_interpreted_answers`、
  `add_material`、`read_card`。事件標 `source: agent`。
- 畫面經對話主機（`chat/host.py`、`chat/api.py`）：`submit_station`、`keep_decision`、`reask_station`。
  事件標 `source: ui`。這三個不在閘道的 `/tools/` 裡，對話的 Pi 叫不到。

**事件五種**（每筆都有 `n`（這張卡的第幾筆）、`type`、`ts`、`source`）：
- `questions_proposed`：哪一關、問題卡（`id`、`header`、`question`、`kind?`、`options[{label, description, value?}]`）。
  「其他」一定有，不記；第 3 關的「沒有例子」也是系統給的，不記。第 1 關一定有一張文體卡（`kind: genre`，
  選項的 `value` 是文體的 slug，`GENRES`）與一張主題卡（`kind: theme`，`value` 是 `THEMES`，#109）；
  角色卡（`kind: persona`，`value` 是現有的角色）可以不問，不問就是 `me`。編輯卡由此讀得到 `theme`、`persona`。
  #109 之前提出、沒有主題卡的第 1 關照舊算得出來（`theme` 是 None），只是新提出的一定要有。
  #109 之前的文體卡沒有 `kind`：每個選項都帶 `value` 的那張就是文體卡（舊紀錄照樣讀得懂）。
  第 1 關可以帶 `origin`（這張卡從哪裡開始，ADR-0025 §3.1）。
  第 6 關一定有一張圖文分鏡卡（`kind: storyboard`，每個選項帶這個方案的圖 `figures[{section, kind, shows?}]`，
  種類只有概念圖、數據圖、表格）與一份主張清單（`claims[{claim, wrong_if}]`，1～5 條，記在提出問題這一筆上，#110）。
  寫稿照這兩個結構化的欄位寫、檢查圖位，不從問題文字裡猜。#110 之前的第 6 關沒有這兩欄，照樣讀得懂。
- `answered`：一張問題卡一筆；一關送出時一批寫（同一個 `submission`，一次 write 寫完）。
  `choice`（選的選項名稱）、`other_text`（「其他」的文字）、`pending`（第 3 關「尚待作者提供」）、
  `no_example`（第 3 關「沒有例子」）四選一；`material`（引用作者素材 `M<n>`）可以單獨給或跟著前兩種；加 `note`。
  第 3 關只選提示不算回答：要有補充、「其他」的文字或作者素材（`_example_problem`）。
  agent 寫的另有 `mode`（`typed`：解讀創作者打的字，附原話 `quote`；`pick_for_me`：「交給 AI 選」）。
- `kept`：要重確認的關按「沿用」，記保留的是哪一次送出。
- `reasked`：「重問這一關」。
- `material_added`：作者素材的編號（`M1`…）、種類、內容的 sha256、檔名、大小。

**編輯卡怎麼算**（`read_card`，從第一筆事件重算）：
- 一關的狀態：`not_reached`（還沒提出問題）、`asking`（問了還沒送出，或重問、重新提出問題之後）、
  `pending_author`（第 3 關記了「尚待作者提供」）、`decided`（選好）、`changed`（改過）、
  `kept`（要重確認之後沿用）、`reconfirm`（要重確認）。只有 `decided`、`changed`、`kept` 算有決定。
- 一關的送出要回答到當時這一關的每一張問題卡才算數；寫到一半被砍的一批不算。
- 已經有決定的一關又送出一次（回頭改），後面已經有決定的關都標要重確認；沿用或重新送出才取消。
- 六關都有決定、沒有要重確認，`writable` 才是 true；寫稿工具用 `require_writable` 再查一次（最後一道關）。
  寫稿的快照用 `snapshot`（拿著鎖一次讀出編輯卡與事件），不讀 `card.json`。
- 讀不懂的一行跳過（`skipped_lines`），不拖垮整張卡。
- 第 6 關有決定時，`claims` 是那次送出回答的那一組問題卡帶的主張，`storyboard` 是分鏡卡選的那個方案的圖
  （選「其他」自己寫、或 #110 之前的紀錄就是 None：寫稿照文字寫，圖位不查）。
- 寫稿工單讀的是 `writing_brief`：只有現在的決定，沒有時間與卡 id（請求逐位元組相同，快取與重播才對得上）。
"""

import fcntl
import hashlib
import logging
import os
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from evidence_forge import conversations
from evidence_forge.documents import read_frontmatter
from evidence_forge.gateway.ledger import append_line
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.harness import default_harness
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import personas

_log = logging.getLogger(__name__)

STATIONS: dict[int, str] = {
    1: "文體＋主題",
    2: "核心觀點",
    3: "只有作者能給的例子",
    4: "邊界",
    5: "工作標題",
    6: "框架、主幹與圖文分鏡",
}
# 第 3 關：作者的例子一定來自作者，agent「你幫我選」只能記這個（ADR-0025 §3.2）
EXAMPLE_STATION = 3
PENDING_LABEL = "尚待作者提供"
# 第 3 關系統給的選項（跟「其他」一樣一定有，不列成提示）：只有作者自己能答（ADR-0025 §3.2）
NO_EXAMPLE_LABEL = "沒有例子"
PICK_FOR_ME_NOTE = "交給 AI 選"
OTHER_LABEL = "其他"

# 文體（ADR-0026 §3.1）。第 1 關的文體卡每個選項帶其中一個 slug（`value`）。
# 清單只有一份：`harness/genres/` 的文體定義檔（主題清單 themes.md 不算）
_GENRE_FILES = default_harness().genre_files()
GENRES = frozenset(path.stem for path in _GENRE_FILES)
# 不需要作者自己經驗的文體（定義檔的 `needs_author_evidence: false`）：
# 「你幫我選」在第 1 關只能選這幾種（ADR-0025 §3.2、ADR-0026 §3.1）
SELF_FREE_GENRES = frozenset(
    path.stem for path in _GENRE_FILES if read_frontmatter(path).get("needs_author_evidence") is False
)
# 主證據是作者自己的使用、測試或專案：第 3 關不能答「沒有例子」（ADR-0025 §3.2）
AUTHOR_EVIDENCE_GENRES = frozenset({"product-case", "benchmark", "postmortem"})
# 主題（ADR-0026 §3.2）：清單只有一份，`harness/genres/themes.md`
THEMES = default_harness().theme_slugs()
# 帶結構化值的問題卡：第 1 關的文體、主題、角色卡（#109，選項的 `value` 是 slug 或角色名稱），
# 第 6 關的圖文分鏡卡（#110，選項的 `figures` 是這個方案的圖）
QuestionKind = Literal["genre", "theme", "persona", "storyboard"]
# 框架、主幹與圖文分鏡（ADR-0025 §3.2）：分鏡卡與主張清單都在這一關
STORYBOARD_STATION = 6
# 分鏡排得出來的種類（ADR-0027 §3.1）：概念圖、數據圖有圖位；表格由寫稿寫成 Markdown 表格，不留圖位
FigureKind = Literal["concept", "chart", "table"]
FIGURE_KINDS: frozenset[str] = frozenset({"concept", "chart", "table"})
DRAWN_KINDS: frozenset[str] = frozenset({"concept", "chart"})
FIGURE_NAMES: dict[str, str] = {"concept": "概念圖", "chart": "數據圖", "table": "表格"}

MAX_QUESTIONS = 4
MIN_OPTIONS, MAX_OPTIONS = 2, 4
MAX_HEADER = 24
MAX_QUESTION = 500
MAX_LABEL = 80
MAX_DESCRIPTION = 300
MAX_NOTE = 2000
MAX_OTHER_TEXT = 4000
MAX_QUOTE = 4000
MAX_TITLE = 120
MAX_FILENAME = 200
# 第 6 關的主張清單（ADR-0025 §3.2：寫之前在主張層級看）與分鏡的一張圖
MIN_CLAIMS, MAX_CLAIMS = 1, 5
MAX_CLAIM = 300
MAX_FIGURES = 12
MAX_SECTION = 80
MAX_SHOWS = 200
# 作者素材一份的上限：口述的逐字稿也放得下，又不會塞爆資料目錄
MAX_MATERIAL_BYTES = 2_000_000

Source = Literal["ui", "agent"]
Mode = Literal["typed", "pick_for_me"]
Status = Literal[
    "not_reached", "asking", "pending_author", "decided", "changed", "kept", "reconfirm"
]
_DECIDED: frozenset[str] = frozenset({"decided", "changed", "kept"})


# --- 事件 -------------------------------------------------------------------------------


class Figure(BaseModel):
    """圖文分鏡的一張圖：放在哪一節、哪一種、讀者這時要看到什麼（ADR-0025 §3.2、ADR-0027 §3.1）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str
    kind: FigureKind
    shows: str | None = None


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    description: str
    # 第 1 關文體、主題、角色卡的選項才有：文體或主題的 slug、角色名稱（照問題卡的 `kind`）
    value: str | None = None
    # 第 6 關分鏡卡的選項才有：這個方案的每一張圖，照文章的順序（空的是整篇不配圖）
    figures: tuple[Figure, ...] | None = None


class Claim(BaseModel):
    """第 6 關列給創作者看的一條主張，與它怎麼會被證明是錯的（ADR-0025 §3.2）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: str
    wrong_if: str


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    header: str
    question: str
    options: tuple[Option, ...]
    # 第 1 關的文體、主題、角色卡；其他問題卡沒有。#109 之前的紀錄沒有這欄（`question_kind` 照舊規則認）
    kind: QuestionKind | None = None


class Origin(BaseModel):
    """這張卡從哪裡開始（ADR-0025 §3.1）：報告的第 N 個題材、創作者的想法、作者素材。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["report_topic", "idea", "material"]
    job_id: str | None = None
    topic: int | None = None
    title: str | None = None


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    ts: AwareDatetime
    source: Source


class QuestionsProposed(_Event):
    type: Literal["questions_proposed"] = "questions_proposed"
    station: int
    questions: tuple[Question, ...]
    origin: Origin | None = None
    # 第 6 關才有：文章會提出的主張（#110）。#110 之前的紀錄沒有這欄
    claims: tuple[Claim, ...] | None = None


class Answered(_Event):
    type: Literal["answered"] = "answered"
    station: int
    submission: str
    question: str
    choice: str | None = None
    other_text: str | None = None
    pending: bool = False
    # 第 3 關：作者自己答「沒有例子」（系統的選項，不是 agent 列的提示）
    no_example: bool = False
    # 引用的作者素材（`M1`…）：第 3 關的例子可以直接是作者素材
    material: str | None = None
    note: str | None = None
    mode: Mode | None = None
    quote: str | None = None


class Kept(_Event):
    type: Literal["kept"] = "kept"
    station: int
    submission: str


class Reasked(_Event):
    type: Literal["reasked"] = "reasked"
    station: int


class MaterialAdded(_Event):
    type: Literal["material_added"] = "material_added"
    material: str
    kind: Literal["text", "file"]
    sha256: str
    filename: str | None = None
    size: int


AnyEvent = QuestionsProposed | Answered | Kept | Reasked | MaterialAdded
_EVENT: TypeAdapter[AnyEvent] = TypeAdapter(
    Annotated[AnyEvent, Field(discriminator="type")]
)


# --- 編輯卡（算出來的狀態） ------------------------------------------------------------------


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    choice: str | None = None
    other_text: str | None = None
    pending: bool = False
    no_example: bool = False
    material: str | None = None
    note: str | None = None


class Submission(BaseModel):
    """一關的一次送出（同一個 `submission` 的那一批回答）。"""

    model_config = ConfigDict(extra="forbid")

    submission: str
    source: Source
    ts: AwareDatetime
    mode: Mode | None = None
    quote: str | None = None
    # 有一張是「尚待作者提供」：這次送出不算有決定
    pending: bool = False
    answers: list[Answer]


class StationQuestion(Question):
    # 算編輯卡時要把舊的一組改成不是現在的
    model_config = ConfigDict(extra="forbid", frozen=False)

    proposed_at: AwareDatetime
    # 是不是這一關現在要回答的那一組（最後一次提出的）
    current: bool


class KeptDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission: str
    ts: AwareDatetime


class StationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station: int
    name: str
    status: Status
    decided: bool
    needs_reconfirm: bool
    # 這一關問過的每一張問題卡（舊的也留著：決策分頁要列「問過的、沒選的」）
    questions: list[StationQuestion] = Field(default_factory=list)
    # 每一次送出，照時間順序
    submissions: list[Submission] = Field(default_factory=list)
    # 現在的決定是哪一次送出；沒有就是 None（要重確認、重問時仍指著原本那次，當預設）
    decision: str | None = None
    kept: KeptDecision | None = None
    reasked_at: AwareDatetime | None = None


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["text", "file"]
    sha256: str
    filename: str | None = None
    size: int
    ts: AwareDatetime


class Blocking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station: int
    status: Status


class CardState(BaseModel):
    """`card.json`。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    conversation_id: str
    title: str | None = None
    origin: Origin | None = None
    # 第 1 關現在的決定選的文體（slug）；還沒選是 None。寫稿（#110）與第 3 關的規則都讀它
    genre: str | None = None
    # 第 1 關現在的決定選的主題（slug）與角色；沒問那張卡、或第 1 關還沒決定是 None（#109）
    theme: str | None = None
    persona: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    events: int = 0
    skipped_lines: int = 0
    # 第一個還沒有決定的關（全部決定了是 None）
    current_station: int | None = 1
    writable: bool = False
    # 擋住寫稿的關：還沒決定、或要重確認的
    blocking: list[Blocking] = Field(default_factory=list)
    stations: list[StationState]
    materials: list[Material] = Field(default_factory=list)
    # 第 6 關現在的決定的主張清單與分鏡（#110）；還沒決定、#110 之前的紀錄是空的與 None，
    # 分鏡選「其他」自己寫的也是 None（寫稿照文字寫，圖位不查）
    claims: list[Claim] = Field(default_factory=list)
    storyboard: list[Figure] | None = None


@dataclass(frozen=True)
class Snapshot:
    """同一個時間點的編輯卡與決策紀錄（`snapshot`）：`events` 剛好是算出 `card` 的那幾筆。"""

    card: CardState
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class Written:
    """一次寫入的結果：哪張卡、寫了哪幾筆事件、寫完之後的編輯卡。"""

    card_id: str
    events: list[dict[str, Any]]
    card: CardState
    # 作者素材：這份內容是不是新的（同一份內容再記一次拿回原本的編號）
    created: bool = True
    material: str | None = None


# --- 路徑與鎖 ---------------------------------------------------------------------------


def cards_dir(data: DataDir, conversation_id: str) -> Path:
    return data.conversation_dir(conversation_id) / "cards"


def _card_dir(data: DataDir, conversation_id: str, card_id: str) -> Path:
    """這個對話的一張卡的資料夾；卡 id 不合格 400，不在這個對話底下 404（不說別的對話有沒有）。"""
    if not isinstance(card_id, str):
        raise ToolRejected(400, "card 要是字串")
    try:
        # 卡 id 會拼進路徑：跟工單 id 同一條字元規則，`../` 之類在這裡就進不來
        check_name("編輯卡 id", card_id)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    root = cards_dir(data, conversation_id)
    folder = root / card_id
    # 資料夾是指到外面的符號連結（例如指到別的對話的卡）就當成沒有
    if (
        folder.is_symlink()
        or not folder.is_dir()
        or not folder.resolve().is_relative_to(root.resolve())
    ):
        raise ToolRejected(404, f"這個對話沒有編輯卡 {card_id}")
    return folder


def _require_conversation(data: DataDir, conversation_id: str) -> None:
    try:
        folder = data.conversation_dir(conversation_id)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    if not (folder / "conversation.json").is_file():
        raise ToolRejected(404, f"沒有這個對話：{conversation_id}")


def new_card_id(now: datetime) -> str:
    """`card-<日期時間>-<隨機>`：照時間排得出先後，又不會兩張同時開的撞在一起。"""
    return f"card-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _new_card(data: DataDir, conversation_id: str, now: datetime) -> str:
    root = cards_dir(data, conversation_id)
    root.mkdir(parents=True, exist_ok=True)
    while True:
        card_id = new_card_id(now)
        try:
            (root / card_id).mkdir()
        except FileExistsError:
            continue
        return card_id


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@contextmanager
def _card_lock(folder: Path) -> Iterator[None]:
    """同一張卡的「讀事件 → 檢查 → 寫事件」一次一個。

    閘道（agent 的工具）與對話主機（畫面的點選）在 `ef serve` 的同一個程序、不同執行緒：先拿程序內的鎖。
    再拿卡資料夾裡 `.lock` 的檔案鎖（flock）：別的程序（另一個 `ef serve`、手動的工具）寫同一張卡也排隊，
    不會兩邊算出同一個 `n`、同一個送出編號。
    """
    with _locks_guard:
        lock = _locks.setdefault(str(folder.resolve()), threading.Lock())
    with lock, (folder / ".lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --- 讀 -------------------------------------------------------------------------------


def _read(folder: Path) -> tuple[list[AnyEvent], int]:
    """決策紀錄的每一筆事件，與讀不懂而跳過的行數（寫到一半被砍的殘行、手改壞的行）。"""
    path = folder / "decisions.jsonl"
    if not path.is_file():
        return [], 0
    found: list[AnyEvent] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            found.append(_EVENT.validate_json(line))
        except ValidationError:
            skipped += 1
    return found, skipped


def read_events(data: DataDir, conversation_id: str, card_id: str) -> list[dict[str, Any]]:
    """這張卡的決策紀錄（讀得懂的每一筆），給畫面的決策分頁。"""
    _require_conversation(data, conversation_id)
    events, _ = _read(_card_dir(data, conversation_id, card_id))
    return [event.model_dump(mode="json") for event in events]


def read_card(data: DataDir, conversation_id: str, card_id: str) -> CardState:
    """從事件重算編輯卡，順手把 `card.json` 換成最新的。"""
    _require_conversation(data, conversation_id)
    folder = _card_dir(data, conversation_id, card_id)
    with _card_lock(folder):
        events, skipped = _read(folder)
        card = _compute(conversation_id, card_id, events, skipped)
        _save_cache(folder, card)
    return card


def snapshot(data: DataDir, conversation_id: str, card_id: str) -> Snapshot:
    """拿著這張卡的鎖讀一次：編輯卡與算出它的那幾筆事件，同一個時間點（給寫稿工單的快照，#110）。

    寫稿要用這一份，**不要讀 `card.json`**：那只是快取，可能比事件舊；分兩次讀（先讀卡、再讀事件）
    中間又可能有人寫了一筆，快照跟它附的決策紀錄就對不上。
    """
    _require_conversation(data, conversation_id)
    folder = _card_dir(data, conversation_id, card_id)
    with _card_lock(folder):
        events, skipped = _read(folder)
        card = _compute(conversation_id, card_id, events, skipped)
        _save_cache(folder, card)
    return Snapshot(card=card, events=[e.model_dump(mode="json") for e in events])


def list_cards(data: DataDir, conversation_id: str) -> list[CardState]:
    """這個對話的每一張卡，照卡 id（開的時間）排。"""
    _require_conversation(data, conversation_id)
    root = cards_dir(data, conversation_id)
    if not root.is_dir():
        return []
    found: list[CardState] = []
    for folder in sorted(root.iterdir()):
        try:
            found.append(read_card(data, conversation_id, folder.name))
        except ToolRejected:
            continue
    return found


def require_writable(data: DataDir, conversation_id: str, card_id: str) -> CardState:
    """寫稿之前的最後一道關（ADR-0025 §3.3）：六關都有決定、沒有要重確認，不然 409 說是哪幾關。"""
    card = read_card(data, conversation_id, card_id)
    if card.writable:
        return card
    raise ToolRejected(409, "還不能照這張編輯卡寫稿：" + _describe_blocking(card.blocking))


def _describe_blocking(blocking: list[Blocking]) -> str:
    words = {
        "not_reached": "還沒問到",
        "asking": "還沒送出",
        "pending_author": f"{PENDING_LABEL}，還沒有決定",
        "reconfirm": "要重確認（沿用或重新送出）",
    }
    return "；".join(
        f"第 {b.station} 關（{STATIONS[b.station]}）{words.get(b.status, b.status)}"
        for b in blocking
    )


def _save_cache(folder: Path, card: CardState) -> None:
    """`card.json` 先寫暫存檔再整個換上去：讀的人只會讀到舊版或新版。

    只是快取：寫不進去（磁碟滿、唯讀）不算失敗。事件已經寫進去了，這時丟錯會讓呼叫的人以為沒記到、
    再送一次，同一關就問兩次。下一次讀卡會再寫一次。
    """
    path = folder / "card.json"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(card.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        _log.warning("編輯卡的快取寫不進去：%s", path, exc_info=True)
        temporary.unlink(missing_ok=True)


# --- 算編輯卡 ----------------------------------------------------------------------------


@dataclass
class _Station:
    number: int
    questions: list[StationQuestion]
    submissions: list[Submission]
    # 最後一次提出的問題卡 id：一次送出要回答到這幾張才算數
    current: tuple[str, ...] = ()
    decision: str | None = None
    reopened: bool = False
    reconfirm: bool = False
    kept: KeptDecision | None = None
    reasked_at: datetime | None = None
    # 問題卡 id → 提出它的那一筆帶的主張（第 6 關，#110）
    claims: dict[str, tuple[Claim, ...]] = field(default_factory=dict)

    def latest(self) -> Submission | None:
        return self.submissions[-1] if self.submissions else None

    def status(self) -> Status:
        if not self.questions:
            return "not_reached"
        latest = self.latest()
        # 正在問（重問、重新提出問題卡）壓過要重確認：這時沒有可以沿用的決定
        if self.reopened or latest is None:
            return "asking"
        if self.reconfirm:
            return "reconfirm"
        if latest.pending:
            return "pending_author"
        if self.kept is not None:
            return "kept"
        complete = sum(1 for s in self.submissions if not s.pending)
        return "changed" if complete > 1 else "decided"


def _compute(
    conversation_id: str,
    card_id: str,
    events: list[AnyEvent],
    skipped: int,
) -> CardState:
    stations = {n: _Station(n, [], []) for n in STATIONS}
    materials: list[Material] = []
    origin: Origin | None = None
    for event in _steps(events):
        if isinstance(event, list):
            _apply_submission(stations, event)
        elif isinstance(event, QuestionsProposed):
            station = stations.get(event.station)
            if station is None:
                continue
            for question in station.questions:
                question.current = False
            station.questions.extend(
                StationQuestion(**q.model_dump(), proposed_at=event.ts, current=True)
                for q in event.questions
            )
            station.current = tuple(q.id for q in event.questions)
            if event.claims is not None:
                station.claims.update((q.id, event.claims) for q in event.questions)
            # 已經送出過的一關又提出新的問題卡：回到還沒送出，原本的決定留著當預設。
            # 要重確認的關也一樣：重新問就是要重新送出，不再能沿用
            if station.submissions:
                station.reopened = True
                station.reconfirm = False
            if event.origin is not None and origin is None:
                origin = event.origin
        elif isinstance(event, Kept):
            station = stations.get(event.station)
            if station is None or station.decision != event.submission:
                continue
            station.reconfirm = False
            station.reopened = False
            station.kept = KeptDecision(submission=event.submission, ts=event.ts)
        elif isinstance(event, Reasked):
            station = stations.get(event.station)
            if station is None:
                continue
            # 重問壓過要重確認：回到還沒送出，要重新送出才有決定，不能再沿用
            station.reopened = True
            station.reconfirm = False
            station.reasked_at = event.ts
        elif isinstance(event, MaterialAdded):
            materials.append(
                Material(
                    id=event.material,
                    kind=event.kind,
                    sha256=event.sha256,
                    filename=event.filename,
                    size=event.size,
                    ts=event.ts,
                )
            )
    # 第 1 關正在重問（或還沒決定）時不給文體、主題、角色：舊答案不能拿來判斷第 3 關、也不能交給寫稿
    first_decided = stations[1].status() in _DECIDED
    genre = _decided_value(stations[1], "genre") if first_decided else None
    theme = _decided_value(stations[1], "theme") if first_decided else None
    persona = _decided_value(stations[1], "persona") if first_decided else None
    states: list[StationState] = []
    for s in stations.values():
        status = s.status()
        if (
            s.number == EXAMPLE_STATION
            and status in _DECIDED
            and _example_problem(_decision_answers(s), genre, origin, genre_known=False) is not None
        ):
            # 第 3 關的決定不再成立（例如改成要作者經驗的文體之後，原本答的是「沒有例子」）：還要作者給
            status = "pending_author"
        states.append(
            StationState(
                station=s.number,
                name=STATIONS[s.number],
                status=status,
                decided=status in _DECIDED,
                needs_reconfirm=s.reconfirm,
                questions=s.questions,
                submissions=s.submissions,
                decision=s.decision,
                kept=s.kept,
                reasked_at=s.reasked_at,
            )
        )
    blocking = [Blocking(station=s.station, status=s.status) for s in states if not s.decided]
    last = stations[STORYBOARD_STATION]
    claims, storyboard = _decided_plan(last) if last.status() in _DECIDED else ((), None)
    return CardState(
        card_id=card_id,
        conversation_id=conversation_id,
        title=origin.title if origin is not None else None,
        origin=origin,
        genre=genre,
        theme=theme,
        persona=persona,
        created_at=events[0].ts if events else None,
        updated_at=events[-1].ts if events else None,
        events=len(events),
        skipped_lines=skipped,
        current_station=blocking[0].station if blocking else None,
        writable=not blocking,
        blocking=blocking,
        stations=states,
        materials=materials,
        claims=list(claims),
        storyboard=None if storyboard is None else list(storyboard),
    )


def _decision_answers(station: _Station) -> list[Answer]:
    found = next((s for s in station.submissions if s.submission == station.decision), None)
    return found.answers if found is not None else []


def _decided_value(station: _Station, kind: QuestionKind) -> str | None:
    """第 1 關現在的決定裡，那一種卡（文體、主題、角色）選的那個選項的 `value`。"""
    questions = {q.id: q for q in station.questions}
    for answer in _decision_answers(station):
        question = questions.get(answer.question)
        if question is None or question_kind(question) != kind:
            continue
        return next((o.value for o in question.options if o.label == answer.choice), None)
    return None


def _decided_plan(station: _Station) -> tuple[tuple[Claim, ...], tuple[Figure, ...] | None]:
    """第 6 關現在的決定：那一組問題卡帶的主張，與分鏡卡選的那個方案的圖。"""
    answers = _decision_answers(station)
    claims = next((station.claims[a.question] for a in answers if a.question in station.claims), ())
    questions = {q.id: q for q in station.questions}
    for answer in answers:
        question = questions.get(answer.question)
        if question is None or question.kind != "storyboard" or answer.choice is None:
            continue
        option = next((o for o in question.options if o.label == answer.choice), None)
        if option is not None and option.figures is not None:
            return claims, option.figures
    return claims, None


def figure_slots(card: CardState) -> list[tuple[int, Figure]]:
    """分鏡裡要留圖位的圖（概念圖、數據圖），照順序編號 1 起；表格不留圖位（ADR-0027 §3.1）。"""
    drawn = [f for f in card.storyboard or () if f.kind in DRAWN_KINDS]
    return list(enumerate(drawn, start=1))


def figure_path(number: int) -> str:
    """第 N 張圖的圖位路徑（配圖照這個檔名產圖，ADR-0027）。"""
    return f"./images/fig-{number}.webp"


# 寫稿工單裡作者素材放的地方（ADR-0025 §3.4：照 sha256 放）
SNAPSHOT_MATERIALS = "input/card/materials/"
_ORIGIN_WORDS = {
    "report_topic": "從分析報告的第 {topic} 個題材開始：有原文，參考來源第一條是原文",
    "idea": "從創作者自己的想法開始：沒有原文",
    "material": "從創作者的作者素材開始：沒有原文",
}


def writing_brief(card: CardState) -> str:
    """寫稿工單讀的編輯卡（`input/card/card.md`）：只放現在的決定，給模型讀（ADR-0025 §3.4）。

    完整的編輯卡與決策紀錄也放進快照，但那兩份很長（問過的、沒選的都在）；寫稿只要這一份。
    不放時間、卡 id、送出編號：同一張卡的決定一樣，這一份就逐位元組一樣，快取與重播才對得上。
    """
    lines = ["# 編輯卡", ""]
    if card.origin is not None:
        lines.append("- 起點：" + _ORIGIN_WORDS[card.origin.kind].format(topic=card.origin.topic))
        if card.origin.title:
            lines.append(f"- 題材：{card.origin.title}")
    else:
        lines.append("- 起點：沒有記下（當成沒有原文）")
    lines.append(f"- 文體：{card.genre or '（沒有記下）'}")
    lines.append(f"- 主題：{card.theme or '（沒有記下）'}")
    lines.append(f"- 角色：{card.persona or 'me'}")
    materials = {m.id: m for m in card.materials}
    for state in card.stations[1:]:
        lines += ["", f"## 第 {state.station} 關：{state.name}", ""]
        decision = next((s for s in state.submissions if s.submission == state.decision), None)
        questions = {q.id: q for q in state.questions}
        for answer in decision.answers if decision is not None else []:
            question = questions.get(answer.question)
            if question is None:
                continue
            lines.append(f"- {question.header}：{question.question}")
            lines += [f"  - {part}" for part in _answer_lines(question, answer, materials)]
    lines += ["", "## 主張（寫之前創作者看過的）", ""]
    if card.claims:
        lines += [f"{n}. {c.claim}（錯在：{c.wrong_if}）" for n, c in enumerate(card.claims, start=1)]
    else:
        lines.append("（這張卡沒有結構化的主張清單：照第 6 關「主張」那張卡的問題與回答）")
    lines += ["", "## 圖文分鏡與圖位", ""]
    if card.storyboard is None:
        lines.append("（這張卡沒有結構化的分鏡：照第 6 關「圖文分鏡」那張卡的回答留圖位）")
    elif not card.storyboard:
        lines.append("整篇不配圖。")
    else:
        drawn = 0
        for figure in card.storyboard:
            shows = f"：{figure.shows}" if figure.shows else ""
            if figure.kind in DRAWN_KINDS:
                # 跟 `figure_slots` 同一種編法：概念圖與數據圖照順序 1、2…
                drawn += 1
                where = f"圖位 {figure_path(drawn)}"
            else:
                where = "寫成 Markdown 表格，不留圖位"
            lines.append(f"- 「{figure.section}」那一節，{FIGURE_NAMES[figure.kind]}{shows}（{where}）")
    if card.materials:
        lines += ["", "## 作者素材", ""]
        for material in card.materials:
            name = f"，{material.filename}" if material.filename else ""
            lines.append(
                f"- {material.id}{name}：{SNAPSHOT_MATERIALS}{material.sha256}（{material.size} bytes）"
            )
    return "\n".join(lines) + "\n"


def _answer_lines(question: StationQuestion, answer: Answer, materials: dict[str, Material]) -> list[str]:
    """一張問題卡現在的回答，給寫稿讀的幾行。"""
    found: list[str] = []
    if answer.no_example:
        found.append(f"作者自己說：{NO_EXAMPLE_LABEL}")
    if answer.choice is not None:
        option = next((o for o in question.options if o.label == answer.choice), None)
        described = f"——{option.description}" if option is not None else ""
        found.append(f"選了：{answer.choice}{described}")
    if answer.other_text is not None:
        found.append(f"創作者自己寫：{answer.other_text}")
    if answer.material is not None:
        material = materials.get(answer.material)
        where = f"（{SNAPSHOT_MATERIALS}{material.sha256}）" if material is not None else ""
        found.append(f"引用作者素材 {answer.material}{where}")
    if answer.note is not None:
        found.append(f"補充：{answer.note}")
    return found


def question_kind(question: Question) -> QuestionKind | None:
    """問題卡是哪一種結構化的卡。#109 之前的紀錄沒有 `kind`：每個選項都帶 `value` 的就是文體卡。"""
    if question.kind is not None:
        return question.kind
    if question.options and all(o.value is not None for o in question.options):
        return "genre"
    return None


def _is_genre_question(question: Question) -> bool:
    return question_kind(question) == "genre"


def _example_problem(
    answers: list[Answer], genre: str | None, origin: Origin | None, *, genre_known: bool = True
) -> str | None:
    """第 3 關的回答能不能算作者的例子；不能就回原因（ADR-0025 §3.2）。

    `genre_known`：寫入時（送出、沿用）文體一定要定了才能確認「沒有例子」；重算編輯卡時第 1 關正在重問，
    就先不拿文體判斷原本的決定（第 1 關重新送出之後，第 3 關會照規則標要重確認，那時再查）。

    - 作者自己的內容才算：「其他」的文字、引用作者素材，或選了提示又在補充寫下內容。只選提示不算：
      提示是 agent 列的「你有沒有遇過……」，不是作者的經歷。
    - 「沒有例子」：`product-case`、`benchmark`、`postmortem` 不行（主證據是作者自己的）；
      `essay` 只有從報告開始（有觸發它的原文）才行。
    """
    for answer in answers:
        if answer.no_example:
            if genre is None and genre_known:
                return (
                    f"第 1 關的文體還沒有決定，不能確認「{NO_EXAMPLE_LABEL}」："
                    "先把第 1 關送出，再回答這一關"
                )
            if genre in AUTHOR_EVIDENCE_GENRES:
                return (
                    f"文體是 {genre}，主證據是作者自己的使用、測試或專案，不能「{NO_EXAMPLE_LABEL}」："
                    "請作者補自己的例子，或回第 1 關換文體"
                )
            if genre == "essay" and (origin is None or origin.kind != "report_topic"):
                return (
                    f"文體是 essay，沒有作者的經驗時要有觸發它的原文；這張卡不是從報告開始的，"
                    f"不能「{NO_EXAMPLE_LABEL}」：請作者補自己的例子，或回第 1 關換文體"
                )
            continue
        if answer.pending:
            continue
        if answer.other_text is None and answer.material is None and answer.note is None:
            return (
                f"第 {EXAMPLE_STATION} 關的選項只是提示：要在補充寫下作者自己的例子、引用作者素材，"
                f"或由作者選「{NO_EXAMPLE_LABEL}」"
            )
    return None


def _steps(events: list[AnyEvent]) -> Iterator[AnyEvent | list[Answered]]:
    """照順序走過事件；同一批回答（連續、同一個送出編號，一次寫進去的）收成一份清單。"""
    batch: list[Answered] = []
    for event in events:
        if batch and not (isinstance(event, Answered) and event.submission == batch[0].submission):
            yield batch
            batch = []
        if isinstance(event, Answered):
            batch.append(event)
        else:
            yield event
    if batch:
        yield batch


def _apply_submission(stations: dict[int, _Station], batch: list[Answered]) -> None:
    first = batch[0]
    station = stations.get(first.station)
    if station is None:
        return
    answered = {a.question for a in batch}
    if not station.current or not set(station.current) <= answered:
        # 沒有回答到當時的每一張問題卡（寫到一半被砍）：這一批不算
        return
    submission = Submission(
        submission=first.submission,
        source=first.source,
        ts=first.ts,
        mode=first.mode,
        quote=first.quote,
        pending=any(a.pending for a in batch),
        answers=[
            Answer(
                question=a.question,
                choice=a.choice,
                other_text=a.other_text,
                pending=a.pending,
                no_example=a.no_example,
                material=a.material,
                note=a.note,
            )
            for a in batch
        ],
    )
    had_decision = station.decision is not None
    # 送出之前就算有決定的後面幾關：這次回頭改了，它們要重確認。正在問、還沒送出的不算（沒有可以沿用的）
    affected = [
        later
        for later in stations.values()
        if later.number > station.number and later.status() in _DECIDED
    ]
    station.submissions.append(submission)
    station.reopened = False
    station.reconfirm = False
    station.kept = None
    if not submission.pending:
        station.decision = submission.submission
    if had_decision:
        # 回頭改了這一關：後面有決定的關都要重確認（原本的決定留著當預設）
        for later in affected:
            later.reconfirm = True
            later.kept = None


# --- 寫 -------------------------------------------------------------------------------


Build = Callable[[CardState, list[AnyEvent], int], tuple[list[AnyEvent], dict[str, Any]]]


def _append(folder: Path, events: list[AnyEvent]) -> None:
    """一批事件用一次 write 寫完：一關的回答不會只寫進一半（`append_line` 是一次 O_APPEND 的 write）。"""
    lines = "".join(event.model_dump_json(exclude_none=True) + "\n" for event in events)
    append_line(folder / "decisions.jsonl", lines)


def _write(
    data: DataDir,
    conversation_id: str,
    card_id: str,
    build: Build,
) -> Written:
    """拿著這張卡的鎖：讀事件、算狀態、叫 `build(狀態, 事件, 下一筆的 n)` 產生新事件、寫進去、重算。"""
    folder = _card_dir(data, conversation_id, card_id)
    with _card_lock(folder):
        events, skipped = _read(folder)
        card = _compute(conversation_id, card_id, events, skipped)
        next_n = max((e.n for e in events), default=0) + 1
        new_events, extra = build(card, events, next_n)
        if new_events:
            _append(folder, new_events)
            events = [*events, *new_events]
            card = _compute(conversation_id, card_id, events, skipped)
        _save_cache(folder, card)
    return Written(
        card_id=card_id,
        events=[e.model_dump(mode="json", exclude_none=True) for e in new_events],
        card=card,
        **extra,
    )


def _station_number(station: object) -> int:
    if isinstance(station, bool) or not isinstance(station, int) or station not in STATIONS:
        raise ToolRejected(400, "station 要是 1～6 的整數")
    return station


def _text(value: object, name: str, limit: int, *, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise ToolRejected(400, f"缺少 {name}")
        return None
    if not isinstance(value, str):
        raise ToolRejected(400, f"{name} 要是字串")
    stripped = value.strip()
    if not stripped:
        if required:
            raise ToolRejected(400, f"{name} 是空的")
        return None
    if len(stripped) > limit:
        raise ToolRejected(400, f"{name} 超過 {limit} 字")
    return stripped


def _value_domain(kind: QuestionKind, known_personas: list[str]) -> frozenset[str]:
    if kind == "genre":
        return GENRES
    if kind == "theme":
        return THEMES
    return frozenset(known_personas)


_KIND_NAMES: dict[QuestionKind, str] = {"genre": "文體", "theme": "主題", "persona": "角色"}


def _parse_questions(
    raw: object, next_id: int, station: int, known_personas: list[str]
) -> list[Question]:
    """`known_personas`：現有的角色（角色卡的 `value` 只能是這幾個）。"""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_QUESTIONS:
        raise ToolRejected(400, f"questions 要是 1～{MAX_QUESTIONS} 張問題卡")
    found: list[Question] = []
    for number, item in enumerate(raw, start=1):
        where = f"第 {number} 張問題卡"
        if not isinstance(item, dict):
            raise ToolRejected(400, f"{where}要是物件")
        options = item.get("options")
        if not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
            raise ToolRejected(
                400, f"{where}要有 {MIN_OPTIONS}～{MAX_OPTIONS} 個選項（「{OTHER_LABEL}」一定有，不用給）"
            )
        raw_kind = item.get("kind")
        if raw_kind is not None and raw_kind not in (*_KIND_NAMES, "storyboard"):
            raise ToolRejected(400, f"{where}：kind 要是 genre、theme、persona 或 storyboard")
        if raw_kind in _KIND_NAMES and station != 1:
            raise ToolRejected(400, f"{where}：只有第 1 關的文體、主題、角色卡帶 kind")
        if raw_kind == "storyboard" and station != STORYBOARD_STATION:
            raise ToolRejected(400, f"{where}：圖文分鏡卡只在第 {STORYBOARD_STATION} 關")
        kind: QuestionKind | None = raw_kind
        parsed: list[Option] = []
        for option in options:
            if not isinstance(option, dict):
                raise ToolRejected(400, f"{where}的選項要是物件")
            label = _text(option.get("label"), f"{where}的選項名稱", MAX_LABEL)
            description = _text(option.get("description"), f"{where}的選項說明", MAX_DESCRIPTION)
            value = _text(option.get("value"), f"{where}的選項 value", MAX_LABEL, required=False)
            assert label is not None and description is not None
            if label == OTHER_LABEL:
                raise ToolRejected(400, f"{where}：「{OTHER_LABEL}」一定有，不用列成選項")
            if station == EXAMPLE_STATION and label == NO_EXAMPLE_LABEL:
                raise ToolRejected(
                    400, f"{where}：「{NO_EXAMPLE_LABEL}」是系統的選項，只有作者能答，不用列成提示"
                )
            if value is not None and station != 1:
                raise ToolRejected(400, f"{where}：只有第 1 關的文體、主題、角色卡選項帶 value")
            figures = _parse_figures(option.get("figures"), f"{where}的選項「{label}」", kind)
            parsed.append(Option(label=label, description=description, value=value, figures=figures))
        if len({o.label for o in parsed}) != len(parsed):
            raise ToolRejected(400, f"{where}的選項名稱重複了")
        values = [o.value for o in parsed if o.value is not None]
        if kind is None and values:
            # 沒寫 kind 卻帶 value：#109 之前的文體卡寫法，照文體卡驗
            kind = "genre"
        if kind is not None and kind != "storyboard":
            name = _KIND_NAMES[kind]
            if len(values) != len(parsed) or len(set(values)) != len(values):
                raise ToolRejected(400, f"{where}：{name}卡的每個選項都要帶不同的 value")
            domain = _value_domain(kind, known_personas)
            outside = [v for v in values if v not in domain]
            if outside:
                listed = "、".join(sorted(domain)) or "（還沒有任何角色）"
                raise ToolRejected(
                    400,
                    f"{where}：{name}卡的 value 要是其中之一（{listed}），不認得 {'、'.join(outside)}",
                )
        header = _text(item.get("header"), f"{where}的標籤（header）", MAX_HEADER)
        question = _text(item.get("question"), f"{where}的問題", MAX_QUESTION)
        assert header is not None and question is not None
        found.append(
            Question(
                id=f"q{next_id + len(found)}",
                header=header,
                question=question,
                options=tuple(parsed),
                kind=kind,
            )
        )
    if station == 1 and sum(1 for q in found if q.kind == "genre") != 1:
        raise ToolRejected(
            400,
            "第 1 關要有一張文體卡（只能一張）：kind 是 genre，每個選項帶文體的 slug"
            "（value，ADR-0026 的 7 種）",
        )
    # 每篇只有一個主題（ADR-0026 §3.2）：第 1 關「文體＋主題」，主題卡也一定要一張；角色卡可以不問（就是 me）
    if station == 1 and sum(1 for q in found if q.kind == "theme") != 1:
        raise ToolRejected(
            400,
            "第 1 關要有一張主題卡（只能一張）：kind 是 theme，每個選項帶主題的 slug"
            f"（value：{'、'.join(sorted(THEMES))}）",
        )
    if sum(1 for q in found if q.kind == "persona") > 1:
        raise ToolRejected(400, "第 1 關的角色卡只能一張")
    # 寫稿照分鏡留圖位、配圖照它產圖（#110）：第 6 關一定有一張結構化的分鏡卡
    if station == STORYBOARD_STATION and sum(1 for q in found if q.kind == "storyboard") != 1:
        raise ToolRejected(
            400,
            f"第 {STORYBOARD_STATION} 關要有一張圖文分鏡卡（只能一張）：kind 是 storyboard，"
            "每個選項帶 figures（每張圖的 section、kind：concept、chart 或 table）",
        )
    return found


def _parse_figures(raw: object, where: str, kind: QuestionKind | None) -> tuple[Figure, ...] | None:
    """分鏡卡選項的圖：每張 `section`、`kind`（concept、chart、table）、`shows`（可以不給）。"""
    if kind != "storyboard":
        if raw is not None:
            raise ToolRejected(400, f"{where}：只有圖文分鏡卡的選項帶 figures")
        return None
    if not isinstance(raw, list) or len(raw) > MAX_FIGURES:
        raise ToolRejected(
            400, f"{where}：分鏡卡的每個選項都要帶 figures（0～{MAX_FIGURES} 張；整篇不配圖就給空的）"
        )
    found: list[Figure] = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolRejected(400, f"{where}的第 {number} 張圖要是物件")
        figure_kind = item.get("kind")
        if figure_kind not in FIGURE_KINDS:
            raise ToolRejected(
                400,
                f"{where}的第 {number} 張圖：kind 要是 concept（概念圖）、chart（數據圖）或 table（表格）；"
                "這個里程碑不排截圖",
            )
        section = _text(item.get("section"), f"{where}的第 {number} 張圖的 section", MAX_SECTION)
        shows = _text(item.get("shows"), f"{where}的第 {number} 張圖的 shows", MAX_SHOWS, required=False)
        assert section is not None
        found.append(Figure(section=section, kind=figure_kind, shows=shows))
    return tuple(found)


def _parse_claims(raw: object, station: int) -> tuple[Claim, ...] | None:
    """第 6 關的主張清單：1～5 條，每條有主張與怎麼會被證明是錯的（ADR-0025 §3.2）。別的關不帶。"""
    if station != STORYBOARD_STATION:
        if raw is not None:
            raise ToolRejected(400, f"只有第 {STORYBOARD_STATION} 關帶主張清單（claims）")
        return None
    if not isinstance(raw, list) or not MIN_CLAIMS <= len(raw) <= MAX_CLAIMS:
        raise ToolRejected(
            400,
            f"第 {STORYBOARD_STATION} 關要帶主張清單 claims：{MIN_CLAIMS}～{MAX_CLAIMS} 條，"
            "每條是 claim（文章會提出的主張）與 wrong_if（怎麼會被證明是錯的）",
        )
    found: list[Claim] = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolRejected(400, f"第 {number} 條主張要是物件")
        claim = _text(item.get("claim"), f"第 {number} 條主張的 claim", MAX_CLAIM)
        wrong_if = _text(item.get("wrong_if"), f"第 {number} 條主張的 wrong_if", MAX_CLAIM)
        assert claim is not None and wrong_if is not None
        found.append(Claim(claim=claim, wrong_if=wrong_if))
    return tuple(found)


def _parse_origin(data: DataDir, conversation_id: str, raw: object) -> Origin | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ToolRejected(400, "origin 要是物件")
    kind = raw.get("kind")
    if kind not in ("report_topic", "idea", "material"):
        raise ToolRejected(400, "origin.kind 要是 report_topic、idea 或 material")
    title = _text(raw.get("title"), "origin.title", MAX_TITLE, required=False)
    job_id = raw.get("job_id")
    topic = raw.get("topic")
    if kind != "report_topic":
        return Origin(kind=kind, title=title)
    if isinstance(topic, bool) or not isinstance(topic, int) or topic < 1:
        raise ToolRejected(400, "從報告的題材開始時，origin.topic 要是題材的編號（1 起算）")
    if not isinstance(job_id, str) or not job_id:
        raise ToolRejected(400, "從報告的題材開始時，origin.job_id 要是那份報告的工單 id")
    # 範圍照 ADR-0021：只認這個對話開過的工單，別的對話的不說存不存在
    if not conversations.owns_job(data, conversation_id, job_id):
        raise ToolRejected(403, "這張工單不是這個對話開的")
    return Origin(kind="report_topic", job_id=job_id, topic=topic, title=title)


def propose_questions(
    data: DataDir,
    conversation_id: str,
    card_id: str | None,
    station: object,
    questions: object,
    *,
    now: datetime,
    origin: object = None,
    claims: object = None,
) -> Written:
    """記下 agent 提出的問題卡（`source: agent`）；`card_id` 是 None 就開一張新卡。

    第 6 關另外帶主張清單（`claims`，#110）；別的關不帶。

    一關一關往下：前面每一關都要有決定才能問這一關（ADR-0025 §3.2「這一關送出才往下」）。
    同一關再提出一次（重問、補問「尚待作者提供」那一題）可以，原本的回答留著當預設。
    """
    _require_conversation(data, conversation_id)
    number = _station_number(station)
    parsed_origin = _parse_origin(data, conversation_id, origin)
    if parsed_origin is not None and number != 1:
        raise ToolRejected(400, "origin 只在第 1 關提出問題時給")
    # 開卡之前先驗問題卡：不合格的不留一張空卡（編號在寫進去時才照這張卡問過幾張決定）
    known_personas = personas(data)
    _parse_questions(questions, 1, number, known_personas)
    parsed_claims = _parse_claims(claims, number)
    if card_id is None:
        if number != 1:
            raise ToolRejected(400, "新的編輯卡從第 1 關開始；要接著問，帶上 card")
        card_id = _new_card(data, conversation_id, now)

    def build(card: CardState, events: list[AnyEvent], next_n: int) -> tuple[list[AnyEvent], dict[str, Any]]:
        waiting = [s for s in card.stations if s.station < number and not s.decided]
        if waiting:
            raise ToolRejected(
                409,
                f"第 {number} 關還不能問：" + _describe_blocking(
                    [Blocking(station=s.station, status=s.status) for s in waiting]
                ),
            )
        asked = sum(len(e.questions) for e in events if isinstance(e, QuestionsProposed))
        parsed = _parse_questions(questions, asked + 1, number, known_personas)
        return [
            QuestionsProposed(
                n=next_n,
                ts=now,
                source="agent",
                station=number,
                questions=tuple(parsed),
                origin=parsed_origin,
                claims=parsed_claims,
            )
        ], {}

    return _write(data, conversation_id, card_id, build)


def _parse_answers(
    raw: object, station: StationState, card: CardState, *, allow_pending: bool
) -> list[Answer]:
    """一關的回答：剛好回答到這一關現在的每一張問題卡，每張一筆，照問題卡的順序。

    一筆是 `choice`（選項名稱）、`other_text`（「其他」）、`pending`（第 3 關「尚待作者提供」，只有 agent）、
    `no_example`（第 3 關「沒有例子」）其中一個；`material`（引用作者素材 `M<n>`）可以單獨給、或跟
    `choice`、`other_text` 一起給；`note` 是補充。
    """
    current = {q.id: q for q in station.questions if q.current}
    if not current:
        raise ToolRejected(409, f"第 {station.station} 關還沒有提出問題卡")
    if not isinstance(raw, list):
        raise ToolRejected(400, "answers 要是清單")
    materials = {m.id for m in card.materials}
    found: dict[str, Answer] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ToolRejected(400, "每一筆回答要是物件")
        question_id = item.get("question")
        question = current.get(question_id) if isinstance(question_id, str) else None
        if question is None:
            raise ToolRejected(
                400,
                f"第 {station.station} 關現在沒有問題卡 {question_id}（現在的是 {'、'.join(current)}）",
            )
        if question.id in found:
            raise ToolRejected(400, f"問題卡 {question.id} 回答了兩次")
        choice = _text(item.get("choice"), "choice", MAX_LABEL, required=False)
        other_text = _text(item.get("other_text"), "other_text", MAX_OTHER_TEXT, required=False)
        material = _text(item.get("material"), "material", MAX_LABEL, required=False)
        pending = item.get("pending", False)
        no_example = item.get("no_example", False)
        if not isinstance(pending, bool) or not isinstance(no_example, bool):
            raise ToolRejected(400, "pending、no_example 要是 true 或 false")
        note = _text(item.get("note"), "note", MAX_NOTE, required=False)
        forms = sum((choice is not None, other_text is not None, pending, no_example))
        if forms > 1 or (forms == 0 and material is None) or ((pending or no_example) and material):
            raise ToolRejected(
                400,
                f"問題卡 {question.id} 要給 choice（選項名稱）或 other_text（「{OTHER_LABEL}」的文字）其中一個",
            )
        if choice is not None and choice not in {o.label for o in question.options}:
            raise ToolRejected(
                400, f"問題卡 {question.id} 沒有選項「{choice}」；自己打的回答放 other_text"
            )
        if material is not None and material not in materials:
            raise ToolRejected(400, f"這張編輯卡沒有作者素材 {material}")
        if pending and not (allow_pending and station.station == EXAMPLE_STATION):
            raise ToolRejected(
                400, f"只有第 {EXAMPLE_STATION} 關能記「{PENDING_LABEL}」，而且只有 agent 能記"
            )
        if no_example and station.station != EXAMPLE_STATION:
            raise ToolRejected(400, f"只有第 {EXAMPLE_STATION} 關能答「{NO_EXAMPLE_LABEL}」")
        kind = question_kind(question)
        # 分鏡卡可以用「其他」自己寫（寫稿照文字寫，圖位不查）；第 1 關的三種卡只能選列出的
        if kind in _KIND_NAMES and (choice is None or material is not None):
            raise ToolRejected(
                400,
                f"問題卡 {question.id} 是{_KIND_NAMES[kind]}卡：要選列出的其中一個"
                f"（不能用「{OTHER_LABEL}」或作者素材）；要的不在上面，就重新提出第 1 關",
            )
        found[question.id] = Answer(
            question=question.id,
            choice=choice,
            other_text=other_text,
            pending=pending,
            no_example=no_example,
            material=material,
            note=note,
        )
    missing = [q for q in current if q not in found]
    if missing:
        raise ToolRejected(
            400,
            f"一關要一次回答到每一張問題卡：第 {station.station} 關還缺 {'、'.join(missing)}",
        )
    answers = [found[q] for q in current]
    if station.station == EXAMPLE_STATION:
        problem = _example_problem(answers, card.genre, card.origin)
        if problem is not None:
            # 只選提示是回答的格式不對（400）；「沒有例子」跟文體對不上是這張卡的狀態不允許（409）
            raise ToolRejected(409 if any(a.no_example for a in answers) else 400, problem)
    return answers


def _picked_for_me(state: StationState) -> list[Answer]:
    """「你幫我選」：每張問題卡選排第一的選項，補充寫「交給 AI 選」（ADR-0025 §3.2）。

    - 第 1 關的文體卡：選排最前面、不需要作者經驗的文體（`SELF_FREE_GENRES`）；一個都沒有就什麼都不記，
      回 409 請 agent 問創作者。
    - 第 1 關的主題卡、角色卡：跟其他卡一樣選排第一的（主題不需要作者經驗，ADR 沒有限制；
      agent 把它認為最合適的排第一，創作者在決策分頁看得到是 AI 選的）。
    - 第 3 關：只記「尚待作者提供」，不算有決定。
    """
    current = [q for q in state.questions if q.current]
    if not current:
        raise ToolRejected(409, f"第 {state.station} 關還沒有提出問題卡")
    if state.station == EXAMPLE_STATION:
        return [
            Answer(question=q.id, pending=True, note=f"{PICK_FOR_ME_NOTE}；{PENDING_LABEL}")
            for q in current
        ]
    found: list[Answer] = []
    for question in current:
        options = question.options
        if _is_genre_question(question):
            options = tuple(o for o in options if o.value in SELF_FREE_GENRES)
            if not options:
                listed = "、".join(sorted(SELF_FREE_GENRES))
                raise ToolRejected(
                    409,
                    f"問題卡 {question.id} 列的文體都要作者自己的經驗，不能替創作者選（只能替他選 {listed}）："
                    "請創作者自己挑文體",
                )
        found.append(Answer(question=question.id, choice=options[0].label, note=PICK_FOR_ME_NOTE))
    return found


def _record(
    data: DataDir,
    conversation_id: str,
    card_id: str,
    station: object,
    answers: object,
    *,
    source: Source,
    now: datetime,
    mode: Mode | None = None,
    quote: str | None = None,
) -> Written:
    _require_conversation(data, conversation_id)
    number = _station_number(station)

    def build(card: CardState, events: list[AnyEvent], next_n: int) -> tuple[list[AnyEvent], dict[str, Any]]:
        state = card.stations[number - 1]
        if source == "agent" and state.decided:
            # 不能蓋掉創作者剛送出（或沿用）的決定；要改就先重問
            raise ToolRejected(
                409,
                f"第 {number} 關已經有決定（{state.status}），要改先用 propose_questions 重問這一關",
            )
        if mode == "pick_for_me":
            parsed = _picked_for_me(state)
        else:
            parsed = _parse_answers(answers, state, card, allow_pending=source == "agent")
        # 送出編號：這張卡第幾次送出（跨關數）
        submission = f"sub{len({e.submission for e in events if isinstance(e, Answered)}) + 1}"
        return [
            Answered(
                n=next_n + i,
                ts=now,
                source=source,
                station=number,
                submission=submission,
                question=a.question,
                choice=a.choice,
                other_text=a.other_text,
                pending=a.pending,
                no_example=a.no_example,
                material=a.material,
                note=a.note,
                mode=mode,
                quote=quote,
            )
            for i, a in enumerate(parsed)
        ], {}

    return _write(data, conversation_id, card_id, build)


def submit_station(
    data: DataDir,
    conversation_id: str,
    card_id: str,
    station: object,
    answers: object,
    *,
    now: datetime,
) -> Written:
    """畫面的「確認並送出這一關」（`source: ui`）。只由對話主機叫，閘道的 `/tools/` 沒有它。"""
    return _record(data, conversation_id, card_id, station, answers, source="ui", now=now)


def record_interpreted_answers(
    data: DataDir,
    conversation_id: str,
    card_id: str,
    station: object,
    answers: object = None,
    *,
    now: datetime,
    pick_for_me: object = False,
    quote: object = None,
) -> Written:
    """agent 記的回答（`source: agent`）：

    - 解讀創作者在主輸入框打的字（`mode: typed`）：`answers` 照問題卡一張一筆，附創作者的原話 `quote`。
    - 「你幫我選」（`pick_for_me`，`mode: pick_for_me`）：不給 `answers`，閘道自己記每張卡排第一的選項，
      補充寫「交給 AI 選」；第 3 關只記「尚待作者提供」，不算有決定（ADR-0025 §3.2）。
    """
    if not isinstance(pick_for_me, bool):
        raise ToolRejected(400, "pick_for_me 要是 true 或 false")
    if pick_for_me:
        if answers not in (None, []):
            raise ToolRejected(400, "pick_for_me 時不用給 answers：閘道照每張卡排第一的選項記")
        return _record(
            data, conversation_id, card_id, station, None,
            source="agent", now=now, mode="pick_for_me",
        )
    said = _text(quote, "quote（創作者打的原話）", MAX_QUOTE)
    return _record(
        data, conversation_id, card_id, station, answers,
        source="agent", now=now, mode="typed", quote=said,
    )


def keep_decision(
    data: DataDir, conversation_id: str, card_id: str, station: object, *, now: datetime
) -> Written:
    """畫面的「沿用」（`source: ui`）：要重確認的一關保留原本那次送出。"""
    _require_conversation(data, conversation_id)
    number = _station_number(station)

    def build(card: CardState, events: list[AnyEvent], next_n: int) -> tuple[list[AnyEvent], dict[str, Any]]:
        state = card.stations[number - 1]
        # 只有「要重確認」的關能沿用：正在重問、或最後一次是「尚待作者提供」的都沒有可以沿用的決定
        if state.status != "reconfirm" or state.decision is None:
            raise ToolRejected(
                409, f"第 {number} 關現在是 {state.status}，沒有要重確認的決定可以沿用"
            )
        if number == EXAMPLE_STATION:
            kept = next(s for s in state.submissions if s.submission == state.decision)
            problem = _example_problem(kept.answers, card.genre, card.origin)
            if problem is not None:
                raise ToolRejected(409, f"第 {number} 關原本的回答不能沿用：{problem}")
        return [Kept(n=next_n, ts=now, source="ui", station=number, submission=state.decision)], {}

    return _write(data, conversation_id, card_id, build)


def reask_station(
    data: DataDir, conversation_id: str, card_id: str, station: object, *, now: datetime
) -> Written:
    """畫面的「重問這一關」（`source: ui`）：回到還沒送出，原本的回答留著當預設。"""
    _require_conversation(data, conversation_id)
    number = _station_number(station)

    def build(card: CardState, events: list[AnyEvent], next_n: int) -> tuple[list[AnyEvent], dict[str, Any]]:
        if not card.stations[number - 1].questions:
            raise ToolRejected(409, f"第 {number} 關還沒問過，不能重問")
        return [Reasked(n=next_n, ts=now, source="ui", station=number)], {}

    return _write(data, conversation_id, card_id, build)


def add_material(
    data: DataDir,
    conversation_id: str,
    card_id: str | None,
    text: object,
    *,
    now: datetime,
    filename: object = None,
) -> Written:
    """記下一份作者素材（`source: agent`）：內容存成 `materials/<sha256>`，寫一次不改。

    同一張卡已經有一模一樣的內容就拿回原本的編號，不再記一筆。`card_id` 是 None 就開一張新卡。
    """
    _require_conversation(data, conversation_id)
    if not isinstance(text, str) or not text.strip():
        raise ToolRejected(400, "text 要是有內容的字串")
    payload = text.encode("utf-8")
    if len(payload) > MAX_MATERIAL_BYTES:
        raise ToolRejected(413, f"作者素材超過 {MAX_MATERIAL_BYTES} bytes")
    name = _text(filename, "filename", MAX_FILENAME, required=False)
    digest = hashlib.sha256(payload).hexdigest()
    if card_id is None:
        card_id = _new_card(data, conversation_id, now)
    folder = _card_dir(data, conversation_id, card_id)

    def build(card: CardState, events: list[AnyEvent], next_n: int) -> tuple[list[AnyEvent], dict[str, Any]]:
        known = next((m for m in card.materials if m.sha256 == digest), None)
        # 存的檔一律檢查：已經記過的也一樣，之前留下的殘檔在這裡補回完整內容（編號不變）
        _store_material(folder / "materials" / digest, digest, payload)
        if known is not None:
            return [], {"created": False, "material": known.id}
        material = f"M{len(card.materials) + 1}"
        return [
            MaterialAdded(
                n=next_n,
                ts=now,
                source="agent",
                material=material,
                kind="text",
                sha256=digest,
                filename=name,
                size=len(payload),
            )
        ], {"created": True, "material": material}

    return _write(data, conversation_id, card_id, build)


def _store_material(target: Path, digest: str, payload: bytes) -> None:
    """內容存成 `materials/<sha256>`：先寫暫存檔再整個換上去，讀的人不會讀到寫一半的。

    檔案已經在（上一次寫完檔、還沒記事件就被砍）而且內容的 sha256 與大小都對，就不再寫；
    對不上（舊版本寫到一半被砍留下的殘檔）就換掉。檔名就是內容的 sha256，換上去的一定是同一份內容。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        existing = target.read_bytes()
        if len(existing) == len(payload) and hashlib.sha256(existing).hexdigest() == digest:
            return
    temporary = target.with_name(f".{digest}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)


def material_file(data: DataDir, conversation_id: str, card_id: str, sha256: str) -> Path:
    """這張卡照 sha256 存的作者素材內容檔（寫稿快照照快照裡的編輯卡拿，不再讀一次卡）。"""
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ToolRejected(400, "作者素材的 sha256 不合格")
    return _card_dir(data, conversation_id, card_id) / "materials" / sha256


def material_path(data: DataDir, conversation_id: str, card_id: str, material_id: str) -> Path:
    """一份作者素材的內容檔（寫稿快照要複製它，#110）。"""
    card = read_card(data, conversation_id, card_id)
    found = next((m for m in card.materials if m.id == material_id), None)
    if found is None:
        raise ToolRejected(404, f"編輯卡 {card_id} 沒有作者素材 {material_id}")
    return _card_dir(data, conversation_id, card_id) / "materials" / found.sha256
