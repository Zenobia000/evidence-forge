"""SEAM-10（整合）：跑到一半 `kill -9` 掉 `ef serve`，重開後從同一段、同一次重跑，
前面的請求全部命中閘道快取，不重付（NFR-RUN-04、NFR-COST-02、TC-RUNNER-03）。

真的開兩次 `ef serve` 程序；Pi 是假的（問一次模型、寫產出，第一次寫完就停著等被殺），
Ollama 是本機 stub，所以不需要 Ollama，也不打付費 API。

另外一條（SEAM-12）：`kill -9` 掉 `ef serve` 時 Pi 在自己的程序群組裡活下來；重開後重跑
同一段同一次，鑰匙跟當機前一模一樣，所以舊的 Pi 要在鑰匙重新登記之前被殺掉。
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from runner_world import ARTICLE, FAKE_PI_LIB, GOAL, NOW, SSE, fake_pi

from evidence_forge.documents import read_job
from evidence_forge.harness import default_harness
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import TIER_ORDER
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes

# 第一次：問模型、寫產出，然後留下自己的 pid 停著等被殺；之後：照常寫完
PI = (
    FAKE_PI_LIB
    + """
MARK = os.environ["PATH"].split(":")[0] + "/asked"
status, _ = ask()
assert status == 200, status
# 真的 agent 會把寫檔的結果送進下一個請求：被拒（409）的話請求就跟上次不同、快取接不上
assert write("summary.json", {"summary": "重跑也一樣"}) == 200
if not os.path.exists(MARK):
    # 先寫暫存再改名：測試一看到記號檔，裡面就一定有完整的 pid
    with open(MARK + ".tmp", "w") as handle:
        handle.write(str(os.getpid()))
    os.replace(MARK + ".tmp", MARK)
    while True:
        time.sleep(1)
event(type="agent_end")
"""
)


@pytest.fixture
def ollama(serve: Callable[[Any], AbstractContextManager[str]]) -> Iterator[Any]:
    calls: list[dict[str, Any]] = []
    stub = FastAPI()

    @stub.post("/v1/messages")
    async def messages(request: Request) -> Response:
        calls.append(json.loads(await request.body()))
        return Response(SSE, media_type="text/event-stream")

    @stub.get("/api/ps")
    async def loaded() -> dict[str, Any]:
        # 開跑前檢查問「模型載入了沒」與載入時的上下文（ADR-0022）
        return {
            "models": [
                {"name": "qwen3.5:9b", "model": "qwen3.5:9b", "context_length": 32768}
            ]
        }

    with serve(stub) as url:
        yield url, calls


def _harness(tmp_path: Path, marks: Path, pi: str = PI) -> Path:
    repo = default_harness()
    root = tmp_path / "harness"
    # 只帶 echo 配方：其他配方用到的 skill 沒複製過來，`ef serve` 啟動時會擋
    (root / "recipes").mkdir(parents=True)
    shutil.copy(repo.recipes / "echo.md", root / "recipes" / "echo.md")
    shutil.copytree(repo.skill_dir("echo"), root / "skills" / "echo")
    shutil.copytree(repo.pi.agent_template, root / "pi" / "agent")
    bin_dir = root / "pi" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    fake_pi(bin_dir, pi, "pi")
    marks.mkdir()
    return root


def _serve(
    harness: Path, data: Path, ollama_url: str, port: int, marks: Path, *extra: str
) -> subprocess.Popen[bytes]:
    env = {
        # PATH 的第一節放記號資料夾：假 Pi 只拿得到 PATH 等幾個環境變數（pi_launch）
        "PATH": f"{marks}:/usr/bin:/bin",
        "EF_DATA": str(data),
        "EF_GATEWAY_MODE": "live",
        "EF_OLLAMA_URL": ollama_url,
        "EF_SECRETS": str(data.parent / "secrets"),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "evidence_forge.cli", "serve", "--port", str(port)]
        # 這裡只測執行器的重開；對話主機有自己的測試（test_chat_serve.py），不佔 8790
        + ["--harness", str(harness), "--interval", "0.1", "--chat-port", "0", *extra],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for(path: Path, process: subprocess.Popen[bytes], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if process.poll() is not None:
            _, err = process.communicate()
            raise AssertionError(f"ef serve 提早結束：{err.decode()}")
        if time.monotonic() > deadline:
            raise AssertionError(f"等了 {timeout_s} 秒還沒看到 {path}")
        time.sleep(0.05)


@pytest.mark.promise("NFR-RUN-04", "NFR-COST-02")
def test_跑到一半殺掉_ef_serve_重開後從同一段同一次重跑_前面的請求命中快取(
    tmp_path: Path, ollama: tuple[str, list[dict[str, Any]]], port: int
) -> None:
    ollama_url, calls = ollama
    marks = tmp_path / "marks"
    harness = _harness(tmp_path, marks)
    data = DataDir(tmp_path / "data")
    data.ensure()
    recipes = load_recipes(default_harness(), tiers=TIER_ORDER)
    job_id = open_job(
        data,
        recipes["echo"],
        PastedText(ARTICLE.read_text(encoding="utf-8")),
        goal=GOAL,
        now=NOW,
    ).job.id
    job_md = data.job_dir(job_id) / "job.md"

    first = _serve(harness, data.root, ollama_url, port, marks)
    pi_pid: int | None = None
    try:
        _wait_for(marks / "asked", first, timeout_s=60)
        pi_pid = int((marks / "asked").read_text())
    finally:
        # kill -9：執行器沒有機會收尾；Pi 在自己的程序群組，要另外殺
        first.kill()
        first.communicate()
        if pi_pid is not None:
            os.kill(pi_pid, signal.SIGKILL)
    assert read_job(job_md).job.status == "running"
    assert len(calls) == 1

    second = _serve(harness, data.root, ollama_url, port, marks, "--once")
    try:
        _, err = second.communicate(timeout=60)
    finally:
        if second.poll() is None:
            second.kill()
            second.communicate()
    assert second.returncode == 0, err.decode()

    job = read_job(job_md).job
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "complete", job.result
    # 沒有多出新的嘗試目錄；重跑的請求跟殺掉前一樣，從帳本回放，後端沒再收到請求
    assert [p.name for p in (data.job_dir(job_id) / "stages" / "echo").iterdir()] == [
        "1"
    ]
    ledger = [
        LedgerEntry.model_validate_json(line)
        for line in (data.job_dir(job_id) / "ledger.jsonl").read_text().splitlines()
    ]
    assert [(e.attempt, e.status, e.cost_usd) for e in ledger] == [
        (1, "ok", 0.0),
        (1, "cache_hit", 0.0),
    ]
    assert len(calls) == 1
    # 當掉那次寫了一半的產出搬到工單資料夾外，沒有丟掉
    parked = data.run / "interrupted" / job_id / "echo-1-1" / "summary.json"
    assert parked.is_file()


# 第一次：問模型、寫產出、留下自己的 pid，然後一直拿同一把鑰匙打閘道寫檔。測試殺掉第一個
# `ef serve` 之後放一個 restarted 記號，從那之後收到的狀態碼記進 late-statuses，
# 所以記到的全是打重開後那個閘道的結果。之後（重開後的新 Pi）：照常寫完。
# 最多敲 120 秒就自己結束：pytest 本身被硬殺、finally 沒跑到時，也不會留下一直跑的程序
PI_KEEPS_KNOCKING = (
    FAKE_PI_LIB
    + """
