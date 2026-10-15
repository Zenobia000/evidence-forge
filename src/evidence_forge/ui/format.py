"""把數字與時間整理成畫面上的字：金額、時長、日期、文類名。

純粹的轉換，不碰檔案、不碰 HTTP。寫法照聊天室 IA（`30-snapshot/plans/design/2026-10-13-chat-ui-ia.md`）。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

# 文類 id → 中文名（CONTEXT.md〈文類〉的 12 種）；不認得的照原樣顯示
GENRE_NAME = {
    "news": "新聞快訊",
    "hot-take": "熱點評論",
    "tutorial": "教學文",
    "tips": "清單技巧",
    "explainer": "解說文",
    "deep-dive": "深度長文",
    "notes": "筆記摘要",
    "review": "評測比較",
    "case-study": "案例拆解",
    "opinion": "觀點文",
    "story": "故事心得",
    "roundup": "精選彙整",
}


def genre_name(genre_id: str | None) -> str:
    if not genre_id:
        return ""
    return GENRE_NAME.get(genre_id, genre_id)


def format_usd(amount: float) -> str:
    """`US$0.21`；有花但不到一分錢寫 `<US$0.01`，免得看起來像沒花。"""
    if 0 < amount < 0.005:
        return "<US$0.01"
    return f"US${amount:.2f}"


def format_duration(delta: timedelta) -> str:
    """`m:ss`；一小時以上 `h:mm:ss`。負的（時鐘差）當 0。"""
    seconds = max(0, int(delta.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def taipei(moment: datetime) -> datetime:
    return moment.astimezone(TAIPEI)


def clock(moment: datetime) -> str:
    """`14:36`。"""
    return taipei(moment).strftime("%H:%M")


def month_day_clock(moment: datetime) -> str:
    """`10/13 14:36`。"""
    return taipei(moment).strftime("%m/%d %H:%M")


def relative_day(moment: datetime, now: datetime) -> str:
    """對話清單的時間：今天寫 `14:12`，昨天寫「昨天」，更早寫 `10/11`。"""
    then, today = taipei(moment), taipei(now)
    if then.date() == today.date():
        return then.strftime("%H:%M")
    if (today.date() - then.date()).days == 1:
        return "昨天"
    return then.strftime("%m/%d")
