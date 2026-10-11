"""帳本的讀寫：每次呼叫一行，只有閘道寫，只增不改（v0.3 §4.4、FR-LG-01）。

格式在 `ledger_models.py`（中立模組，帳單與 UI 也讀）；這裡負責寫進檔案、
從回應讀用量、做重播索引。重播模式也從 `ledger.jsonl` 讀錄音。
"""

import json
import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from evidence_forge.ledger_models import IntakeEntry, LedgerEntry, LedgerStatus
from evidence_forge.paths import DataDir

_log = logging.getLogger(__name__)

__all__ = [
    "IntakeEntry",
    "LedgerEntry",
    "LedgerStatus",
    "Reply",
    "Usage",
    "append",
    "append_intake",
    "read",
    "read_reply",
    "responses_by_call",
]

_lock = threading.Lock()


def _append_line(path: Path, line: str) -> None:
    """一行用一次 `O_APPEND` 的 write 寫完。

    `ef new`（另一個程序）也會寫投入帳本；每行一次 write、檔案用 O_APPEND 開，
    兩個程序同時寫也不會把兩行交錯成一行。同一個程序裡的執行緒另外上鎖。

    檔尾不是換行，表示上一個寫的程序在一行的中間被砍了（或只寫進一部分）：
    先補一個換行把那半行封起來，這一筆才不會黏在它後面、跟著一起解析失敗而少算錢。
    半行本身讀的時候跳過（`read`、`gateway/budget.py`）。兩個程序剛好同時補，
    最多多出一行空行，讀的時候也跳過。
    """
    data = line.encode("utf-8")
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size > 0 and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data
            written = os.write(fd, data)
        finally:
            os.close(fd)
    if written != len(data):
        raise OSError(f"帳本只寫進 {written}／{len(data)} bytes：{path}")


def append(job_dir: Path, entry: LedgerEntry) -> None:
    """在 `jobs/<id>/ledger.jsonl` 加一行。"""
    _append_line(job_dir / "ledger.jsonl", entry.model_dump_json() + "\n")


def append_intake(data: DataDir, entry: IntakeEntry) -> None:
    """在 `$EF_DATA/ledger/intake.jsonl` 加一行（投入時的抓取，S0.5 用）。

    月上限的加總會讀這份檔（`gateway/budget.py`），寫進來的金額下一次預留時就算進去。
    """
    data.intake_ledger.parent.mkdir(parents=True, exist_ok=True)
    _append_line(data.intake_ledger, entry.model_dump_json() + "\n")


def read(path: Path) -> list[LedgerEntry]:
    """讀一份工單帳本。讀不懂的行（例如寫到一半被砍的半行）跳過，記一筆警告指出 `檔名:行號`。

    一行壞掉不能讓整本帳讀不出來；用位元組讀，半行切在中文字中間也只壞那一行。
    """
    if not path.is_file():
        return []
    entries: list[LedgerEntry] = []
    for number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(LedgerEntry.model_validate_json(line))
        except ValueError:
            _log.warning("帳本 %s:%d 這一行讀不懂，跳過", path, number)
    return entries


