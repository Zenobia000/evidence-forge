"""SEAM-01（整合）：`run_pi` 真的開出子程序時，子程序看到的 stdin、環境變數與工作目錄。

單元測試鎖住 `build_pi_command` 組出的值；這裡確認那些值真的交到子程序手上。
用一支假的 Pi（Python 小程式）回報它看到的東西，不需要 Node。

另外（SEAM-12）：Pi 跑的期間程序群組編號記在 `run/pi-pids/`，`kill_leftover` 只殺
環境變數裡還拿著同一把鑰匙的程序。
"""

import json
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir
from evidence_forge.runner.pi_launch import (
    PiHarness,
    build_pi_command,
    kill_leftover,
    pid_file,
    run_pi,
)

# 假 Pi：把 stdin 指到哪裡、環境變數、工作目錄印成一行 JSON 事件
REPORTER = """
import json, os
print(json.dumps({
    "type": "report",
    "stdin": os.path.realpath("/proc/self/fd/0"),
    "env": dict(os.environ),
    "cwd": os.getcwd(),
}), flush=True)
"""


KEY = JobKey("20261011-120000-abcd1234", "echo", 1)


def _run(
    tmp_path: Path,
    base_env: dict[str, str],
    script: str = REPORTER,
    prompt: str = "做這一段",
    pid_path: Path | None = None,
) -> dict[str, object]:
    fake = tmp_path / "fake-pi"
    fake.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    data = DataDir(tmp_path / "data")
    command = build_pi_command(
        harness=PiHarness(
            pi_bin=fake,
            system_prompt=tmp_path / "system-prompt.md",
            ef_tools=tmp_path / "index.ts",
            agent_template=tmp_path / "agent",
        ),
        data=data,
        key=KEY,
        tier="local",
        thinking="off",
        skill_file=tmp_path / "SKILL.md",
        gateway_url="http://127.0.0.1:8787",
        prompt=prompt,
        base_env=base_env,
    )
    result = run_pi(
        command,
        log_path=data.run / "pi-logs" / "x.jsonl",
        timeout_s=60,
        pid_path=pid_path,
    )
    assert result.ok, result.stderr_tail
    [report] = [event for event in result.events if event.get("type") == "report"]
    return report


@pytest.fixture
def stdin_is_a_pipe() -> Iterator[None]:
    """把這個測試程序的 fd 0 暫時換成一條管子。

    pytest 自己的 stdin 常常已經是 /dev/null，子程序就算直接繼承也會看到 /dev/null，
    測試就測不出東西；換成管子之後，只有 run_pi 明確接 /dev/null 才會過。
    """
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.usefixtures("stdin_is_a_pipe")
def test_子程序的_stdin_是_dev_null(tmp_path: Path) -> None:
    report = _run(tmp_path, {"PATH": "/usr/bin:/bin"})
    assert report["stdin"] == "/dev/null"


@pytest.mark.promise("NFR-SEC-04")
def test_子程序拿不到雲端金鑰_只有白名單與_Pi_需要的變數(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        {
            "PATH": "/usr/bin:/bin",
            "ANTHROPIC_API_KEY": "sk-fake-xxxx-canary",
            "HOME": "/root",
        },
    )
    env = report["env"]
    assert isinstance(env, dict)
    assert "sk-fake-xxxx-canary" not in json.dumps(env)
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / "data" / "run" / "pi-agent")
    assert env["HOME"] == env["PI_CODING_AGENT_DIR"]


def test_子程序的工作目錄固定是根目錄(tmp_path: Path) -> None:
    # 工作目錄會出現在 Pi 送出的系統提示裡（<cwd>）；固定成 / 請求才逐位元組相同，重播對得上
    report = _run(tmp_path, {"PATH": "/usr/bin:/bin"})
    assert report["cwd"] == "/"


# --- 前一個 `ef serve` 留下的 Pi（SEAM-12） ----------------------------------------

# 假 Pi：回報提示詞指到的那個檔在不在、內容是什麼，以及自己的 pid
PID_REPORTER = """
import json, os, sys
path = sys.argv[-1]
print(json.dumps({
    "type": "report",
    "pid": os.getpid(),
    "record": open(path).read() if os.path.exists(path) else None,
}), flush=True)
"""


def test_Pi_跑的期間記著自己的程序群組編號_結束後刪掉(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    record = pid_file(data, KEY)
    report = _run(
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        script=PID_REPORTER,
        prompt=str(record),
        pid_path=record,
    )
    assert report["record"] == f"{report['pid']}\n"
    assert not record.exists()


def _sleeper(tmp_path: Path, key: str) -> subprocess.Popen[bytes]:
    """一支在自己程序群組裡睡著的程序，環境變數帶著 `EF_JOB_KEY=<key>`。

    等它真的開始跑（寫出記號檔）才回來：exec 剛完成的那一瞬間，/proc 裡還讀不到新的環境變數。
    """
    ready = tmp_path / f"ready-{len(list(tmp_path.glob('ready-*')))}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; open(sys.argv[1], 'w').close(); time.sleep(60)",
            str(ready),
        ],
        env={"PATH": "/usr/bin:/bin", "EF_JOB_KEY": key},
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline, "睡著的程序沒有起來"
        time.sleep(0.01)
    return process


def _stop(process: subprocess.Popen[bytes]) -> None:
    """測試自己開的程序自己收：還活著就殺掉，再收屍。"""
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


@pytest.mark.promise("NFR-SEC-02")
def test_還拿著這把鑰匙的舊_Pi_整個程序群組被殺掉_紀錄刪掉(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    old = _sleeper(tmp_path, str(KEY))
    try:
        record = pid_file(data, KEY)
        record.parent.mkdir(parents=True)
        record.write_text(f"{old.pid}\n", encoding="ascii")

        assert kill_leftover(data, KEY) == old.pid
        assert old.wait(timeout=10) == -signal.SIGKILL
        assert not record.exists()
    finally:
        _stop(old)


@pytest.mark.promise("NFR-SEC-02")
def test_編號已經是別的程序_環境變數裡不是這把鑰匙_不殺(tmp_path: Path) -> None:
    # pid 被重用：紀錄裡的編號現在是別人的程序（這裡是另一張單的 Pi）
    data = DataDir(tmp_path / "data")
    other = _sleeper(tmp_path, str(JobKey("20261011-120000-ffff0000", "echo", 1)))
    try:
        record = pid_file(data, KEY)
        record.parent.mkdir(parents=True)
        record.write_text(f"{other.pid}\n", encoding="ascii")

        assert kill_leftover(data, KEY) is None
        assert other.poll() is None
        assert not record.exists()
    finally:
        _stop(other)


def test_沒有紀錄_什麼都不做(tmp_path: Path) -> None:
    assert kill_leftover(DataDir(tmp_path / "data"), KEY) is None
