"""SEAM-06（整合）：硬檢查由執行器直接呼叫 `checks/`，不交給模型（FR-VF-01）。

- 整理段後跑 C1、C5：沒過算這段這次失敗，走重試與升級，用完是 `failed`。
- 二創段後跑 C2～C5：沒過帶著結果回二創段修，到 `auto_fix` 上限是 `incomplete`。
- 結果寫 `checks/checks.vN.json`；agent 自己寫的 `checks.json` 不採用。
- 檢查本身跑不動（skill 的 schema 壞掉）時，工單帶著原因進待審，不重試，其他工單照常跑。
"""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from runner_world import FAKE_PI_LIB, World, fake_harness, runner_world

from evidence_forge.checks import ChecksReport
from evidence_forge.models import Recipe

DIGEST_SCHEMA = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {"type": "object", "required": ["claim", "quote"]},
        }
    },
}
COMPOSE_SCHEMA = {"type": "object", "required": ["facts"]}
ECHO_SCHEMA = {"type": "object", "required": ["summary"]}

# 原文（tests/fixtures/articles/tracer.md）裡真的有的一句
REAL_QUOTE = "以前每天大約有兩成的麵包賣不掉"
FAKE_QUOTE = "老闆說麵包每天都全部賣光"
GOOD_FACT = "改成預約之後，賣剩的麵包少了一半以上[^S1]。"
BAD_FACT = "改成預約之後，賣剩的麵包少了一半以上[^S9]。"

# 照段與第幾次決定交什麼：DIGEST_QUOTE、COMPOSE_FACT 由各條測試在前面定義
PI_BODY = """
ask()
if STAGE == "echo":
    write("summary.json", {"summary": "好"})
elif STAGE == "digest":
    write("claims.json", {"claims": [{"claim": "麵包少賣剩", "quote": DIGEST_QUOTE(ATTEMPT)}]})
else:
    fact = COMPOSE_FACT(ATTEMPT)
    write("draft.md", "# 預約制的麵包店\\n\\n" + fact + "\\n\\n[^S1]: 原文\\n")
    write("facts.json", {"facts": [{"fact": fact}]})
event(type="agent_end")
"""


def _pi(digest_quote: str, compose_fact: str, extra: str = "") -> str:
    return (
        FAKE_PI_LIB
        + f"DIGEST_QUOTE = lambda attempt: {digest_quote}\n"
        + f"COMPOSE_FACT = lambda attempt: {compose_fact}\n"
        + extra
        + PI_BODY
    )


def _recipe(*, max_attempts: int = 2, auto_fix: int = 1) -> Recipe:
    return Recipe.model_validate(
        {
            "name": "checked",
            "description": "test",
            "stages": [
                {
                    "id": "digest",
                    "skill": "digest",
                    "tier": "local",
                    "output": ["claims.json"],
                },
                {
                    "id": "compose",
                    "skill": "compose",
                    "tier": "local",
                    "output": ["draft.md", "facts.json"],
                },
            ],
            "on_feedback": "compose",
            "max_attempts": max_attempts,
            "auto_fix": auto_fix,
        }
    )


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return fake_harness(
        tmp_path / "harness",
        {"digest": DIGEST_SCHEMA, "compose": COMPOSE_SCHEMA, "echo": ECHO_SCHEMA},
    )


@pytest.fixture
def world_for(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    harness_root: Path,
) -> Iterator[Callable[..., World]]:
    with runner_world(tmp_path, serve) as build:

        def checked(script: str, **runner: Any) -> World:
            return build(
                script,
                recipes={"checked": _recipe(**runner)},
                harness_root=harness_root,
            )

        yield checked


def _reports(world: World, job_id: str) -> list[ChecksReport]:
    directory = world.data.job_dir(job_id) / "checks"
    paths = sorted(directory.glob("checks.v*.json"), key=lambda p: int(p.name[8:-5]))
    return [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8")) for p in paths
    ]


@pytest.mark.promise("FR-VF-01", "FR-DG-01")
def test_整理段的引句是捏造的_重試到上限仍沒過_工單以失敗進待審(
    world_for: Callable[..., World],
) -> None:
    world = world_for(_pi(repr(FAKE_QUOTE), repr(GOOD_FACT)))
    job_id = world.job(recipe="checked")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "C1" in job.result.reason and "digest" in job.result.reason
    reports = _reports(world, job_id)
    assert [(r.stage, r.attempt, r.failed_checks) for r in reports] == [
        ("digest", 1, ("C1",)),
        ("digest", 2, ("C1",)),
    ]
    # 二創段從沒開始
    assert {entry.stage for entry in world.ledger(job_id)} == {"digest"}


