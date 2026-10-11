"""帳本 `jobs/<id>/ledger.jsonl`：每次呼叫一行，只有閘道寫，只增不改（v0.3 §4.4）。

S0.3 只寫打通需要的欄位（段、次、模型、token、延遲、狀態、hash）；
預算、金額、GPU 秒數、排隊時間在 S0.4 補上。重播模式也從這份格式讀錄音。
"""

import json
import threading
from collections.abc import Iterable
from pathlib import Path

from evidence_forge.models import LedgerEntry

_lock = threading.Lock()


def append(job_dir: Path, entry: LedgerEntry) -> None:
    line = entry.model_dump_json(exclude_none=False) + "\n"
    with _lock, (job_dir / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line)


def read(path: Path) -> list[LedgerEntry]:
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def usage_from_sse(stream: bytes) -> tuple[int, int]:
    """從 Anthropic 格式的串流讀出輸入與輸出 token 數；讀不到就是 0。"""
    input_tokens = output_tokens = 0
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
            usage = event.get("message", {}).get("usage", {})
            input_tokens = int(usage.get("input_tokens", 0) or 0)
        elif event.get("type") == "message_delta":
            usage = event.get("usage", {})
            output_tokens = int(usage.get("output_tokens", output_tokens) or 0)
    return input_tokens, output_tokens


def index_by_call(entries: Iterable[LedgerEntry]) -> dict[tuple[str, int, str], str]:
    """（段, 次, 請求 hash）→ 回應 hash。只收成功的呼叫。

    出錯的呼叫也可能存了完整的回應本文（例如後端回 500），
    但那不能當成功的回應重播給 Pi，所以照狀態挑。
    """
    return {
        (entry.stage, entry.attempt, entry.request_hash): entry.response_hash
        for entry in entries
        if entry.kind == "llm"
        and entry.status in ("ok", "cache_hit")
        and entry.response_hash is not None
    }
