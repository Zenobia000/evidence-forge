"""SEAM-01（整合）：執行器 ↔ Pi 程序 ↔ 閘道，用假的 Pi 跑完整條打通路徑。

假 Pi、假後端與閘道的組法在 `tests/runner_world.py`。
"""

import json
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from runner_world import (
    ARTICLE,
    BAD_OUTPUT,
    FAKE_PI_HEAD,
    FAKE_PI_LIB,
    NOW,
    WELL_BEHAVED,
    World,
    runner_world,
)

from evidence_forge.documents import read_job
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.gateway.ledger import append_intake
from evidence_forge.harness import default_harness
from evidence_forge.jobkey import JobKey
from evidence_forge.models import Recipe
from evidence_forge.ledger_models import IntakeEntry
from evidence_forge.models import Event
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job

# 原文裡的提示注入得逞時，agent 會試著寫審稿決定與狀態檔
INJECTED = (
    FAKE_PI_HEAD
    + """
results = {}
for path in ["review/decision.v1.json", "job.md", "events.jsonl", "ledger.jsonl"]:
    status, _ = call("/tools/write_file", {"path": path, "content": '{"action": "approve"}'})
    results[path] = status
write("summary.json", {"summary": "照常完成"})
event(type="agent_end", rejected=results)
"""
)

CRASHES = (
    FAKE_PI_HEAD
    + """
print("boom", file=sys.stderr)
sys.exit(3)
"""
)

# 打 strong（雲端）；被拒就把錯誤印到 stderr、非零結束，跟 Pi 收到錯誤時一樣
CLOUD_CALL = (
    FAKE_PI_LIB
    + """
status, body = call("/v1/messages?beta=true", {"model": "strong", "stream": True,
    "messages": [{"role": "user", "content": sys.argv[-1]}]})
if status != 200:
    print(body.decode(), file=sys.stderr)
    sys.exit(1)
event(type="agent_end")
"""
)


@pytest.fixture
def world_for(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Callable[..., World]]:
    with runner_world(tmp_path, serve) as build:
        yield build


def _events(data: DataDir, job_id: str) -> list[Event]:
    lines = (data.job_dir(job_id) / "events.jsonl").read_text(encoding="utf-8")
    return [Event.model_validate_json(line) for line in lines.splitlines()]


@pytest.mark.promise("FR-RC-01", "NFR-KEEP-02")
def test_開單後跑完唯一一段_產出合格_工單結束且結果完成(
    world_for: Callable[..., World],
) -> None:
    world = world_for(WELL_BEHAVED)
    job_id = world.job()

    assert world.runner.run_once() == job_id

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "done"
    assert job.result is not None and job.result.outcome == "complete"
    summary = world.data.job_dir(job_id) / "stages" / "echo" / "1" / "summary.json"
    assert json.loads(summary.read_text(encoding="utf-8")) == {
        "summary": "這是一段測試用的摘要。"
    }
    assert len(world.backend_calls) == 1


@pytest.mark.promise("NFR-KEEP-02", "NFR-RUN-02")
def test_狀態轉換依序寫進事件紀錄(world_for: Callable[..., World]) -> None:
    world = world_for(WELL_BEHAVED)
    job_id = world.job()
    world.runner.run_once()

    transitions = [
        (event.from_status, event.to_status)
        for event in _events(world.data, job_id)
        if event.kind == "status"
    ]
    assert transitions == [
        (None, "queued"),
        ("queued", "running"),
        ("running", "done"),
    ]


def test_沒有排隊中的工單就什麼都不做(world_for: Callable[..., World]) -> None:
    assert world_for(WELL_BEHAVED).runner.run_once() is None


@pytest.mark.promise("NFR-SEC-02", "NFR-KEEP-02")
def test_agent_試著寫審稿決定與狀態檔_全部被拒_工單照常結束(
    world_for: Callable[..., World],
) -> None:
    world = world_for(INJECTED)
    job_id = world.job()
    world.runner.run_once()

    job_dir = world.data.job_dir(job_id)
    assert not (job_dir / "review").exists()
    job = read_job(job_dir / "job.md").job
    assert job.status == "done"
    for event in _events(world.data, job_id):
        assert "approve" not in event.message
    # Pi 自己回報的被拒結果：四個都是 403
    log = next((world.data.run / "pi-logs").glob(f"{job_id}-*.jsonl"))
    last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert set(last["rejected"].values()) == {403}


@pytest.mark.promise("NFR-RUN-03")
def test_產出不合_schema_工單以失敗結束並寫明原因(
    world_for: Callable[..., World],
) -> None:
    world = world_for(BAD_OUTPUT)
    job_id = world.job()
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "done"
    assert job.result is not None and job.result.outcome == "failed"
    assert "summary.json" in job.result.reason


