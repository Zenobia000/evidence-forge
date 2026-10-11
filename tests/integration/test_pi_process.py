"""SEAM-01（整合）：`run_pi` 真的開出子程序時，子程序看到的 stdin、環境變數與工作目錄。

單元測試鎖住 `build_pi_command` 組出的值；這裡確認那些值真的交到子程序手上。
用一支假的 Pi（Python 小程式）回報它看到的東西，不需要 Node。
"""

import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir
from evidence_forge.runner.pi_launch import PiHarness, build_pi_command, run_pi

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


def _run(tmp_path: Path, base_env: dict[str, str]) -> dict[str, object]:
    fake = tmp_path / "fake-pi"
    fake.write_text(f"#!{sys.executable}\n{REPORTER}", encoding="utf-8")
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
        key=JobKey("20261011-120000-abcd1234", "echo", 1),
        tier="local",
        thinking="off",
        skill_file=tmp_path / "SKILL.md",
        gateway_url="http://127.0.0.1:8787",
        prompt="做這一段",
        base_env=base_env,
    )
    result = run_pi(command, log_path=data.run / "pi-logs" / "x.jsonl", timeout_s=60)
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
            "ANTHROPIC_API_KEY": "sk-ant-canary",
            "HOME": "/root",
        },
    )
    env = report["env"]
    assert isinstance(env, dict)
    assert "sk-ant-canary" not in json.dumps(env)
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / "data" / "run" / "pi-agent")
    assert env["HOME"] == env["PI_CODING_AGENT_DIR"]


def test_子程序的工作目錄固定是根目錄(tmp_path: Path) -> None:
    # 工作目錄會出現在 Pi 送出的系統提示裡（<cwd>）；固定成 / 請求才逐位元組相同，重播對得上
    report = _run(tmp_path, {"PATH": "/usr/bin:/bin"})
    assert report["cwd"] == "/"
