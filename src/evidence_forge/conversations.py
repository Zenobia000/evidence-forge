"""對話資料夾（#80 spike；位置與規則的提案見 ADR-0021 草稿）。

```
$EF_DATA/conversations/<對話 id>/
  conversation.json   對話的設定：標題、建立時間、預算（只有聊天主機寫）
  jobs.jsonl          這個對話開過的工單，一行一張（只有閘道寫，只增不改）
  ledger.jsonl        對話本身的模型呼叫（只有閘道寫，只增不改）
  pi-session/         Pi 的對話紀錄（只有 Pi 寫；`--session-dir`，session id 就是對話 id）
```

工單本身仍在 `jobs/<工單 id>/`，不搬進對話資料夾：執行器、帳單與既有工具都照舊找得到。
「這張工單屬於哪個對話」的單一真相是 `jobs.jsonl`；對話的工具只能碰列在這裡的工單。
"""

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeFloat,
    NonNegativeInt,
)

from evidence_forge.jobkey import check_name
from evidence_forge.paths import DataDir

DEFAULT_BUDGET_USD = 1.0


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    created_at: AwareDatetime
    # 對話本身的模型呼叫（追問、開工、讀報告）的美元上限；工單各自有自己的預算
    budget_usd: NonNegativeFloat = DEFAULT_BUDGET_USD
    # 開過幾次 Pi：每開一次加一，對話鑰匙是 conv:<id>:<這個數>
    launches: NonNegativeInt = 0


def _meta(data: DataDir, conversation_id: str) -> Path:
    check_name("對話 id", conversation_id)
    return data.conversation_dir(conversation_id) / "conversation.json"


def new_id(now: datetime) -> str:
    """`c-<日期時間>-<隨機>`：照時間排得出先後，又不會兩個同時開的撞在一起。"""
    return f"c-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def create(data: DataDir, now: datetime, title: str = "新對話") -> Conversation:
    conversation = Conversation(id=new_id(now), title=title, created_at=now)
    path = _meta(data, conversation.id)
    path.parent.mkdir(parents=True, exist_ok=False)
    save(data, conversation)
    return conversation


def save(data: DataDir, conversation: Conversation) -> None:
    path = _meta(data, conversation.id)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(conversation.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read(data: DataDir, conversation_id: str) -> Conversation:
    return Conversation.model_validate_json(
        _meta(data, conversation_id).read_text(encoding="utf-8")
    )


def list_all(data: DataDir) -> list[Conversation]:
    """所有對話，最近開的在上面。讀不懂的資料夾略過。"""
    found: list[Conversation] = []
    if not data.conversations.is_dir():
        return found
    for path in data.conversations.glob("*/conversation.json"):
        try:
            found.append(Conversation.model_validate_json(path.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda c: c.created_at, reverse=True)


def budget_usd(data: DataDir, conversation_id: str) -> float:
    """對話的預算；讀不到就是 0（雲端呼叫一律不放行，跟工單一樣）。"""
    try:
        return read(data, conversation_id).budget_usd
    except (OSError, ValueError):
        return 0.0


def _jobs_file(data: DataDir, conversation_id: str) -> Path:
    check_name("對話 id", conversation_id)
    return data.conversation_dir(conversation_id) / "jobs.jsonl"


def jobs(data: DataDir, conversation_id: str) -> list[str]:
    """這個對話開過的工單 id，照開的順序，不重複。"""
    path = _jobs_file(data, conversation_id)
    if not path.is_file():
        return []
    seen: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            job_id = json.loads(line)["job_id"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(job_id, str) and job_id not in seen:
            seen.append(job_id)
    return seen


def add_job(data: DataDir, conversation_id: str, job_id: str, now: datetime) -> None:
    """記下這個對話開了這張工單；已經記過就不再記。"""
    if job_id in jobs(data, conversation_id):
        return
    line = json.dumps({"job_id": job_id, "ts": now.isoformat()}, ensure_ascii=False)
    with _jobs_file(data, conversation_id).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def owns_job(data: DataDir, conversation_id: str, job_id: str) -> bool:
    return job_id in jobs(data, conversation_id)
