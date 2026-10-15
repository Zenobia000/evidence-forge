"""右側面板讀的東西：這個對話開過的每張工單的報告與文章（FR-UI-11、IA §5）。

只讀工單資料夾，不寫、不問執行器。檔案長相（這個對話開過哪些單看對話的 `jobs.jsonl`）：

- 分析：`stages/investigate/<次>/report.md`（一句話、下一步、五節，ADR-0024；舊報告是另一組五節，照樣讀得懂）
  與 `sources.json`（來源、查證結果、題材），
  `stages/brief/<次>/brief.json`（關鍵主張的原文），`input/sources/S<n>.md`（放好的來源，檔頭有抓取時間）。
- 重構：`stages/rewrite/<次>/article.md`、`article.json`（標題），`checks/checks.vN.json`
  （C3 的重疊條目；只顯示，不擋，PRD §7）。
- 哪一段交什麼檔，看配方的 `output`，不看段的名字（跟 `chat/desk.py` 同一個做法）。

「讀不到」一律回在結果裡（`error`），不丟例外：一個檔壞了不能讓整個面板打不開（IA §7）。
工單狀態只有排隊、執行中、結束；舊資料夾的待審、通過、丟棄讀成結束（`models.JobStatus`）。
"""

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from markupsafe import Markup
from pydantic import ValidationError

from evidence_forge import conversations
from evidence_forge.budget import micros_to_usd, usd_to_micros
from evidence_forge.checks.analysis import (
    MARK,
    VERDICT_MARKS,
    fact_check_marks,
    section_items,
    topic_entries,
)
from evidence_forge.checks.text import normalize_for_match, tokenize
from evidence_forge.conversations import ConversationJob
from evidence_forge.documents import read_job
from evidence_forge.jobkey import check_name
from evidence_forge.models import Item, Job, Recipe
from evidence_forge.paths import DataDir
from evidence_forge.ui import markdown
from evidence_forge.ui.format import genre_name, month_day_clock

REPORT_FILE = "report.md"
ARTICLE_FILE = "article.md"
REWRITE_RECIPE = "rewrite"
# 結果上寫了這幾個字就標「未校準」（執行器放的原因以「未校準：」開頭）
UNCALIBRATED = "未校準"
# 角色沒寫時當成 me（舊的 job.md 沒有 persona 這欄）
DEFAULT_PERSONA = "me"

Kind = Literal["analysis", "rewrite"]
Verdict = Literal["disputed", "outdated", "unfound", "supported"]

# 查證的排序（決定 2）：有爭議 → 過時 → 查不到 → 有佐證
VERDICT_ORDER: tuple[Verdict, ...] = ("disputed", "outdated", "unfound", "supported")
VERDICT_LABEL: dict[Verdict, str] = {
    "disputed": "有爭議",
    "outdated": "過時",
    "unfound": "查不到",
    "supported": "有佐證",
}
# sources.json 的 verdict 值 → 畫面上的種類
_VERDICT_OF = {
    "disputed": "disputed",
    "outdated": "outdated",
    "not_found": "unfound",
    "supported": "supported",
}

# 報告的五節（ADR-0024）；舊報告的「事實與觀點」「研究題材」放在「作者的看法」「可以寫的題材」的位置，節名照原樣顯示
SECTIONS = ("作者在說什麼", "查證", "作者的看法", "可以寫的題材", "來源清單")
_OLD_SECTIONS = {"作者的看法": "事實與觀點", "可以寫的題材": "研究題材"}
_CITATION = re.compile(r"\[(S\d+)\](?!\()")
_LEAD_LABEL = re.compile(r"^\s*(?:[-*]\s*)?(?:K\d+)?\s*(?:有爭議|過時|有佐證|查不到)?\s*[:：]\s*")
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# C3 總結條目的理由（`checks/overlap.py`）：「<份>：4-gram 重疊率 6.0%（提醒門檻…）」；沒有「<份>：」的舊寫法當成原文
_OVERLAP = re.compile(r"^(?:(?P<label>[^：]+)：)?\d+-gram 重疊率 (?P<rate>\d+(?:\.\d+)?)%")


# ---------------------------------------------------------------------------
# 這個對話的工單
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobInfo:
    job_id: str
    kind: Kind
    job: Job
    row: ConversationJob
    recipe: Recipe | None
    # 第幾條版本鏈（同種工單裡，從 1 起算）與在鏈上的第幾版
    chain: int
    depth: int
    label: str
    # 這張單交出報告或文章的那一段的 id
    stage_id: str | None
    spent_usd: float
    # 同一條版本鏈上的上一版（對照用）；第一版或換了角色另開一條鏈就是 None
    previous_id: str | None = None


def job_kind(job: Job, recipe: Recipe | None) -> Kind:
    if recipe is not None:
        if any(ARTICLE_FILE in stage.output for stage in recipe.stages):
            return "rewrite"
        return "analysis"
    return "rewrite" if job.recipe == REWRITE_RECIPE else "analysis"


