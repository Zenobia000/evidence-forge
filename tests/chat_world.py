"""對話主機測試共用：一個講 Pi RPC 協定的假 Pi，與等條件成立的小工具。

假 Pi 只做主機會碰到的那幾件事（rpc.md、rpc-commands.md）：
- 紀錄存在 `--session-dir/<--session-id>.jsonl`，開起來先讀回來：殺掉再開就是接回同一份紀錄。
- `prompt`：正在回答時沒帶 `streamingBehavior` 就拒絕（success: false）；帶了就排隊，回答完接著跑。
  一輪回答發 `agent_start` → 使用者訊息的 `message_end` → 助理訊息的 `message_end` → `agent_settled`。
  回答要花多久看設定的 `turn_s`（在另一條執行緒跑，stdin 照樣在讀）。
- `/ef-job-done <工單>`：擴充指令，馬上處理（正在回答也一樣）。紀錄裡已經有這張單的 `ef-job` 通知就什麼都不做；
  沒有就放一則 `custom` 訊息（`message_end` 事件），再開一輪回答（正在回答就排在後面）。
- `get_messages`：回完整紀錄；`slow_get_messages_s` 讓它晚一點回，後送的指令的回應會先到。
- stdin 關掉就結束；`ignore_eof` 時不結束（模擬卡住的 Pi，給殺孤兒的測試）。
- `stuck` 時根本不讀 stdin（模擬事件迴圈卡死的 Pi）。
- `fail_job_done`／`reject_job_done`：前幾次 `/ef-job-done` 丟錯（`extension_error`、照樣回成功）或被拒。
- `start_delay_s`：接受 prompt 之後過這麼久才 `agent_start`。
- `spawn_child`：開一個同群組的子程序（`sleep 300`），編號寫在 `--session-dir/child.pid`。

設定放在假 Pi 旁邊的 `fake-pi.json`（`fake_chat_harness(..., settings)`）：主機只把 PATH 等幾個
環境變數交給 Pi，測試的設定不能從環境變數進去。
- 收到的每個指令記在 `--session-dir/commands.jsonl`，給測試檢查。
"""

import json
import stat
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from evidence_forge.harness import Harness

