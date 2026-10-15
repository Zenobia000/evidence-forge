"""測試共用：一張六關都由畫面送出過的編輯卡（直接走 `cards.py`，不經模型）。

寫稿（#110）的測試都從這裡拿卡：起點、文體、主題、角色、作者素材都可以換。
內容全部是為測試寫的虛構短句。
"""

from datetime import UTC, datetime
from typing import Any

from evidence_forge import cards
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
EXAMPLE = "我自己連續三個月在週三晚上去分館數過人"
CLAIMS = [
    {"claim": "夜間時段頭兩個月人流最高", "wrong_if": "第三個月起的人流比頭兩個月高"},
    {"claim": "人事費從購書預算挪過來", "wrong_if": "預算書裡購書費沒有減少"},
]
STORYBOARD = [
    {"section": "缺口在哪", "kind": "concept", "shows": "白天與晚上的使用者"},
    {"section": "數字怎麼看", "kind": "chart", "shows": "九個月的人流"},
    {"section": "怎麼取捨", "kind": "table"},
]


def _options(*labels: str) -> list[dict[str, Any]]:
    return [{"label": label, "description": f"{label}的說明"} for label in labels]


def station_cards(station: int, genre: str, theme: str, persona: str | None) -> list[dict[str, Any]]:
    """每一關的問題卡：選項第一個就是測試要選的。"""
    if station == 1:
        found: list[dict[str, Any]] = [
            {
                "kind": "genre",
                "header": "文體",
                "question": "主證據是什麼？",
                "options": [
                    {"label": genre, "description": "選這個", "value": genre},
                    {"label": "trend" if genre != "trend" else "explainer", "description": "另一個",
                     "value": "trend" if genre != "trend" else "explainer"},
                ],
            },
            {
                "kind": "theme",
                "header": "主題",
                "question": "讀者最在意哪一個？",
                "options": [
                    {"label": f"主題 {theme}", "description": "選這個", "value": theme},
                    {"label": "另一個主題", "description": "不選",
                     "value": "tools" if theme != "tools" else "adoption"},
                ],
            },
        ]
        if persona is not None:
            found.append(
                {
                    "kind": "persona",
                    "header": "角色",
                    "question": "用誰的聲音？",
                    "options": [
                        {"label": f"角色 {persona}", "description": "選這個", "value": persona},
                        {"label": "創作者自己", "description": "不選", "value": "me"},
                    ],
                }
            )
        return found
    names = {2: "觀點", 3: "例子", 4: "邊界", 5: "工作標題"}
    if station in names:
        return [{"header": names[station], "question": f"{names[station]}怎麼定？",
                 "options": _options(f"{names[station]}甲", f"{names[station]}乙")}]
    return [
        {"header": "框架", "question": "用哪種結構？", "options": _options("問題 → 證據 → 判斷", "場景 → 數字")},
        {
            "kind": "storyboard",
            "header": "圖文分鏡",
            "question": "每節配不配圖？",
            "options": [
                {"label": "照這份配圖", "description": "概念圖、數據圖、表格各一", "figures": STORYBOARD},
                {"label": "不配圖", "description": "只有文字", "figures": []},
            ],
        },
        {"header": "主張", "question": "照這份主張寫嗎？", "options": _options("照這份寫", "要刪改（寫在補充）")},
    ]


def decided_card(
    data: DataDir,
    cid: str,
    *,
    origin: dict[str, Any] | None = None,
    genre: str = "research-review",
    theme: str = "education",
    persona: str | None = None,
    material: str | None = None,
) -> str:
    """一張六關都有決定的卡；`material` 給了就先記成作者素材 M1，第 3 關引用它。"""
    card_id = cards.propose_questions(
        data, cid, None, 1, station_cards(1, genre, theme, persona), now=NOW,
        origin=origin or {"kind": "idea", "title": "夜間開館"},
    ).card_id
    if material is not None:
        cards.add_material(data, cid, card_id, material, now=NOW, filename="notes.md")
    for station in range(1, 7):
        if station > 1:
            cards.propose_questions(
                data, cid, card_id, station, station_cards(station, genre, theme, persona), now=NOW,
                claims=CLAIMS if station == 6 else None,
            )
        current = [q for q in cards.read_card(data, cid, card_id).stations[station - 1].questions if q.current]
        answers: list[dict[str, Any]] = [{"question": q.id, "choice": q.options[0].label} for q in current]
        if station == 3:
            extra: dict[str, Any] = {"note": EXAMPLE}
            if material is not None:
                extra["material"] = "M1"
            answers = [{**a, **extra} for a in answers]
        cards.submit_station(data, cid, card_id, station, answers, now=NOW)
    assert cards.read_card(data, cid, card_id).writable
    return card_id
