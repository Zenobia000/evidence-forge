"""手動投入（FR-IN-01、SEAM-09）：網址、貼文字、上傳檔案 → `inbox/manual/<項目>/` → 開單。

順序：
1. 先決定實際生效的目標：有填就用（去掉前後空白），沒填就用配方的 `default_goal`。
2. 先抓取與抽文字，再寫素材項目：
   - 網址：經閘道的 `Fetcher`（跟 agent 的 `fetch_url` 同一套網址檢查與快取），
     帳記在 `ledger/intake.jsonl`。讀不到（403、付費牆、robots、內網位址）就記一份
     標成讀不到的素材項目、寫明原因，丟 `SourceUnreadable`，**不開工單**。
   - 檔案：只收 Markdown、純文字、PDF；PDF 抽不到文字就報錯。
3. 同一個內容 hash 只建一份素材項目（`item.json`、`content.md`、`raw/` 原檔），重複投入沿用。
4. 同一份素材、同一份配方、同樣的目標、**同一個對話**已有排隊中、執行中或完成的工單，就回傳那一張，
   不另開；其中一樣不同就開新的。失敗、未完成、丟棄的單不拿回：再丟一次就是要重新分析。
   `again`（重做）一律不走去重。
   兩個對話丟同一篇文章各開各的單（ADR-0021 §3.6）；命令列 `ef new` 沒有對話（None），
   只跟同樣沒有對話的工單去重。

工單的 `job.md` 與 `events.jsonl` 只有執行器寫，所以開單放在 runner。
"""

import fcntl
import hashlib
import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath

from pydantic import ValidationError

from evidence_forge.documents import FrontmatterError, JobDocument, read_job, write_job
from evidence_forge.extract import pdf_to_text
from evidence_forge.gateway.fetch import Fetcher, cache_key, fetch_for_intake
from evidence_forge.models import Budget, Event, FeedRef, Item, Job, Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner import events
from evidence_forge.runner.stage_inputs import default_genre
from evidence_forge.write_once import write_once

MANUAL_FEED = "manual"
_TEXT_SUFFIXES = (".md", ".markdown", ".txt")


@dataclass(frozen=True)
class PastedText:
    text: str


@dataclass(frozen=True)
class UploadedFile:
    """上傳的檔：原本的檔名（看副檔名決定怎麼讀）與內容。"""

    name: str
    data: bytes


@dataclass(frozen=True)
class WebPage:
    url: str


Source = PastedText | UploadedFile | WebPage


class SourceUnreadable(Exception):
    """網址讀不到（403、付費牆、robots、內網位址…）；已記一份標成讀不到的素材項目，沒開工單。"""

    def __init__(self, item: Item) -> None:
        super().__init__(item.unavailable_reason)
        self.item = item
        self.reason = item.unavailable_reason


@dataclass(frozen=True)
class Intake:
    job: Job
    # False：同一份素材、同樣的目標已有沒丟棄的工單，回傳的是那一張
    created: bool


@dataclass(frozen=True)
class _Material:
    """抓取與抽文字之後、寫進 inbox 之前的一篇東西。"""

    text: str
    title: str
    url: str | None = None
    author: str | None = None
    raw_name: str | None = None
    raw: bytes | None = None


def _title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return "（沒有標題）"


def effective_goal(recipe: Recipe, goal: str | None) -> str:
    """實際生效的目標：有填就用（去掉前後空白），沒填就用配方的預設目標。"""
    chosen = (goal or "").strip() or (recipe.default_goal or "").strip()
    if not chosen:
        raise ValueError(f"沒填目標，配方 {recipe.name} 也沒有預設目標（default_goal）")
    return chosen


def _from_file(upload: UploadedFile) -> _Material:
    name = PurePath(upload.name.replace("\\", "/")).name or "upload"
    suffix = PurePath(name).suffix.lower()
    if suffix == ".pdf":
        text = pdf_to_text(upload.data)
    elif suffix in _TEXT_SUFFIXES:
        try:
            text = upload.data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError(f"{name} 不是 UTF-8 的文字檔") from None
    else:
        raise ValueError(
            f"不支援的檔案格式 {suffix or '（沒有副檔名）'}：只收 Markdown、純文字、PDF"
        )
    return _Material(text=text, title=_title(text), raw_name=name, raw=upload.data)