@pytest.mark.promise("FR-VF-01")
def test_整理段第一次捏造_第二次照原文_通過後接著跑二創(
    world_for: Callable[..., World],
) -> None:
    quote = f"{FAKE_QUOTE!r} if attempt == '1' else {REAL_QUOTE!r}"
    world = world_for(_pi(quote, repr(GOOD_FACT)))
    job_id = world.job(recipe="checked")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert [(r.stage, r.attempt, r.passed) for r in _reports(world, job_id)] == [
        ("digest", 1, False),
        ("digest", 2, True),
        ("compose", 1, True),
    ]


@pytest.mark.promise("FR-VF-01", "FR-CP-01")
def test_二創的引用對不到來源_自動修到上限仍沒過_以未完成進待審並列出沒過的檢查(
    world_for: Callable[..., World],
) -> None:
    world = world_for(_pi(repr(REAL_QUOTE), repr(BAD_FACT)), auto_fix=1)
    job_id = world.job(recipe="checked")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "incomplete"
    assert "C2" in job.result.reason
    compose = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert [(r.attempt, r.failed_checks) for r in compose] == [
        (1, ("C2",)),
        (2, ("C2",)),
    ]


@pytest.mark.promise("FR-VF-01")
def test_自動修的那一次_任務說明帶著上一次沒過的檢查(
    world_for: Callable[..., World],
) -> None:
    fact = f"{BAD_FACT!r} if attempt == '1' else {GOOD_FACT!r}"
    world = world_for(_pi(repr(REAL_QUOTE), fact), auto_fix=1)
    job_id = world.job(recipe="checked")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    prompts = [call["messages"][0]["content"] for call in world.backend_calls]
    # digest、compose 第 1 次、compose 第 2 次（修）
    assert len(prompts) == 3
    assert "C2" not in prompts[1]
    assert "C2" in prompts[2] and "S9" in prompts[2]


@pytest.mark.promise("FR-VF-01", "NFR-SEC-02")
def test_agent_自己寫一份全部通過的_checks_json_執行器不採用(
    world_for: Callable[..., World],
) -> None:
    forged = """
_write = write
def write(name, content):
    if name == "draft.md":
        _write("checks.json", {"entries": [
            {"check": c, "status": "pass", "reason": "都過了"}
            for c in ("C1", "C2", "C3", "C4", "C5")]})
    return _write(name, content)
"""
    world = world_for(_pi(repr(REAL_QUOTE), repr(BAD_FACT), forged), auto_fix=0)
    job_id = world.job(recipe="checked")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "incomplete"
    assert (world.data.job_dir(job_id) / "stages/compose/1/checks.json").is_file()
    compose = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert compose[-1].failed_checks == ("C2",)


@pytest.mark.promise("FR-VF-01")
def test_skill_的_schema_是壞掉的_JSON_工單帶著原因進待審_其他工單照常跑(
    world_for: Callable[..., World], harness_root: Path
) -> None:
    (harness_root / "skills" / "digest" / "schema.json").write_text(
        "{ not json", encoding="utf-8"
    )
    world = world_for(_pi(repr(REAL_QUOTE), repr(GOOD_FACT)))
    broken = world.job(recipe="checked")
    healthy_recipe = Recipe.model_validate(
        {
            "name": "echo",
            "description": "test",
            "stages": [
                {
                    "id": "echo",
                    "skill": "echo",
                    "tier": "local",
                    "output": ["summary.json"],
                }
            ],
            "on_feedback": "echo",
        }
    )
    world.runner.recipes = {**world.runner.recipes, "echo": healthy_recipe}
    healthy = world.job(recipe="echo", text="另一篇完全不同的文章，內容是測試用的。")

    assert world.runner.run_once() == broken
    assert world.runner.run_once() == healthy

    job = world.read(broken)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert "schema" in job.result.reason
    # 只跑了一次，不重試
    assert [entry.attempt for entry in world.ledger(broken)] == [1]
    other = world.read(healthy)
    assert other.result is not None and other.result.outcome == "complete"


def test_檢查結果的檔案是合法的_checks_report(world_for: Callable[..., World]) -> None:
    world = world_for(_pi(repr(REAL_QUOTE), repr(GOOD_FACT)))
    job_id = world.job(recipe="checked")
    world.runner.run_once()

    path = world.data.job_dir(job_id) / "checks" / "checks.v1.json"
    report = ChecksReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert report.files == ("stages/digest/1/claims.json",)
