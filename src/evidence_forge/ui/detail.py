"""工單詳情頁要用的資料：資訊欄、六個分頁。只讀，不寫。"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from markdown_it import MarkdownIt
from markupsafe import Markup

from evidence_forge.checks.report import CheckEntry
from evidence_forge.models import Event
from evidence_forge.paths import DataDir
from evidence_forge.ui import present, reading
from evidence_forge.ui.present import Badge, BillRow, EventRow, StageCell

# 事件紀錄最多列幾筆；完整的在 events.jsonl。
EVENT_LIMIT = 50

# 稿子是 LLM 根據別人的文章寫的，不可信：raw HTML 不放行（html=False 會把標籤轉成文字），
# markdown-it 也會擋掉 javascript: 之類的連結。圖片整個關掉：`![](https://…)` 會讓瀏覽器
# 一打開稿子就連到外面去（洩漏 IP、讓對方知道有人在看）；S0 沒有圖，圖在 S3 才進來。
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable("image")


def render_markdown(text: str) -> Markup:
    return Markup(_MARKDOWN.render(text))


@dataclass(frozen=True)
class ClaimView:
    id: str
    kind_label: str
    kind: str
    text: str
    quote: str
    location: str


@dataclass(frozen=True)
class CheckView:
    name: str
    code: str
    status: str
    status_label: str
    reason: str
    where: str
    excerpt: str


@dataclass(frozen=True)
class InfoRow:
    label: str
    value: str


@dataclass
class DetailView:
    job_id: str
    title: str
    status: str
    status_label: str
    tab: str
    tabs: list[dict[str, object]]
    badges: list[Badge]
    meta_line: str
    goal: str
    recipe: str
    genre_text: str
    genre_uncalibrated: bool
    spent: str
    budget: str
    meter: int
    result_text: str
    version_text: str
    polling: bool
    stages: list[StageCell] = field(default_factory=list)
    # 目前分頁的內容；沒有內容時 empty 是 (標題, 說明)
    empty: tuple[str, str] | None = None
    error: str | None = None
    draft_html: Markup | None = None
    draft_versions: list[int] = field(default_factory=list)
    draft_version: int | None = None
    genre_line: str = ""
    claims: list[ClaimView] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    source_rows: list[InfoRow] = field(default_factory=list)
    check_groups: list[tuple[str, str, list[CheckView]]] = field(default_factory=list)
    check_summary: str = ""
    check_note: str = ""
    bill_rows: list[BillRow] = field(default_factory=list)
    bill_total: str = ""
    bill_budget: str = ""
    event_rows: list[EventRow] = field(default_factory=list)
    event_total: int = 0


def _location_text(row: CheckEntry) -> str:
    loc = row.location
    if loc is None or not loc.file:
        return ""
    if loc.line is not None:
        return f"{loc.file}:{loc.line}"
    return f"{loc.file}{loc.pointer or ''}"


def _build_checks(view: DetailView, job_dir: Path) -> None:
    read = reading.read_checks(job_dir)
    if read.error:
        view.error = read.error
        return
    if read.checks is None:
        return
    by_status: dict[str, list[CheckView]] = {"fail": [], "warn": [], "pass": []}
    for row in sorted(read.checks.entries, key=lambda r: r.check):
        by_status[row.status].append(
            CheckView(
                name=present.CHECK_NAME.get(row.check, ""),
                code=row.check,
                status=row.status,
                status_label=present.CHECK_STATUS_LABEL[row.status],
                reason=row.reason,
                where=_location_text(row),
                excerpt=row.excerpt[:200],
            )
        )
    view.check_groups = [
        (key, present.CHECK_STATUS_LABEL[key], by_status[key])
        for key in ("fail", "warn", "pass")
        if by_status[key]
    ]
    fails, warns = len(by_status["fail"]), len(by_status["warn"])
    if fails or warns:
        view.check_summary = f"{fails + warns} 項要你看：{fails} 項沒過、{warns} 項提醒"
    elif by_status["pass"]:
        view.check_summary = "C1～C7 全部通過"
    else:
        view.check_groups = []
    if not view.check_groups:
        view.empty = ("檢查結果是空的", "檢查段有跑，但沒有留下任何一條結果。")


def _build_digest(view: DetailView, job_dir: Path) -> None:
    read = reading.read_digest(job_dir)
    if read.error:
        view.error = read.error
        return
    digest = read.digest
    if digest is None:
        return
    if digest.genre is not None and digest.genre.primary:
        genre = present.genre_name(digest.genre.primary)
        parts = [f"主：{genre}"]
        if digest.genre.secondary:
            parts.append(
                "次："
                + "、".join(present.genre_name(g) for g in digest.genre.secondary)
            )
        if digest.genre.confidence is not None:
            parts.append(f"信心 {digest.genre.confidence:.2f}")
        view.genre_line = "・".join(parts)
    for claim in digest.claims:
        view.claims.append(
            ClaimView(
                id=claim.id,
                kind=claim.kind,
                kind_label=present.CLAIM_KIND_LABEL.get(claim.kind, claim.kind),
                text=claim.text,
                quote=claim.quote,
                location=f"原文 ¶{claim.location}"
                if isinstance(claim.location, int)
                else (claim.location or ""),
            )
        )
    view.directions = list(digest.research_directions)


def _build_bill(view: DetailView, job_dir: Path, budget_usd: float) -> None:
    entries, _ = reading.read_ledger_entries(job_dir)
    view.bill_rows = present.build_bill(entries)
    view.bill_total = present.format_usd(reading.total_spend(entries))
    view.bill_budget = f"{budget_usd:.2f}"


def build_detail(
    data_dir: DataDir,
    recipes_dir: Path,
    job_id: str,
    tab: str | None,
    version: int | None,
    now: datetime,
) -> DetailView:
    """讀一張工單，整理成詳情頁。找不到丟 `JobNotFound`，`job.md` 壞了丟 `JobUnreadable`。"""
    brief = reading.load_brief(data_dir, job_id)
    job = brief.job
    job_dir = data_dir.job_dir(job_id)
    entries, _ = reading.read_ledger_entries(job_dir)
    spent = reading.total_spend(entries)
    current = tab if tab in present.TAB_IDS else present.default_tab(job)

    badges: list[Badge] = []
    # 退件重跑後的單可能還留著上一輪的結果；排隊、執行中時結果是「跑完才有」。
    shows_result = job.result is not None and job.status not in ("queued", "running")
    result_text = "跑完才有"
    if job.result is not None and shows_result:
        tone, icon, text = present.result_badge(job.result)
        result_text = text
        badges.append(Badge(tone, icon, text))
    elif job.status == "running" and job.stage:
        badges.append(
            Badge(
                "",
                "running",
                f"執行中・{job.stage.index}／{job.stage.total} {present.stage_name(job.stage.id)}",
            )
        )
    else:
        badges.append(Badge("", job.status, present.STATUS_LABEL[job.status]))
    if job.status == "running" and job.waiting is not None:
        icon, text = present.waiting_label(job.waiting)
        badges.append(Badge("", icon, text))
    badges.extend(present.reminders(data_dir, job_dir, job))
    if job.status in ("approved", "discarded"):
        badges.insert(0, Badge("", job.status, present.STATUS_LABEL[job.status]))

    meta = ""
    if job.status == "running" and job.stage:
        meta = f"這段已跑 {present.format_duration(now - job.stage.started_at)}・每 5 秒更新"
    elif job.status == "queued":
        meta = (
            f"排隊 {present.queue_wait_text(now - brief.since)}・還沒花錢・每 5 秒更新"
        )

    recipe = reading.read_recipe_if_any(recipes_dir, job.recipe)
    stages: list[StageCell] = []
    if recipe is not None and job.status in ("queued", "running"):
        stages = present.build_stage_strip(recipe, job, entries, now)

    genre = present.genre_name(job.target_genre) if job.target_genre else ""
    uncalibrated = bool(job.target_genre) and not reading.genre_is_calibrated(
        data_dir, job.target_genre or ""
    )
    version_text = f"v{job.versions.draft}" if job.versions.draft else "還沒有稿子"
    events, _ = reading.read_jsonl(job_dir / "events.jsonl", Event)

    tab_list: list[dict[str, object]] = []
    for tab_id, label in present.TABS:
        note = ""
        if tab_id == "events" and events:
            note = str(len(events))
        tab_list.append(
            {"id": tab_id, "label": label, "selected": tab_id == current, "note": note}
        )

    view = DetailView(
        job_id=job_id,
        title=present.job_title(data_dir, job_dir, job),
        status=job.status,
        status_label=present.STATUS_LABEL[job.status],
        tab=current,
        tabs=tab_list,
        badges=badges,
        meta_line=meta,
        goal=job.goal,
        recipe=job.recipe,
        genre_text=genre,
        genre_uncalibrated=uncalibrated,
        spent=present.format_usd(spent),
        budget=f"{job.budget.usd:.2f}",
        meter=present.meter_percent(spent, job.budget.usd),
        result_text=result_text,
        version_text=version_text,
        polling=job.status in ("queued", "running"),
        stages=stages,
    )

    if current == "draft":
        versions = reading.draft_versions(job_dir)
        view.draft_versions = versions
        chosen = (
            version if version in versions else (versions[-1] if versions else None)
        )
        draft_text = reading.read_draft(job_dir, chosen) if chosen else None
        if draft_text is not None and chosen is not None:
            view.draft_version = chosen
            view.draft_html = render_markdown(draft_text)
        else:
            view.empty = present.empty_message("draft", job)
    elif current == "digest":
        _build_digest(view, job_dir)
        if (
            view.error is None
            and not view.claims
            and not view.genre_line
            and not view.directions
        ):
            view.empty = present.empty_message("digest", job)
    elif current == "sources":
        item = reading.read_item(data_dir, job_dir, job)
        if item is None:
            view.empty = present.empty_message("sources", job)
        else:
            fetched = present.local(item.fetched_at, now).strftime("%Y-%m-%d %H:%M")
            view.source_rows = [InfoRow("標題", item.title)]
            if item.url:
                view.source_rows.append(InfoRow("原文", item.url))
            if item.author:
                view.source_rows.append(InfoRow("作者", item.author))
            view.source_rows.append(InfoRow("抓取時間", fetched))
    elif current == "checks":
        _build_checks(view, job_dir)
        if view.error is None and not view.check_groups and view.empty is None:
            view.empty = present.empty_message("checks", job)
        if job.result is not None and job.result.outcome != "complete":
            view.check_note = f"這張單停下來的原因：{job.result.reason}"
    elif current == "bill":
        _build_bill(view, job_dir, job.budget.usd)
        if not view.bill_rows:
            view.empty = present.empty_message("bill", job)
    else:
        view.event_total = len(events)
        view.event_rows = present.build_events(events, now, EVENT_LIMIT)
        if not view.event_rows:
            view.empty = present.empty_message("events", job)

    _mark_checks_tab(view, _count_attention(job_dir))
    return view


def _count_attention(job_dir: Path) -> int:
    read = reading.read_checks(job_dir)
    if read.checks is None:
        return 0
    return sum(1 for row in read.checks.entries if row.status != "pass")


def _mark_checks_tab(view: DetailView, attention: int) -> None:
    """「檢查結果」分頁旁標「n 項要看」：不管現在開哪一頁都看得到。"""
    if not attention:
        return
    for entry in view.tabs:
        if entry["id"] == "checks":
            entry["note"] = f"{attention} 項要看"
