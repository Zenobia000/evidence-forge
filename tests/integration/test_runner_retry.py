"""SEAM-05（整合）：產出不合格時，同一級重試、到上限依配方升級、仍失敗就進待審。

基礎設施故障（後端連不上）不重試也不升級。用假 Pi 打真的閘道，後端是本機 stub。
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from runner_world import (
    BAD_OUTPUT,
    FAKE_PI_LIB,
    WELL_BEHAVED,
    World,
    fake_harness,
    runner_world,
)

from evidence_forge.jobkey import JobKey
from evidence_forge.models import Recipe, RecipeStage


def _recipe(
    *, max_attempts: int = 2, escalate_to: str | None = "strong", auto_fix: int = 0
) -> Recipe:
    return Recipe.model_validate(
        {
            "name": "retry",
            "description": "test",
            "stages": [
                RecipeStage.model_validate(
                    {
                        "id": "echo",
                        "skill": "echo",
                        "tier": "local",
                        "output": ["summary.json"],
                        "escalate_to": escalate_to,
                    }
                )
            ],
            "on_feedback": "echo",
            "max_attempts": max_attempts,
            "auto_fix": auto_fix,
        }
    )


@pytest.fixture
def world_for(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Callable[..., World]]:
    with runner_world(tmp_path, serve) as build:
        yield build


@pytest.mark.promise("NFR-RUN-03", "FR-MD-01")
def test_產出一直不合格_本級試到上限再升一級試一次_仍失敗就以失敗進待審(
    world_for: Callable[..., World],
) -> None:
    world = world_for(BAD_OUTPUT, recipes={"retry": _recipe()})
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert "第 3 次" in job.result.reason and "summary.json" in job.result.reason
    # 每次嘗試有自己的目錄；帳本：本級兩次，第三次是升上來的 strong
    attempts = sorted(
        p.name for p in (world.data.job_dir(job_id) / "stages" / "echo").iterdir()
    )
    assert attempts == ["1", "2", "3"]
    calls = [
        (entry.attempt, entry.tier, entry.escalated_from)
        for entry in world.ledger(job_id)
    ]
    assert calls == [(1, "local", None), (2, "local", None), (3, "strong", "local")]


@pytest.mark.promise("NFR-RUN-03")
def test_第一次不合格_第二次合格_工單完成_不升級(
    world_for: Callable[..., World],
) -> None:
    script = (
        FAKE_PI_LIB
        + """
ask()
write("summary.json", {"summary": "第二次才對"} if ATTEMPT == "2" else {"x": 1})
"""
    )
    world = world_for(script, recipes={"retry": _recipe()})
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete"
    assert [entry.tier for entry in world.ledger(job_id)] == ["local", "local"]


@pytest.mark.promise("NFR-RUN-03")
def test_沒寫升級的配方_試到上限就失敗(world_for: Callable[..., World]) -> None:
    world = world_for(BAD_OUTPUT, recipes={"retry": _recipe(escalate_to=None)})
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert [entry.attempt for entry in world.ledger(job_id)] == [1, 2]


def test_合格的產出一次就過_第一次的編號是_1(world_for: Callable[..., World]) -> None:
    world = world_for(WELL_BEHAVED, recipes={"retry": _recipe()})
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    assert [entry.attempt for entry in world.ledger(job_id)] == [1]


@pytest.fixture
def down_world_for(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Callable[..., World]]:
    with runner_world(tmp_path, serve, fault="backend_down") as build:
        yield build


# 跟真的 Pi 一樣：模型呼叫失敗只記在事件裡，結束碼照樣是 0
GIVES_UP_QUIETLY = (
    FAKE_PI_LIB
    + """
status, body = ask()
if status != 200:
    message = json.loads(body)["error"]["message"]
    event(type="message_end", message={"role": "assistant", "content": [],
        "stopReason": "error", "errorMessage": message})
