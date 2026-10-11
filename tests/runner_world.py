"""執行器整合測試共用：假 Pi、假模型後端、真的閘道，組成一個可以跑工單的小世界。

假 Pi 是一支小程式：照 Pi `--mode json` 的格式印事件，並且真的用工單鑰匙打閘道的
`/v1/messages?beta=true` 與 `/tools/*`，跟 ef-tools 送的請求一樣。它從 `--model ef/<等級>`
讀出這一次用哪一級、從工單鑰匙讀出段與第幾次，所以同一支腳本在重試與升級時照樣寫對地方。
模型後端是本機 stub，所以不需要 Ollama。
"""

import asyncio
import json
import stat
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import Response

from evidence_forge.documents import read_job
from evidence_forge.gateway.app import ActiveStages, GatewayConfig, JobBoard, create_app
from evidence_forge.harness import Harness, default_harness
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import Event, Job, Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes
from evidence_forge.runner.runner import Runner

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=TAIPEI)
ARTICLE = Path(__file__).parent / "fixtures" / "articles" / "tracer.md"
GOAL = "用一段話說明這篇在講什麼"

# 假 Pi 的共同部分：讀環境變數裡的閘道與鑰匙，提供 call()、ask()、write() 打閘道。
FAKE_PI_LIB = """
import json, os, sys, time, urllib.request, urllib.error
URL = os.environ["EF_GATEWAY_URL"]
KEY = os.environ["EF_JOB_KEY"]
_, JOB, STAGE, ATTEMPT = KEY.split(":")
TIER = sys.argv[sys.argv.index("--model") + 1].split("/", 1)[1]
OUT = "stages/" + STAGE + "/" + ATTEMPT + "/"
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
    print(json.dumps(fields, ensure_ascii=False), flush=True)
def ask(text=None):
    return call("/v1/messages?beta=true", {"model": TIER, "stream": True,
        "messages": [{"role": "user", "content": text or sys.argv[-1]}]})
def write(name, content):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    status, _ = call("/tools/write_file", {"path": OUT + name, "content": content})
    return status
event(type="session", version=3)
event(type="agent_start")
"""

# 打通用的開頭：問一次模型、讀一次素材
FAKE_PI_HEAD = (
    FAKE_PI_LIB
    + """
status, _ = ask()
assert status == 200, status
event(type="tool_execution_start", toolCallId="t1", toolName="read_file")
status, body = call("/tools/read_file", {"path": "input/content.md"})
assert status == 200, status
event(type="tool_execution_end", toolCallId="t1", toolName="read_file", isError=False)
"""
)

WELL_BEHAVED = (
    FAKE_PI_HEAD
    + """
assert write("summary.json", {"summary": "這是一段測試用的摘要。"}) == 200
event(type="agent_end")
"""
)

BAD_OUTPUT = (
    FAKE_PI_HEAD
    + """
write("summary.json", {"summery": "欄位名打錯"})
event(type="agent_end")
"""
)

SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
    b'{"input_tokens":1,"output_tokens":0}}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


@dataclass
class Backend:
    """假的模型後端（Ollama 與 Anthropic 都打它）：記下收到的請求、回 `reply`；
    `hold` 清掉時卡住不回。"""

    calls: list[dict[str, Any]] = field(default_factory=list)
    hold: threading.Event = field(default_factory=threading.Event)
    reply: bytes = SSE

    def __post_init__(self) -> None:
        self.hold.set()


@dataclass
class World:
    data: DataDir
    runner: Runner
    gateway_url: str
    stages: ActiveStages
    board: JobBoard
    backend: Backend

    @property
    def backend_calls(self) -> list[dict[str, Any]]:
        return self.backend.calls

    def job(
        self,
        budget_usd: float = 0.5,
        recipe: str = "echo",
        text: str | None = None,
        now: datetime = NOW,
    ) -> str:
        return open_job(
            self.data,
            self.runner.recipes[recipe],
            PastedText(text or ARTICLE.read_text(encoding="utf-8")),
            goal=GOAL,
            now=now,
            budget_usd=budget_usd,
        ).job.id

    def read(self, job_id: str) -> Job:
        return read_job(self.data.job_dir(job_id) / "job.md").job

    def events(self, job_id: str) -> list[Event]:
        lines = (self.data.job_dir(job_id) / "events.jsonl").read_text(encoding="utf-8")
        return [Event.model_validate_json(line) for line in lines.splitlines()]

    def ledger(self, job_id: str) -> list[LedgerEntry]:
        path = self.data.job_dir(job_id) / "ledger.jsonl"
        if not path.is_file():
            return []
        return [
            LedgerEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]


def fake_pi(directory: Path, script: str, name: str = "fake-pi") -> Path:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_harness(root: Path, skills: Mapping[str, Mapping[str, Any]]) -> Path:
    """測試用的 harness 資料夾：每個 skill 一份 `SKILL.md` 與給定的 `schema.json`。"""
    for name, schema in skills.items():
        skill = root / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8"
        )
        (skill / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (root / "recipes").mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def runner_world(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    **gateway: Any,
) -> Iterator[Callable[..., World]]:
    """開好假後端與閘道；回傳 `build(script, recipes=..., harness_root=..., **runner)`。"""
    data = DataDir(tmp_path / "data")
    data.ensure()
    stages = ActiveStages()
    backend = Backend()
    # 假的雲端金鑰（帶 xxxx，guard-secrets 認得是假的）
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    (secrets / "anthropic").write_text("sk-fake-xxxx-runner", encoding="utf-8")

    stub = FastAPI()

    @stub.post("/v1/messages")
    async def messages(request: Request) -> Response:
        backend.calls.append(json.loads(await request.body()))
        while not backend.hold.is_set():
            # 測試要觀察「等模型回應」時，在這裡卡住
            await asyncio.sleep(0.01)
        return Response(backend.reply, media_type="text/event-stream")

    with serve(stub) as backend_url:
        config = GatewayConfig(
            **{
                "data": data,
                "mode": "live",
                "ollama_url": backend_url,
                # strong 也打同一個 stub：被預算擋下的測試斷言它一個請求都沒收到
                "anthropic_url": backend_url,
                "secrets_dir": secrets,
                "clock": lambda: NOW,
                **gateway,
            }
        )
        board = JobBoard(config.clock)
        with serve(create_app(config, stages, board)) as gateway_url:
            pis = tmp_path / "pis"
            pis.mkdir(exist_ok=True)
            counter = iter(range(1_000_000))

            def build(
                script: str,
                *,
                recipes: Mapping[str, Recipe] | None = None,
                harness_root: Path | None = None,
                **runner: Any,
            ) -> World:
                repo = default_harness()
                harness = Harness(
                    root=harness_root or repo.root,
                    pi=replace(
                        repo.pi, pi_bin=fake_pi(pis, script, f"pi-{next(counter)}")
                    ),
                )
                built = Runner(
                    **{
                        "data": data,
                        "harness": harness,
                        "recipes": recipes
                        if recipes is not None
                        else load_recipes(harness, tiers=config.aliases.keys()),
                        "stages": stages,
                        "gateway_url": gateway_url,
                        "clock": lambda: NOW,
                        "base_env": {"PATH": "/usr/bin:/bin"},
                        "board": board,
                        **runner,
                    }
                )
                return World(data, built, gateway_url, stages, board, backend)

            yield build
