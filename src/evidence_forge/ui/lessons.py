"""對話串流裡的 lesson 提議（ADR-0026 §3.3、#114）。

agent 提議的 lesson（閘道的 `propose_lesson`）在對話裡長成一張小卡：寫著那一行規則、為什麼、「同意」與「不要」兩顆按鈕。
**按鈕是畫面的，agent 沒有對應的工具**：按下去打對話主機的 API，由閘道把那一行附加到角色的 `lessons.md`（同意），
或什麼都不寫（不要）。決定之後小卡收成置中的一行系統小字，留在原來的位置。

這裡只把 `lesson_proposals.Proposal` 整理成串流的項目（跟 `interview.thread_items` 一樣照時間插進 Pi 的紀錄裡）；
互動在 `static/js/lessons.js`。資料都是純文字，由模板跳脫。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evidence_forge.ui import interview
from evidence_forge.ui.format import clock


@dataclass(frozen=True)
class LessonCard:
    """還在等創作者決定的 lesson 提議。"""

    proposal: str
    persona: str
    # 同意的話會寫進 lessons.md 的那一行，去掉開頭的 `- `（畫面上不用露出清單符號）
    text: str
    why: str
    time: str
    kind: str = "lesson"


Item = LessonCard | interview.Line


def _parse(value: Any) -> datetime | None:
    return interview.parse_time(value)


def thread_items(proposals: list[dict[str, Any]]) -> list[tuple[datetime, Item]]:
    """每筆提議一個項目：還在等的是小卡（提議的時間），同意、不要的是一行系統小字（決定的時間，照實放在按下去的那一刻）。

    讀不懂時間的提議跳過（資料壞了不拖垮整個串流）。
    """
    items: list[tuple[datetime, Item]] = []
    for proposal in proposals:
        proposed = _parse(proposal.get("proposed_at"))
        if proposed is None:
            continue
        persona = str(proposal.get("persona", ""))
        status = proposal.get("status")
        if status == "pending":
            preview = str(proposal.get("preview", ""))
            text = preview[2:] if preview.startswith("- ") else preview
            items.append(
                (
                    proposed,
                    LessonCard(
                        proposal=str(proposal.get("id", "")),
                        persona=persona,
                        text=text,
                        why=str(proposal.get("why", "")),
                        time=clock(proposed),
                    ),
                )
            )
            continue
        decided = _parse(proposal.get("decided_at")) or proposed
        if status == "approved" and proposal.get("already_present"):
            # 同一個角色、同一天已經有一模一樣的一條：沒有重複寫（lesson_proposals.approve）
            line = interview.Line(
                f"你同意了這筆 lesson，「{persona}」的 lessons 已經有一樣的一條，沒有重複寫入・{clock(decided)}", "check"
            )
        elif status == "approved":
            line = interview.Line(f"你同意了這筆 lesson，已寫進「{persona}」的 lessons・{clock(decided)}", "check")
        else:
            line = interview.Line(f"你不要這筆 lesson，沒有寫任何東西・{clock(decided)}", "x")
        items.append((decided, line))
    return items
