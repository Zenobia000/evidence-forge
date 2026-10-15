"""照編輯卡寫稿的錄音與重播共用的那一張卡（#110）。

錄音與重播一定要開出一模一樣的寫稿工單（請求逐位元組相同才對得上錄音），所以卡的內容只寫在這裡：
從 `analyze-library` 錄音的報告第 1 個題材開始，用虛構角色 `nightowl`（`tests/fixtures/authors/nightowl/`）
寫研究評論。內容全部是為測試寫的虛構短句，不是任何真實作家或品牌的資料（ADR-0014：錄音會進公開 repo）。

錄音時當腳本跑（見 `test_rewrite_replay.py` 的說明）：

    EF_DATA=$D uv run python tests/card_writing.py <分析工單 id> <harness>

會開對話、記好六關、照編輯卡開一張寫稿工單，最後一行印出工單 id。
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from evidence_forge import cards, conversations
from evidence_forge.chat.desk import Desk
from evidence_forge.harness import Harness
from evidence_forge.models import TIER_ORDER
from evidence_forge.paths import DataDir, resolve_data_dir
from evidence_forge.runner.recipes import load_recipes

NOW = datetime(2026, 10, 15, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
PERSONA = "nightowl"
GENRE = "research-review"
THEME = "education"
TOPIC_TITLE = "夜間開館的人流為什麼先高後降"
# 作者素材 M1：創作者自己的紀錄（虛構）
NOTES = (
    "週三晚上 19:00～21:00，我在社區分館門口自己數的人數："
    "第一週 31 人、第三週 24 人、第六週 19 人、第十週 17 人，之後大致持平。"
)
CLAIMS = [
    {"claim": "新開的夜間時段，頭三個月的人流通常比之後高", "wrong_if": "第四個月起的人流沒有下降"},
    {"claim": "評估夜間時段要看第四個月以後的平均", "wrong_if": "第四個月以後人流還在大幅變動"},
]
FIGURES = [
    {"section": "該看哪一條線", "kind": "chart", "shows": "九個月的人流，前三個月與之後分開看"},
    {"section": "評估怎麼排", "kind": "table"},
]


def _option(label: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"label": label, "description": description, **extra}


STATIONS: dict[int, list[dict[str, Any]]] = {
    1: [
        {
            "kind": "genre",
            "header": "文體",
            "question": "這篇的主證據是什麼、讀者讀完要做哪個決定？",
            "options": [
                _option("研究評論", "用館方與調查的數字看夜間時段", value="research-review"),
                _option("理論教學", "講清楚開幕效應怎麼影響判斷", value="explainer"),
            ],
        },
        {
            "kind": "theme",
            "header": "主題",
            "question": "讀者做決定時最在意哪一個？",
            "options": [
                _option("決策原理與實作教學", "讀者要學會怎麼看數字", value="education"),
                _option("落地導入與失敗復盤", "讀者要決定留不留夜間時段", value="adoption"),
            ],
        },
        {
            "kind": "persona",
            "header": "角色",
            "question": "用誰的聲音寫？",
            "options": [
                _option("夜貓", "社區分館老志工的口吻", value=PERSONA),
                _option("創作者自己", "用 me 的聲音", value="me"),
            ],
        },
    ],
    2: [
        {
            "header": "觀點",
            "question": "這篇的核心觀點叫什麼？",
            "options": [
                _option("開幕效應", "新時段頭幾個月的人流靠好奇心撐著，熱度退了才看得到真正的需求"),
                _option("時段錯配", "晚上的人少是因為時段跟通勤錯開"),
            ],
        }
    ],
    3: [
        {
            "header": "例子",
            "question": "有沒有只有你能給的例子？",
            "options": [
                _option("你有沒有自己數過晚上的人？", "寫下你數到的數字"),
                _option("你有沒有問過晚上來的人為什麼來？", "寫下他們說的"),
            ],
        }
    ],
    4: [
        {
            "header": "成立條件",
            "question": "這個觀點在什麼條件下成立？",
            "options": [
                _option("有連續九個月以上的紀錄", "看得到熱度退去之後的樣子"),
                _option("只有頭三個月的紀錄", "還看不出來"),
            ],
        },
        {
            "header": "失效條件",
            "question": "什麼情況下不成立？",
            "options": [
                _option("中途改了開放時間或辦了大型活動", "人流的變化不是熱度退去造成的"),
                _option("附近新開了自習空間", "人被分流了"),
            ],
        },
    ],
    5: [
        {
            "header": "工作標題",
            "question": "先用哪個當錨？",
            "options": [
                _option("夜間開館，先別急著看前三個月", "直接給判斷"),
                _option("晚上的圖書館為什麼越來越冷清", "從現象切入"),
            ],
        }
    ],
    6: [
        {
            "header": "框架",
            "question": "用哪種結構？",
            "options": [
                _option("場景 → 數字 → 判斷", "從一個晚上切進來，再看數字，最後給評估方法"),
                _option("問題 → 證據 → 判斷", "先問，再給證據"),
            ],
        },
        {
            "header": "大綱",
            "question": "各節的順序與主張？",
            "options": [
                _option("三節", "熱鬧的頭三個月、該看哪一條線、評估怎麼排"),
                _option("兩節", "熱鬧的頭三個月、評估怎麼排"),
            ],
        },
        {
            "kind": "storyboard",
            "header": "圖文分鏡",
            "question": "每節配不配圖？",
            "options": [
                _option("一張數據圖加一個表格", "第 2 節數據圖、第 3 節表格", figures=FIGURES),
                _option("都不配圖", "整篇只有文字", figures=[]),
            ],
        },
        {
            "header": "主張",
            "question": "1. 新開的夜間時段，頭三個月的人流通常比之後高（錯在：第四個月起的人流沒有下降）"
            "2. 評估夜間時段要看第四個月以後的平均（錯在：第四個月以後人流還在大幅變動）",
            "options": [
                _option("照這份寫", "寫稿照這份主張"),
                _option("要刪改（寫在補充）", "補充寫要改哪條"),
            ],
        },
    ],
}


def build_card(data: DataDir, analysis_job: str) -> tuple[str, str]:
    """開一個對話、把分析工單記進去，再把六關一路記到可以寫（畫面送出的，`source: ui`）。回（對話 id, 卡 id）。"""
    cid = conversations.create(data, NOW).id
    conversations.add_job(data, cid, conversations.ConversationJob(job_id=analysis_job, ts=NOW))
    origin = {"kind": "report_topic", "job_id": analysis_job, "topic": 1, "title": TOPIC_TITLE}
    card_id = cards.propose_questions(data, cid, None, 1, STATIONS[1], now=NOW, origin=origin).card_id
    cards.add_material(data, cid, card_id, NOTES, now=NOW, filename="週三晚上的人數.md")
    for station in range(1, 7):
        if station > 1:
            claims = CLAIMS if station == 6 else None
            cards.propose_questions(data, cid, card_id, station, STATIONS[station], now=NOW, claims=claims)
        current = [q for q in cards.read_card(data, cid, card_id).stations[station - 1].questions if q.current]
        answers: list[dict[str, Any]] = [{"question": q.id, "choice": q.options[0].label} for q in current]
        if station == 3:
            answers = [{**a, "material": "M1", "note": "第十週之後就停在十七、八個人"} for a in answers]
        cards.submit_station(data, cid, card_id, station, answers, now=NOW)
    return cid, card_id


def open_writing(data: DataDir, harness: Harness, analysis_job: str, budget_usd: float) -> str:
    """建好那張卡，照畫面按「照這張編輯卡寫稿」的路開寫稿工單（工作台的 `write_from_card`）。回工單 id。"""
    cid, card_id = build_card(data, analysis_job)
    desk = Desk(data, load_recipes(harness, TIER_ORDER), "analyze", clock=lambda: NOW, budget_usd=budget_usd)
    shown = cards.read_card(data, cid, card_id).events
    job_id = desk.write_from_card(cid, card_id, shown)["job_id"]
    assert isinstance(job_id, str)
    return job_id


if __name__ == "__main__":
    print(open_writing(resolve_data_dir(), Harness.at(Path(sys.argv[2])), sys.argv[1], 0.05))
