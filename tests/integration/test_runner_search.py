"""SEAM-05（整合）：補料段的搜尋碰到 credits 用完或 Tavily 故障時，執行器怎麼收尾。

- credits 用完（NFR-COST-01、`research.feature`〈搜尋額度用完就停〉）：這一段用已經找到的資料收尾，
  後面的段照跑，單子最後以「未完成：預算用完」進待審。
- Tavily 故障（NFR-RUN-05）：這一段交不出合格產出才標失敗，不重試、不升級；
  產出合格的話以產出為準，故障不會害一個收得了尾的段失敗。

假 Pi 直接打閘道的 `/tools/web_search`（跟 ef-tools 送的一樣），Tavily 是本機 stub。
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from runner_world import FAKE_PI_LIB, World, runner_world
from tavily_stub import FAKE_KEY, Tavily, status

from evidence_forge.models import Recipe, RecipeStage

Serve = Callable[[Any], AbstractContextManager[str]]


def _stage(stage_id: str, credits: int = 0) -> RecipeStage:
    return RecipeStage.model_validate(
        {
            "id": stage_id,
            "skill": "echo",
            "tier": "local",
            "output": ["summary.json"],
            "escalate_to": "strong",
            "tavily_credits": credits,
        }
    )


def _recipe(*stages: RecipeStage) -> Recipe:
    return Recipe.model_validate(
        {
            "name": "search",
            "description": "test",
            "stages": list(stages),
            "on_feedback": stages[-1].id,
            "max_attempts": 2,
            "auto_fix": 0,
        }
    )


@pytest.fixture
def tavily(serve: Serve) -> Iterator[Tavily]:
    stub = Tavily()
    with serve(stub.app()) as url:
        stub.url = url
        yield stub


@pytest.fixture
def world_for(
    tmp_path: Path, serve: Serve, tavily: Tavily
) -> Iterator[Callable[..., World]]:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "tavily").write_text(FAKE_KEY, encoding="utf-8")
    with runner_world(tmp_path, serve, tavily_url=tavily.url) as build:
        yield build


# 補料段搜尋兩次（第二次可能被 credits 擋下，agent 照樣往下走），然後用找到的資料寫產出
SEARCHES_TWICE = (
    FAKE_PI_LIB
    + """
ask()
if STAGE == "research":
    for query in ("第一次", "第二次"):
        call("/tools/web_search", {"query": query})
write("summary.json", {"summary": "用已經找到的資料寫的摘要。"})
event(type="agent_end")
"""
)


@pytest.mark.promise("NFR-COST-01", "FR-RS-01")
def test_credits_用完_補料段照樣收尾_後面的段照跑_單子以未完成進待審(
    world_for: Callable[..., World],
) -> None:
    recipe = _recipe(_stage("research", credits=1), _stage("compose"))
    world = world_for(SEARCHES_TWICE, recipes={"search": recipe})
    job_id = world.job(recipe="search")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "incomplete"
    assert job.result.reason.startswith("預算用完"), job.result.reason
    assert "research" in job.result.reason
    # 補料段一次就過（不重試、不升級），二創段照跑
    calls = [
        (entry.stage, entry.attempt, entry.kind, entry.status)
        for entry in world.ledger(job_id)
    ]
    assert calls == [
        ("research", 1, "llm", "ok"),
        ("research", 1, "search", "ok"),
        ("research", 1, "search", "budget_exceeded"),
        ("compose", 1, "llm", "ok"),
    ]


def test_credits_夠用_單子完成(world_for: Callable[..., World]) -> None:
    recipe = _recipe(_stage("research", credits=2), _stage("compose"))
    world = world_for(SEARCHES_TWICE, recipes={"search": recipe})
    job_id = world.job(recipe="search")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result


def _search_fails_then_writes(summary: object) -> str:
    """搜尋碰到 Tavily 故障，接著一次成功的模型呼叫，再寫產出。

    模型呼叫成功不代表 Tavily 好了：告示板上的 Tavily 故障不能被它洗掉。
    """
    return (
        FAKE_PI_LIB
        + f"""
status, body = call("/tools/web_search", {{"query": "故障"}})
assert status == 502, (status, body)
ask()
write("summary.json", {summary!r})
event(type="agent_end")
"""
    )


@pytest.mark.promise("NFR-RUN-05", "FR-MD-01")
def test_Tavily_故障而這一段交不出合格產出_以失敗進待審_不重試也不升級(
    world_for: Callable[..., World], tavily: Tavily
) -> None:
    tavily.replies = [status(500)] * 10
    world = world_for(
        _search_fails_then_writes({"summery": "欄位名打錯"}),
        recipes={"search": _recipe(_stage("research", credits=5))},
    )
    job_id = world.job(recipe="search")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert job.result.reason.startswith("Tavily 沒回應"), job.result.reason
    assert "HTTP 500" in job.result.reason
    # 只有第 1 次：沒有同級重試，也沒有升到 strong（付費模型）
    assert {entry.attempt for entry in world.ledger(job_id)} == {1}
    assert len(tavily.received) == 1


@pytest.mark.promise("NFR-RUN-05")
def test_Tavily_故障但這一段用手上的資料交出合格產出_算這段過了(
    world_for: Callable[..., World], tavily: Tavily
) -> None:
    tavily.replies = [status(500)] * 10
    world = world_for(
        _search_fails_then_writes({"summary": "沒搜到，用素材本身寫的摘要。"}),
        recipes={"search": _recipe(_stage("research", credits=5))},
    )
    job_id = world.job(recipe="search")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
