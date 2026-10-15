"""工作進度列的資料（`ef_job` 事件，IA §4.2、NFR-OBS-02）。

一張工單此刻的樣子，全部從真的狀態來，不估算、不放百分比：
- 狀態、第幾段、這段從什麼時候開始、第幾次：`job.md`（執行器寫）。
- 在等什麼：工單在跑時以閘道的告示板為準（呼叫進行中只有閘道知道），告示板沒有才用 `job.md` 記的。
- 花了多少、用了幾個搜尋額度：帳本（`SpendTally` 只讀新增的部分）。

「這段跑了多久」不在這裡算：畫面拿 `stage.started_at` 自己數，主機不用每秒推一次。
格式寫在 PR 與 `chat/api.py`，給 #86 的畫面用。
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from evidence_forge.budget import micros_to_usd
from evidence_forge.documents import read_job
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import SpendTally
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir

# 還沒結束的狀態；其他的都算結束（工單狀態收成三種之後也一樣，#89）
UNFINISHED = frozenset({"queued", "running"})
# 重構配方開的單畫面上叫「寫作中」，其他叫「分析中」（IA §3 的狀態小標）
REWRITE_RECIPE = "rewrite"


def job_progress(
    data: DataDir,
    job_id: str,
    *,
    recipes: Mapping[str, Recipe],
    board: JobBoard | None,
    tally: SpendTally,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """一張工單的進度；讀不到 `job.md` 丟 `OSError`／`ValueError`。"""
    job = read_job(data.job_dir(job_id) / "job.md").job
    recipe = recipes.get(job.recipe)
    stage: dict[str, Any] | None = None
    if job.stage is not None:
        skill = next(
            (s.skill for s in (recipe.stages if recipe else ()) if s.id == job.stage.id),
            None,
        )
        stage = {
            "id": job.stage.id,
            "skill": skill,
            "index": job.stage.index,
            "total": job.stage.total,
            "attempt": job.stage.attempt,
            "tier": job.stage.tier,
            "started_at": job.stage.started_at.isoformat(),
        }
    waiting = job.waiting
    if board is not None and job.status == "running":
        waiting = board.waiting(job_id) or waiting
    finished = job.status not in UNFINISHED
    return {
        "type": "ef_job",
        "job_id": job_id,
        "conversation_id": job.conversation,
        "recipe": job.recipe,
        "kind": "rewrite" if job.recipe == REWRITE_RECIPE else "analysis",
        "status": job.status,
        "finished": finished,
        "outcome": job.result.outcome if job.result is not None else None,
        "reason": job.result.reason if job.result is not None else None,
        "stage": stage,
        "stages_total": len(recipe.stages) if recipe is not None else None,
        "waiting": (
            None
            if waiting is None
            else {
                "kind": waiting.kind,
                "tool": waiting.tool,
                "since": waiting.since.isoformat(),
            }
        ),
        "spent_usd": micros_to_usd(tally.job_micros(job_id)),
        "budget_usd": job.budget.usd,
        "credits_used": tally.job_credits(job_id),
        "budget_credits": job.budget.tavily_credits,
        "at": clock().isoformat(),
    }


def same_progress(a: Mapping[str, Any] | None, b: Mapping[str, Any]) -> bool:
    """除了 `at` 都一樣：沒變就不重推。"""
    if a is None:
        return False
    return {k: v for k, v in a.items() if k != "at"} == {
        k: v for k, v in b.items() if k != "at"
    }
