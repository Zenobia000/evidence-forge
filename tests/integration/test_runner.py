"""SEAM-01（整合）：執行器 ↔ Pi 程序 ↔ 閘道，用假的 Pi 跑完整條打通路徑。

假 Pi 是一支小程式：照 Pi `--mode json` 的格式印事件，並且真的用工單鑰匙打閘道的
`/v1/messages?beta=true` 與 `/tools/*`，跟 ef-tools 送的請求一樣。
模型後端是本機 stub，所以不需要 Ollama。
"""

import json
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response

from evidence_forge.documents import read_job
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, JobBoard, create_app
from evidence_forge.gateway.ledger import append_intake
from evidence_forge.ledger_models import IntakeEntry
from evidence_forge.harness import Harness, default_harness
from evidence_forge.jobkey import JobKey
from evidence_forge.models import Event
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes
from evidence_forge.runner.runner import Runner

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=TAIPEI)
ARTICLE = Path(__file__).parents[1] / "fixtures" / "articles" / "tracer.md"

# 假 Pi 的共同部分：讀環境變數裡的閘道與鑰匙，提供 call() 打閘道。
FAKE_PI_HEAD = """
import json, os, sys, urllib.request, urllib.error
URL = os.environ["EF_GATEWAY_URL"]
KEY = os.environ["EF_JOB_KEY"]
def call(path, body):
    request = urllib.request.Request(
        URL + path, data=json.dumps(body).encode(),
        headers={"x-api-key": KEY, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
def event(**fields):
    print(json.dumps(fields), flush=True)
event(type="session", version=3)
event(type="agent_start")
status, _ = call("/v1/messages?beta=true", {"model": "local", "stream": True,
    "messages": [{"role": "user", "content": sys.argv[-1]}]})
assert status == 200, status
event(type="tool_execution_start", toolName="read_file")
status, body = call("/tools/read_file", {"path": "input/content.md"})
assert status == 200, status
event(type="tool_execution_end", toolName="read_file", isError=False)
"""

WELL_BEHAVED = (
    FAKE_PI_HEAD
    + """
summary = {"summary": "這是一段測試用的摘要。"}
status, _ = call("/tools/write_file", {"path": "stages/echo/1/summary.json",
    "content": json.dumps(summary, ensure_ascii=False)})
assert status == 200, status
event(type="agent_end")
"""
)

# 原文裡的提示注入得逞時，agent 會試著寫審稿決定與狀態檔
INJECTED = (
    FAKE_PI_HEAD
    + """
results = {}
for path in ["review/decision.v1.json", "job.md", "events.jsonl", "ledger.jsonl"]:
    status, _ = call("/tools/write_file", {"path": path, "content": '{"action": "approve"}'})
    results[path] = status
call("/tools/write_file", {"path": "stages/echo/1/summary.json",
    "content": json.dumps({"summary": "照常完成"})})
event(type="agent_end", rejected=results)
"""
)