event(type="agent_end")
"""
)


@pytest.mark.promise("NFR-RUN-05", "FR-MD-01")
def test_後端連不上_不重試也不升級_以失敗進待審_原因是_Ollama_沒回應(
    down_world_for: Callable[..., World],
) -> None:
    world = down_world_for(GIVES_UP_QUIETLY, recipes={"retry": _recipe()})
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert job.result.reason.startswith("Ollama 沒回應")
    assert [entry.tier for entry in world.ledger(job_id)] == ["local"]
    assert world.backend_calls == []


def test_Pi_結束碼_0_但模型呼叫失敗_原因寫出_Pi_回報的錯誤(
    world_for: Callable[..., World],
) -> None:
    script = (
        FAKE_PI_LIB
        + """
event(type="message_end", message={"role": "assistant", "content": [],
    "stopReason": "error", "errorMessage": "重播模式：沒有錄過這個請求"})
event(type="agent_end")
"""
    )
    world = world_for(
        script, recipes={"retry": _recipe(max_attempts=1, escalate_to=None)}
    )
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "沒有錄過這個請求" in job.result.reason


# 後端過載時常見的樣子：狀態碼 200，串流裡送 error 事件。這份樣本只有這一個毛病
# （有開頭、有結尾），所以斷言靠的是 error 事件本身，不是「沒送到結尾」（L-008）
OVERLOADED_200 = (
    b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
    b'{"input_tokens":1,"output_tokens":0}}}\n\n'
    b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
    b'"message":"Overloaded"}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)

# 跟真的 Pi 一樣：串流裡收到 error 事件，這一回合以錯誤結束，結束碼照樣是 0
GIVES_UP_ON_STREAM_ERROR = (
    FAKE_PI_LIB
    + """
ask()
event(type="message_end", message={"role": "assistant", "content": [],
    "stopReason": "error", "errorMessage": "Overloaded"})
event(type="agent_end")
"""
)


@pytest.mark.promise("NFR-RUN-05", "FR-MD-01")
def test_後端回_200_但串流裡是_overloaded_error_算後端故障_不重試也不升級(
    world_for: Callable[..., World],
) -> None:
    world = world_for(GIVES_UP_ON_STREAM_ERROR, recipes={"retry": _recipe()})
    world.backend.reply = OVERLOADED_200
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert job.result.reason.startswith("Ollama 沒回應"), job.result.reason
    assert "overloaded_error" in job.result.reason
    # 只有第 1 次的那一筆：沒有同級重試，也沒有升到 strong
    assert [
        (entry.attempt, entry.tier, entry.status) for entry in world.ledger(job_id)
    ] == [(1, "local", "error")]
    assert len(world.backend_calls) == 1


@pytest.mark.promise("NFR-RUN-05")
def test_碰過一次後端故障但_Pi_自己重試成功_產出合格就算這段過了(
    world_for: Callable[..., World],
) -> None:
    world = world_for(WELL_BEHAVED, recipes={"retry": _recipe()})
    job_id = world.job(recipe="retry")
    # 閘道在這一段這一次記過一次故障（例如一次 503），之後 Pi 重試成功
    world.board.report_outage(
        JobKey(job_id, "echo", 1), "ollama", "模型後端回 HTTP 503"
    )

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result


@pytest.mark.promise("NFR-RUN-03")
def test_skill_少了_schema_json_產出沒辦法驗_以失敗進待審_不重試(
    world_for: Callable[..., World], tmp_path: Path
) -> None:
    # 配方直接交給執行器（不經 load_recipes），模擬跑到一半 schema.json 被刪掉
    root = fake_harness(tmp_path / "harness", {"echo": {"type": "object"}})
    (root / "skills" / "echo" / "schema.json").unlink()
    world = world_for(WELL_BEHAVED, recipes={"retry": _recipe()}, harness_root=root)
    job_id = world.job(recipe="retry")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed", job.result
    assert "沒辦法驗" in job.result.reason and "schema.json" in job.result.reason
    # skill 自己缺東西，重跑模型修不好：只有第 1 次
    assert [entry.attempt for entry in world.ledger(job_id)] == [1]
