"""SEAM-10（整合）：Pi 跑的時候，執行器把「在等什麼」寫進 `job.md`（NFR-OBS-02）。

來源有兩個：閘道的告示板（等 GPU、等限流、等模型回應；呼叫進行中只有閘道知道）、
Pi 的 JSON 事件（正在跑哪個工具）。`job.md` 只有執行器寫。
"""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from runner_world import FAKE_PI_LIB, WELL_BEHAVED, World, runner_world
from tavily_stub import FAKE_KEY, Tavily, status

from evidence_forge.models import Recipe, Waiting


def _watch(
    world: World,
    job_id: str,
    until: Callable[[Waiting | None], bool],
    timeout_s: float = 15,
) -> Waiting | None:
    """一直讀 job.md，直到「在等什麼」符合條件；逾時就失敗。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        waiting = world.read(job_id).waiting
        if until(waiting):
            return waiting
        time.sleep(0.02)
    raise AssertionError(f"{timeout_s} 秒內 job.md 沒有出現預期的等待狀態")


@pytest.mark.promise("NFR-OBS-02")
def test_模型還沒回應時_job_md_寫著在等模型回應_回完就清掉(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> None:
    with runner_world(tmp_path, serve) as build:
        world = build(WELL_BEHAVED, progress_interval_s=0.05)
        world.backend.hold.clear()
        job_id = world.job()
        world.runner.start_available()
        try:
            waiting = _watch(
                world, job_id, lambda w: w is not None and w.kind == "model"
            )
        finally:
            world.backend.hold.set()
            world.runner.wait(timeout_s=30)
    assert waiting is not None
    job = world.read(job_id)
    assert job.status == "review" and job.waiting is None
    # 進度欄位：第幾段／共幾段、開始時間
    assert job.stage is not None and (job.stage.index, job.stage.total) == (1, 1)


@pytest.mark.promise("NFR-OBS-02", "NFR-PAR-03")
def test_被限流退避時_job_md_寫著在等限流(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> None:
    with runner_world(tmp_path, serve, fault="rate_limit", backoff_base_s=1.5) as build:
        world = build(WELL_BEHAVED, progress_interval_s=0.05)
        job_id = world.job()
        world.runner.start_available()
        try:
            _watch(world, job_id, lambda w: w is not None and w.kind == "rate_limit")
        finally:
            world.runner.wait(timeout_s=30)
    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete"


@pytest.mark.promise("NFR-OBS-02")
def test_Pi_正在跑工具時_job_md_寫著工具名稱(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> None:
    release = tmp_path / "release"
    script = (
        FAKE_PI_LIB
        + f"""
event(type="tool_execution_start", toolCallId="t1", toolName="fetch_url", args={{}})
while not os.path.exists({str(release)!r}):
    time.sleep(0.02)
event(type="tool_execution_end", toolCallId="t1", toolName="fetch_url", isError=False)
write("summary.json", {{"summary": "好"}})
"""
    )
    with runner_world(tmp_path, serve) as build:
        world = build(script, progress_interval_s=0.05)
        job_id = world.job()
        world.runner.start_available()
        try:
            waiting = _watch(
                world, job_id, lambda w: w is not None and w.kind == "tool"
            )
        finally:
            release.touch()
            world.runner.wait(timeout_s=30)
    assert waiting is not None and waiting.tool == "fetch_url"


@pytest.mark.promise("NFR-OBS-02", "NFR-PAR-03")
def test_搜尋被_Tavily_限流退避時_job_md_寫著在等限流_不是執行工具中(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> None:
    # Pi 的事件說 web_search 正在跑；閘道知道得更細：它在等 Tavily 的限流
    script = (
        FAKE_PI_LIB
        + """
event(type="tool_execution_start", toolCallId="t1", toolName="web_search", args={})
status, _ = call("/tools/web_search", {"query": "限流"})
assert status == 200, status
event(type="tool_execution_end", toolCallId="t1", toolName="web_search", isError=False)
write("summary.json", {"summary": "好"})
"""
    )
    recipe = Recipe.model_validate(
        {
            "name": "search",
            "description": "test",
            "stages": [
                {
                    "id": "research",
                    "skill": "echo",
                    "tier": "local",
                    "output": ["summary.json"],
                    "tavily_credits": 1,
                }
            ],
            "on_feedback": "research",
        }
    )
    tavily = Tavily(replies=[status(429, **{"retry-after": "1.5"})])
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "tavily").write_text(FAKE_KEY, encoding="utf-8")
    with (
        serve(tavily.app()) as tavily_url,
        runner_world(
            tmp_path, serve, tavily_url=tavily_url, rate_limit_retries=1
        ) as build,
    ):
        world = build(script, recipes={"search": recipe}, progress_interval_s=0.05)
        job_id = world.job(recipe="search")
        world.runner.start_available()
        try:
            _watch(world, job_id, lambda w: w is not None and w.kind == "rate_limit")
        finally:
            world.runner.wait(timeout_s=30)
    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    [entry] = [e for e in world.ledger(job_id) if e.kind == "search"]
    assert entry.queue_ms >= 1400, entry
