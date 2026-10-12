"""對話的工作台：替對話開分析工單、讀工單的狀態與結果（#80 spike）。

閘道的 `chat_tools` 只管鑰匙與範圍，開單與讀結果在這裡：這裡知道配方與資料目錄。
開單照舊走 `runner.intake.open_job`：同一份素材、同樣的目標已有工單就拿回那一張，不重複分析（US-21）。
開出來的單是「排隊中」，由同一個程序裡的執行器照配方跑（ADR-0019 §3.3）。
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from evidence_forge.documents import read_job
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import (
    PastedText,
    Source,
    SourceUnreadable,
    WebPage,
    open_job,
)

# 讀結果時每個檔最多回多少字：報告整份給對話的模型看，太長就截掉並說明
MAX_REPORT_CHARS = 20_000


class Desk:
    def __init__(
        self,
        data: DataDir,
        recipes: dict[str, Recipe],
        recipe_name: str,
        *,
        clock: Callable[[], datetime],
        board: JobBoard | None = None,
        fetcher: Fetcher | None = None,
        budget_usd: float = 0.5,
    ) -> None:
        if recipe_name not in recipes:
            raise ValueError(
                f"沒有配方 {recipe_name}（有：{'、'.join(sorted(recipes))}）"
            )
        self._data = data
        self._recipes = recipes
        self._recipe = recipes[recipe_name]
        self._clock = clock
        self._board = board
        self._fetcher = fetcher
        self._budget_usd = budget_usd

    def open_analysis(
        self, *, url: str | None, text: str | None, goal: str | None
    ) -> dict[str, Any]:
        source: Source = WebPage(url) if url is not None else PastedText(text or "")
        try:
            intake = open_job(
                self._data,
                self._recipe,
                source,
                goal=goal,
                now=self._clock(),
                fetcher=self._fetcher,
                budget_usd=self._budget_usd,
            )
        except SourceUnreadable as exc:
            # 讀不到就不開工，原因交回對話講清楚（FR-IN-01）
            return {"status": "unavailable", "reason": exc.reason}
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
        return {
            "job_id": intake.job.id,
            "created": intake.created,
            "status": intake.job.status,
            "recipe": intake.job.recipe,
            "stages": len(self._recipe.stages),
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = read_job(self._data.job_dir(job_id) / "job.md").job
        found: dict[str, Any] = {"job_id": job_id, "status": job.status}
        if job.result is not None:
            found["outcome"] = job.result.outcome
            found["reason"] = job.result.reason
        if job.stage is not None:
            found["stage"] = {
                "id": job.stage.id,
                "index": job.stage.index,
                "total": job.stage.total,
                "attempt": job.stage.attempt,
                "started_at": job.stage.started_at.isoformat(),
            }
        waiting = job.waiting
        if self._board is not None and job.status == "running":
            # 執行器每段才寫一次 job.md；在等什麼以閘道的告示板為準，比較即時
            waiting = self._board.waiting(job_id) or waiting
        if waiting is not None:
            found["waiting"] = {"kind": waiting.kind, "tool": waiting.tool}
        return found

    def report(self, job_id: str) -> dict[str, Any]:
        """工單的結果：配方最後一段、編號最大那一次的產出檔。還沒跑完就只回狀態。"""
        found = self.status(job_id)
        if found["status"] in ("queued", "running"):
            found["note"] = "還沒跑完；跑完會自動通知這個對話"
            return found
        job = read_job(self._data.job_dir(job_id) / "job.md").job
        recipe = self._recipes.get(job.recipe)
        files: dict[str, str] = {}
        if recipe is not None:
            last = recipe.stages[-1]
            attempt_dir = _latest_attempt(self._data.job_dir(job_id) / "stages" / last.id)
            if attempt_dir is not None:
                for name in last.output:
                    path = attempt_dir / name
                    if path.is_file():
                        text = path.read_text(encoding="utf-8", errors="replace")
                        if len(text) > MAX_REPORT_CHARS:
                            text = text[:MAX_REPORT_CHARS] + "\n…（後面截掉了）"
                        files[path.relative_to(self._data.job_dir(job_id)).as_posix()] = text
        found["files"] = files
        return found


def _latest_attempt(stage_root: Path) -> Path | None:
    if not stage_root.is_dir():
        return None
    attempts = [p for p in stage_root.iterdir() if p.is_dir() and p.name.isdigit()]
    return max(attempts, key=lambda p: int(p.name)) if attempts else None