def _from_url(data: DataDir, fetcher: Fetcher, url: str, now: datetime) -> _Material:
    record = fetch_for_intake(fetcher, url).record
    if record.status != "ok":
        item = Item(
            id=f"url-{cache_key(url)[:16]}",
            feed=MANUAL_FEED,
            url=record.url,
            title=record.url,
            fetched_at=now,
            unavailable=True,
            unavailable_reason=record.reason,
        )
        try:
            write_once(_item_dir(data, item) / "item.json", _item_json(item))
        except FileExistsError:
            pass  # 之前就讀不到過：留第一次的紀錄（只增不改），這次的原因在 intake.jsonl
        raise SourceUnreadable(item)
    raw = fetcher.blobs.get(record.raw_hash) if record.raw_hash else None
    return _Material(
        text=record.text,
        title=record.title or _title(record.text),
        url=record.url,
        author=record.author,
        raw_name="page.html" if raw is not None else None,
        raw=raw,
    )


def _item_dir(data: DataDir, item: Item) -> Path:
    return data.inbox / MANUAL_FEED / item.id


def _item_json(item: Item) -> str:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _write_item(data: DataDir, material: _Material, now: datetime) -> tuple[Item, Path]:
    """同一個內容 hash 只建一份素材項目；已經有了就沿用。"""
    content_hash = hashlib.sha256(material.text.encode("utf-8")).hexdigest()
    item = Item(
        id=content_hash[:16],
        feed=MANUAL_FEED,
        url=material.url,
        title=material.title,
        author=material.author,
        fetched_at=now,
        content_hash=content_hash,
    )
    item_dir = _item_dir(data, item)
    if not (item_dir / "item.json").is_file():
        _publish_item(item_dir, item, material)
    return item, item_dir


def _publish_item(item_dir: Path, item: Item, material: _Material) -> None:
    """先在旁邊的暫存資料夾寫完，再一次改名成正式的素材項目資料夾。

    寫到一半被中斷（Ctrl-C、WSL 當掉）時，正式資料夾不會出現半份，下次投入同一份
    內容照樣寫得進去；兩個程序同時投入同一份內容，先改名成功的那份留下，另一份丟掉。
    """
    staging = item_dir.parent / f".{item.id}.{uuid.uuid4().hex}"
    try:
        write_once(staging / "content.md", material.text)
        if material.raw_name is not None and material.raw is not None:
            write_once(staging / "raw" / material.raw_name, material.raw)
        write_once(staging / "item.json", _item_json(item))
        try:
            staging.rename(item_dir)
        except OSError:
            if not (item_dir / "item.json").is_file():
                raise
            # 別的程序先完成了同一份內容：沿用它的
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _open_job_for(
    data: DataDir, item_path: str, recipe: str, goal: str, conversation: str | None
) -> Job | None:
    """同一份素材、同一份配方、同樣目標、同一個對話，而且拿回來有用的工單；沒有就是 None。"""
    if not data.jobs.is_dir():
        return None
    for job_dir in sorted(data.jobs.iterdir()):
        try:
            job = read_job(job_dir / "job.md").job
        except (OSError, FrontmatterError, ValidationError):
            continue  # 正在建立或壞掉的工單不算
        if (
            job.feed.item == item_path
            and job.recipe == recipe
            and _reusable(job)
            and job.goal.strip() == goal
            and job.conversation == conversation
        ):
            return job
    return None


def _reusable(job: Job) -> bool:
    """去重拿得回來的單：排隊中、執行中，或跑完而且完成。

    失敗與未完成的拿回來，那篇文章在這個對話裡就再也分析不了（ADR-0021 §3.6）。
    不靠「排除丟棄」：工單狀態收成三種之後，丟棄的單讀起來就是跑完了（#89）。
    """
    if job.status in ("queued", "running"):
        return True
    return (
        job.status != "discarded"
        and job.result is not None
        and job.result.outcome == "complete"
    )


def same_source(data: DataDir, job: Job, source: Source) -> bool:
    """這張單是不是從同一份素材開的；不抓網頁、不寫任何東西。

    閘道在對話已有單在跑時，先用它判斷這次會不會只是去重拿回那一張，不會的話抓之前就擋下。
    貼的文字比內容 hash；網址比素材項目記的網址（同一個網址內容變了，就等真的抓了再說）。
    """
    match source:
        case PastedText(text=text):
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            item_dir = data.inbox / MANUAL_FEED / content_hash[:16]
            return job.feed.item == item_dir.relative_to(data.root).as_posix()
        case WebPage(url=url):
            try:
                item = Item.model_validate_json(
                    (data.root / job.feed.item / "item.json").read_text("utf-8")
                )
            except (OSError, ValueError):
                return False
            return item.url == url.strip()
        case UploadedFile():
            return False


def _new_job_dir(data: DataDir, base: str) -> tuple[str, Path]:
    """獨佔建立工單資料夾；同一秒同一份素材撞名就加 -2、-3。"""
    data.jobs.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        job_id = base if suffix == 1 else f"{base}-{suffix}"
        try:
            (data.jobs / job_id).mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return job_id, data.jobs / job_id