def _output_stage(recipe: Recipe | None, kind: Kind) -> str | None:
    if recipe is None:
        return None
    wanted = ARTICLE_FILE if kind == "rewrite" else REPORT_FILE
    named = next((s.id for s in recipe.stages if wanted in s.output), None)
    if named is not None or kind == "rewrite":
        return named
    # 沒有一段交 report.md 的分析配方（打通用的 echo 這類）：最後一段
    return recipe.stages[-1].id


def safe_path(root: Path, path: Path) -> Path | None:
    """`path` resolve 之後還在 `root` 底下就回 resolve 過的路徑；符號連結（檔案或中間任何一層資料夾）
    指到外面就是 None。畫面讀工單資料夾裡的任何檔都先過這裡，不然連結指到哪就把哪裡的內容送出去。"""
    try:
        resolved = path.resolve()
        inside = resolved.is_relative_to(root.resolve())
    except (OSError, RuntimeError):  # 迴圈的符號連結
        return None
    return resolved if inside else None


def safe_file(root: Path, path: Path) -> Path | None:
    safe = safe_path(root, path)
    return safe if safe is not None and safe.is_file() else None


def safe_children(root: Path, folder: Path) -> list[Path]:
    """`folder` 底下的東西，剔掉指到 `root` 外面的。"""
    if safe_path(root, folder) is None or not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir()) if safe_path(root, p) is not None]