FAKE_RPC_PI = r'''
import json, os, sys, threading, time

argv = sys.argv
session_dir = argv[argv.index("--session-dir") + 1]
session_id = argv[argv.index("--session-id") + 1]
os.makedirs(session_dir, exist_ok=True)
session_path = os.path.join(session_dir, session_id + ".jsonl")
commands_path = os.path.join(session_dir, "commands.jsonl")
settings_path = os.path.join(os.path.dirname(os.path.abspath(argv[0])), "fake-pi.json")
settings = json.load(open(settings_path)) if os.path.exists(settings_path) else {}
turn_s = float(settings.get("turn_s", 0))
slow_get = float(settings.get("slow_get_messages_s", 0))

out_lock = threading.Lock()
state_lock = threading.Lock()
messages = []
if os.path.exists(session_path):
    with open(session_path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                messages.append(json.loads(line))
busy = False
queue = []
# 前幾次 /ef-job-done 故意失敗：丟錯（extension_error、照樣回成功）或被拒（success: false）
failures = {"error": int(settings.get("fail_job_done", 0)), "reject": int(settings.get("reject_job_done", 0))}
if settings.get("spawn_child"):
    # Pi 開的子程序：跟 Pi 同一個程序群組、繼承它的環境變數
    import subprocess
    child = subprocess.Popen(["sleep", "300"])
    with open(os.path.join(session_dir, "child.pid"), "w") as handle_child:
        handle_child.write(str(child.pid))


def emit(record):
    with out_lock:
        sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def keep(message):
    messages.append(message)
    with open(session_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    emit({"type": "message_start", "message": message})
    emit({"type": "message_end", "message": message})


def run_turns(first):
    global busy
    item = first
    while item is not None:
        # Pi 接受了 prompt、還沒 agent_start 的空檔（例如還在準備這一輪）
        if settings.get("start_delay_s"):
            time.sleep(float(settings["start_delay_s"]))
        emit({"type": "agent_start"})
        if item["kind"] == "user":
            keep({"role": "user", "content": item["text"]})
            reply = "回覆：" + item["text"]
        else:
            reply = "收到工單 " + item["job_id"]
        if turn_s:
            time.sleep(turn_s)
        keep({"role": "assistant", "content": [{"type": "text", "text": reply}]})
        emit({"type": "agent_end", "messages": []})
        with state_lock:
            item = queue.pop(0) if queue else None
            if item is None:
                busy = False
        emit({"type": "queue_update", "steering": [], "followUp": [q.get("text", "") for q in queue]})
    emit({"type": "agent_settled", "aborted": False})


def start_or_queue(item):
    global busy
    with state_lock:
        if busy:
            queue.append(item)
            return "queued"
        busy = True
    threading.Thread(target=run_turns, args=(item,), daemon=True).start()
    return "started"


def respond(command, success=True, data=None, error=None):
    record = {"type": "response", "command": command.get("type"), "success": success}
    if "id" in command:
        record["id"] = command["id"]
    if data is not None:
        record["data"] = data
    if error is not None:
        record["error"] = error
    emit(record)


def handle(command):
    with open(commands_path, "a", encoding="utf-8") as handle_:
        handle_.write(json.dumps(command, ensure_ascii=False) + "\n")
    kind = command.get("type")
    if kind == "get_messages":
        def later():
            time.sleep(slow_get)
            respond(command, data={"messages": list(messages)})
        threading.Thread(target=later, daemon=True).start()
        return
    if kind == "get_state":
        respond(command, data={"isStreaming": busy, "sessionId": session_id})
        return
    if kind != "prompt":
        respond(command, success=False, error="unknown command")
        return
    text = command.get("message", "")
    if text.startswith("/ef-job-done "):
        job_id = text.split(" ", 1)[1].strip()
        if failures["reject"] > 0:
            failures["reject"] -= 1
            respond(command, success=False, error="command rejected")
            return
        if failures["error"] > 0:
            failures["error"] -= 1
            # 跟真的 Pi 一樣：處理函式丟錯時發 extension_error，然後照樣回成功
            emit({"type": "extension_error", "extensionPath": "command:ef-job-done",
                  "event": "command", "error": "gateway returned HTTP 503"})
            respond(command, data={"disposition": "handled"})
            return
        already = any(
            m.get("role") == "custom"
            and m.get("customType") == "ef-job"
            and (m.get("details") or {}).get("job_id") == job_id
            for m in messages
        )
        if not already:
            keep({"role": "custom", "customType": "ef-job", "content": "[ef-job] " + job_id,
                  "display": True, "details": {"job_id": job_id}})
            start_or_queue({"kind": "job", "job_id": job_id})
        respond(command, data={"disposition": "handled"})
        return
    with state_lock:
        streaming = busy
    if streaming and not command.get("streamingBehavior"):
        respond(command, success=False, error="Agent is already processing. Specify streamingBehavior")
        return
    respond(command, data={"disposition": start_or_queue({"kind": "user", "text": text})})


if settings.get("stuck"):
    # 卡住的 Pi：不讀 stdin，管線塞滿之後寫的人會卡住
    while True:
        time.sleep(1)

for raw in sys.stdin.buffer:
    try:
        command = json.loads(raw)
    except ValueError:
        continue
    handle(command)

if settings.get("ignore_eof"):
    while True:
        time.sleep(1)
'''


def fake_chat_harness(root: Path, settings: Mapping[str, Any] | None = None) -> Harness:
    """`harness/pi/node_modules/.bin/pi` 換成假 Pi；系統提示與 ef-tools 只要檔案存在。"""
    harness = Harness.at(root)
    harness.pi.pi_bin.parent.mkdir(parents=True, exist_ok=True)
    (harness.pi.pi_bin.parent / "fake-pi.json").write_text(
        json.dumps(dict(settings or {})), encoding="utf-8"
    )
    harness.pi.pi_bin.write_text(f"#!{sys.executable}\n{FAKE_RPC_PI}", encoding="utf-8")
    harness.pi.pi_bin.chmod(harness.pi.pi_bin.stat().st_mode | stat.S_IXUSR)
    (root / "pi" / "chat-system-prompt.md").write_text("test\n", encoding="utf-8")
    harness.pi.ef_tools.parent.mkdir(parents=True, exist_ok=True)
    harness.pi.ef_tools.write_text("// test\n", encoding="utf-8")
    return harness


def wait_until(check: Callable[[], bool], timeout_s: float = 10.0, what: str = "") -> None:
    """等條件成立；逾時就失敗。等的是事件真的發生，不是拿時間當斷言。"""
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"等了 {timeout_s} 秒還沒成立：{what}")
        time.sleep(0.02)


def pid_alive(pid: int) -> bool:
    """這個程序還在（殭屍不算）。"""
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except OSError:
        return False
    return "State:\tZ" not in status