MARKS = os.environ["PATH"].split(":")[0]
MARK = MARKS + "/asked"
status, _ = ask()
assert status == 200, status
assert write("summary.json", {"summary": "重跑也一樣"}) == 200
if not os.path.exists(MARK):
    with open(MARK + ".tmp", "w") as handle:
        handle.write(str(os.getpid()))
    os.replace(MARK + ".tmp", MARK)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if not os.path.exists(MARKS + "/restarted"):
            continue
        try:
            status = write("late.txt", "舊的 Pi 在重開之後寫的")
        except OSError:
            continue  # 新的閘道還沒起來
        with open(MARKS + "/late-statuses", "a") as handle:
            handle.write(str(status) + "\\n")
event(type="agent_end")
"""
)


def _running(pid: int) -> bool:
    """這個程序還在跑；已經死了、只是還沒被收屍（殭屍）的不算。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.promise("NFR-SEC-02", "NFR-KEEP-02", "NFR-RUN-04")
def test_重開時舊的_Pi_還活著_重跑之前先被殺掉_它拿舊鑰匙來的請求全部被拒(
    tmp_path: Path, ollama: tuple[str, list[dict[str, Any]]], port: int
) -> None:
    ollama_url, calls = ollama
    marks = tmp_path / "marks"
    harness = _harness(tmp_path, marks, PI_KEEPS_KNOCKING)
    data = DataDir(tmp_path / "data")
    data.ensure()
    recipes = load_recipes(default_harness(), tiers=TIER_ORDER)
    job_id = open_job(
        data,
        recipes["echo"],
        PastedText(ARTICLE.read_text(encoding="utf-8")),
        goal=GOAL,
        now=NOW,
    ).job.id
    job_md = data.job_dir(job_id) / "job.md"

    first = _serve(harness, data.root, ollama_url, port, marks)
    old_pi: int | None = None
    try:
        try:
            _wait_for(marks / "asked", first, timeout_s=60)
            old_pi = int((marks / "asked").read_text())
        finally:
            # kill -9：執行器沒有機會收尾，舊的 Pi 在自己的程序群組裡活下來
            first.kill()
            first.communicate()
        assert _running(old_pi)
        (marks / "restarted").touch()

        second = _serve(harness, data.root, ollama_url, port, marks, "--once")
        try:
            _, err = second.communicate(timeout=60)
        finally:
            if second.poll() is None:
                second.kill()
                second.communicate()
        assert second.returncode == 0, err.decode()

        # 重跑之前舊的 Pi 已經被殺掉；它拿舊鑰匙打新閘道的請求一個都沒被接受
        assert not _running(old_pi)
        late = marks / "late-statuses"
        statuses = late.read_text().split() if late.exists() else []
        assert "200" not in statuses, statuses
        stage_dir = data.job_dir(job_id) / "stages" / "echo" / "1"
        assert [p.name for p in stage_dir.iterdir()] == ["summary.json"]
        job = read_job(job_md).job
        assert job.result is not None and job.result.outcome == "complete", job.result
        assert len(calls) == 1
    finally:
        # 修壞的時候舊的 Pi 會一直活著：只殺這個測試自己開的那一個
        if old_pi is not None and _running(old_pi):
            os.kill(old_pi, signal.SIGKILL)
