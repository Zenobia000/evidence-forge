"""對話鑰匙 `conv:<對話 id>:<第幾次開>`（ADR-0021）。

工單鑰匙（`jobkey.py`）只在一段一次的 Pi 活著時有效；對話的 Pi 是常駐的 RPC 程序，
可能被殺掉再接回，所以鑰匙綁的是「這個對話第幾次開 Pi」：每接回一次編號加一，
閘道只認最新登記的那一把，前一個沒死透的 Pi 拿舊鑰匙來一律拒絕（跟工單鑰匙同一張登記表 `ActiveStages`）。

為了讓閘道的模型轉送、預留、告示板不分兩套，這把鑰匙對外跟工單鑰匙一樣有
`job_id`、`stage`、`attempt` 三欄：

- `job_id` 是 `conv:<對話 id>`：帶冒號，不可能跟工單 id 撞（工單 id 不准有冒號）。
  它只在閘道的記憶體裡當擁有者 id（登記表、預留、告示板），**不拼進路徑、不寫進帳本的 `job_id`**：
  路徑一律經 `DataDir.owner_dir` 導到 `conversations/<對話 id>/`，帳本那一行寫 `conversation_id`
  （`ledger_owner`）。冒號在 Windows 的磁碟上不能當檔名，`DataDir.job_dir` 看到冒號就報錯。
- `stage` 固定是 `chat`，`attempt` 是第幾次開 Pi。
"""

import re
from dataclasses import dataclass

from evidence_forge.jobkey import InvalidJobKey, JobKey, check_name

PREFIX = "conv:"
STAGE = "chat"
_KEY = re.compile(r"conv:([^:]+):([1-9][0-9]{0,5})")


@dataclass(frozen=True)
class ConversationKey:
    conversation_id: str
    launch: int

    def __post_init__(self) -> None:
        # 對話 id 會拿來拼 `conversations/<對話 id>/`，字元規則跟工單 id 一樣
        check_name("對話 id", self.conversation_id)
        if self.launch < 1:
            raise InvalidJobKey(f"第幾次開要從 1 起算：{self.launch}")

    def __str__(self) -> str:
        return f"{PREFIX}{self.conversation_id}:{self.launch}"

    @property
    def job_id(self) -> str:
        """閘道內部登記、預留、帳本用的擁有者 id：`conv:<對話 id>`。"""
        return f"{PREFIX}{self.conversation_id}"

    @property
    def stage(self) -> str:
        return STAGE

    @property
    def attempt(self) -> int:
        return self.launch

    @classmethod
    def parse(cls, text: str) -> "ConversationKey":
        match = _KEY.fullmatch(text)
        if match is None:
            raise InvalidJobKey("對話鑰匙要是 conv:<對話 id>:<第幾次開>")
        return cls(match.group(1), int(match.group(2)))


# 閘道收到的鑰匙：工單的一段一次，或一個對話的一次開 Pi
Principal = JobKey | ConversationKey


def parse_principal(text: str) -> Principal:
    if text.startswith(PREFIX):
        return ConversationKey.parse(text)
    return JobKey.parse(text)


def ledger_owner(key: Principal) -> dict[str, str]:
    """帳本那一行記誰的帳：工單寫 `job_id`，對話寫 `conversation_id`（ADR-0021 §3.3）。

    `conv:<對話 id>` 只在閘道的記憶體裡當擁有者 id，不寫進帳本的 `job_id`、也不拼進任何路徑。
    """
    if isinstance(key, ConversationKey):
        return {"conversation_id": key.conversation_id}
    return {"job_id": key.job_id}


def conversation_of(owner_id: str) -> str | None:
    """`conv:<對話 id>` 拿回對話 id；是工單 id 就回 None。"""
    return owner_id[len(PREFIX) :] if owner_id.startswith(PREFIX) else None
