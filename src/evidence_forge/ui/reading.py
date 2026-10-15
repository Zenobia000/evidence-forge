"""UI 讀資料夾的地方：只讀，不寫（FR-UI-01、SEAM-08）。

工單、事件、帳本、素材項目、檢查結果都用契約模型讀（`models`、`checks.report`），
不自己解析格式；對不上格式就回報「讀不懂」，不假裝沒事。
整理結果（`claims.json`）是已經退場的 `digest` skill 的產出（ADR-0020），只有舊工單才有；
這裡只定「UI 要用到的那幾個欄位」的寬鬆模型：多出來的欄位忽略、缺的用預設值。新的介面見 #86。
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from evidence_forge.checks.report import ChecksReport
from evidence_forge.documents import (
    FrontmatterError,
    JobDocument,
    read_job,
    read_recipe,
)
from evidence_forge.models import (
    Event,
    Item,
    Job,
    JobStatus,
    Recipe,
)
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir
from evidence_forge.write_once import latest_version

# 文類判斷信心低於這個值，待審要提醒（整理 skill 的預設門檻；設定檔的位置還沒定）。
LOW_CONFIDENCE = 0.6

# 通過、丟棄兩欄只顯示最近幾張（IA §3）。
RECENT_LIMIT = 20

# 工單編號只准是一段檔名：擋掉 `../` 之類走出工單資料夾的路徑。
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

ModelT = TypeVar("ModelT", bound=BaseModel)


class DataDirError(Exception):
    """讀不到資料目錄；`reason` 是給人看的原因。"""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}：{reason}")
        self.path = path
        self.reason = reason


class JobNotFound(Exception):
    """資料目錄裡沒有這張單。"""


class JobUnreadable(Exception):
    """有這張單的資料夾，但 `job.md` 讀不懂。"""

    def __init__(self, job_id: str, reason: str) -> None:
        super().__init__(f"{job_id}：{reason}")
        self.job_id = job_id
        self.reason = reason


def check_data_dir(data_dir: DataDir) -> None:
    """資料目錄讀得到就什麼都不做；否則丟 `DataDirError`，附原因。

    只有 `jobs/` 還沒出現不算錯：那是「還沒有工單」。
    """
    root = data_dir.root
    if not root.exists():
        raise DataDirError(root, "資料夾不存在")
    if not root.is_dir():
        raise DataDirError(root, "這個路徑不是資料夾")
    try:
        next(iter(root.iterdir()), None)
    except PermissionError:
        raise DataDirError(root, "沒有讀取權限") from None
    except OSError as exc:
        raise DataDirError(root, f"讀取失敗（{exc.strerror or exc}）") from None


# ---------------------------------------------------------------------------
# 寬鬆讀 jsonl 與 json
# ---------------------------------------------------------------------------


def read_jsonl(path: Path, model: type[ModelT]) -> tuple[list[ModelT], list[int]]:
    """逐行讀 jsonl。檔案不存在是空的；讀不懂的行略過並回報行號。

    執行器、閘道可能正寫到一半，最後一行不完整是正常的，所以不丟錯。
    """
    if not path.is_file():
        return [], []
    rows: list[ModelT] = []
    bad: list[int] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate_json(line))
            except ValidationError:
                bad.append(number)
    return rows, bad


# ---------------------------------------------------------------------------
# UI 自己的寬鬆模型：整理結果、檢查結果
# ---------------------------------------------------------------------------


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class DigestClaim(_Loose):
    id: str = ""
    # fact／data／opinion；不認得的種類照字面顯示
    kind: str = ""
    text: str = ""
    quote: str = ""
    location: str | int | None = None


class DigestGenre(_Loose):
    primary: str = ""
    secondary: list[str] = []
    confidence: float | None = None


class DigestFile(_Loose):
    genre: DigestGenre | None = None
    claims: list[DigestClaim] = []
    research_directions: list[str] = []

    @field_validator("research_directions", mode="before")
    @classmethod
    def _direction_text(cls, value: object) -> object:
        """digest skill 寫的是 `{question, evidence_needed}`；畫面只要一行字。"""
        if not isinstance(value, list):
            return value
        return [
            f"{item.get('question', '')}（需要的證據：{item.get('evidence_needed', '')}）"
            if isinstance(item, dict)
            else item
            for item in value
        ]


# ---------------------------------------------------------------------------
# 單張工單的素材
# ---------------------------------------------------------------------------


def safe_job_id(job_id: str) -> bool:
    return bool(_SAFE_JOB_ID.fullmatch(job_id))


def _load_json_model(
    path: Path, model: type[ModelT]
) -> tuple[ModelT | None, str | None]:
    """讀一個 JSON 檔成模型。回傳 (結果, 錯誤)：檔案不存在兩個都是 None。"""
    if not path.is_file():
        return None, None
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8"))), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"讀不到 {path.name}（{exc}）"
    except json.JSONDecodeError as exc:
        return None, f"{path.name} 不是合法的 JSON（{exc.msg}，第 {exc.lineno} 行）"
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "最外層"
        return None, f"{path.name} 的格式讀不懂（{where}：{first['msg']}）"


def _numbered_children(directory: Path) -> list[Path]:
    """`stages/<段>/` 底下以數字命名的資料夾，編號大的在前。"""
    if not directory.is_dir():
        return []
    found = [d for d in directory.iterdir() if d.is_dir() and d.name.isdigit()]
    return sorted(found, key=lambda d: int(d.name), reverse=True)


@dataclass(frozen=True)
class DigestRead:
    digest: DigestFile | None
    error: str | None
    attempt: int | None


def read_digest(job_dir: Path) -> DigestRead:
    """最新一次嘗試的整理結果（`stages/digest/<n>/claims.json`）。"""
    for attempt_dir in _numbered_children(job_dir / "stages" / "digest"):
        digest, error = _load_json_model(attempt_dir / "claims.json", DigestFile)
        if digest is not None or error is not None:
            return DigestRead(digest, error, int(attempt_dir.name))
    return DigestRead(None, None, None)


@dataclass(frozen=True)
class ChecksRead:
    checks: ChecksReport | None
    error: str | None
    version: int | None


def read_checks(job_dir: Path) -> ChecksRead:
    """最新版的檢查結果（`checks/checks.vN.json`）。"""
    directory = job_dir / "checks"
    version = latest_version(directory, "checks", ".json")
    if version == 0:
        return ChecksRead(None, None, None)
    checks, error = _load_json_model(
        directory / f"checks.v{version}.json", ChecksReport
    )
    return ChecksRead(checks, error, version)


def draft_versions(job_dir: Path) -> list[int]:
    """已經有的稿子版本，小到大。"""
    latest = latest_version(job_dir / "drafts", "draft", ".md")
    return [
        v
        for v in range(1, latest + 1)
        if (job_dir / "drafts" / f"draft.v{v}.md").is_file()
    ]


def read_draft(job_dir: Path, version: int) -> str | None:
    path = job_dir / "drafts" / f"draft.v{version}.md"
    try:
        return path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return None


def read_item(data_dir: DataDir, job_dir: Path, job: Job) -> Item | None:
    """這張單的素材項目：先看工單自己的 `input/` 複本，沒有再看 inbox 原件。"""
    candidates = [
        job_dir / "input" / "item.json",
        data_dir.root / job.feed.item / "item.json",
    ]
    root = data_dir.root.resolve()
    for path in candidates:
        # feed.item 是 job.md 裡的字串：絕對路徑或 `../` 會跳出資料目錄，那種不讀。
        if not path.resolve().is_relative_to(root) or not path.is_file():
            continue
        try:
            return Item.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def genre_is_calibrated(data_dir: DataDir, genre: str) -> bool:
    """任何一位作家有這個文類的風格檔，就算校準過（ADR-0015）。`_` 開頭是範本，不算。"""
    authors = data_dir.authors
    if not authors.is_dir() or genre.startswith("_"):
        return False
    return any(
        (author / "genres" / f"{genre}.md").is_file()
        for author in authors.iterdir()
        if author.is_dir()
    )


def read_recipe_if_any(recipes_dir: Path, name: str) -> Recipe | None:
    """配方讀不到或讀不懂都回 None：段落進度條少了而已，不拖垮整頁。"""
    if not safe_job_id(name):
        return None
    path = recipes_dir / f"{name}.md"
    if not path.is_file():
        return None
    try:
        return read_recipe(path)
    except (FrontmatterError, ValidationError, OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# 一張工單的概要（看板用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobBrief:
    """從 `job.md` 與 `events.jsonl` 讀出來的、排欄位需要的最少東西。"""

    doc: JobDocument
    events: list[Event]
    # 進到目前狀態的時間：排隊多久、跑完的日期都用它
    since: datetime

    @property
    def job(self) -> Job:
        return self.doc.job


def _brief(data_dir: DataDir, job_id: str) -> JobBrief:
    job_dir = data_dir.job_dir(job_id)
    md = job_dir / "job.md"
    try:
        doc = read_job(md)
    except (FrontmatterError, ValidationError, UnicodeDecodeError, OSError) as exc:
        raise JobUnreadable(job_id, _short(exc)) from None
    events, _ = read_jsonl(job_dir / "events.jsonl", Event)
    entered = [
        e.ts for e in events if e.kind == "status" and e.to_status == doc.job.status
    ]
    since = (
        entered[-1]
        if entered
        else datetime.fromtimestamp(md.stat().st_mtime, tz=timezone.utc)
    )
    return JobBrief(doc, events, since)


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


@dataclass(frozen=True)
class BoardJobs:
    by_status: dict[JobStatus, list[JobBrief]]
    unreadable: list[JobUnreadable]
    # 各欄實際有幾張（通過、丟棄只顯示最近 20 張，但張數是全部）
    counts: dict[JobStatus, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


# 看板的排序：排隊先來先做；其他新的在前。
_OLDEST_FIRST: set[JobStatus] = {"queued"}


def load_board(data_dir: DataDir) -> BoardJobs:
    """掃 `jobs/*/job.md`，依狀態分欄。讀不懂的單另外列出，不讓一張壞單拖垮整個看板。"""
    check_data_dir(data_dir)
    by_status: dict[JobStatus, list[JobBrief]] = {
        "queued": [],
        "running": [],
        "review": [],
        "approved": [],
        "discarded": [],
    }
    unreadable: list[JobUnreadable] = []
    jobs_dir = data_dir.jobs
    entries = sorted(jobs_dir.iterdir()) if jobs_dir.is_dir() else []
    for entry in entries:
        if not entry.is_dir() or not (entry / "job.md").is_file():
            continue
        try:
            brief = _brief(data_dir, entry.name)
        except JobUnreadable as exc:
            unreadable.append(exc)
            continue
        by_status[brief.job.status].append(brief)
    counts: dict[JobStatus, int] = {s: len(v) for s, v in by_status.items()}
    for status, briefs in by_status.items():
        briefs.sort(key=lambda b: b.since, reverse=status not in _OLDEST_FIRST)
        if status in ("approved", "discarded"):
            by_status[status] = briefs[:RECENT_LIMIT]
    return BoardJobs(by_status, unreadable, counts)


def load_brief(data_dir: DataDir, job_id: str) -> JobBrief:
    """讀單張工單；編號不合法或資料夾不在丟 `JobNotFound`，`job.md` 壞了丟 `JobUnreadable`。"""
    check_data_dir(data_dir)
    if not safe_job_id(job_id) or not (data_dir.job_dir(job_id) / "job.md").is_file():
        raise JobNotFound(job_id)
    return _brief(data_dir, job_id)


# ---------------------------------------------------------------------------
# 帳本相關：花費與帳單
# ---------------------------------------------------------------------------


def read_ledger_entries(job_dir: Path) -> tuple[list[LedgerEntry], list[int]]:
    return read_jsonl(job_dir / "ledger.jsonl", LedgerEntry)


def total_spend(entries: list[LedgerEntry]) -> float:
    return sum(e.cost_usd for e in entries)


def count_status(data_dir: DataDir, status: JobStatus) -> int:
    """某個狀態有幾張單（導覽列的待審數用）。讀不到資料目錄當 0。"""
    try:
        check_data_dir(data_dir)
    except DataDirError:
        return 0
    jobs_dir = data_dir.jobs
    if not jobs_dir.is_dir():
        return 0
    count = 0
    for entry in jobs_dir.iterdir():
        md = entry / "job.md"
        if not md.is_file():
            continue
        try:
            count += read_job(md).job.status == status
        except (FrontmatterError, ValidationError, UnicodeDecodeError, OSError):
            continue
    return count
