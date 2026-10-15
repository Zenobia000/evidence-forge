"""對話的 Pi 指令列（#85，從 #80 spike 搬來）：沿用段的封鎖旗標，只多 RPC 與接回紀錄需要的那幾個。"""

from pathlib import Path

from evidence_forge.chat.pi_rpc import build_rpc_command
from evidence_forge.convkey import ConversationKey
from evidence_forge.harness import Harness
from evidence_forge.paths import DataDir

# 段的 Pi 也有的封鎖旗標（runner/pi_launch.py 模組說明）；少一個 agent 就多一種能力
LOCKDOWN = ("-nc", "--no-builtin-tools", "--no-mcp", "-na", "-ne", "--no-prompt-templates", "--no-skills", "--offline")


def _command(tmp_path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    command = build_rpc_command(
        harness=Harness.at(tmp_path / "harness"),
        data=DataDir(tmp_path / "data"),
        key=ConversationKey("c1", 3),
        gateway_url="http://127.0.0.1:9",
        base_env={"PATH": "/bin", "ANTHROPIC_API_KEY": "secret", "HOME": "/home/me"},
    )
    return command.argv, dict(command.env)


def test_封鎖旗標一個都不少(tmp_path: Path) -> None:
    argv, _ = _command(tmp_path)
    for flag in LOCKDOWN:
        assert flag in argv, flag
    assert "--print" not in argv and "--no-session" not in argv


def test_rpc_模式_接回同一份紀錄(tmp_path: Path) -> None:
    argv, _ = _command(tmp_path)
    assert argv[argv.index("--mode") + 1] == "rpc"
    assert argv[argv.index("--session-id") + 1] == "c1"
    assert argv[argv.index("--session-dir") + 1] == str(
        tmp_path / "data" / "conversations" / "c1" / "pi-session"
    )
    assert argv[argv.index("--api-key") + 1] == "conv:c1:3"


def test_環境變數不帶金鑰_只開對話的工具(tmp_path: Path) -> None:
    _, env = _command(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env
    assert env["HOME"] != "/home/me"
    assert env["EF_TOOLSET"] == "conversation"
    assert env["EF_JOB_KEY"] == "conv:c1:3"