BAD_OUTPUT = (
    FAKE_PI_HEAD
    + """
call("/tools/write_file", {"path": "stages/echo/1/summary.json",
    "content": json.dumps({"summery": "欄位名打錯"})})
event(type="agent_end")
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
    FAKE_PI_HEAD.split("status, _ = call(")[0]
    + """
status, body = call("/v1/messages?beta=true", {"model": "strong", "stream": True,
    "messages": [{"role": "user", "content": sys.argv[-1]}]})
if status != 200:
    print(body.decode(), file=sys.stderr)
    sys.exit(1)
event(type="agent_end")
"""
)

SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
    b'{"input_tokens":1,"output_tokens":0}}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


@dataclass
class World:
    data: DataDir
    runner: Runner
    gateway_url: str
    stages: ActiveStages
    backend_calls: list[dict[str, Any]]

    def job(self, budget_usd: float = 0.5) -> str:
        recipe = self.runner.recipes["echo"]
        return open_job(
            self.data,
            recipe,
            PastedText(ARTICLE.read_text(encoding="utf-8")),
            goal="用一段話說明這篇在講什麼",
            now=NOW,
            budget_usd=budget_usd,
        ).job.id


def _fake_pi(tmp_path: Path, script: str) -> Path:
    path = tmp_path / "fake-pi"
    path.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def world_for(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Callable[[str], World]]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    stages = ActiveStages()
    backend_calls: list[dict[str, Any]] = []
    # 假的雲端金鑰（帶 xxxx，guard-secrets 認得是假的）
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "anthropic").write_text("sk-fake-xxxx-runner", encoding="utf-8")

    stub = FastAPI()

    @stub.post("/v1/messages")
    async def messages(request: Request) -> Response:
        backend_calls.append(json.loads(await request.body()))
        return Response(SSE, media_type="text/event-stream")

    with serve(stub) as ollama_url:
        config = GatewayConfig(
            data=data,
            mode="live",
            ollama_url=ollama_url,
            # strong 也打同一個 stub：被預算擋下的測試斷言它一個請求都沒收到
            anthropic_url=ollama_url,
            secrets_dir=secrets,
            clock=lambda: NOW,
        )
        board = JobBoard(config.clock)
        with serve(create_app(config, stages, board)) as gateway_url:

            def build(script: str) -> World:
                repo = default_harness()
                harness = Harness(
                    root=repo.root,
                    pi=replace(repo.pi, pi_bin=_fake_pi(tmp_path, script)),
                )
                runner = Runner(
                    data=data,
                    harness=harness,
                    recipes=load_recipes(harness, tiers=config.aliases.keys()),
                    stages=stages,
                    gateway_url=gateway_url,
                    clock=lambda: NOW,
                    base_env={"PATH": "/usr/bin:/bin"},
                    board=board,
                )
                return World(data, runner, gateway_url, stages, backend_calls)

            yield build


def _events(data: DataDir, job_id: str) -> list[Event]:
    lines = (data.job_dir(job_id) / "events.jsonl").read_text(encoding="utf-8")
    return [Event.model_validate_json(line) for line in lines.splitlines()]


@pytest.mark.promise("FR-RC-01", "NFR-KEEP-02")
def test_開單後跑完唯一一段_產出合格_工單進待審且結果完成(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(WELL_BEHAVED)
    job_id = world.job()

    assert world.runner.run_once() == job_id

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "complete"
    summary = world.data.job_dir(job_id) / "stages" / "echo" / "1" / "summary.json"
    assert json.loads(summary.read_text(encoding="utf-8")) == {
        "summary": "這是一段測試用的摘要。"
    }
    assert len(world.backend_calls) == 1


@pytest.mark.promise("NFR-KEEP-02", "NFR-RUN-02")
def test_狀態轉換依序寫進事件紀錄(world_for: Callable[[str], World]) -> None:
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
        ("running", "review"),
    ]


def test_沒有排隊中的工單就什麼都不做(world_for: Callable[[str], World]) -> None:
    assert world_for(WELL_BEHAVED).runner.run_once() is None


@pytest.mark.promise("NFR-SEC-02", "NFR-KEEP-02")
def test_agent_試著寫審稿決定與狀態檔_全部被拒_工單照常停在待審(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(INJECTED)
    job_id = world.job()
    world.runner.run_once()

    job_dir = world.data.job_dir(job_id)
    assert not (job_dir / "review").exists()
    job = read_job(job_dir / "job.md").job
    assert job.status == "review"
    for event in _events(world.data, job_id):
        assert "approve" not in event.message
    # Pi 自己回報的被拒結果：四個都是 403
    log = next((world.data.run / "pi-logs").glob(f"{job_id}-*.jsonl"))
    last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert set(last["rejected"].values()) == {403}


@pytest.mark.promise("NFR-RUN-03")
def test_產出不合_schema_工單以失敗進待審並寫明原因(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(BAD_OUTPUT)
    job_id = world.job()
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert "summary.json" in job.result.reason


def test_Pi_非零結束_工單以失敗進待審並附上錯誤輸出(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(CRASHES)
    job_id = world.job()
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.result is not None and job.result.outcome == "failed"
    assert "3" in job.result.reason and "boom" in job.result.reason


@pytest.mark.promise("NFR-SEC-02")
def test_段結束後鑰匙失效_拿舊鑰匙讀寫都被拒(world_for: Callable[[str], World]) -> None:
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
    world_for: Callable[[str], World],
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
def test_工單預算不夠_Pi_被擋下_工單以未完成進待審_原因是預算用完(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(CLOUD_CALL)
    # 一次 strong 呼叫要預留約 US$0.16
    job_id = world.job(budget_usd=0.05)
    world.runner.run_once()

    job = read_job(world.data.job_dir(job_id) / "job.md").job
    assert job.status == "review"
    assert job.result is not None
    assert (job.result.outcome, job.result.reason) == ("incomplete", "預算用完")
    assert world.backend_calls == []


@pytest.mark.promise("NFR-COST-01", "FR-LG-01")
def test_本月額度用完_工單以未完成進待審_原因是本月額度用完(
    world_for: Callable[[str], World],
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
