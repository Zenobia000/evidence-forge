"""一個對話的 Pi 程序（`chat/pi_rpc.py`）：回應照 id 對、結束就撤銷鑰匙、不留孤兒。

Pi 是講 RPC 協定的假 Pi（`tests/chat_world.py`），不需要 Node、不打模型。
"""

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from chat_world import fake_chat_harness, pid_alive, wait_until

from evidence_forge.chat.pi_rpc import (
    PiExited,
    PiProcess,
    build_rpc_command,
    kill_leftover,
    pid_file,
)
from evidence_forge.convkey import ConversationKey
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.paths import DataDir


class Started:
    def __init__(self, tmp_path: Path, settings: dict[str, Any] | None = None) -> None:
        self.data = DataDir(tmp_path / "data")
        self.harness = fake_chat_harness(tmp_path / "harness", settings)
        self.stages = ActiveStages()
        self.events: list[dict[str, Any]] = []
        self.exits: list[int] = []
        self.exited = threading.Event()

    def open(self, launch: int = 1) -> PiProcess:
        key = ConversationKey("c1", launch)
        command = build_rpc_command(
            harness=self.harness,
            data=self.data,
            key=key,
            gateway_url="http://127.0.0.1:9",
            base_env=os.environ,
        )

        def exited(code: int) -> None:
            self.exits.append(code)
            self.exited.set()

        pi = PiProcess(
            command,
            key,
            self.stages,
            on_event=self.events.append,
            on_exit=exited,
            stderr_path=self.data.run / "chat" / "c1.stderr",
            pid_path=pid_file(self.data, "c1"),
        )
        pi.start()
        return pi


def test_回應照_id_對_不照回來的順序(tmp_path: Path) -> None:
    world = Started(tmp_path, {"slow_get_messages_s": 0.5})
    pi = world.open()
    try:
        results: dict[str, Any] = {}

        def slow() -> None:
            results["messages"] = pi.request({"type": "get_messages"})

        thread = threading.Thread(target=slow)
        thread.start()
        # get_messages 晚 0.5 秒才回；後送的 get_state 先回來，不能被當成 get_messages 的回應
        state = pi.request({"type": "get_state"})
        thread.join(5)
        assert state["command"] == "get_state"
        assert results["messages"]["command"] == "get_messages"
        assert results["messages"]["data"] == {"messages": []}
        # 有人在等的回應不會再當成事件送出去
        assert not [e for e in world.events if e.get("type") == "response"]
    finally:
        pi.stop()


def test_關掉之後鑰匙撤銷_程序結束_送指令丟_PiExited(tmp_path: Path) -> None:
    world = Started(tmp_path)
    pi = world.open()
    assert world.stages.is_active(pi.key)
    pid = pi.pid
    assert pid is not None and pid_file(world.data, "c1").is_file()

    pi.stop()

    assert not world.stages.is_active(pi.key)
    assert not pid_alive(pid)
    assert world.exits == [0]
    assert not pid_file(world.data, "c1").exists()
    with pytest.raises(PiExited):
        pi.send({"type": "get_state"})


def test_Pi_自己死掉_等回應的人馬上醒來_鑰匙撤銷(tmp_path: Path) -> None:
    world = Started(tmp_path, {"slow_get_messages_s": 30})
    pi = world.open()
    errors: list[BaseException] = []

    def wait_forever() -> None:
        try:
            pi.request({"type": "get_messages"}, timeout_s=30)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=wait_forever)
    thread.start()
    wait_until(lambda: (world.data.conversation_dir("c1") / "pi-session" / "commands.jsonl").is_file())
    pi.kill()
    thread.join(5)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], PiExited)
    world.exited.wait(5)
    assert not world.stages.is_active(pi.key)


def test_關不掉的_Pi_整個程序群組被殺(tmp_path: Path) -> None:
    world = Started(tmp_path, {"ignore_eof": True})
    pi = world.open()
    pid = pi.pid
    assert pid is not None
    pi.stop(wait_s=0.3)
    assert not pid_alive(pid)
    assert not world.stages.is_active(pi.key)


def test_主機被硬殺留下的_Pi_下次開之前被殺掉_別人的程序不碰(tmp_path: Path) -> None:
    world = Started(tmp_path, {"ignore_eof": True})
    pi = world.open()
    pid = pi.pid
    assert pid is not None
    # 模擬主機被硬殺：Pi 還活著、程序編號紀錄還在
    record = pid_file(world.data, "c1")
    assert record.read_text().strip() == str(pid)

    assert kill_leftover(world.data, "c1") == pid
    # 環境變數讀不到時記憶體已經放掉、不會再送請求；程序表上的那一格稍後才收掉
    wait_until(lambda: not pid_alive(pid), what="留下的 Pi 結束")
    assert not record.exists()

    # 紀錄裡的編號已經是別人的程序（pid 重用）：不殺
    other = subprocess.Popen(["sleep", "30"])
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(f"{other.pid}\n")
        assert kill_leftover(world.data, "c1") is None
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()
    pi.join()


def test_卡死的_Pi_寫到一半也關得掉(tmp_path: Path) -> None:
    # 回歸（審查必修）：Pi 不讀 stdin 時，送大訊息的人卡在寫入、拿著寫入鎖；
    # 關掉的人不能跟著卡住，要直接殺整個程序群組
    world = Started(tmp_path, {"stuck": True})
    pi = world.open()
    pid = pi.pid
    assert pid is not None
    errors: list[BaseException] = []

    def write_big() -> None:
        try:
            pi.send({"type": "prompt", "message": "長" * 1_000_000})
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=write_big, daemon=True)
    writer.start()
    wait_until(lambda: pi._write_lock.locked(), what="寫入卡住")

    stopper = threading.Thread(target=lambda: pi.stop(wait_s=0.3), daemon=True)
    stopper.start()
    stopper.join(10)
    assert not stopper.is_alive()
    assert not pid_alive(pid)
    writer.join(5)
    assert errors and isinstance(errors[0], PiExited)
    assert not world.stages.is_active(pi.key)