def read_text(root: Path, path: Path) -> str | None:
    """讀 `root` 底下的文字檔；不在底下、讀不到、不是 UTF-8 都是 None。"""
    safe = safe_file(root, path)
    if safe is None:
        return None
    try:
        return safe.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_json(root: Path, path: Path) -> Any | None:
    text = read_text(root, path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def spent_micros(job_dir: Path) -> int:
    """一張工單帳本的總花費（微美元）；讀不懂的行跳過，帳本指到外面就當沒有。"""
    path = safe_file(job_dir, job_dir / "ledger.jsonl")
    if path is None:
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            total += usd_to_micros(float(json.loads(line).get("cost_usd", 0.0)))
        except (ValueError, TypeError, AttributeError):
            continue
    return total


# 不在任何對話裡的工單（命令列 `ef new` 開的、對話功能之前的）借用的對話 id：
# 真的對話 id 是 `c-…`，不會撞上；畫面把它當成一個唯讀的「對話」，右側面板照樣讀得到報告與文章
LOOSE = "loose"


def owned_job_ids(data: DataDir) -> set[str]:
    """列在任何一個對話的 `jobs.jsonl` 裡的工單。對話資料夾讀不懂 `conversation.json` 也算：帳不能掉。"""
    owned: set[str] = set()
    if data.conversations.is_dir():
        for folder in sorted(data.conversations.iterdir()):
            if not folder.is_dir():
                continue
            try:
                owned |= {row.job_id for row in conversations.jobs(data, folder.name)}
            except ValueError:
                continue  # 資料夾名字不合規則：不是對話
    return owned


def load_job(job_dir: Path) -> Job | None:
    """`job.md`；讀不懂、不在工單資料夾裡（符號連結指到外面）的是 None。"""
    path = safe_file(job_dir, job_dir / "job.md")
    if path is None:
        return None
    try:
        return read_job(path).job
    except (OSError, ValueError, ValidationError):
        return None


def _persona_of(job: Job) -> str:
    return job.persona or DEFAULT_PERSONA


def _build_infos(
    data: DataDir,
    recipes: dict[str, Recipe],
    entries: list[tuple[ConversationJob, Job]],
    *,
    only_outputs: bool = False,
) -> list[JobInfo]:
    """工單排成版本鏈與標籤。

    版本鏈：`parent`（先看 `jobs.jsonl` 的列，沒有再看 `job.md`）指到同種工單的，接在它後面；
    重構還要角色相同——換角色是另一篇文章，另開一條鏈（CONTEXT.md〈版本鏈〉）。
    標籤在只有一條鏈時是 `v2`，有好幾條時是 `文章 2 v1`。
    """
    found: list[JobInfo] = []
    chains = {"analysis": 0, "rewrite": 0}
    by_id: dict[str, JobInfo] = {}
    for row, job in entries:
        recipe = recipes.get(job.recipe)
        kind = job_kind(job, recipe)
        if only_outputs and (recipe is None or not _delivers(recipe)):
            continue
        parent = by_id.get(row.parent or job.parent or "")
        linked = (
            parent is not None
            and parent.kind == kind
            and (kind == "analysis" or _persona_of(parent.job) == _persona_of(job))
        )
        if linked and parent is not None:
            chain, depth, previous = parent.chain, parent.depth + 1, parent.job_id
        else:
            chains[kind] += 1
            chain, depth, previous = chains[kind], 1, None
        info = JobInfo(
            job_id=row.job_id,
            kind=kind,
            job=job,
            row=row,
            recipe=recipe,
            chain=chain,
            depth=depth,
            label="",
            stage_id=_output_stage(recipe, kind),
            spent_usd=micros_to_usd(spent_micros(data.job_dir(row.job_id))),
            previous_id=previous,
        )
        by_id[row.job_id] = info
        found.append(info)
    many = {kind: chains[kind] > 1 for kind in chains}
    noun = {"analysis": "報告", "rewrite": "文章"}
    return [
        _with_label(
            info,
            f"{noun[info.kind]} {info.chain} v{info.depth}" if many[info.kind] else f"v{info.depth}",
        )
        for info in found
    ]


def _delivers(recipe: Recipe) -> bool:
    """這份配方交報告或文章：舊配方（打通用的 echo、整理加二創的 restructure）的單沒有可以放進面板的東西。"""
    return any(REPORT_FILE in stage.output or ARTICLE_FILE in stage.output for stage in recipe.stages)


def conversation_jobs(
    data: DataDir, recipes: dict[str, Recipe], conversation_id: str
) -> list[JobInfo]:
    """這個對話開過的工單，照開的順序；讀不懂的 `job.md` 跳過。"""
    if conversation_id == LOOSE:
        return loose_jobs(data, recipes)
    entries: list[tuple[ConversationJob, Job]] = []
    for row in conversations.jobs(data, conversation_id):
        job = load_job(data.job_dir(row.job_id))
        if job is not None:
            entries.append((row, job))
    return _build_infos(data, recipes, entries)


def loose_folders(data: DataDir) -> list[Path]:
    """不在任何對話裡的工單資料夾，照名字（開單時間）排。

    當月總表（`bills.month_view`）、花費分頁（`bills.conversation_cost`）、清單與面板都用這一份：
    同一批單，帳才對得上。名字不合工單 id 規則、`jobs/<id>` 是指到 `jobs/` 外面的符號連結的不算。
    """
    if not data.jobs.is_dir():
        return []
    owned = owned_job_ids(data)
    found: list[Path] = []
    for folder in sorted(data.jobs.iterdir()):
        if folder.name in owned or not folder.is_dir():
            continue
        try:
            check_name("工單 id", folder.name)
        except ValueError:
            continue
        if safe_path(data.jobs, folder) is not None:
            found.append(folder)
    return found


def loose_jobs(data: DataDir, recipes: dict[str, Recipe]) -> list[JobInfo]:
    """不在任何對話裡、而且交報告或文章的工單（面板打得開的）；其他的只有花費，看 `loose_folders`。

    版本鏈照 `job.md` 的 `parent`。讀不懂的 `job.md` 跳過。
    """
    entries: list[tuple[ConversationJob, Job]] = []
    for folder in loose_folders(data):
        job = load_job(folder)
        if job is None:
            continue
        try:
            stamp = datetime.fromtimestamp((folder / "job.md").stat().st_mtime).astimezone()
            entries.append((ConversationJob(job_id=folder.name, ts=stamp, parent=job.parent), job))
        except (OSError, ValueError, ValidationError):
            continue
    return _build_infos(data, recipes, entries, only_outputs=True)


def _with_label(info: JobInfo, label: str) -> JobInfo:
    return JobInfo(**{**info.__dict__, "label": label})


def latest_output_dir(data: DataDir, info: JobInfo) -> Path | None:
    """交出報告或文章的那一段的資料夾：編號最大、而且真的有那個檔的那一次。

    最後一次可能是當掉、沒寫完的（沒有 `article.md`），那就往前找有交出東西的；都沒有才給編號最大的。
    """
    if info.stage_id is None:
        return None
    job_dir = data.job_dir(info.job_id)
    attempts = sorted(
        (p for p in safe_children(job_dir, job_dir / "stages" / info.stage_id) if p.is_dir() and p.name.isdigit()),
        key=lambda p: -int(p.name),
    )
    wanted = ARTICLE_FILE if info.kind == "rewrite" else REPORT_FILE
    delivered = next((p for p in attempts if safe_file(job_dir, p / wanted) is not None), None)
    return delivered or (attempts[0] if attempts else None)


def has_output(data: DataDir, info: JobInfo) -> bool:
    """這張單真的交出了報告或文章（面板打得開）。"""
    folder = latest_output_dir(data, info)
    wanted = ARTICLE_FILE if info.kind == "rewrite" else REPORT_FILE
    return folder is not None and safe_file(data.job_dir(info.job_id), folder / wanted) is not None


def finished_at(data: DataDir, info: JobInfo) -> datetime | None:
    """產出檔最後寫的時間；沒有產出就沒有。"""
    folder = latest_output_dir(data, info)
    if folder is None:
        return None
    job_dir = data.job_dir(info.job_id)
    stamps = [p.stat().st_mtime for p in safe_children(job_dir, folder) if p.is_file()]
    return datetime.fromtimestamp(max(stamps)).astimezone() if stamps else None


def original_item(data: DataDir, job: Job) -> Item | None:
    """這張單的素材項目（原文的標題與網址）；讀不到、或沒有素材項目（照自己的想法寫的稿）是 None。"""
    if job.feed is None:
        return None
    text = read_text(data.root, data.root / job.feed.item / "item.json")
    if text is None:
        return None
    try:
        return Item.model_validate_json(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    id: str
    title: str
    url: str | None
    fetched_at: str | None = None


@dataclass(frozen=True)
class FactCheck:
    claim_id: str
    claim: str
    verdict: Verdict
    label: str
    note_html: Markup | None
    sources: list[SourceRef]


@dataclass(frozen=True)
class Topic:
    title: str
    note_html: Markup | None
    sources: list[SourceRef]
    # 報告裡的編號（「寫第 N 個」）；舊報告沒有編號
    number: int | None = None


@dataclass(frozen=True)
class ReportView:
    job_id: str
    label: str
    outcome: str | None
    reason: str
    spent_usd: float
    finished: datetime | None
    error: str | None = None
    # 結果卡的摘要：報告開頭那一句話，舊報告用「作者在說什麼」的第一句
    lead: str = ""
    # 報告開頭那一句話（第一個 `##` 之前、不是下一步的那一行）；舊報告沒有
    summary: str = ""
    # 「▶ 下一步：…」那一行；舊報告沒有
    next_step: str = ""
    intro_html: Markup = Markup("")
    views_heading: str = "作者的看法"
    views_html: Markup = Markup("")
    verify_html: Markup = Markup("")
    checks: list[FactCheck] = field(default_factory=list)
    topics_heading: str = "可以寫的題材"
    topics: list[Topic] = field(default_factory=list)
    topics_html: Markup = Markup("")
    sources: list[SourceRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # 有疑慮＝有爭議＋過時（決定 2）
    doubtful: int = 0
    supported: int = 0
    unfound: int = 0


def _sections(report: str) -> dict[str, str]:
    """報告切成節：節名 → 內文。新舊兩組節名都認（舊報告的節名照原樣當鍵）；不認得的標題併進前一節。"""
    names = (*SECTIONS, *_OLD_SECTIONS.values())
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in report.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            current = next((name for name in names if heading.startswith(name)), None)
            if current is not None:
                found.setdefault(current, [])
                continue
        if current is not None:
            found[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def _slot(sections: dict[str, str], name: str) -> str:
    """這一格用的節名：新的節名在就用它，不然用舊報告的節名（沒有舊的對應就還是新的）。"""
    old = _OLD_SECTIONS.get(name)
    if name in sections or old is None or old not in sections:
        return name
    return old


def _opening(report: str) -> tuple[str, str]:
    """第一個 `##` 之前的（一句話, 下一步那一行）。一級標題不算；舊報告兩個都是空的。"""
    lead = ""
    next_step = ""
    for line in report.splitlines():
        if line.startswith("## "):
            break
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.lstrip("▶ ").startswith("下一步") and not next_step:
            next_step = text
        elif not lead:
            lead = text
    return lead, next_step


def _blocks(section: str) -> list[str]:
    """一節裡一條一條的內容：清單的每一項，或空行隔開的段落。"""
    blocks: list[str] = []
    for chunk in re.split(r"\n\s*\n", section):
        lines = chunk.splitlines()
        if any(re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line) for line in lines):
            for line in lines:
                if re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line):
                    blocks.append(line.strip())
                elif blocks:
                    blocks[-1] += " " + line.strip()
        elif chunk.strip():
            blocks.append(" ".join(line.strip() for line in lines))
    return blocks


_SAFE_URL = re.compile(r"https?://[^\s<>\"']+")


def safe_url(url: object) -> str | None:
    """只放行 http、https 的網址：sources.json 是 agent 寫的，不能讓 `javascript:` 之類變成連結。"""
    if isinstance(url, str) and _SAFE_URL.fullmatch(url.strip()):
        return url.strip()
    return None


def _link_citations(text: str, urls: dict[str, str | None]) -> str:
    """`[S2]` 換成指到來源網址的連結；不在清單上或沒有（安全的）網址的照原樣。"""

    def replace(match: re.Match[str]) -> str:
        url = safe_url(urls.get(match.group(1)))
        if not url:
            return match.group(0)
        # 網址裡的括號會提早結束 Markdown 的連結，換成百分比編碼
        return f"[{match.group(1)}]({url.replace('(', '%28').replace(')', '%29')})"

    return _CITATION.sub(replace, text)


def _first_sentence(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    end = re.search(r"[。！？!?]", text)
    sentence = text[: end.end()] if end else text
    return sentence if len(sentence) <= limit else sentence[:limit] + "…"


def _source_header(job_dir: Path, path: Path) -> dict[str, str]:
    text = read_text(job_dir, path) or ""
    header = text.split("\n\n---\n\n", 1)[0]
    found: dict[str, str] = {}
    for line in header.splitlines():
        key, sep, value = line.partition("：")
        if sep:
            found[key.strip()] = value.strip()
    return found


def placed_sources(data: DataDir, job_id: str) -> dict[str, SourceRef]:
    """放進工單的來源（`input/sources/S<n>.md`，檔頭有標題、網址、抓取時間）。"""
    job_dir = data.job_dir(job_id)
    found: dict[str, SourceRef] = {}
    for path in safe_children(job_dir, job_dir / "input" / "sources"):
        if not (path.name.startswith("S") and path.suffix == ".md"):
            continue
        header = _source_header(job_dir, path)
        fetched = header.get("抓取時間")
        stamp = None
        if fetched:
            try:
                stamp = month_day_clock(datetime.fromisoformat(fetched))
            except ValueError:
                stamp = None
        found[path.stem] = SourceRef(path.stem, header.get("標題", path.stem), safe_url(header.get("網址")), stamp)
    return found


def _source_refs(
    ids: list[str], listed: dict[str, SourceRef], placed: dict[str, SourceRef]
) -> list[SourceRef]:
    refs: list[SourceRef] = []
    for source_id in ids:
        base = listed.get(source_id)
        if base is None:
            continue
        fetched = placed.get(source_id)
        refs.append(
            SourceRef(
                base.id,
                base.title,
                base.url,
                fetched.fetched_at if fetched is not None else None,
            )
        )
    return refs


def report_view(data: DataDir, info: JobInfo) -> ReportView:
    base: dict[str, Any] = {
        "job_id": info.job_id,
        "label": info.label,
        "outcome": info.job.result.outcome if info.job.result else None,
        "reason": info.job.result.reason if info.job.result else "",
        "spent_usd": info.spent_usd,
        "finished": finished_at(data, info),
    }
    folder = latest_output_dir(data, info)
    job_dir = data.job_dir(info.job_id)
    report = read_text(job_dir, folder / REPORT_FILE) if folder is not None else None
    if report is None:
        where = (folder / REPORT_FILE) if folder is not None else data.job_dir(info.job_id)
        return ReportView(error=f"讀不到報告 {info.label} 的檔案：{where}", **base)

    sections = _sections(report)
    placed = placed_sources(data, info.job_id)
    listed_raw = read_json(job_dir, folder / "sources.json") if folder is not None else None
    brief = _read_brief(job_dir)

    item = original_item(data, info.job)
    original = SourceRef("S1", item.title if item else "原文", safe_url(item.url) if item else None, None)
    if item is not None:
        original = SourceRef("S1", item.title, safe_url(item.url), month_day_clock(item.fetched_at))

    listed: dict[str, SourceRef] = {}
    if isinstance(listed_raw, dict):
        for source in listed_raw.get("sources", []):
            if isinstance(source, dict) and isinstance(source.get("id"), str):
                listed[source["id"]] = SourceRef(
                    source["id"], str(source.get("title", source["id"])), safe_url(source.get("url")), None
                )
    urls: dict[str, str | None] = {"S1": original.url, **{k: v.url for k, v in listed.items()}}

    def html(name: str) -> Markup:
        body = sections.get(name, "")
        return markdown.render(_link_citations(body, urls)) if body else Markup("")

    verify_blocks = _blocks(sections.get("查證", ""))
    # 舊報告（ADR-0024 之前）：節名是「事實與觀點」「研究題材」，查證帶 K 編號、照編號對；
    # 新格式的報告只照標記與位置對，對不上就不接來源，不退回舊的那一套
    legacy = any(name in sections for name in _OLD_SECTIONS.values())
    checks = _fact_checks(report, listed_raw, brief, verify_blocks, listed, placed, urls, legacy=legacy)
    views = _slot(sections, "作者的看法")
    topics_name = _slot(sections, "可以寫的題材")
    topics = _topics(
        report, listed_raw, sections.get(topics_name, ""), listed, placed, urls, legacy=legacy
    )
    opening, next_step = _opening(report)
    ordered = sorted(listed.values(), key=lambda s: int(s.id[1:]) if s.id[1:].isdigit() else 0)
    sources = [original, *_source_refs([s.id for s in ordered], listed, placed)]
    # 數字照 sources.json：報告文字跟它對不上、不接來源時，結果卡的數字也不能跟著不見
    counts = _verdict_counts(listed_raw)
    tags: list[str] = []
    if isinstance(brief, dict):
        genre = brief.get("genre")
        if isinstance(genre, dict) and isinstance(genre.get("primary"), str):
            tags.append(f"文體：{genre_name(genre['primary'])}")
        tags += [f"主題：{t}" for t in brief.get("topics", []) if isinstance(t, str)]
    return ReportView(
        error=None,
        lead=_first_sentence(opening or sections.get("作者在說什麼", "")),
        summary=opening,
        next_step=next_step,
        intro_html=html("作者在說什麼"),
        views_heading=views,
        views_html=html(views),
        verify_html=html("查證"),
        checks=checks,
        topics_heading=topics_name,
        topics=topics,
        topics_html=html(topics_name),
        sources=sources,
        tags=tags,
        doubtful=counts["disputed"] + counts["outdated"],
        supported=counts["supported"],
        unfound=counts["unfound"],
        **base,
    )


def _read_brief(job_dir: Path) -> Any | None:
    attempts = [
        p for p in safe_children(job_dir, job_dir / "stages" / "brief") if p.is_dir() and p.name.isdigit()
    ]
    for attempt in sorted(attempts, key=lambda p: -int(p.name)):
        parsed = read_json(job_dir, attempt / "brief.json")
        if parsed is not None:
            return parsed
    return None


def _verdict_counts(listed_raw: Any) -> dict[Verdict, int]:
    """sources.json 的 fact_checks 裡每一種結果有幾條。"""
    counts: dict[Verdict, int] = dict.fromkeys(VERDICT_ORDER, 0)
    entries = listed_raw.get("fact_checks", []) if isinstance(listed_raw, dict) else []
    for entry in entries:
        verdict = _VERDICT_OF.get(str(entry.get("verdict"))) if isinstance(entry, dict) else None
        if verdict is not None:
            counts[verdict] += 1  # type: ignore[index]
    return counts


def _fact_checks(
    report: str,
    listed_raw: Any,
    brief: Any,
    blocks: list[str],
    listed: dict[str, SourceRef],
    placed: dict[str, SourceRef],
    urls: dict[str, str | None],
    *,
    legacy: bool,
) -> list[FactCheck]:
    """查證一條一條：結果與來源看 `sources.json`，文字看報告。

    新報告沒有編號（ADR-0024），每條開頭是【有爭議】這類標記，順序跟 `fact_checks` 一樣：
    標記一條一條對得上（`checks/analysis.py` 也這樣擋）才照位置把來源接上去；對不上（連一個標記都沒有也算）
    就回空的，畫面照報告原文顯示那一節，不把來源掛到別條上。
    `legacy`（舊報告）每條帶著主張編號（「K1 有爭議：…」），照編號對。
    """
    if not isinstance(listed_raw, dict):
        return []
    entries = [entry for entry in listed_raw.get("fact_checks", []) if isinstance(entry, dict)]
    if not legacy:
        items = section_items(report, "查證")
        marks = [match.group(1) if (match := MARK.match(text)) else None for _, text in items]
        if not items or marks != fact_check_marks(listed_raw):
            return []
        paired = [(entry, _split_fact(text, urls)) for entry, (_, text) in zip(entries, items, strict=True)]
    else:
        claims: dict[str, str] = {}
        if isinstance(brief, dict):
            for claim in brief.get("key_claims", []):
                if isinstance(claim, dict) and isinstance(claim.get("id"), str):
                    claims[claim["id"]] = str(claim.get("claim", ""))
        paired = []
        for entry in entries:
            claim_id = str(entry.get("claim", ""))
            text = claims.get(claim_id, claim_id)
            paired.append((entry, (text, _note(blocks, [claim_id, text[:10]], urls))))
    found: list[FactCheck] = []
    for entry, (text, note) in paired:
        verdict = _VERDICT_OF.get(str(entry.get("verdict")))
        if verdict is None:
            continue
        ids = [s for s in entry.get("sources", []) if isinstance(s, str)]
        found.append(
            FactCheck(
                claim_id=str(entry.get("claim", "")),
                claim=text,
                verdict=verdict,  # type: ignore[arg-type]
                label=VERDICT_MARKS[str(entry.get("verdict"))],
                note_html=note,
                sources=_source_refs(ids, listed, placed),
            )
        )
    rank = {v: i for i, v in enumerate(VERDICT_ORDER)}
    return sorted(found, key=lambda c: rank[c.verdict])


def _split_fact(text: str, urls: dict[str, str | None]) -> tuple[str, Markup | None]:
    """新報告的一條查證「【有爭議】事實：說明」拆成（事實, 說明）；沒有冒號就整條是事實。"""
    body = MARK.sub("", text, count=1).strip()
    fact, sep, rest = body.partition("：")
    if not sep or not rest.strip():
        return body.replace("**", ""), None
    return fact.replace("**", "").strip(), markdown.render_inline(_link_citations(rest.strip(), urls))


def _note(blocks: list[str], needles: list[str], urls: dict[str, str | None]) -> Markup | None:
    """舊報告裡講這一條的那一塊（含編號或主張開頭的字）；去掉開頭重複的「K1 有爭議：」。"""
    for block in blocks:
        if any(
            needle and (re.search(rf"\b{re.escape(needle)}\b", block) if re.fullmatch(r"K\d+", needle) else needle in block)
            for needle in needles
        ):
            body = _LEAD_LABEL.sub("", _LIST_MARKER.sub("", block), count=1).strip()
            return markdown.render_inline(_link_citations(body, urls)) if body else None
    return None


def _topics(
    report: str,
    listed_raw: Any,
    section: str,
    listed: dict[str, SourceRef],
    placed: dict[str, SourceRef],
    urls: dict[str, str | None],
    *,
    legacy: bool,
) -> list[Topic]:
    """可以寫的題材。新報告照 `### 1.` 切（跟 `checks/analysis.py` 同一份切法）；
    題材跟 `sources.json` 的 topics 一樣多、標題一樣、順序一樣（`check_topic_alignment` 擋的那一條）
    才照位置接來源，對不上就一個都不接，不拿別的題材的來頂。新報告沒切出題材就回空的，畫面照原文顯示。
    `legacy`（舊報告的研究題材）照 `sources.json` 的題材標題去報告裡找說明。"""
    raw = listed_raw.get("topics", []) if isinstance(listed_raw, dict) else []
    entries = [entry for entry in raw if isinstance(entry, dict)]

    def refs(entry: dict[str, Any] | None) -> list[SourceRef]:
        if entry is None:
            return []
        ids = [s for s in entry.get("sources", []) if isinstance(s, str)]
        return _source_refs(ids, listed, placed)

    if not legacy:
        numbered = topic_entries(report)
        aligned = [normalize_for_match(title) for _, _, title, _ in numbered] == [
            normalize_for_match(str(entry.get("title", ""))) for entry in entries
        ]
        return [
            Topic(
                title,
                markdown.render(_link_citations(body, urls)) if body else None,
                refs(entries[index]) if aligned else [],
                number,
            )
            for index, (_, number, title, body) in enumerate(numbered)
        ]
    blocks = _blocks(section)
    found: list[Topic] = []
    for entry in entries:
        if not isinstance(entry.get("title"), str):
            continue
        title = entry["title"]
        note = None
        for block in blocks:
            if title[:8] in block:
                body = _LIST_MARKER.sub("", block)
                body = body.replace(title, "", 1).lstrip("：: ，,").strip()
                note = markdown.render_inline(_link_citations(body, urls)) if body else None
                break
        found.append(Topic(title, note, refs(entry)))
    return found


# ---------------------------------------------------------------------------
# 文章
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Overlap:
    """跟所有來源（含原文）逐字重疊裡最高的那一個數字，與是哪一份。只顯示，不擋（PRD §7）。"""

    rate: float
    source: str
    # 該份的檢查結果是不是 warn（超過提醒門檻）；畫面只當說明，不改變任何結果
    warned: bool


@dataclass(frozen=True)
class ArticleView:
    job_id: str
    label: str
    outcome: str | None
    reason: str
    spent_usd: float
    finished: datetime | None
    error: str | None = None
    title: str = ""
    text: str = ""
    html: Markup = Markup("")
    words: int = 0
    persona: str = DEFAULT_PERSONA
    genre: str = ""
    uncalibrated: bool = False
    uncalibrated_note: str = ""
    overlap: Overlap | None = None
    diff_html: Markup | None = None
    previous_label: str | None = None
    previous_words: int | None = None


# 照編輯卡寫的文章開頭的 front matter（title、description、genre、theme，#110）
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)


def without_front_matter(article: str) -> str:
    """去掉開頭的 front matter：面板與對照只顯示文章本身，字數也不算它。"""
    return _FRONT_MATTER.sub("", article, count=1)


def _body(article: str) -> str:
    """正文：front matter 與參考來源那一節不算字數（參考來源本來就是照抄出處）。"""
    lines = without_front_matter(article).splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if re.match(r"#{1,6}\s*參考來源", lines[index]):
            return "\n".join(lines[:index])
    return article


def count_words(article: str) -> int:
    return len(tokenize(_body(article)))


def _checks_entries(data: DataDir, info: JobInfo) -> list[dict[str, Any]]:
    """這張單最後一份、屬於交文章那一段的檢查結果的條目。"""
    job_dir = data.job_dir(info.job_id)
    if info.stage_id is None:
        return []
    best: tuple[int, int, list[dict[str, Any]]] | None = None
    for path in safe_children(job_dir, job_dir / "checks"):
        number = re.search(r"^checks\.v(\d+)\.json$", path.name)
        report = read_json(job_dir, path)
        if number is None or not isinstance(report, dict) or report.get("stage") != info.stage_id:
            continue
        entries = [e for e in report.get("entries", []) if isinstance(e, dict)]
        key = (int(report.get("attempt", 0) or 0), int(number.group(1)))
        if best is None or key > best[:2]:
            best = (key[0], key[1], entries)
    return best[2] if best is not None else []


def overlap_of(
    data: DataDir, info: JobInfo, placed: dict[str, SourceRef] | None = None
) -> Overlap | None:
    """從 C3 的總結條目（理由以「<份>：n-gram 重疊率 x%」開頭）找最高的那一份。"""
    found: list[tuple[float, str, bool]] = []
    for entry in _checks_entries(data, info):
        if entry.get("check") != "C3" or entry.get("location"):
            continue
        match = _OVERLAP.match(str(entry.get("reason", "")))
        if match is None:
            continue
        label = (match["label"] or "原文").strip()
        found.append((float(match["rate"]), label, entry.get("status") == "warn"))
    if not found:
        return None
    rate, label, warned = max(found, key=lambda f: f[0])
    sources = placed if placed is not None else placed_sources(data, info.job_id)
    if label in sources:
        label = f"{label} {sources[label].title}"
    return Overlap(rate, label, warned)


def article_view(data: DataDir, info: JobInfo, previous: JobInfo | None) -> ArticleView:
    base: dict[str, Any] = {
        "job_id": info.job_id,
        "label": info.label,
        "outcome": info.job.result.outcome if info.job.result else None,
        "reason": info.job.result.reason if info.job.result else "",
        "spent_usd": info.spent_usd,
        "finished": finished_at(data, info),
    }
    folder = latest_output_dir(data, info)
    job_dir = data.job_dir(info.job_id)
    text = read_text(job_dir, folder / ARTICLE_FILE) if folder is not None else None
    if text is None or folder is None:
        where = (folder / ARTICLE_FILE) if folder is not None else data.job_dir(info.job_id)
        return ArticleView(error=f"讀不到文章 {info.label} 的檔案：{where}", **base)
    meta = read_json(job_dir, folder / "article.json")
    title = meta.get("title") if isinstance(meta, dict) else None
    if not isinstance(title, str) or not title.strip():
        heading = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = heading.group(1).strip() if heading else info.label
    reason = base["reason"]
    uncalibrated = UNCALIBRATED in reason
    note = next(
        (part.strip() for part in re.split(r"[；;]", reason) if UNCALIBRATED in part), ""
    )
    diff_html = None
    previous_words = None
    previous_label = None
    if previous is not None:
        prev_folder = latest_output_dir(data, previous)
        prev_text = (
            read_text(data.job_dir(previous.job_id), prev_folder / ARTICLE_FILE)
            if prev_folder is not None
            else None
        )
        if prev_text is not None:
            diff_html = diff_articles(without_front_matter(prev_text), without_front_matter(text))
            previous_words = count_words(prev_text)
            previous_label = previous.label
    return ArticleView(
        error=None,
        title=title.strip(),
        text=text,
        html=markdown.render(without_front_matter(text)),
        words=count_words(text),
        persona=info.job.persona or DEFAULT_PERSONA,
        genre=genre_name(info.job.target_genre),
        uncalibrated=uncalibrated,
        uncalibrated_note=note,
        overlap=overlap_of(data, info),
        diff_html=diff_html,
        previous_label=previous_label,
        previous_words=previous_words,
        **base,
    )


# ---------------------------------------------------------------------------
# 跟上一版對照：新增的包 <ins>，刪掉的包 <del>（沒用顏色以外的訊號也看得出：底色與刪除線）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    kind: str  # h1..h6、p、li-ul、li-ol
    text: str


def _units(markdown_text: str) -> list[_Unit]:
    units: list[_Unit] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            units.append(_Unit("p", " ".join(paragraph)))
            paragraph.clear()

    for line in markdown_text.splitlines():
        stripped = line.strip()
        heading = re.match(r"(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"([-*+])\s+(.*)$", stripped)
        numbered = re.match(r"\d+[.)]\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            units.append(_Unit(f"h{len(heading.group(1))}", heading.group(2)))
        elif bullet:
            flush()
            units.append(_Unit("li-ul", bullet.group(2)))
        elif numbered:
            flush()
            units.append(_Unit("li-ol", numbered.group(1)))
        else:
            paragraph.append(stripped)
    flush()
    return units


def _sentences(text: str) -> list[str]:
    return [s for s in re.findall(r"[^。！？!?]+[。！？!?]*", text) if s] or [text]


def _wrap(op: str, text: str) -> str:
    inner = markdown.render_inline(text)
    if op == "ins":
        return f'<ins class="ch-ins">{inner}</ins>'
    if op == "del":
        return f'<del class="ch-del">{inner}</del>'
    return str(inner)


def _inline_diff(old: str, new: str) -> str:
    a, b = _sentences(old), _sentences(new)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            parts.append(_wrap("eq", "".join(b[j1:j2])))
        else:
            if i2 > i1:
                parts.append(_wrap("del", "".join(a[i1:i2])))
            if j2 > j1:
                parts.append(_wrap("ins", "".join(b[j1:j2])))
    return "".join(parts)


def _open(kind: str) -> str:
    return {"li-ul": "<ul>", "li-ol": "<ol>"}.get(kind, "")


def diff_articles(old: str, new: str) -> Markup:
    """兩版文章的對照 HTML。段落對不上的整段標新增或刪掉；對得上但有改的，逐句標。"""
    a, b = _units(old), _units(new)
    matcher = difflib.SequenceMatcher(None, [(u.kind, u.text) for u in a], [(u.kind, u.text) for u in b], autojunk=False)
    out: list[str] = []
    open_list: str | None = None

    def emit(kind: str, inner: str) -> None:
        nonlocal open_list
        if kind.startswith("li-"):
            wanted = "ul" if kind == "li-ul" else "ol"
            if open_list != wanted:
                if open_list:
                    out.append(f"</{open_list}>")
                out.append(f"<{wanted}>")
                open_list = wanted
            out.append(f"<li>{inner}</li>")
            return
        if open_list:
            out.append(f"</{open_list}>")
            open_list = None
        tag = kind if kind.startswith("h") else "p"
        out.append(f"<{tag}>{inner}</{tag}>")

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for unit in b[j1:j2]:
                emit(unit.kind, _wrap("eq", unit.text))
            continue
        olds, news = a[i1:i2], b[j1:j2]
        paired = min(len(olds), len(news)) if tag == "replace" else 0
        for old_unit, new_unit in zip(olds[:paired], news[:paired], strict=True):
            if old_unit.kind == new_unit.kind:
                emit(new_unit.kind, _inline_diff(old_unit.text, new_unit.text))
            else:
                emit(old_unit.kind, _wrap("del", old_unit.text))
                emit(new_unit.kind, _wrap("ins", new_unit.text))
        for unit in olds[paired:]:
            emit(unit.kind, _wrap("del", unit.text))
        for unit in news[paired:]:
            emit(unit.kind, _wrap("ins", unit.text))
    if open_list:
        out.append(f"</{open_list}>")
    return Markup("\n".join(out))

