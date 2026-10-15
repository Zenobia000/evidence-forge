"""角色的 lessons（ADR-0026 §3.3、FR-RW-04）：訪談與寫稿開工前讀最近十筆。

`$EF_DATA/authors/<角色>/lessons.md` 一筆一行（`- ` 開頭），新的在後（範本是 #106 的
`harness/templates/author-lessons.md`，說明寫在 HTML 註解裡）。
這裡只讀：寫進去是 #114 的事（agent 提議、創作者同意，閘道附加），對話與它的 agent 從不寫角色資料夾。

對話的 agent 沒有讀檔工具，所以經閘道的 `read_lessons`（`gateway/chat_tools.py`）讀：
只讀得到某個角色的這一份、最近十筆，碰不到角色資料夾的其他檔。
角色資料夾或 `lessons.md` 是符號連結就不讀：連結可以指到資料目錄以外，對話的鑰匙不該借此讀到別的檔。
"""

import re

from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import AUTHOR, personas

LESSONS_FILE = "lessons.md"
# 訪談與寫稿開工前讀幾筆（ADR-0026 §3.3）
RECENT = 10

# 範本的說明寫在 HTML 註解裡（裡面也有 `- ` 開頭的格式示範），不算一筆
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ENTRY = re.compile(r"^\s*[-*]\s+(\S.*?)\s*$")


def latest_lessons(data: DataDir, persona: str) -> list[str]:
    """這個角色最近十筆 lessons，舊的在前；還沒有 `lessons.md` 就是空的。

    - 角色名稱不合格回 400；沒有這個角色回 404，附上現有的角色。
    - 創作者自己（`me`）還沒有資料夾不算錯：沒有 lessons 就不讀（ADR-0026 §3.3），回空的。
    - 角色資料夾或 `lessons.md` 是符號連結、或解開之後跑出角色資料夾，回 403、不讀。
    """
    try:
        check_name("角色", persona)
    except InvalidJobKey as exc:
        raise ToolRejected(400, str(exc)) from None
    existing = personas(data)
    if persona not in existing:
        if persona == AUTHOR:
            return []
        listed = "、".join(existing) or "（還沒有任何角色）"
        raise ToolRejected(404, f"沒有這個角色：{persona}；現有的角色：{listed}")
    folder = data.authors / persona
    path = folder / LESSONS_FILE
    if folder.is_symlink() or path.is_symlink():
        raise ToolRejected(403, f"角色 {persona} 的 lessons 是符號連結，不讀")
    if not path.exists():
        return []
    resolved = path.resolve()
    if not resolved.is_file() or resolved.parent != data.authors.resolve() / persona:
        raise ToolRejected(403, f"角色 {persona} 的 lessons 不在角色資料夾裡，不讀")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    entries = [
        found.group(1)
        for line in _COMMENT.sub("", text).splitlines()
        if (found := _ENTRY.match(line))
    ]
    return entries[-RECENT:]
