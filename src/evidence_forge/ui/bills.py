"""帳單：從帳本算本月總表與每個對話的花費（FR-LG-02、IA §6）。只讀帳本與工單資料夾，不寫。

帳本有三種來源（`ledger_models.py`）：`jobs/<id>/ledger.jsonl`（工單）、
`conversations/<id>/ledger.jsonl`（對話本身，ADR-0021；這裡列成「對話本身」一項）、`ledger/intake.jsonl`（投入時的抓取）。
月份依 Asia/Taipei 的日曆月切，跟月上限（`gateway/budget.py`）同一條規則。
金額一律先換成整數微美元再加（`budget.usd_to_micros`），不用浮點數累加。

讀不懂的帳本行跳過，並記下檔案與行號給畫面標出來（IA §7）：其餘照算，所以總數可能偏低。
「報告幾份、文章幾篇」：同一條版本鏈（v1、v2…）算一份或一篇，所有版本的錢都算進去（創作者 2026-10-13 確認）。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from evidence_forge import conversations
from evidence_forge.budget import micros_to_usd, usd_to_micros
from evidence_forge.ledger_models import IntakeEntry, LedgerEntry
from evidence_forge.models import Recipe, Tier
from evidence_forge.paths import DataDir
from evidence_forge.ui import artifacts

TAIPEI = ZoneInfo("Asia/Taipei")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

# 段的 id → 畫面上的名稱；對話本身（段 `chat`）另外列。不認得的照原樣顯示
SKILL_LABEL = {
    "brief": "brief",
    "investigate": "查證與找題材",
    "rewrite": "重構 rewrite",
    "chat": "對話本身",
    # 舊版配方的段
    "digest": "整理",
    "research": "補料",
    "compose": "二創",
    "verify": "檢查",
    "echo": "打通",
}
TIER_LABEL: dict[Tier, str] = {"local": "本機", "strong": "雲端（強）", "max": "雲端（最強）"}
NO_CONVERSATION = "不在對話裡的工單"
INTAKE = "投入抓取"


@dataclass(frozen=True)
class Bar:
    label: str
    usd: float
    # 長條的寬度（0～320）；最長的那一條拉滿
    width: int = 0


@dataclass(frozen=True)
class PersonaRow:
    persona: str
    articles: int
    versions: int
    uncalibrated: int
    usd: float


@dataclass(frozen=True)
class WhereRow:
    label: str
    # 本機與雲端是呼叫次數；搜尋是 credits
    calls: int
    gpu_seconds: float | None
    usd: float
    unit: str = ""


@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str | None
    title: str
    reports: int
    articles: int
    versions: int
    last_activity: datetime | None
    usd: float


@dataclass(frozen=True)
class BadLine:
    where: str
    # 第幾行；0 是整個檔案讀不懂（例：`conversation.json` 壞了，帳照樣算）
    line: int


@dataclass(frozen=True)
class MonthView:
    month: str
    previous_month: str
    next_month: str | None
    total_usd: float
    conversation_count: int
    reports: int
    articles: int
    report_average: float
    article_average: float
    by_skill: list[Bar]
    by_model: list[Bar]
    by_persona: list[PersonaRow]
    by_where: list[WhereRow]
    conversations: list[ConversationRow]
    bad_lines: list[BadLine] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.conversations


def month_of(moment: datetime) -> str:
    return moment.astimezone(TAIPEI).strftime("%Y-%m")


def parse_month(value: str | None, now: datetime) -> str:
    """網址的 `?month=`；不合法（含想拼路徑的）就用這個月。"""
    if value is not None and _MONTH.match(value):
        return value
    return month_of(now)


def _shift(month: str, delta: int) -> str:
    year, number = int(month[:4]), int(month[5:])
    index = year * 12 + (number - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass
class _PersonaStats:
    chains: set[tuple[str, int]] = field(default_factory=set)
    versions: int = 0
    uncalibrated: set[tuple[str, int]] = field(default_factory=set)
    micros: int = 0


def _read_ledger(
    path: Path, data: DataDir, bad: list[BadLine]
) -> list[LedgerEntry]:
    # 帳本是符號連結、指到資料夾外面的，不讀：跟面板讀工單檔案同一條規則
    safe = artifacts.safe_file(path.parent, path)
    if safe is None:
        return []
    found: list[LedgerEntry] = []
    where = path.relative_to(data.root).as_posix()
    for number, line in enumerate(safe.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            found.append(LedgerEntry.model_validate_json(line))
        except ValidationError:
            bad.append(BadLine(where, number))
    return found


def _model_label(entry: LedgerEntry) -> str:
    if entry.kind == "search":
        return "Tavily 搜尋"
    if entry.kind != "llm" or entry.tier is None:
        return "其他工具"
    if entry.escalated_from is not None and entry.tier != "local":
        return "雲端（升級）"
    return TIER_LABEL[entry.tier]


def _bars(totals: dict[str, int]) -> list[Bar]:
    biggest = max(totals.values(), default=0)
    ordered = sorted(totals.items(), key=lambda item: -item[1])
    return [
        Bar(label, micros_to_usd(micros), round(320 * micros / biggest) if biggest else 0)
        for label, micros in ordered
    ]


def _bucket_label(entry: LedgerEntry) -> str:
    """本機／雲端表的列：本機、雲端、搜尋、其他工具（抓取、腳本、圖片）。"""
    if entry.kind == "search":
        return "搜尋（Tavily）"
    if entry.kind == "llm":
        return "本機" if entry.tier == "local" else "雲端"
    return "其他工具"


def _conversation_folders(data: DataDir) -> list[Path]:
    """對話資料夾全部掃一遍，不管 `conversation.json` 讀不讀得懂：帳不能掉。"""
    if not data.conversations.is_dir():
        return []
    return [p for p in sorted(data.conversations.iterdir()) if p.is_dir()]


def month_view(
    data: DataDir, recipes: dict[str, Recipe], month: str, *, now: datetime
) -> MonthView:
    bad: list[BadLine] = []
    skills: dict[str, int] = {}
    models: dict[str, int] = {}
    where: dict[str, list[float]] = {"本機": [0, 0.0, 0], "雲端": [0, 0.0, 0], "搜尋（Tavily）": [0, 0.0, 0]}
    rows: list[ConversationRow] = []
    persona_stats: dict[str, _PersonaStats] = {}
    report_chains: dict[tuple[str, int], int] = {}
    article_chains: dict[tuple[str, int], int] = {}
    total = 0

    def count(entry: LedgerEntry) -> int:
        """一行帳進所有分項：skill、模型、本機／雲端。回它的微美元。"""
        micros = usd_to_micros(entry.cost_usd)
        skill = SKILL_LABEL.get(entry.stage, entry.stage)
        skills[skill] = skills.get(skill, 0) + micros
        label = _model_label(entry)
        models[label] = models.get(label, 0) + micros
        bucket = where.setdefault(_bucket_label(entry), [0, 0.0, 0])
        bucket[0] += entry.credits if entry.kind == "search" else 1
        if entry.kind == "llm" and entry.tier == "local":
            bucket[1] += entry.gpu_seconds
        bucket[2] += micros
        return micros

    def account(
        group: str, infos: list[artifacts.JobInfo], monthly: dict[str, list[LedgerEntry]]
    ) -> tuple[int, int, int]:
        """把這個月有花費的工單算進報告、文章、角色的統計。回（報告幾份、文章幾篇、文章幾版）。"""
        reports: set[int] = set()
        articles: set[int] = set()
        versions = 0
        for info in infos:
            entries = monthly.get(info.job_id, [])
            if not entries:
                continue
            micros = sum(usd_to_micros(e.cost_usd) for e in entries)
            key = (group, info.chain)
            if info.kind == "analysis":
                reports.add(info.chain)
                report_chains[key] = report_chains.get(key, 0) + micros
                continue
            articles.add(info.chain)
            versions += 1
            article_chains[key] = article_chains.get(key, 0) + micros
            stats = persona_stats.setdefault(info.job.persona or artifacts.DEFAULT_PERSONA, _PersonaStats())
            stats.chains.add(key)
            stats.versions += 1
            if info.job.result is not None and artifacts.UNCALIBRATED in info.job.result.reason:
                stats.uncalibrated.add(key)
            stats.micros += micros
        return len(reports), len(articles), versions

    def in_month(entries: list[LedgerEntry]) -> list[LedgerEntry]:
        return [e for e in entries if month_of(e.ts) == month]

    for folder in _conversation_folders(data):
        try:
            data.conversation_dir(folder.name)
        except ValueError:
            continue  # 資料夾名字不合規則：不是對話
        infos = artifacts.conversation_jobs(data, recipes, folder.name)
        own = in_month(_read_ledger(folder / "ledger.jsonl", data, bad))
        monthly = {
            info.job_id: in_month(_read_ledger(data.job_dir(info.job_id) / "ledger.jsonl", data, bad))
            for info in infos
        }
        entries = [*own, *(e for rows_ in monthly.values() for e in rows_)]
        if not entries:
            continue
        try:
            title = conversations.read(data, folder.name).title
        except (OSError, ValueError):
            # 設定檔壞了也不能讓帳掉：掛在對話 id 底下，並列進壞掉的檔
            title = folder.name
            bad.append(BadLine((folder / "conversation.json").relative_to(data.root).as_posix(), 0))
        spend = sum(count(e) for e in entries)
        total += spend
        reports, articles, versions = account(folder.name, infos, monthly)
        rows.append(
            ConversationRow(
                conversation_id=folder.name,
                title=title,
                reports=reports,
                articles=articles,
                versions=versions,
                last_activity=max(e.ts for e in entries),
                usd=micros_to_usd(spend),
            )
        )

    # 不在任何對話裡的工單（命令列開的）：交報告或文章的算進報告與文章，其他的只算花費
    loose_infos = artifacts.loose_jobs(data, recipes)
    loose_monthly: dict[str, list[LedgerEntry]] = {}
    for folder in artifacts.loose_folders(data):
        found = in_month(_read_ledger(folder / "ledger.jsonl", data, bad))
        if found:
            loose_monthly[folder.name] = found
    loose_entries = [e for rows_ in loose_monthly.values() for e in rows_]
    if loose_entries:
        spend = sum(count(e) for e in loose_entries)
        total += spend
        reports, articles, versions = account(artifacts.LOOSE, loose_infos, loose_monthly)
        rows.append(
            ConversationRow(
                conversation_id=artifacts.LOOSE,
                title=NO_CONVERSATION,
                reports=reports,
                articles=articles,
                versions=versions,
                last_activity=max(e.ts for e in loose_entries),
                usd=micros_to_usd(spend),
            )
        )

    # 投入時的抓取（那時還沒有工單）：自己一列，分項才加得起來
    fetched = 0
    fetch_calls = 0
    fetch_last: datetime | None = None
    intake = data.intake_ledger
    if intake.is_file():
        for number, line in enumerate(intake.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = IntakeEntry.model_validate_json(line)
            except ValidationError:
                bad.append(BadLine(intake.relative_to(data.root).as_posix(), number))
                continue
            if month_of(row.ts) == month:
                fetch_calls += 1
                fetched += usd_to_micros(row.cost_usd)
                fetch_last = max(fetch_last, row.ts) if fetch_last else row.ts
    if fetch_calls:
        total += fetched
        skills[INTAKE] = skills.get(INTAKE, 0) + fetched
        models[INTAKE] = models.get(INTAKE, 0) + fetched
        where[INTAKE] = [fetch_calls, 0.0, fetched]
        rows.append(ConversationRow(None, INTAKE, 0, 0, 0, fetch_last, micros_to_usd(fetched)))

    rows.sort(key=lambda r: r.last_activity or datetime.min.replace(tzinfo=TAIPEI), reverse=True)
    reports, articles = len(report_chains), len(article_chains)
    persona_rows = [
        PersonaRow(
            persona=name,
            articles=len(stats.chains),
            versions=stats.versions,
            uncalibrated=len(stats.uncalibrated),
            usd=micros_to_usd(stats.micros),
        )
        for name, stats in sorted(persona_stats.items())
    ]
    current = month_of(now)
    return MonthView(
        month=month,
        previous_month=_shift(month, -1),
        next_month=_shift(month, 1) if month < current else None,
        total_usd=micros_to_usd(total),
        conversation_count=sum(1 for r in rows if r.conversation_id not in (None, artifacts.LOOSE)),
        reports=reports,
        articles=articles,
        report_average=micros_to_usd(sum(report_chains.values())) / reports if reports else 0.0,
        article_average=micros_to_usd(sum(article_chains.values())) / articles if articles else 0.0,
        by_skill=_bars(skills),
        by_model=_bars(models),
        by_persona=persona_rows,
        by_where=[
            WhereRow(
                label,
                int(values[0]),
                values[1] if label == "本機" else None,
                micros_to_usd(int(values[2])),
                "credits" if label.startswith("搜尋") else "",
            )
            for label, values in where.items()
        ],
        conversations=rows,
        bad_lines=bad,
    )


# ---------------------------------------------------------------------------
# 對話的「花費」分頁
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostRow:
    label: str
    models: str
    escalation: str
    when: datetime | None
    usd: float


@dataclass(frozen=True)
class ConversationCost:
    rows: list[CostRow]
    total_usd: float
    # 不在對話裡的工單沒有對話本身，也沒有預算
    budget_usd: float | None
    bad_lines: list[BadLine]


def _models_text(entries: list[LedgerEntry]) -> str:
    """用到的模型，照出現的先後，不重複；搜尋寫 Tavily。"""
    seen: list[str] = []
    for entry in sorted(entries, key=lambda e: e.ts):
        label = "Tavily" if entry.kind == "search" else _model_label(entry)
        if entry.kind not in ("llm", "search"):
            continue
        if label not in seen:
            seen.append(label)
    # 本機在前、雲端在後、搜尋最後（IA 的寫法）
    order = ["本機", "雲端（強）", "雲端（升級）", "雲端（最強）", "Tavily"]
    return "＋".join(sorted(seen, key=lambda s: order.index(s) if s in order else len(order)))


def _escalations(entries: list[LedgerEntry]) -> str:
    found: list[str] = []
    for entry in entries:
        if entry.kind == "llm" and entry.tier and entry.escalated_from:
            text = f"{TIER_LABEL[entry.escalated_from]}→{TIER_LABEL[entry.tier]}"
            if text not in found:
                found.append(text)
    return "、".join(found) or "—"


def conversation_cost(
    data: DataDir, recipes: dict[str, Recipe], conversation_id: str
) -> ConversationCost:
    """這個對話的花費：每次工作一列（分析 v1、文章 v1…），對話本身另列一行，合計是全部（不分月）。

    `loose`（不在任何對話裡的工單）沒有對話本身，也沒有預算。
    """
    bad: list[BadLine] = []
    rows: list[CostRow] = []
    total = 0
    infos = artifacts.conversation_jobs(data, recipes, conversation_id)

    def add(label: str, entries: list[LedgerEntry]) -> None:
        nonlocal total
        micros = sum(usd_to_micros(e.cost_usd) for e in entries)
        total += micros
        rows.append(
            CostRow(
                label,
                _models_text(entries) or "—",
                _escalations(entries),
                min((e.ts for e in entries), default=None),
                micros_to_usd(micros),
            )
        )

    for info in infos:
        noun = "分析" if info.kind == "analysis" else "文章"
        add(f"{noun} {info.label}", _read_ledger(data.job_dir(info.job_id) / "ledger.jsonl", data, bad))
    if conversation_id == artifacts.LOOSE:
        # 面板打不開的單（舊配方、讀不懂的 job.md）只要有花費也列出來：跟當月總表同一批，帳才對得上
        listed = {info.job_id for info in infos}
        for folder in artifacts.loose_folders(data):
            if folder.name in listed:
                continue
            entries = _read_ledger(folder / "ledger.jsonl", data, bad)
            if not entries:
                continue
            job = artifacts.load_job(folder)
            what = f"{job.recipe}" if job is not None else "job.md 讀不懂"
            add(f"工單 {folder.name}（{what}）", entries)
    if conversation_id == artifacts.LOOSE:
        rows.sort(key=lambda r: (r.when is None, r.when))
        return ConversationCost(rows=rows, total_usd=micros_to_usd(total), budget_usd=None, bad_lines=bad)
    own = _read_ledger(data.conversation_dir(conversation_id) / "ledger.jsonl", data, bad)
    own_micros = sum(usd_to_micros(e.cost_usd) for e in own)
    total += own_micros
    rows.append(CostRow("對話本身", _models_text(own) or "—", _escalations(own), None, micros_to_usd(own_micros)))
    return ConversationCost(
        rows=rows,
        total_usd=micros_to_usd(total),
        budget_usd=conversations.budget_usd(data, conversation_id),
        bad_lines=bad,
    )
