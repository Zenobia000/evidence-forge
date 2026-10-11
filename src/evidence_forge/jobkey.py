"""工單鑰匙 `job:<工單 id>:<段>:<第幾次>`（ADR-0011 §3.6、架構草案 v0.3 §3.3）。

執行器開 Pi 時用 `--api-key` 傳這把鑰匙；Pi 的模型請求與 ef-tools 的工具呼叫都帶著它，
閘道據此知道是哪張單、哪一段、第幾次，並算出能讀寫的範圍。
鑰匙裡的每一節都會拿來拼路徑，所以字元限制得很緊：不准有 `/`、`..`、冒號或空白。
"""

import re
from dataclasses import dataclass

# 工單 id 與段 id 允許的字元：英數字開頭，之後英數字、點、底線、連字號。
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_KEY = re.compile(r"job:([^:]+):([^:]+):([1-9][0-9]{0,5})")


class InvalidJobKey(ValueError):
    """鑰匙格式不對，或其中一節含有不准的字元。"""


def check_name(kind: str, value: str) -> None:
    """工單 id 與段 id 共用的字元規則；不合格丟 `InvalidJobKey`。"""
    if not _NAME.fullmatch(value) or ".." in value:
        raise InvalidJobKey(f"{kind} 含有不准的字元：{value!r}")


@dataclass(frozen=True)
class JobKey:
    job_id: str
    stage: str
    attempt: int

    def __post_init__(self) -> None:
        check_name("工單 id", self.job_id)
        check_name("段 id", self.stage)
        if self.attempt < 1:
            raise InvalidJobKey(f"第幾次要從 1 起算：{self.attempt}")

    def __str__(self) -> str:
        return f"job:{self.job_id}:{self.stage}:{self.attempt}"

    @classmethod
    def parse(cls, text: str) -> "JobKey":
        match = _KEY.fullmatch(text)
        if match is None:
            raise InvalidJobKey("鑰匙要是 job:<工單 id>:<段>:<第幾次>")
        return cls(match.group(1), match.group(2), int(match.group(3)))