def recipe_credits(recipe: Recipe) -> int:
    """配方各段 `tavily_credits` 的總和：開單沒指定 credits 時，這張單的 Tavily 上限。"""
    return sum(stage.tavily_credits for stage in recipe.stages)


def open_job(
    data: DataDir,
    recipe: Recipe,
    source: Source,
    *,
    goal: str | None,
    now: datetime,
    fetcher: Fetcher | None = None,
    budget_usd: float = 0.5,
    budget_credits: int | None = None,
    genre: str | None = None,
    conversation: str | None = None,
    again: bool = False,
    before_create: Callable[[], None] | None = None,
) -> Intake:
    """投入一篇東西並開單（或回傳已有的那張）。

    `conversation` 是開單的對話 id，記進 `job.md`、也是去重的鍵的一部分；命令列不給。
    `again` 是「重做」：不走去重，一定開新單（US-21 第 3 點）。
    `before_create` 在確定要開新單（去重沒拿到舊單）時、建資料夾之前呼叫，在同一把鎖裡；
    它丟例外就不開單。閘道用它擋「這個對話已經有單在跑」，去重拿回舊單時不擋。

    `budget_credits` 是這張單的 Tavily credits 上限；沒給就用配方各段 `tavily_credits` 的總和
    （`recipe_credits`），配方沒有一段要搜尋就是 0。

    錯誤：網址讀不到丟 `SourceUnreadable`；內容是空的、檔案格式不支援、PDF 抽不到字、
    沒有目標可用丟 `ValueError`（含 `ExtractError`）。兩種都不開單。
    """
    chosen_goal = effective_goal(recipe, goal)
    # 目標文類沒指定就用作家設定的預設文類，開單時記進 job.md（FR-CP-03、ADR-0015 §3.5）
    genre = genre or default_genre(data)
    match source:
        case PastedText(text=text):
            material = _Material(text=text, title=_title(text))
        case UploadedFile():
            material = _from_file(source)
        case WebPage(url=url):
            material = _from_url(data, fetcher or Fetcher.from_env(data), url, now)
    if not material.text.strip():
        raise ValueError("內容是空的")

    item, item_dir = _write_item(data, material, now)
    item_path = item_dir.relative_to(data.root).as_posix()
    # 「找已有的工單 → 開資料夾 → 寫 job.md」整段鎖住：送出被連按兩下、或 UI 與 `ef new`
    # 同時投入同一份素材時，後到的那個等前一個寫完 job.md 再掃描，拿到同一張
    with _item_lock(data, item):
        existing = (
            None
            if again
            else _open_job_for(data, item_path, recipe.name, chosen_goal, conversation)
        )
        if existing is not None:
            return Intake(job=existing, created=False)
        if before_create is not None:
            before_create()
        budget = Budget(
            usd=budget_usd,
            tavily_credits=(
                recipe_credits(recipe) if budget_credits is None else budget_credits
            ),
        )
        job = _create_job(
            data,
            recipe,
            item,
            item_path,
            material,
            chosen_goal,
            now,
            budget,
            genre,
            conversation,
        )
    return Intake(job=job, created=True)


@contextmanager
def _item_lock(data: DataDir, item: Item) -> Iterator[None]:
    """以素材項目為單位的跨程序鎖（`flock`）；不同素材互不等待。

    鎖檔放在 `run/locks/`（執行期暫存），不放進素材項目資料夾：素材項目只增不改。
    程序結束（含當掉）時作業系統會放掉鎖，不會留下卡死的鎖。
    """
    path = data.run / "locks" / f"item-{item.id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _create_job(
    data: DataDir,
    recipe: Recipe,
    item: Item,
    item_path: str,
    material: _Material,
    goal: str,
    now: datetime,
    budget: Budget,
    genre: str | None,
    conversation: str | None,
) -> Job:
    assert item.content_hash is not None  # 讀得到的素材項目一定有
    job_id, job_dir = _new_job_dir(data, f"{now:%Y%m%d-%H%M%S}-{item.content_hash[:8]}")
    # 素材複製一份進工單：之後 inbox 清掉也不影響（v0.3 §2.1）
    write_once(job_dir / "input" / "content.md", material.text)
    job = Job(
        id=job_id,
        recipe=recipe.name,
        feed=FeedRef(connector=MANUAL_FEED, name=MANUAL_FEED, item=item_path),
        goal=goal,
        target_genre=genre,
        conversation=conversation,
        budget=budget,
        status="queued",
    )
    # 先寫開單事件、最後寫 job.md：執行器看到 job.md 時，「開單」一定已經在事件紀錄裡
    events.append(
        job_dir,
        Event(
            ts=now, kind="status", from_status=None, to_status="queued", message="開單"
        ),
    )
    write_job(job_dir / "job.md", JobDocument(job=job))
    return job
