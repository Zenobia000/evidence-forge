"""把讀進來的資料整理成畫面要的字：標籤、時間、金額、卡片與詳情頁的資料。

純粹的轉換，不碰 HTTP、不寫檔。要顯示什麼字，照 S0 UI IA §3、§4 與 ADR-0017。
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from evidence_forge.models import (
    Event,
    Job,
    JobResult,
    JobStatus,
    LedgerEntry,
    Recipe,
    Tier,
    Waiting,
)
from evidence_forge.paths import DataDir
from evidence_forge.ui import reading
from evidence_forge.ui.reading import JobBrief

STATUS_LABEL: dict[JobStatus, str] = {
    "queued": "排隊",
    "running": "執行中",
    "review": "待審",
    "approved": "通過",
    "discarded": "丟棄",
}

# 段的名稱（skill 名稱對到畫面上的字）；不認得的段照原樣顯示。
STAGE_NAME = {
    "digest": "整理",
    "research": "補料",
    "compose": "二創",
    "verify": "檢查",
}

# 文類 id → 中文名（CONTEXT.md〈文類〉的 12 種）。
GENRE_NAME = {
    "news": "新聞快訊",
    "hot-take": "熱點評論",
    "tutorial": "教學文",
    "tips": "清單技巧",
    "explainer": "解說文",
    "deep-dive": "深度長文",
    "notes": "筆記摘要",
    "review": "評測比較",
    "case-study": "案例拆解",
    "opinion": "觀點文",
    "story": "故事心得",
    "roundup": "精選彙整",
}

TIER_LABEL: dict[Tier, str] = {
    "local": "本機",
    "strong": "雲端（強）",
    "max": "雲端（最強）",
}

CLAIM_KIND_LABEL = {"fact": "事實", "data": "數據", "opinion": "觀點"}

CHECK_NAME = {
    "C1": "引句真實",
    "C2": "引用完整",
    "C3": "不照抄",
    "C4": "素材使用",
    "C5": "格式",
    "C6": "引用撐得住",
    "C7": "符合目標",
}

CHECK_STATUS_LABEL = {"fail": "沒過", "warn": "提醒", "pass": "過"}

# 「在等什麼」：資料來自 job.md 的 waiting（執行器依 Pi 事件與閘道狀態寫入），不放假動畫。
WAITING_LABEL = {"gpu": "等 GPU", "rate_limit": "等限流", "model": "等模型回應"}
TOOL_LABEL = {
    "fetch_url": "抓取網頁中",
    "web_search": "搜尋中",
    "download_image": "下載圖片中",
    "run_script": "執行腳本中",
}


def stage_name(stage_id: str) -> str:
    return STAGE_NAME.get(stage_id, stage_id)


def genre_name(genre_id: str | None) -> str:
    if not genre_id:
        return ""
    return GENRE_NAME.get(genre_id, genre_id)


def waiting_label(waiting: Waiting) -> tuple[str, str]:
    """(圖示名, 文字)。工具名稱不認得時，照原樣附在後面。"""
    if waiting.kind == "tool":
        tool = waiting.tool or ""
        return "tool", TOOL_LABEL.get(tool, f"執行工具中：{tool}")
    icon = {"gpu": "gpu", "rate_limit": "rate", "model": "clock"}[waiting.kind]
    return icon, WAITING_LABEL[waiting.kind]


def format_duration(delta: timedelta) -> str:
    """`m:ss`；一小時以上 `h:mm:ss`。負的（時鐘差）當 0。"""
    seconds = max(0, int(delta.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_usd(amount: float) -> str:
    """`US$0.21`；有花但不到一分錢寫 `<US$0.01`，免得看起來像沒花。"""
    if 0 < amount < 0.005:
        return "<US$0.01"
    return f"US${amount:.2f}"


def queue_wait_text(delta: timedelta) -> str:
    minutes = int(max(0, delta.total_seconds()) // 60)
    return f"{minutes} 分鐘" if minutes else "不到 1 分鐘"


def meter_percent(spent: float, budget: float) -> int:
    """花費條的長度（0～100）。預算是 0 而已經有花費，條拉滿。"""
    if budget <= 0:
        return 100 if spent > 0 else 0
    return min(100, int(spent / budget * 100))


def result_badge(result: JobResult) -> tuple[str, str, str]:
    """(tone, 圖示, 文字)，文字照 IA §3 的結果標籤。"""
    if result.outcome == "complete":
        return "complete", "check", "完成"
    if result.outcome == "incomplete":
        return "incomplete", "warn", f"未完成：{result.reason}"
    return "failed", "x", f"失敗：{result.reason}"


def local(moment: datetime, now: datetime) -> datetime:
    """轉成「現在」所用的時區，畫面上的時間才和使用者的鐘一致。"""
    return moment.astimezone(now.tzinfo)


# ---------------------------------------------------------------------------
# 看板的卡片
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Badge:
    tone: str
    icon: str
    text: str


@dataclass(frozen=True)
class Card:
    job_id: str
    title: str
    status: JobStatus
    feed: str
    badges: list[Badge] = field(default_factory=list)
    # 排隊
    queued_for: str | None = None
    rerun_note: str | None = None
    # 執行中
    stage_index: str | None = None
    stage: str | None = None
    stage_elapsed: str | None = None
    waiting: Badge | None = None
    # 有花費才顯示
    spent: str | None = None
    budget: str | None = None
    meter: int = 0
    # 待審、通過、丟棄
    version: str | None = None
    date: str | None = None


FEED_LABEL = {"manual": "手動"}


def feed_label(job: Job) -> str:
    return FEED_LABEL.get(job.feed.name, job.feed.name)


def job_title(data_dir: DataDir, job_dir: Path, job: Job) -> str:
    item = reading.read_item(data_dir, job_dir, job)
    return item.title if item else job.id


def reminders(data_dir: DataDir, job_dir: Path, job: Job) -> list[Badge]:
    """待審要標的兩個提醒（IA §3、ADR-0015）：文類判斷信心低、目標文類未校準。"""
    found: list[Badge] = []
    digest = reading.read_digest(job_dir).digest
    if (
        digest is not None
        and digest.genre is not None
        and digest.genre.confidence is not None
        and digest.genre.confidence < reading.LOW_CONFIDENCE
    ):
        found.append(Badge("genre", "question", "文類判斷信心低"))
    if job.target_genre and not reading.genre_is_calibrated(data_dir, job.target_genre):
        found.append(Badge("genre", "question", "目標文類未校準"))
    return found


def build_card(data_dir: DataDir, brief: JobBrief, now: datetime) -> Card:
    job = brief.job
    job_dir = data_dir.job_dir(job.id)
    card = Card(
        job_id=job.id,
        title=job_title(data_dir, job_dir, job),
        status=job.status,
        feed=feed_label(job),
    )
    entries, _ = reading.read_ledger_entries(job_dir)
    spent = reading.total_spend(entries)
    if job.status == "queued":
        rerun = (
            f"退件重跑，將產生 v{job.versions.draft + 1}"
            if job.versions.draft
            else None
        )
        return replace(
            card, queued_for=queue_wait_text(now - brief.since), rerun_note=rerun
        )
    if job.status == "running":
        waiting = None
        if job.waiting is not None:
            icon, text = waiting_label(job.waiting)
            waiting = Badge("waiting", icon, text)
        card = replace(
            card,
            spent=format_usd(spent),
            budget=f"{job.budget.usd:.2f}",
            meter=meter_percent(spent, job.budget.usd),
            waiting=waiting,
        )
        stage = job.stage
        if stage is None:
            return card
        return replace(
            card,
            stage_index=f"{stage.index}／{stage.total}",
            stage=stage_name(stage.id),
            stage_elapsed=format_duration(now - stage.started_at),
        )
    if job.status == "review":
        assert job.result is not None  # 契約保證：待審一定有結果
        tone, icon, text = result_badge(job.result)
        return replace(
            card,
            badges=[Badge(tone, icon, text), *reminders(data_dir, job_dir, job)],
            spent=format_usd(spent),
            version=f"v{job.versions.draft}" if job.versions.draft else "沒有稿子",
        )
    return replace(
        card, spent=format_usd(spent), date=local(brief.since, now).strftime("%m/%d")
    )


# ---------------------------------------------------------------------------
# 詳情頁
# ---------------------------------------------------------------------------

TABS: list[tuple[str, str]] = [
    ("draft", "稿子"),
    ("digest", "整理結果"),
    ("sources", "來源與圖"),
    ("checks", "檢查結果"),
    ("bill", "帳單"),
    ("events", "事件紀錄"),
]
TAB_IDS = [tab_id for tab_id, _ in TABS]


def default_tab(job: Job) -> str:
    """進詳情頁時先開哪一頁（Done when）。

    執行中、排隊、失敗看事件紀錄；待審完成與通過看稿子；未完成看檢查結果；丟棄看稿子。
    """
    if job.status in ("queued", "running"):
        return "events"
    if job.status == "review":
        assert job.result is not None
        if job.result.outcome == "failed":
            return "events"
        return "checks" if job.result.outcome == "incomplete" else "draft"
    return "draft"


def empty_message(tab: str, job: Job) -> tuple[str, str]:
    """某分頁沒有內容時，寫明為什麼（IA §7）。"""
    failed = job.result is not None and job.result.outcome == "failed"
    waiting_to_run = job.status in ("queued", "running")
    if tab == "draft":
        if waiting_to_run:
            return "稿子還沒寫", "二創段跑完後，v1 會出現在這裡。"
        if failed:
            return "沒有稿子", "這張單失敗了，還沒寫到稿子。原因看「事件紀錄」。"
        return "沒有稿子", "這張單沒有產出任何一版稿子。"
    if tab == "digest":
        if waiting_to_run:
            return "還沒跑到整理段", "整理段跑完後，主張與研究方向會出現在這裡。"
        return "沒有整理結果", "這張單沒有跑完整理段，原因看「事件紀錄」。"
    if tab == "checks":
        if waiting_to_run:
            return "還沒跑到檢查段", "二創完成後會跑 C1～C7。"
        return "沒有檢查結果", "這張單沒有跑到檢查段，原因看「事件紀錄」。"
    if tab == "sources":
        return "沒有來源資料", "找不到這張單的素材項目（item.json）。"
    if tab == "bill":
        return "還沒有花費", "這張單還沒有任何一筆帳本紀錄。"
    return "還沒有事件", "執行器還沒有為這張單記下任何事件。"


@dataclass(frozen=True)
class BillRow:
    label: str
    models: str
    escalation: str
    elapsed: str
    gpu_seconds: str
    amount: str


def build_bill(entries: list[LedgerEntry]) -> list[BillRow]:
    """帳本依「段＋第幾次」歸成一列；順序照第一筆出現的時間。"""
    groups: dict[tuple[str, int], list[LedgerEntry]] = {}
    for entry in sorted(entries, key=lambda e: e.ts):
        groups.setdefault((entry.stage, entry.attempt), []).append(entry)
    rows: list[BillRow] = []
    for (stage, attempt), group in groups.items():
        models: list[str] = []
        escalations: list[str] = []
        for entry in group:
            if entry.kind == "llm" and entry.tier:
                _add_unique(models, TIER_LABEL[entry.tier])
                if entry.escalated_from:
                    _add_unique(
                        escalations,
                        f"{TIER_LABEL[entry.escalated_from]}→{TIER_LABEL[entry.tier]}",
                    )
            else:
                _add_unique(
                    models,
                    {
                        "search": "Tavily",
                        "fetch": "抓取",
                        "image": "圖片",
                        "script": "腳本",
                    }[entry.kind],
                )
        gpu = sum(e.gpu_seconds for e in group)
        latency = timedelta(milliseconds=sum(e.latency_ms for e in group))
        label = stage_name(stage) + (f"（第 {attempt} 次）" if attempt > 1 else "")
        rows.append(
            BillRow(
                label=label,
                models="＋".join(models),
                escalation="、".join(escalations) or "—",
                elapsed=format_duration(latency),
                gpu_seconds=f"{gpu:.0f}" if gpu else "—",
                amount=format_usd(sum(e.cost_usd for e in group)),
            )
        )
    return rows


def _add_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


@dataclass(frozen=True)
class EventRow:
    time: str
    stage: str
    kind: str
    detail: str


EVENT_KIND_LABEL = {
    "status": "狀態",
    "stage": "段",
    "decision": "決定",
    "error": "錯誤",
}


def build_events(events: list[Event], now: datetime, limit: int) -> list[EventRow]:
    """最新的在前，最多 `limit` 筆。"""
    rows: list[EventRow] = []
    for event in sorted(events, key=lambda e: e.ts, reverse=True)[:limit]:
        detail = event.message
        if event.kind == "status" and event.to_status:
            move = STATUS_LABEL[event.to_status]
            if event.from_status:
                move = f"{STATUS_LABEL[event.from_status]}→{move}"
            detail = f"{move}　{detail}".strip()
        if event.attempt and event.attempt > 1:
            detail = f"第 {event.attempt} 次嘗試　{detail}".strip()
        rows.append(
            EventRow(
                time=local(event.ts, now).strftime("%H:%M:%S"),
                stage=stage_name(event.stage) if event.stage else "—",
                kind=EVENT_KIND_LABEL[event.kind],
                detail=detail,
            )
        )
    return rows


@dataclass(frozen=True)
class StageCell:
    state: str  # done | running | todo
    name: str
    index: int
    note: str


def build_stage_strip(
    recipe: Recipe, job: Job, entries: list[LedgerEntry], now: datetime
) -> list[StageCell]:
    """段落進度條（整理 → 補料 → 二創 → 檢查）。段的順序與等級來自配方。"""
    current = job.stage.index if job.stage else 0
    cells: list[StageCell] = []
    for index, stage in enumerate(recipe.stages, start=1):
        tier = TIER_LABEL[stage.tier]
        spent = timedelta(
            milliseconds=sum(e.latency_ms for e in entries if e.stage == stage.id)
        )
        if job.status == "running" and index == current and job.stage:
            cells.append(
                StageCell(
                    "running",
                    stage_name(stage.id),
                    index,
                    f"進行中・{format_duration(now - job.stage.started_at)}・{tier}",
                )
            )
        elif job.status == "running" and index < current:
            cells.append(
                StageCell(
                    "done",
                    stage_name(stage.id),
                    index,
                    f"完成・{format_duration(spent)}・{tier}",
                )
            )
        else:
            cells.append(StageCell("todo", stage_name(stage.id), index, "還沒到"))
    return cells