def test_Pi_非零結束_工單以失敗結束並附上錯誤輸出(
    world_for: Callable[..., World],
) -> None:
    world = world_for(CRASHES)
    job_id = world.job()
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.result is not None and job.result.outcome == "failed"
    assert "3" in job.result.reason and "boom" in job.result.reason


@pytest.mark.promise("NFR-SEC-02")
def test_段結束後鑰匙失效_拿舊鑰匙讀寫都被拒(world_for: Callable[..., World]) -> None:
    world = world_for(WELL_BEHAVED)
    job_id = world.job()
    world.runner.run_once()

    response = httpx.post(
        f"{world.gateway_url}/tools/read_file",
        json={"path": "input/content.md"},
        headers={"x-api-key": str(JobKey(job_id, "echo", 1))},
        timeout=10,
    )
    assert response.status_code == 403


@pytest.mark.promise("NFR-COST-06")
def test_用前一次存下的帳本與_blobs_重播整條流程_後端一個請求都沒收到(
    world_for: Callable[..., World],
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
) -> None:
    # 第一次：live 模式打本機 stub，閘道把請求與回應存進 blobs/、帳本記下 hash
    recorded = world_for(WELL_BEHAVED)
    first = recorded.job()
    recorded.runner.run_once()
    assert len(recorded.backend_calls) == 1
    recording = tmp_path / "recording"
    shutil.copytree(recorded.data.blobs, recording / "blobs")
    shutil.copy(
        recorded.data.job_dir(first) / "ledger.jsonl", recording / "ledger.jsonl"
    )

    # 第二次：全新的資料目錄、重播模式，後端位址指到一個沒人聽的埠
    data = DataDir(tmp_path / "replayed")
    data.ensure()
    stages = ActiveStages()
    config = GatewayConfig(
        data=data, mode="replay", replay_dir=recording, ollama_url="http://127.0.0.1:9"
    )
    with serve(create_app(config, stages)) as gateway_url:
        runner = replace(
            recorded.runner, data=data, stages=stages, gateway_url=gateway_url
        )
        job_id = open_job(
            data,
            runner.recipes["echo"],
            PastedText(ARTICLE.read_text(encoding="utf-8")),
            goal="用一段話說明這篇在講什麼",
            now=NOW,
        ).job.id
        assert runner.run_once() == job_id

    job = read_job(data.job_dir(job_id) / "job.md").job
    assert job.result is not None and job.result.outcome == "complete"
    assert len(recorded.backend_calls) == 1
    lines = (
        (data.job_dir(job_id) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line)["status"] for line in lines] == ["cache_hit"]


@pytest.mark.promise("NFR-COST-01")
def test_工單預算不夠_Pi_被擋下_工單以未完成結束_原因是預算用完(
    world_for: Callable[..., World],
) -> None:
    world = world_for(CLOUD_CALL)
    # 一次 strong 呼叫要預留約 US$0.16
    job_id = world.job(budget_usd=0.05)
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "done"
    assert job.result is not None
    assert (job.result.outcome, job.result.reason) == ("incomplete", "預算用完")
    assert world.backend_calls == []


@pytest.mark.promise("NFR-COST-01", "FR-LG-01")
def test_本月額度用完_工單以未完成結束_原因是本月額度用完(
    world_for: Callable[..., World],
) -> None:
    world = world_for(CLOUD_CALL)
    # 投入帳本裡的抓取已經花掉 US$9.95，月上限預設 US$10
    append_intake(
        world.data,
        IntakeEntry(
            ts=NOW,
            kind="fetch",
            url="https://example.com/a",
            cost_usd=9.95,
            latency_ms=5,
            status="ok",
        ),
    )
    job_id = world.job(budget_usd=1.0)
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.result is not None
    assert (job.result.outcome, job.result.reason) == ("incomplete", "本月額度用完")
    assert world.backend_calls == []


@pytest.mark.promise("NFR-RUN-02")
def test_段裡丟例外_Pi_執行檔不見_工單以失敗結束_不卡在執行中(
    world_for: Callable[..., World],
) -> None:
    world = world_for(WELL_BEHAVED)
    world.runner.harness = replace(
        world.runner.harness,
        pi=replace(world.runner.harness.pi, pi_bin=Path("/nonexistent/pi")),
    )
    job_id = world.job()

    assert world.runner.run_once() == job_id

    job = world.read(job_id)
    assert job.status == "done"
    assert job.result is not None and job.result.outcome == "failed"
    assert "/nonexistent/pi" in job.result.reason
    # 鑰匙有撤銷
    assert not world.stages.is_active(JobKey(job_id, "echo", 1))


