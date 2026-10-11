"""手動投入：把一段貼上的文字寫成素材項目，開一張排隊中的工單（SEAM-09 最薄的一層）。

S0.3 只做貼文字。網址與上傳檔案、同素材同目標回傳既有工單、沒填目標用配方預設目標，
都在 S0.5（FR-IN-01）。工單的 `job.md` 與 `events.jsonl` 只有執行器寫，所以開單放在 runner。
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from evidence_forge.documents import JobDocument, write_job
from evidence_forge.models import Budget, Event, FeedRef, Item, Job, Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner import events
from evidence_forge.write_once import write_once

MANUAL_FEED = "manual"


def _title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return "（沒有標題）"


def _write_item(data: DataDir, text: str, now: datetime) -> tuple[Item, Path]:
    """同一個內容 hash 只建一份素材項目；已經有了就沿用。"""
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    item = Item(
        id=content_hash[:16],
        feed=MANUAL_FEED,
        title=_title(text),
        fetched_at=now,
        content_hash=content_hash,
    )
    item_dir = data.inbox / MANUAL_FEED / item.id
    if not (item_dir / "item.json").is_file():
        write_once(item_dir / "content.md", text)
        write_once(
            item_dir / "item.json",
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
    return item, item_dir


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


def open_job(
    data: DataDir,
    recipe: Recipe,
    *,
    text: str,
    goal: str,
    now: datetime,
    budget_usd: float = 0.5,
) -> Job:
    """寫素材項目、開一張排隊中的工單，回傳工單。"""
    if not text.strip():
        raise ValueError("內容是空的")
    if not goal.strip():
        raise ValueError("要寫目標（S0.3 還沒有配方的預設目標）")
    item, item_dir = _write_item(data, text, now)
    job_id, job_dir = _new_job_dir(
        data, f"{now:%Y%m%d-%H%M%S}-{item.content_hash[:8]}"
    )
    # 素材複製一份進工單：之後 inbox 清掉也不影響（v0.3 §2.1）
    write_once(job_dir / "input" / "content.md", text)
    job = Job(
        id=job_id,
        recipe=recipe.name,
        feed=FeedRef(
            connector=MANUAL_FEED,
            name=MANUAL_FEED,
            item=item_dir.relative_to(data.root).as_posix(),
        ),
        goal=goal.strip(),
        budget=Budget(usd=budget_usd),
        status="queued",
    )
    write_job(job_dir / "job.md", JobDocument(job=job))
    events.append(
        job_dir,
        Event(ts=now, kind="status", from_status=None, to_status="queued", message="開單"),
    )
    return job
