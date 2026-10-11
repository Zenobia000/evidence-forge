"""資料夾契約：各個檔案的格式（架構草案 v0.3 §2～§4）。

後面每一張票都 import 這裡的模型，欄位名稱是執行器、閘道、UI、帳單之間的約定。
改名或刪欄位就是改契約，要一起改所有讀寫它的地方。

共同規則：
- 多出不認得的欄位一律拒絕（`extra="forbid"`），打錯字的欄位不會被默默忽略。
- 模型不可變（`frozen=True`）；要改就 `model_copy(update=...)` 產生新的一份。
- 時間一律帶時區（`AwareDatetime`），寫出來是 ISO 8601。
"""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

# 工單只有這五種狀態（NFR-RUN-02、ADR-0010）。
JobStatus = Literal["queued", "running", "review", "approved", "discarded"]

# 進待審時的結果：做完、沒做完（預算用完、檢查修不好）、失敗（系統錯誤）。
Outcome = Literal["complete", "incomplete", "failed"]

# 模型等級的別名，對到哪個實際模型由閘道決定（ADR-0011）。由低到高排列。
Tier = Literal["local", "strong", "max"]
TIER_ORDER: tuple[Tier, ...] = ("local", "strong", "max")

# 正在等什麼（features/review.feature〈看板說明正在等什麼〉）：等 GPU、等限流、
# 等模型回應；正在跑工具時是 tool，並在 `tool` 寫工具名稱。
WaitingKind = Literal["gpu", "rate_limit", "model", "tool"]

# 內容 hash：SHA-256 的 64 個小寫十六進位字元，不加 "sha256:" 前綴
# （`blobs/<hash>` 直接拿它當檔名，冒號在 Windows 磁碟上不能當檔名）。
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _tier_rank(tier: Tier) -> int:
    return TIER_ORDER.index(tier)


