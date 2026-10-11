"""帳本的讀寫：每次呼叫一行，只有閘道寫，只增不改（v0.3 §4.4、FR-LG-01）。

格式在 `ledger_models.py`（中立模組，帳單與 UI 也讀）；這裡負責寫進檔案、
從串流讀用量、做重播索引。重播模式也從 `ledger.jsonl` 讀錄音。
"""

import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from evidence_forge.ledger_models import IntakeEntry, LedgerEntry, LedgerStatus
from evidence_forge.paths import DataDir

__all__ = [
    "IntakeEntry",
    "LedgerEntry",
    "LedgerStatus",
    "Usage",
    "append",
    "append_intake",
    "read",
    "responses_by_call",
    "usage_from_sse",
]

_lock = threading.Lock()


def _append_line(path: Path, line: str) -> None:
    """一行用一次 `O_APPEND` 的 write 寫完。

    `ef new`（另一個程序）也會寫投入帳本；每行一次 write、檔案用 O_APPEND 開，
    兩個程序同時寫也不會把兩行交錯成一行。同一個程序裡的執行緒另外上鎖。
    """
    data = line.encode("utf-8")
    with _lock:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
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
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@dataclass(frozen=True)
class Usage:
    """一次模型呼叫的 token 用量（Anthropic 的 usage 欄位）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _count(usage: dict[str, object], name: str, default: int) -> int:
    value = usage.get(name)
    return value if isinstance(value, int) and value >= 0 else default


def usage_from_sse(stream: bytes) -> Usage:
    """從 Anthropic 格式的串流讀出用量；讀不到的欄位是 0。

    `message_start` 帶輸入與快取的 token；`message_delta` 帶累計的輸出 token，
    有時也帶更新過的輸入與快取數字，有帶就以它為準。中途斷掉的串流讀到哪算到哪。
    """
    counts = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for line in stream.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[len("data:") :])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "message_start":
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        elif event.get("type") == "message_delta":
            usage = event.get("usage")
        else:
            continue
        if not isinstance(usage, dict):
            continue
        counts["input"] = _count(usage, "input_tokens", counts["input"])
        counts["output"] = _count(usage, "output_tokens", counts["output"])
        counts["cache_read"] = _count(
            usage, "cache_read_input_tokens", counts["cache_read"]
        )
        counts["cache_write"] = _count(
            usage, "cache_creation_input_tokens", counts["cache_write"]
        )
    return Usage(
        input_tokens=counts["input"],
        output_tokens=counts["output"],
        cache_read_tokens=counts["cache_read"],
        cache_write_tokens=counts["cache_write"],
    )


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
