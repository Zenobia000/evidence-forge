"""角色的 lessons（ADR-0026 §3.3、FR-RW-04）：訪談與寫稿開工前讀最近十筆。

`$EF_DATA/authors/<角色>/lessons.md` 一筆一行（`- ` 開頭），新的在後（範本 `harness/templates/author-lessons.md`）。
這裡只讀：寫進去是 #114 的事（agent 提議、創作者同意，閘道附加），對話與它的 agent 從不寫角色資料夾。

對話的 agent 沒有讀檔工具，所以經閘道的 `read_lessons`（`gateway/chat_tools.py`）讀：
只讀得到某個角色的這一份、最近十筆，碰不到角色資料夾的其他檔。
"""

import re

from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import personas

LESSONS_FILE = "lessons.md"
# 訪談與寫稿開工前讀幾筆（ADR-0026 §3.3）
RECENT = 10

# 範本的說明寫在 HTML 註解裡（裡面也有 `- ` 開頭的格式示範），不算一筆
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ENTRY = re.compile(r"^\s*[-*]\s+(\S.*?)\s*$")


def latest_lessons(data: DataDir, persona: str, count: int = RECENT) -> list[str]:
    """這個角色最近 `count` 筆 lessons，舊的在前；還沒有 `lessons.md` 就是空的。

    角色名稱不合格回 400；沒有這個角色回 404，附上現有的角色。
    """
    try:
        check_name("角色", persona)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    existing = personas(data)
    if persona not in existing:
        listed = "、".join(existing) or "（還沒有任何角色）"
        raise ToolRejected(404, f"沒有這個角色：{persona}；現有的角色：{listed}")
    path = data.authors / persona / LESSONS_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    entries = [
        found.group(1)
        for line in _COMMENT.sub("", text).splitlines()
        if (found := _ENTRY.match(line))
    ]
    return entries[-count:] if count > 0 else []