class _Contract(BaseModel):
    """所有檔案格式共用的設定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# job.md 的 frontmatter（v0.3 §2.3）
# ---------------------------------------------------------------------------


class FeedRef(_Contract):
    """這張工單的素材從哪個訂閱源的哪個素材項目來。"""

    connector: str
    name: str
    # 素材項目在資料目錄裡的相對路徑，例：inbox/some-newsletter/abc123
    item: str


class Budget(_Contract):
    """這張工單的上限。金額是美元；Tavily 用 credits 計。"""

    usd: float = Field(ge=0)
    tavily_credits: NonNegativeInt = 0


class StageProgress(_Contract):
    """目前跑到配方的哪一段。index 從 1 起算（「3／4 二創」的 3）。"""

    id: str
    index: PositiveInt
    total: PositiveInt
    attempt: PositiveInt
    started_at: AwareDatetime

    @model_validator(mode="after")
    def _index_within_total(self) -> Self:
        if self.index > self.total:
            raise ValueError(f"第 {self.index} 段超過配方的 {self.total} 段")
        return self


class Waiting(_Contract):
    """執行中的工單此刻在等什麼，從什麼時候開始等。"""

    kind: WaitingKind
    since: AwareDatetime
    # 只有 kind 是 tool 時才有，例：fetch_url
    tool: str | None = None

    @model_validator(mode="after")
    def _tool_named_only_for_tool(self) -> Self:
        if (self.kind == "tool") != (self.tool is not None):
            raise ValueError("kind 是 tool 時要寫工具名稱，其他情況不寫")
        return self


class JobResult(_Contract):
    """進待審時的結果與原因。沒做完或失敗一定要寫原因。"""

    outcome: Outcome
    reason: str = ""

    @model_validator(mode="after")
    def _reason_required_unless_complete(self) -> Self:
        if self.outcome != "complete" and not self.reason.strip():
            raise ValueError(f"結果是 {self.outcome} 時要寫原因")
        return self


class Versions(_Contract):
    """各種產出目前最新是第幾版；0 表示還沒有。"""

    draft: NonNegativeInt = 0


class Job(_Contract):
    """`job.md` 的 frontmatter：一張工單的設定與目前狀態。只有執行器寫。"""

    id: str
    recipe: str
    feed: FeedRef
    goal: str
    # 目標文類（ADR-0015）；沒指定時由執行器依訂閱源或作家設定的預設補上。
    target_genre: str | None = None
    budget: Budget
    status: JobStatus
    stage: StageProgress | None = None
    waiting: Waiting | None = None
    result: JobResult | None = None
    versions: Versions = Versions()

    @model_validator(mode="after")
    def _status_consistency(self) -> Self:
        if self.status == "review" and self.result is None:
            raise ValueError("待審的工單一定要有結果（complete／incomplete／failed）")
        if self.waiting is not None and self.status != "running":
            raise ValueError("只有執行中的工單會在等東西")
        return self


# ---------------------------------------------------------------------------
# 配方的 frontmatter（v0.3 §3.1）
# ---------------------------------------------------------------------------


class RecipeStage(_Contract):
    """配方裡的一段：用哪個 skill、哪一級模型、要產出哪些檔。"""

    id: str
    skill: str
    tier: Tier
    output: tuple[str, ...]
    # 這段要一起載入的其他 skill，例：二創段的 my-style
    uses: tuple[str, ...] = ()
    # 這段可以用掉的 Tavily credits
    tavily_credits: NonNegativeInt = 0
    # 同一級試到 max_attempts 還不合格時，改用哪一級再試；沒寫就不升級
    escalate_to: Tier | None = None

    @model_validator(mode="after")
    def _escalate_upwards(self) -> Self:
        if self.escalate_to is not None and _tier_rank(self.escalate_to) <= _tier_rank(
            self.tier
        ):
            raise ValueError(
                f"{self.id} 段只能從 {self.tier} 往上升級，不能升到 {self.escalate_to}"
            )
        return self


class Recipe(_Contract):
    """一份配方：整條流程有哪幾段、退件從哪段重跑、每段最多試幾次。"""

    name: str
    description: str
    stages: tuple[RecipeStage, ...] = Field(min_length=1)
    # 退件時預設從哪一段重跑
    on_feedback: str
    # 每段最多試幾次（含升級前）
    max_attempts: PositiveInt = 2
    # 檢查沒過時，自動回寫作段修幾次
    auto_fix: NonNegativeInt = 1

    @model_validator(mode="after")
    def _stages_consistent(self) -> Self:
        ids = [stage.id for stage in self.stages]
        duplicated = sorted({stage_id for stage_id in ids if ids.count(stage_id) > 1})
        if duplicated:
            raise ValueError(f"段的 id 重複：{', '.join(duplicated)}")
        if self.on_feedback not in ids:
            raise ValueError(f"on_feedback 指到不存在的段：{self.on_feedback}")
        return self


# ---------------------------------------------------------------------------
# inbox/<訂閱源>/<項目 id>/item.json（v0.3 §2.1）。只有膠水層寫。
# ---------------------------------------------------------------------------


class Item(_Contract):
    """一個素材項目的出處與內容 hash。同一個內容 hash 只開一次工單。"""

    id: str
    # 訂閱源名稱（feeds.yaml 裡的 name；手動投入是 manual）
    feed: str
    # 原文網址；貼上的文字、上傳的檔案沒有
    url: str | None = None
    title: str
    author: str | None = None
    published_at: AwareDatetime | None = None
    fetched_at: AwareDatetime
    content_hash: Sha256Hex


# ---------------------------------------------------------------------------
# jobs/<id>/events.jsonl 的一行（v0.3 §2.1）。只有執行器寫，只增不改。
# ---------------------------------------------------------------------------

# status：狀態轉換；stage：某段開始、結束、重試、升級；decision：套用或略過審稿決定；
# error：出錯。細節寫在 message。
EventKind = Literal["status", "stage", "decision", "error"]


class Event(_Contract):
    """工單發生的一件事。狀態轉換的 from_status 是 None 表示工單剛建立。"""

    ts: AwareDatetime
    kind: EventKind
    from_status: JobStatus | None = None
    to_status: JobStatus | None = None
    stage: str | None = None
    attempt: PositiveInt | None = None
    message: str = ""

    @model_validator(mode="after")
    def _statuses_only_on_status_events(self) -> Self:
        if self.kind == "status" and self.to_status is None:
            raise ValueError("狀態轉換事件要寫轉成什麼狀態（to_status）")
        if self.kind != "status" and (
            self.from_status is not None or self.to_status is not None
        ):
            raise ValueError("只有 status 事件帶 from_status／to_status")
        return self


# ---------------------------------------------------------------------------
# jobs/<id>/review/decision.vN.json：決定檔（S0 UI IA §4.1、§8）。只有 UI 寫。
# ---------------------------------------------------------------------------

# approve 通過；reject 退件（附意見、選重跑段）；discard 丟棄；
# retry 失敗後重試；reedit 通過後又想改。
DecisionAction = Literal["approve", "reject", "discard", "retry", "reedit"]


class Decision(_Contract):
    """創作者在待審做的一個決定，由執行器讀了套用。

    `draft_version` 是做決定時看的稿子版本（還沒有稿子是 0）。兩個分頁同時審同一張單時，
    執行器只套用版本對得上的第一份，其餘標已失效（S0 開發計畫 S0.9）。
    """

    action: DecisionAction
    comment: str = ""
    # 退件或再改時從哪一段重跑；沒寫就用配方的 on_feedback
    rerun_stage: str | None = None
    draft_version: NonNegativeInt
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _fields_fit_the_action(self) -> Self:
        if self.action == "reject" and not self.comment.strip():
            raise ValueError("退件要寫一句意見")
        if self.rerun_stage is not None and self.action not in ("reject", "reedit"):
            raise ValueError(f"{self.action} 不選重跑的段")
        return self


# ---------------------------------------------------------------------------
# jobs/<id>/ledger.jsonl 的一行（v0.3 §4.4、NFR-OBS-01）。只有閘道寫，只增不改。
# ---------------------------------------------------------------------------

LedgerKind = Literal["llm", "search", "fetch", "image", "script"]
LedgerStatus = Literal[
    "ok", "cache_hit", "budget_exceeded", "rate_limited", "timeout", "error"
]


class LedgerEntry(_Contract):
    """一次模型或工具呼叫的用量、花費、時間與結果。"""

    # 是哪一張、哪一段、第幾次
    ts: AwareDatetime
    job_id: str
    stage: str
    attempt: PositiveInt
    kind: LedgerKind
    # 用了哪一級、實際哪個模型、是不是升級上來的（只有 llm 有）
    tier: Tier | None = None
    model: str | None = None
    escalated_from: Tier | None = None
    # 用量
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    # 花費：Tavily credits、美元、本機 GPU 秒數（本機金額是 0）
    credits: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    gpu_seconds: NonNegativeFloat = 0.0
    # 時間：第一個字、總延遲、排隊等 GPU／等限流；不是串流的呼叫沒有第一個字的時間
    ttft_ms: NonNegativeInt | None = None
    latency_ms: NonNegativeInt
    queue_ms: NonNegativeInt = 0
    status: LedgerStatus
    # 對回 blobs/ 的原文；呼叫沒有拿到回應時 response_hash 是 None
    request_hash: Sha256Hex
    response_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def _model_calls_name_their_tier(self) -> Self:
        if self.kind == "llm" and (self.tier is None or self.model is None):
            raise ValueError("模型呼叫要寫用了哪一級（tier）與實際模型（model）")
        if self.escalated_from is not None and (
            self.tier is None
            or _tier_rank(self.escalated_from) >= _tier_rank(self.tier)
        ):
            raise ValueError("升級只會從低一級上來")
        return self
