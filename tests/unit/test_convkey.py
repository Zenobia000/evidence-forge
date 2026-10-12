"""對話鑰匙 `conv:<對話 id>:<第幾次開>`（ADR-0021）：格式、跟工單鑰匙分得開、拼路徑前擋掉怪字元。"""

from pathlib import Path

import pytest

from evidence_forge.convkey import ConversationKey, conversation_of, parse_principal
from evidence_forge.jobkey import InvalidJobKey, JobKey
from evidence_forge.paths import DataDir


def test_對話鑰匙寫出來再讀回去是同一把() -> None:
    key = ConversationKey("c-20261013-050932-8fadc3", 2)
    assert str(key) == "conv:c-20261013-050932-8fadc3:2"
    assert ConversationKey.parse("conv:c-20261013-050932-8fadc3:2") == key


def test_閘道內部用的擁有者_id_帶冒號_不會跟工單撞() -> None:
    key = ConversationKey("c1", 3)
    assert (key.job_id, key.stage, key.attempt) == ("conv:c1", "chat", 3)
    assert conversation_of("conv:c1") == "c1"
    assert conversation_of("20261013-050933-0a080719") is None


def test_分得出對話鑰匙與工單鑰匙() -> None:
    assert parse_principal("conv:c1:1") == ConversationKey("c1", 1)
    assert parse_principal("job:j1:echo:1") == JobKey("j1", "echo", 1)


@pytest.mark.parametrize(
    "text",
    [
        "conv:../x:1",
        "conv:c1:0",
        "conv:c1",
        "conv:a/b:1",
        "conv::1",
        "conv:c1:1:extra",
        "conv:.hidden:1",
    ],
)
def test_怪鑰匙一律拒絕(text: str) -> None:
    with pytest.raises(InvalidJobKey):
        parse_principal(text)


def test_帳本跟著擁有者走(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    assert data.owner_dir("conv:c1") == tmp_path / "conversations" / "c1"
    assert data.owner_dir("20261013-1") == tmp_path / "jobs" / "20261013-1"

