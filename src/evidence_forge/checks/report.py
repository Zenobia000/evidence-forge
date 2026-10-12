"""檢查結果的格式：`jobs/<id>/checks/checks.vN.json`（架構草案 v0.3 §2.1、§6）。

一份檢查結果是「某段某次的產出」被檢查後的所有條目。每一條附：
是哪一項檢查、`pass`／`fail`／`warn`、位置、理由，以及出問題的那段文字。

現在用到的只有 C5（格式與出處，擋）與 C3（逐字重疊，只顯示），由執行器直接呼叫本套件算出來
（ADR-0020 §3.4）。C1、C2、C4 已經退場；編號留在 `CheckId` 裡，舊工單的 `checks.vN.json` 還讀得出來。
軟檢查 C6、C7 的格式（`parse_soft_checks`）是給還沒做的 `verify` 段，模型回報的 C1～C5 一律不收。
欄位設定照 `models.py` 的共同規則：多出的欄位拒絕、不可變、時間帶時區。
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Self, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)

from evidence_forge.paths import JobDir
from evidence_forge.write_once import VersionedFile, write_next_version

HardCheckId = Literal["C1", "C2", "C3", "C4", "C5"]
SoftCheckId = Literal["C6", "C7"]
CheckId = HardCheckId | SoftCheckId
CheckStatus = Literal["pass", "fail", "warn"]

SOFT_CHECKS: frozenset[str] = frozenset(get_args(SoftCheckId))


class _Contract(BaseModel):
    """與 `models.py` 相同的共同設定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Location(_Contract):
    """問題在哪裡。

    `file` 是工單資料夾裡的相對路徑或產出檔名（例：`drafts/draft.v2.md`、`claims.json`）。
    文字檔用 `line`、`column`（從 1 起算）；JSON 檔用 `pointer`（RFC 6901，例：`/claims/3/quote`）。
    """

    file: str
    line: PositiveInt | None = None
    column: PositiveInt | None = None
    pointer: str | None = None


def json_pointer(parts: Iterable[str | int]) -> str:
    """一串鍵與索引組成 JSON pointer（RFC 6901）：~ 寫成 ~0、/ 寫成 ~1（順序不能反）。"""
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


class CheckEntry(_Contract):
    """一項檢查的一條結果。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # 讓發佈出去的 JSON Schema 也寫明「軟檢查不會 fail」，不只靠 Python 的驗證器。
        json_schema_extra={
            "if": {"properties": {"check": {"enum": [*get_args(SoftCheckId)]}}},
            "then": {"properties": {"status": {"enum": ["pass", "warn"]}}},
        },
    )

    check: CheckId
    status: CheckStatus
    reason: str = Field(min_length=1)
    location: Location | None = None
    # 出問題的那段文字（捏造的引句、照抄的片段、圖的網址）；整體判斷時留空
    excerpt: str = ""

    @model_validator(mode="after")
    def _soft_checks_never_fail(self) -> Self:
        # 軟檢查只提醒、不擋（PRD §7）：C6、C7 不會有 fail。
        if self.check in SOFT_CHECKS and self.status == "fail":
            raise ValueError(f"{self.check} 是軟檢查，只能 pass 或 warn")
        if not self.reason.strip():
            raise ValueError("每一條檢查結果都要寫理由")
        return self


class ChecksReport(_Contract):
    """`checks.vN.json` 的內容：某段某次的產出的全部檢查條目。"""

    stage: str
    attempt: PositiveInt
    # 被檢查的檔案，工單資料夾裡的相對路徑（例：`drafts/draft.v2.md`）
    files: tuple[str, ...] = ()
    created_at: AwareDatetime
    entries: tuple[CheckEntry, ...]

    @property
    def failed_checks(self) -> tuple[CheckId, ...]:
        """有 fail 條目的檢查，照 C1～C7 排序、不重複。執行器用它判斷要不要回去修。"""
        failed = {entry.check for entry in self.entries if entry.status == "fail"}
        return tuple(sorted(failed))

    @property
    def passed(self) -> bool:
        """沒有任何 fail（warn 不擋）。"""
        return not self.failed_checks


def write_checks_report(job_dir: Path, report: ChecksReport) -> VersionedFile:
    """把檢查結果寫成 `<job_dir>/checks/checks.vN.json`，N 是下一號，舊版不動。

    這是本套件唯一寫檔的地方，由執行器呼叫；其他函式都只判斷、不寫檔。
    """
    payload = report.model_dump_json(indent=2) + "\n"
    return write_next_version(JobDir(job_dir).checks, "checks", ".json", payload)


def checks_report_schema() -> dict[str, Any]:
    """`checks.vN.json` 的 JSON Schema（2020-12），給 UI、帳單等不用 Python 讀的地方對照。"""
    schema = ChecksReport.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


# ---------------------------------------------------------------------------
# 軟檢查 C6、C7 的介面：`verify` 段的產出（S0.8 實作）
# ---------------------------------------------------------------------------


class SoftCheckEntry(_Contract):
    """`verify` 段回報的一條軟檢查結果：只能是 C6、C7，只能 pass 或 warn，一定要有理由。

    C6 引用撐得住：被引用的來源真的支持那句話（逐條判斷，位置指到那句）。
    C7 符合目標：稿子有照工單的目標寫（整體判斷，位置可以留空）。
    """

    check: SoftCheckId
    status: Literal["pass", "warn"]
    reason: str = Field(min_length=1)
    location: Location | None = None
    excerpt: str = ""


class SoftCheckReport(_Contract):
    """`verify` 段產出的 `checks.json`：`{"entries": [...]}`。"""

    entries: tuple[SoftCheckEntry, ...]


def parse_soft_checks(payload: object) -> tuple[CheckEntry, ...]:
    """把 `verify` 段的產出轉成檢查條目，由執行器併進 `checks.vN.json`。

    只收 C6、C7：模型回報的 C1～C5 一律拒收（丟 `ValueError`），硬檢查只能由執行器自己算
    （測試藍圖 SEAM-06 的攻擊案例）；C6、C7 寫 fail 也拒收，軟檢查只提醒、不擋。
    """
    report = SoftCheckReport.model_validate(payload)
    return tuple(
        CheckEntry.model_validate(entry.model_dump()) for entry in report.entries
    )
