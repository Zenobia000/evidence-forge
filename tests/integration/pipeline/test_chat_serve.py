"""整條走一次（#85 的端到端檢查）：真的 `ef serve` 程序、真的 Pi（對話與段都是）、閘道、執行器、對話主機與 API，
模型是照劇本回答的假 Anthropic（`EF_ANTHROPIC_URL`），不花錢。

畫面（#86）會做的事，這裡用 HTTP 做：開對話 → 送一句「分析：…」→ 對話的 agent 叫 `analyze` →
執行器照配方跑段的 Pi → 工單跑完 → 主機送 `/ef-job-done` → ef-tools 放通知 → agent 叫 `read_report` 整理。
同時用 SSE 收事件，確認進度（`ef_job`）一路推過來。最後關掉 `ef serve`，Pi 一個都不留。

配方是只有一段、`strong` 等級的 echo（`chat-echo`），寫在暫時的 harness 裡；`analyze` 配方還在 #78。
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_world import pid_alive, wait_until
from fake_anthropic import FakeAnthropic

from evidence_forge.harness import default_harness

REAL = default_harness()
FAKE_KEY = "sk-fake-xxxx-anthropic-chat-serve"
CHAT_ECHO = """---
name: chat-echo
description: 測試用：一段、strong，讀素材寫一個摘要
stages:
  - { id: echo, skill: echo, tier: strong, output: [summary.json] }
on_feedback: echo
default_goal: 用兩三句話說明這篇文章在講什麼
max_attempts: 1
auto_fix: 0
---

對話主機的端到端測試用。
"""

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(
        not REAL.pi.pi_bin.exists() or shutil.which("node") is None,
        reason="沒有鎖版的 Pi：先用 nvm 的 Node 在 harness/pi/ 跑 npm ci",
    ),
]

Serve = Callable[[Any], AbstractContextManager[str]]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        found: int = sock.getsockname()[1]
        return found


def _harness(root: Path) -> Path:
    """真的 Pi、ef-tools、skill，只換配方。"""
    root.mkdir()
    for name in ("pi", "skills", "genres", "conversions"):
        (root / name).symlink_to(REAL.root / name)
    (root / "recipes").mkdir()
    (root / "recipes" / "chat-echo.md").write_text(CHAT_ECHO, encoding="utf-8")
    return root


@pytest.fixture
def ef_serve(
    tmp_path: Path, serve: Serve, port: int
) -> Iterator[tuple[str, Path, FakeAnthropic, subprocess.Popen[bytes]]]:
    data = tmp_path / "data"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "anthropic").write_text(FAKE_KEY + "\n", encoding="utf-8")
    model = FakeAnthropic()
    chat_port = _free_port()
    with serve(model.app()) as model_url:
        process = subprocess.Popen(
            [sys.executable, "-m", "evidence_forge.cli", "serve", "--port", str(port)]
            + ["--chat-port", str(chat_port), "--chat-recipe", "chat-echo"]
            + ["--harness", str(_harness(tmp_path / "harness")), "--interval", "0.2"],
            env={
                "PATH": os.environ["PATH"],
                "EF_DATA": str(data),
                "EF_SECRETS": str(secrets),
                "EF_ANTHROPIC_URL": model_url,
                "EF_GATEWAY_MODE": "live",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        api = f"http://127.0.0.1:{chat_port}"
        try:

            def up() -> bool:
                if process.poll() is not None:
                    _, err = process.communicate()
                    raise AssertionError(f"ef serve 提早結束：{err.decode()}")
                try:
                    return httpx.get(f"{api}/api/conversations", timeout=1).status_code == 200
                except httpx.HTTPError:
                    return False

            wait_until(up, timeout_s=30, what="ef serve 起來")
            yield api, data, model, process
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _collect(url: str, events: list[dict[str, Any]], stop: threading.Event) -> None:
    with httpx.stream("GET", url, timeout=60) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
            if stop.is_set():
                return


def _texts(api: str, cid: str) -> list[str]:
    messages = httpx.get(f"{api}/api/conversations/{cid}/messages", timeout=60).json()["messages"]
    return [
        block["text"]
        for m in messages
        if m.get("role") == "assistant"
        for block in m.get("content", [])
        if block.get("type") == "text"
    ]


@pytest.mark.promise("FR-CH-01", "NFR-OBS-02", "NFR-RUN-04")
def test_丟文章到拿到整理_全程經過_ef_serve_關掉之後不留_Pi(
    ef_serve: tuple[str, Path, FakeAnthropic, subprocess.Popen[bytes]],
) -> None:
    api, data, model, process = ef_serve
    cid = httpx.post(f"{api}/api/conversations", json={}).json()["id"]
    events: list[dict[str, Any]] = []
    stop = threading.Event()
    reader = threading.Thread(
        target=_collect, args=(f"{api}/api/conversations/{cid}/events", events, stop), daemon=True
    )
    reader.start()
    wait_until(lambda: bool(events), what="SSE 連上")

    sent = httpx.post(
        f"{api}/api/conversations/{cid}/messages",
        json={"text": "分析：# 圖書館的夜班\n\n夜班的人力成本與使用率。"},
        timeout=60,
    )
    assert sent.status_code == 202, sent.text

    wait_until(
        lambda: any(t.startswith("報告重點：") for t in _texts(api, cid)),
        timeout_s=120,
        what="工單跑完、通知、讀報告、整理",
    )
    texts = _texts(api, cid)
    assert texts[0] == "開始分析了。"
    summary = next(t for t in texts if t.startswith("報告重點："))
    assert "假的摘要" in summary

    # 進度一路推過來：排隊 → 執行中（第 1／1 段）→ 結束
    wait_until(lambda: any(e.get("type") == "ef_job" and e["finished"] for e in events))
    progress = [e for e in events if e.get("type") == "ef_job"]
    assert [p["status"] for p in progress][0] == "queued"
    running = [p for p in progress if p["status"] == "running" and p["stage"]]
    assert running and running[0]["stage"]["index"] == 1 and running[0]["stage"]["total"] == 1
    assert progress[-1]["outcome"] == "complete"
    assert progress[-1]["spent_usd"] > 0
    assert {"type": "ef_conversation", "title": "圖書館的夜班"} in [
        {k: v for k, v in e.items() if k != "seq"} for e in events
    ]
    # 每則都帶主機的流水號，照送出的順序遞增
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    stop.set()

    detail = httpx.get(f"{api}/api/conversations/{cid}").json()
    assert detail["title"] == "圖書館的夜班"
    assert detail["latest_job"]["outcome"] == "complete"
    assert detail["spent_usd"] > 0
    # 對話本身的花費進了對話的帳本
    ledger = data / "conversations" / cid / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows and all(r["conversation_id"] == cid and r.get("job_id") is None for r in rows)
    # 對話與段都打了假 Anthropic；沒有任何一個請求出這台機器
    assert len(model.requests) >= 5

    pids = [int(p.read_text()) for p in (data / "run" / "chat-pids").glob("*.pid")]
    assert pids
    # 關掉 ef serve（Ctrl-C）：正常結束，對話的 Pi 一個都不留
    process.send_signal(signal.SIGINT)
    assert process.wait(timeout=30) == 0
    for pid in pids:
        wait_until(partial(lambda p: not pid_alive(p), pid), timeout_s=30, what="對話的 Pi 結束")
    assert not list((data / "run" / "chat-pids").glob("*.pid"))

