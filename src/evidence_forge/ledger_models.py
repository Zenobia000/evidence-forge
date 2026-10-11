"""帳本一行的格式（v0.3 §4.4、NFR-OBS-01、FR-LG-01）。

兩種帳本，格式各一個模型：

- `jobs/<id>/ledger.jsonl`（`LedgerEntry`）：工單的模型與工具呼叫。
- `$EF_DATA/ledger/intake.jsonl`（`IntakeEntry`）：投入時的抓取，那時還沒有工單。

放在中立的模組、檔名含 ledger：改欄位的 PR 會被當成 T3；帳單與 UI 讀帳本時只 import 這裡，
不用連帶 import 閘道（FastAPI、httpx）。讀寫只有閘道做（`gateway/ledger.py`）。
"""

from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from evidence_forge.budget import Amount
from evidence_forge.models import TIER_ORDER, Sha256Hex, Tier

LedgerKind = Literal["llm", "search", "fetch", "image", "script"]
LedgerStatus = Literal[
    "ok", "cache_hit", "budget_exceeded", "rate_limited", "timeout", "error"
]


class LedgerEntry(BaseModel):
    """工單的一次模型或工具呼叫：用量、花費、時間與結果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

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
    cost_usd: Amount = 0.0
    gpu_seconds: Amount = 0.0
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
            or TIER_ORDER.index(self.escalated_from) >= TIER_ORDER.index(self.tier)
        ):
            raise ValueError("升級只會從低一級上來")
        return self


class IntakeEntry(BaseModel):
    """投入時的一次抓取（或抽文字的腳本）。還沒有工單，所以沒有工單、段、次與模型等級。

    欄位跟 `LedgerEntry` 同名同義（NFR-OBS-01），多一個 `url` 讓人找得到是哪個網址；
    抓取讀不到（403、付費牆、robots、內網）也要記一行，狀態不是 `ok`。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: AwareDatetime
    kind: LedgerKind
    # 抓了哪個網址；貼上的文字、上傳的檔沒有網址
    url: str | None = None
    credits: NonNegativeInt = 0
    cost_usd: Amount = 0.0
    ttft_ms: NonNegativeInt | None = None
    latency_ms: NonNegativeInt
    queue_ms: NonNegativeInt = 0
    status: LedgerStatus
    # 對回 blobs/ 或抓取快取的原文；沒有存原文就是 None
    request_hash: Sha256Hex | None = None
    response_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def _no_model_calls_at_intake(self) -> Self:
        if self.kind == "llm":
            raise ValueError("投入時不呼叫模型；模型呼叫一定屬於某張工單")
        return self
