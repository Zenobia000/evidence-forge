"""對話資料夾（ADR-0021 §3.2）。

```
$EF_DATA/conversations/<對話 id>/
  conversation.json   對話的設定：標題、建立時間、預算、開過幾次 Pi、通知過哪些工單（只有對話主機寫）
  jobs.jsonl          這個對話開過的工單，一行一張（只有閘道寫，只增不改）
  ledger.jsonl        對話本身的模型呼叫（只有閘道寫，只增不改）
  pi-session/         Pi 的對話紀錄（只有 Pi 寫；`--session-dir`，session id 就是對話 id）
```

工單本身仍在 `jobs/<工單 id>/`，不搬進對話資料夾：執行器、帳單與既有工具都照舊找得到。
「這個對話讀得到哪張工單」的單一真相是 `jobs.jsonl`；對話的工具只碰得到列在這裡的工單。
`job.md` 的 `conversation` 只記「是誰開的」，給投入去重用，不拿來判斷能不能讀。
"""

import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
)

from evidence_forge.gateway.ledger import append_line
from evidence_forge.paths import DataDir

DEFAULT_BUDGET_USD = 1.0
# 還沒丟素材的對話叫這個；第一張工單開出來時，主機換成那份素材的標題（IA §3）
DEFAULT_TITLE = "新對話"


class Conversation(BaseModel):
    """`conversation.json`。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    created_at: AwareDatetime
    # 對話本身的模型呼叫（追問、開工、讀報告）的美元上限；開出去的工單各自用工單的預算
    budget_usd: NonNegativeFloat = DEFAULT_BUDGET_USD
    # 開過幾次 Pi：每開一次加一，對話鑰匙是 conv:<id>:<這個數>
    launches: NonNegativeInt = 0
    # 結束通知已經確定進了 Pi 紀錄的工單（主機看到 `ef-job` 訊息的 `message_end` 才記，#85）。
    # 主機重開時只補送不在這裡的；ef-tools 自己也照紀錄去重，這裡漏記頂多多送一次、不會多一則通知
    notified_jobs: list[str] = Field(default_factory=list)
    # 創作者看過結果的最後一張工單（#86）：對話最新的一張結束的單不是它，清單就標「有新結果」。
    # 畫面打開對話時回報，主機寫；舊的 conversation.json 沒有這欄，讀進來是 None
    seen_job: str | None = None


class ConversationJob(BaseModel):
    """`jobs.jsonl` 的一行：這個對話開了哪張工單。

    每一版（補查、再改、換角色）都是一張新工單，用 `version` 與 `parent` 串起來：
    `parent` 是上一版的工單 id，第一版是 None。這張票只開分析，所以一律寫第 1 版；
    兩個欄位先定好，之後的重構不用改帳本格式。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    ts: AwareDatetime
    version: PositiveInt = 1
    parent: str | None = None


def _folder(data: DataDir, conversation_id: str) -> Path:
    # 對話 id 的字元規則在 `DataDir.conversation_dir` 檢查
    return data.conversation_dir(conversation_id)


def new_id(now: datetime) -> str:
    """`c-<日期時間>-<隨機>`：照時間排得出先後，又不會兩個同時開的撞在一起。"""
    return f"c-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def create(data: DataDir, now: datetime, title: str = DEFAULT_TITLE) -> Conversation:
    conversation = Conversation(id=new_id(now), title=title, created_at=now)
    _folder(data, conversation.id).mkdir(parents=True, exist_ok=False)
    save(data, conversation)
    return conversation


def save(data: DataDir, conversation: Conversation) -> None:
    """先寫暫存檔再整個換上去：讀的人只會讀到舊版或新版。"""
    path = _folder(data, conversation.id) / "conversation.json"
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(conversation.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read(data: DataDir, conversation_id: str) -> Conversation:
    path = _folder(data, conversation_id) / "conversation.json"
    return Conversation.model_validate_json(path.read_text(encoding="utf-8"))


def list_all(data: DataDir) -> list[Conversation]:
    """所有讀得到的對話，照 id（建立時間）排；讀不懂的跳過。"""
    if not data.conversations.is_dir():
        return []
    found: list[Conversation] = []
    for folder in sorted(data.conversations.iterdir()):
        try:
            found.append(read(data, folder.name))
        except (OSError, ValueError):
            continue
    return found


def budget_usd(data: DataDir, conversation_id: str) -> float:
    """對話的預算；讀不到就是 0（雲端呼叫一律不放行，跟工單讀不到 `job.md` 一樣）。"""
    try:
        return read(data, conversation_id).budget_usd
    except (OSError, ValueError):
        return 0.0


def _jobs_file(data: DataDir, conversation_id: str) -> Path:
    return _folder(data, conversation_id) / "jobs.jsonl"


def jobs(data: DataDir, conversation_id: str) -> list[ConversationJob]:
    """這個對話開過的工單，照開的順序，同一張只算第一次。讀不懂的行跳過。"""
    path = _jobs_file(data, conversation_id)
    if not path.is_file():
        return []
    found: dict[str, ConversationJob] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = ConversationJob.model_validate_json(line)
        except ValidationError:
            continue
        found.setdefault(row.job_id, row)
    return list(found.values())


def owns_job(data: DataDir, conversation_id: str, job_id: str) -> bool:
    return any(row.job_id == job_id for row in jobs(data, conversation_id))


def add_job(data: DataDir, conversation_id: str, row: ConversationJob) -> None:
    """記下這個對話開了這張工單；已經記過就不再記（投入去重拿回同一張時）。

    跟帳本同一套追加寫入（`append_line`）：一行一次寫完；上一筆寫到一半被砍留下的殘行
    先補換行封起來，這一筆才不會黏在它後面、一起讀不懂。殘行本身讀的時候跳過。
    """
    if owns_job(data, conversation_id, row.job_id):
        return
    append_line(_jobs_file(data, conversation_id), row.model_dump_json() + "\n")
