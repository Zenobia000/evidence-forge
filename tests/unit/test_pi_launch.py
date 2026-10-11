"""SEAM-01（單元）：執行器組給 Pi 的指令列與環境變數。

每個安全旗標少一個就紅；環境變數只帶白名單，使用者的雲端金鑰與 `~/.pi` 都進不來。
"""

import json
from pathlib import Path

import pytest

from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir
from evidence_forge.runner.pi_launch import (
    PiHarness,
    build_pi_command,
    prepare_agent_dir,
)

KEY = JobKey(job_id="20261011-120000-abcd1234", stage="echo", attempt=1)


def _harness(tmp_path: Path) -> PiHarness:
    pi_dir = tmp_path / "harness" / "pi"
    (pi_dir / "agent").mkdir(parents=True)
    (pi_dir / "agent" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "ef": {
                        "baseUrl": "http://127.0.0.1:0",
                        "api": "anthropic-messages",
                        "apiKey": "placeholder",
                        "models": [{"id": "local", "reasoning": True}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return PiHarness(
        pi_bin=pi_dir / "node_modules" / ".bin" / "pi",
        system_prompt=pi_dir / "system-prompt.md",
        ef_tools=pi_dir / "extensions" / "ef-tools" / "index.ts",
        agent_template=pi_dir / "agent",
    )


def _command(
    tmp_path: Path, base_env: dict[str, str] | None = None
) -> tuple[list[str], dict[str, str], DataDir]:
    data = DataDir(tmp_path / "data")
    cmd = build_pi_command(
        harness=_harness(tmp_path),
        data=data,
        key=KEY,
        tier="local",
        thinking="off",
        skill_file=tmp_path / "skills" / "echo" / "SKILL.md",
        gateway_url="http://127.0.0.1:8787",
        prompt="做這一段",
        base_env=base_env if base_env is not None else {"PATH": "/usr/bin"},
    )
    return list(cmd.argv), dict(cmd.env), data


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "flag",
    ["-nc", "--no-builtin-tools", "--no-mcp", "-na", "-ne", "--no-prompt-templates"],
)
def test_關掉內建工具_MCP_自動擴充_提示範本與上層說明檔(
    tmp_path: Path, flag: str
) -> None:
    argv, _, _ = _command(tmp_path)
    assert flag in argv


@pytest.mark.promise("NFR-SEC-02")
def test_只用_e_載入_ef_tools(tmp_path: Path) -> None:
    argv, _, _ = _command(tmp_path)
    extensions = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-e"]
    assert extensions == [
        str(tmp_path / "harness" / "pi" / "extensions" / "ef-tools" / "index.ts")
    ]


def _value_after(argv: list[str], flag: str) -> str:
    assert argv.count(flag) == 1, f"{flag} 要剛好出現一次"
    return argv[argv.index(flag) + 1]


@pytest.mark.promise("NFR-SEC-04")
def test_金鑰欄位帶的是工單鑰匙(tmp_path: Path) -> None:
    argv, _, _ = _command(tmp_path)
    assert _value_after(argv, "--api-key") == "job:20261011-120000-abcd1234:echo:1"


def test_JSON_事件模式_明確的_thinking_與等級別名(tmp_path: Path) -> None:
    argv, _, _ = _command(tmp_path)
    assert _value_after(argv, "--mode") == "json"
    assert _value_after(argv, "--thinking") == "off"
    assert _value_after(argv, "--model") == "ef/local"
    assert "--print" in argv
    assert "--no-session" in argv


@pytest.mark.promise("NFR-SEC-02")
def test_只把這段的_SKILL_md_接在我們的系統提示後面_不用_Pi_的_skill_機制(
    tmp_path: Path,
) -> None:
    # ADR-0018：Pi 1.1.0 換掉系統提示又沒有內建讀檔工具時，--skill 帶不進任何內容
    argv, _, _ = _command(tmp_path)
    assert "--no-skills" in argv
    assert "--skill" not in argv
    assert _value_after(argv, "--system-prompt") == str(
        tmp_path / "harness" / "pi" / "system-prompt.md"
    )
    assert _value_after(argv, "--append-system-prompt") == str(
        tmp_path / "skills" / "echo" / "SKILL.md"
    )


def test_任務說明放在最後_前面用雙橫線隔開(tmp_path: Path) -> None:
    argv, _, _ = _command(tmp_path)
    assert argv[-2:] == ["--", "做這一段"]


@pytest.mark.promise("NFR-SEC-02")
def test_Pi_設定資料夾指到資料目錄的_run_pi_agent(tmp_path: Path) -> None:
    _, env, data = _command(tmp_path)
    assert env["PI_CODING_AGENT_DIR"] == str(data.root / "run" / "pi-agent")


@pytest.mark.promise("NFR-SEC-04")
def test_環境變數只帶白名單_雲端金鑰不交給_Pi(tmp_path: Path) -> None:
    _, env, _ = _command(
        tmp_path,
        base_env={
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-fake-xxxx-canary",
            "OPENAI_API_KEY": "sk-canary",
            "HOME": "/home/someone",
        },
    )
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "sk-fake-xxxx-canary" not in json.dumps(env)
    # HOME 也換掉，Pi 找不到使用者家目錄裡的設定與 OAuth
    assert env["HOME"] != "/home/someone"
    assert env["PATH"] == "/usr/bin"


@pytest.mark.promise("NFR-SEC-02")
def test_stdin_接_dev_null(tmp_path: Path) -> None:
    # stdin 開著的話 Pi 會一直等著讀，把讀到的東西接在任務說明前面（#10）
    cmd = build_pi_command(
        harness=_harness(tmp_path),
        data=DataDir(tmp_path / "data"),
        key=KEY,
        tier="local",
        thinking="off",
        skill_file=tmp_path / "SKILL.md",
        gateway_url="http://127.0.0.1:8787",
        prompt="做這一段",
        base_env={},
    )
    assert cmd.stdin == Path("/dev/null")


def test_ef_tools_從環境變數拿到閘道位址與鑰匙(tmp_path: Path) -> None:
    _, env, _ = _command(tmp_path)
    assert env["EF_GATEWAY_URL"] == "http://127.0.0.1:8787"
    assert env["EF_JOB_KEY"] == "job:20261011-120000-abcd1234:echo:1"


def test_準備_Pi_設定資料夾_把閘道位址填進範本(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    agent_dir = prepare_agent_dir(_harness(tmp_path), data, "http://127.0.0.1:8787")
    models = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    assert agent_dir == data.root / "run" / "pi-agent"
    assert models["providers"]["ef"]["baseUrl"] == "http://127.0.0.1:8787"
    # 範本本身不動
    template = tmp_path / "harness" / "pi" / "agent" / "models.json"
    assert "127.0.0.1:0" in template.read_text(encoding="utf-8")