@dataclass(frozen=True)
class Usage:
    """一次模型呼叫的 token 用量（Anthropic 的 usage 欄位）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and value >= 0


def _count(usage: dict[str, object], name: str, default: int) -> int:
    value = usage.get(name)
    return value if _is_count(value) else default


def _has_input(fields: object) -> bool:
    """這份 usage 有沒有寫輸入 token 數。沒有的話，後端實際算了多少錢無從得知。"""
    return isinstance(fields, dict) and _is_count(fields.get("input_tokens"))


def _merge(current: Usage, fields: dict[str, object]) -> Usage:
    """Anthropic 的 usage 欄位蓋過目前的數字；沒帶的欄位留著原值。"""
    return Usage(
        input_tokens=_count(fields, "input_tokens", current.input_tokens),
        output_tokens=_count(fields, "output_tokens", current.output_tokens),
        cache_read_tokens=_count(
            fields, "cache_read_input_tokens", current.cache_read_tokens
        ),
        cache_write_tokens=_count(
            fields, "cache_creation_input_tokens", current.cache_write_tokens
        ),
    )


@dataclass(frozen=True)
class Reply:
    """一份回應裡結算要知道的事。"""

    # 讀到的用量；讀不到的欄位是 0
    usage: Usage
    # 是不是一則完整的訊息：讀到開頭（message_start，它的 usage 寫了輸入 token 數）、
    # 收到結尾（message_stop）、中間沒有 error 事件（不是串流的回應：是一則 message，
    # 最上層的 usage 寫了輸入 token 數）。不是的話，結算不知道後端實際算了多少錢，
    # 也不能把它當成功的回應回放
    whole: bool
    # 串流裡 error 事件寫的原因（`overloaded_error：Overloaded`；沒寫細節就是 `error`）；
    # 沒有 error 事件是 None。這是後端自己說它出錯了，閘道據此回報後端故障（ADR-0011 §3.3）
    error: str | None = None


# error 事件的原因最多留這麼長：它會進待審原因與事件紀錄
_ERROR_TEXT_LIMIT = 200


def _error_text(event: dict[str, object]) -> str:
    """error 事件的 `error.type` 與 `error.message`；兩個都沒有就是 `error`。"""
    detail = event.get("error")
    if not isinstance(detail, dict):
        return "error"
    parts = [str(detail[name]) for name in ("type", "message") if detail.get(name)]
    return "：".join(parts)[:_ERROR_TEXT_LIMIT] or "error"


def _read_json_message(text: str) -> Reply:
    """不是串流的回應：整則訊息一次回來，用量在最上層的 `usage`。"""
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return Reply(Usage(), whole=False)
    if not isinstance(message, dict) or message.get("type") != "message":
        return Reply(Usage(), whole=False)
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return Reply(Usage(), whole=False)
    return Reply(_merge(Usage(), usage), whole=_has_input(usage))


def read_reply(body: bytes) -> Reply:
    """讀後端的回應：Anthropic 格式的串流（SSE），或不是串流的一整則 JSON 訊息。

    串流：`message_start` 帶輸入與快取的 token；`message_delta` 帶累計的輸出 token，
    有時也帶更新過的輸入與快取數字，有帶就以它為準。中途斷掉的串流讀到哪算到哪。
    HTTP 200 的串流也可能在中途送 `event: error`（後端過載時常見做法），或沒送到
    `message_stop` 就結束；那都不是一則完整的訊息，只看狀態碼會把它當成功。
    """
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not any(line.startswith(("data:", "event:")) for line in lines):
        return _read_json_message(text)
    usage = Usage()
    started = stopped = False
    error: str | None = None
    # 上一行是 `event: error`：這一行 data 是它的內容，就算 data 裡沒寫 type
    after_error_line = False
    for line in lines:
        if line.startswith("event:"):
            after_error_line = line[len("event:") :].strip() == "error"
            if after_error_line and error is None:
                error = "error"
            continue
        if not line.startswith("data:"):
            continue
        follows_error, after_error_line = after_error_line, False
        try:
            event = json.loads(line[len("data:") :])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "error" or follows_error:
            # 細節在 data 這一行；先前只從 event 行知道「有錯」的，換成有細節的原因
            if error is None or error == "error":
                error = _error_text(event)
            continue
        if kind == "message_stop":
            stopped = True
            continue
        if kind == "message_start":
            message = event.get("message")
            fields = message.get("usage") if isinstance(message, dict) else None
        elif kind == "message_delta":
            fields = event.get("usage")
        else:
            continue
        if not isinstance(fields, dict):
            continue
        usage = _merge(usage, fields)
        started |= kind == "message_start" and _has_input(fields)
    return Reply(usage, whole=started and stopped and error is None, error=error)


CallKey = tuple[str, int, str]


def responses_by_call(
    entries: Iterable[LedgerEntry], statuses: tuple[LedgerStatus, ...]
) -> dict[CallKey, list[str]]:
    """（段, 次, 請求 hash）→ 照帳本順序排的回應 hash。只收狀態在 `statuses` 裡的呼叫。

    同一段同一次可能送出兩個一模一樣的請求、拿到不同的回應；用清單保留順序，
    第 n 次相同的請求回放第 n 份，不會兩次都拿到後面那一份。
    出錯的呼叫也可能存了完整的回應本文（例如後端回 500），
    但那不能當成功的回應交給 Pi，所以照狀態挑。
    """
    index: dict[CallKey, list[str]] = {}
    for entry in entries:
        if (
            entry.kind == "llm"
            and entry.status in statuses
            and entry.response_hash is not None
        ):
            key = (entry.stage, entry.attempt, entry.request_hash)
            index.setdefault(key, []).append(entry.response_hash)
    return index
