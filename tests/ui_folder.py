"""測試共用：用手寫的假資料夾（不是執行器產的）當 UI 的輸入。

所有檔案都是為測試現編的內容，不含任何真實文章（ADR-0014）。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evidence_forge.documents import JobDocument, write_job
from evidence_forge.models import Job
from evidence_forge.paths import DataDir

TAIPEI = timezone(timedelta(hours=8))
# 假的「現在」：所有「跑了多久」「排隊多久」都相對於它，測試才不會看時間。
NOW = datetime(2026, 10, 11, 14, 30, 0, tzinfo=TAIPEI)


class Folder:
    """一個暫時的資料目錄，附幾個「蓋出一張工單」的小工具。"""

    now = NOW

    def __init__(self, root: Path) -> None:
        self.data = DataDir(root)
        self.data.ensure()

    @staticmethod
    def at(minutes_ago: float = 0, seconds_ago: float = 0) -> datetime:
        """相對於 NOW 往前推的時間點。"""
        return NOW - timedelta(minutes=minutes_ago, seconds=seconds_ago)

    def job(
        self,
        job_id: str,
        *,
        status: str = "queued",
        title: str | None = None,
        goal: str = "整理成給工程師看的解說文",
        budget_usd: float = 2.0,
        target_genre: str | None = None,
        stage: dict[str, Any] | None = None,
        waiting: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        draft_version: int = 0,
        feed_name: str = "manual",
        notes: str = "",
    ) -> Path:
        """寫一張工單的 `job.md`（以及有給 title 時的素材項目）。回傳工單資料夾。"""
        item_dir = f"inbox/{feed_name}/{job_id}"
        data: dict[str, Any] = {
            "id": job_id,
            "recipe": "restructure",
            "feed": {"connector": feed_name, "name": feed_name, "item": item_dir},
            "goal": goal,
            "target_genre": target_genre,
            "budget": {"usd": budget_usd},
            "status": status,
            "stage": stage,
            "waiting": waiting,
            "result": result,
            "versions": {"draft": draft_version},
        }
        job = Job.model_validate(data)
        job_dir = self.data.job_dir(job_id)
        write_job(job_dir / "job.md", JobDocument(job=job, notes=notes))
        if title is not None:
            item_path = self.data.root / item_dir
            item_path.mkdir(parents=True, exist_ok=True)
            (item_path / "item.json").write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "feed": feed_name,
                        "url": f"https://example.test/{job_id}",
                        "title": title,
                        "fetched_at": self.at(30).isoformat(),
                        "content_hash": "a" * 64,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return job_dir

    def stage(
        self, index: int, total: int, stage_id: str, seconds_ago: float
    ) -> dict[str, Any]:
        return {
            "id": stage_id,
            "index": index,
            "total": total,
            "attempt": 1,
            "started_at": self.at(seconds_ago=seconds_ago).isoformat(),
        }

    def events(self, job_id: str, rows: list[dict[str, Any]]) -> None:
        path = self.data.job_dir(job_id) / "events.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def status_event(
        self, job_id: str, to_status: str, minutes_ago: float, frm: str | None = None
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": self.at(minutes_ago).isoformat(),
            "kind": "status",
            "to_status": to_status,
        }
        if frm:
            row["from_status"] = frm
        return row

    def ledger(self, job_id: str, rows: list[dict[str, Any]]) -> None:
        path = self.data.job_dir(job_id) / "ledger.jsonl"
        full = [
            {
                "ts": self.at(10).isoformat(),
                "job_id": job_id,
                "stage": "digest",
                "attempt": 1,
                "kind": "llm",
                "tier": "local",
                "model": "qwen3.5:9b",
                "latency_ms": 1000,
                "status": "ok",
                "request_hash": "b" * 64,
                **row,
            }
            for row in rows
        ]
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in full),
            encoding="utf-8",
        )

    def draft(self, job_id: str, version: int, text: str) -> None:
        directory = self.data.job_dir(job_id) / "drafts"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"draft.v{version}.md").write_text(text, encoding="utf-8")

    def json_file(self, job_id: str, relative: str, payload: object) -> None:
        path = self.data.job_dir(job_id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def checks(
        self, job_id: str, entries: list[dict[str, Any]], version: int = 1
    ) -> None:
        """寫一份合格的 `checks/checks.vN.json`（欄位照 `checks.report.ChecksReport`）。"""
        self.json_file(
            job_id,
            f"checks/checks.v{version}.json",
            {
                "stage": "verify",
                "attempt": 1,
                "files": ["drafts/draft.v1.md"],
                "created_at": self.at(2).isoformat(),
                "entries": entries,
            },
        )