def test_有一張的_job_md_壞掉_略過它_照樣跑下一張(
    world_for: Callable[..., World],
) -> None:
    world = world_for(WELL_BEHAVED)
    broken = world.data.jobs / "20200101-000000-broken"
    broken.mkdir(parents=True)
    (broken / "job.md").write_text("---\nnot: [valid\n---\n", encoding="utf-8")
    job_id = world.job()

    assert world.runner.run_once() == job_id
    assert world.read(job_id).status == "done"


@pytest.mark.promise("NFR-PAR-01")
def test_同時最多跑兩張_最舊的先跑_第三張等有空位才開始(
    world_for: Callable[..., World], tmp_path: Path
) -> None:
    release = tmp_path / "release"
    script = (
        FAKE_PI_LIB
        + f"""
while not os.path.exists({str(release)!r}):
    time.sleep(0.02)
write("summary.json", {{"summary": "好"}})
"""
    )
    world = world_for(script, max_parallel=2)
    # 工單 id 開頭是開單時間：第 3 篇最晚開，內容 hash 排在前面也一樣排最後
    first, second, third = (
        world.job(
            text=f"第 {n} 篇測試文章，內容各不相同。", now=NOW + timedelta(minutes=n)
        )
        for n in (1, 2, 3)
    )

    assert world.runner.start_available() == [first, second]
    assert world.read(third).status == "queued"
    assert world.runner.start_available() == []

    release.touch()
    world.runner.wait(timeout_s=30)
    assert sorted(world.runner.finished()) == sorted([first, second])
    assert world.runner.start_available() == [third]
    world.runner.wait(timeout_s=30)
    for job_id in (first, second, third):
        job = world.read(job_id)
        assert job.result is not None and job.result.outcome == "complete"


@pytest.mark.promise("NFR-RUN-01")
def test_開跑前檢查沒過_工單留在排隊_事件紀錄寫明是哪一項_好了之後自己開始(
    world_for: Callable[..., World],
) -> None:
    problems = ["Ollama 沒回應：連不上 http://127.0.0.1:11434"]
    world = world_for(WELL_BEHAVED, preflight=lambda recipe: list(problems))
    job_id = world.job()

    assert world.runner.run_once() is None
    assert world.runner.run_once() is None
    assert world.read(job_id).status == "queued"
    errors = [e.message for e in world.events(job_id) if e.kind == "error"]
    # 同樣的原因只寫一次
    assert len(errors) == 1 and "Ollama 沒回應" in errors[0]
    assert world.backend_calls == []

    problems.clear()
    assert world.runner.run_once() == job_id
    assert world.read(job_id).status == "done"


@pytest.mark.promise("NFR-RUN-06")
def test_每份段產出記著模型對照_skill_的_git_sha_與配方版本(
    world_for: Callable[..., World],
) -> None:
    world = world_for(WELL_BEHAVED)
    job_id = world.job()
    world.runner.run_once()

    job_dir = world.data.job_dir(job_id)
    # 版本紀錄是執行器寫的，放在 provenance/；stages/ 只有 agent 經閘道寫（ADR-0010 §3 第 3 點）
    record = json.loads(
        (job_dir / "provenance" / "echo.1.json").read_text(encoding="utf-8")
    )
    assert [p.name for p in (job_dir / "stages" / "echo" / "1").iterdir()] == [
        "summary.json"
    ]
    repo = default_harness().root.parent
    skill_sha = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "-1",
            "--format=%H",
            "--",
            "harness/skills/echo",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    recipe_sha = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "-1",
            "--format=%H",
            "--",
            "harness/recipes/echo.md",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert record["skill"]["name"] == "echo"
    assert record["skill"]["git_sha"] == skill_sha
    assert record["recipe"]["name"] == "echo"
    assert record["recipe"]["git_sha"] == recipe_sha
    assert record["models"]["local"] == {"backend": "ollama", "model": "qwen3.5:9b"}
    assert len(record["models_sha256"]) == 64
    assert (record["tier"], record["escalated_from"]) == ("local", None)


@pytest.mark.promise("NFR-RUN-01", "NFR-RUN-02")
def test_開跑前檢查自己丟例外_當成沒過_工單留在排隊_執行器不停(
    world_for: Callable[..., World],
) -> None:
    def broken(recipe: Recipe) -> list[str]:
        raise TypeError("'NoneType' object is not iterable")

    world = world_for(WELL_BEHAVED, preflight=broken)
    job_id = world.job()

    assert world.runner.run_once() is None
    assert world.runner.start_available() == []
    assert world.read(job_id).status == "queued"
    errors = [e.message for e in world.events(job_id) if e.kind == "error"]
    assert len(errors) == 1 and "TypeError" in errors[0]
