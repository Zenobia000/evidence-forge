"""角色資料夾的規格（ADR-0026 §3.3）：每一部分叫什麼、放哪裡、沒有的時候怎麼辦。**唯一一份**。

只有資料，沒有輸入輸出，所以很輕：執行器（`runner/stage_inputs.py`）與畫面（`ui/personas.py`、`persona.py`）
都從這裡拿檔名，不再各寫一份字串。`persona.py` 把這裡的名字原樣再匯出，`persona.PARTS` 也找得到。

`PARTS` 之外的 `META_PART`（`persona.json`，顯示名稱）不是創作者寫的內容，是角色頁記的小檔，
所以不列在 `PARTS`（畫面不把它當成「缺了要補」的一部分）；資料夾名稱才是角色的身分。
檔案內容的格式範本在 `harness/templates/author-*.md`；匯入腳本（`persona_import.py`）寫出來的檔名跟這裡一樣。
"""

from dataclasses import dataclass

from evidence_forge.ui.format import GENRE_NAME


@dataclass(frozen=True)
class PartSpec:
    """角色資料夾的一部分：叫什麼、放哪裡、沒有的時候怎麼辦。"""

    key: str
    label: str
    path: str  # 相對於角色資料夾；結尾 `/` 是資料夾
    holds: str
    required: bool
    # 缺的時候實際會怎樣（畫面照字標出來）
    fallback: str


PARTS: tuple[PartSpec, ...] = (
    PartSpec(
        "voice",
        "聲音",
        "voice.md",
        "語氣、用字、節奏、舉例習慣、禁忌，包括可以用程式擋的句型規則",
        True,
        "必要：沒有它就不是一個角色，寫稿沒有聲音可以學",
    ),
    PartSpec(
        "genres",
        "文體風格",
        "genres/",
        "每種文體一份 <slug>.md：這個角色怎麼寫它、多帶什麼價值、文末導去哪裡（CTA）",
        False,
        "沒有的文體標「未校準」，只用聲音加通用定義寫",
    ),
    PartSpec(
        "craft",
        "手藝",
        "craft/",
        "開場、標題、金句與收尾、圖文編排，一個主題一份 Markdown",
        False,
        "標題與金句先讀舊的共用規則 style/headline-punchline-system.md；其他項目不讀",
    ),
    PartSpec(
        "themes",
        "主題",
        "themes.md",
        "這個角色用哪幾個主題、對每個主題的主張",
        False,
        "訪談第 1 關只列通用的主題說明",
    ),
    PartSpec(
        "lessons",
        "退件累積的規則",
        "lessons.md",
        "創作者退件或指正後累積的規則，一筆一條，新的在後；只有閘道寫",
        False,
        "不讀",
    ),
    PartSpec(
        "brand",
        "品牌設定",
        "brand.yaml",
        "配圖用的色票、字型、封面預設",
        False,
        "用中性的預設值（灰階加一個通用重點色）",
    ),
)
PART_BY_KEY = {part.key: part for part in PARTS}
# 顯示名稱的小檔：只有角色頁寫（`rename`、建立、複製），內容是 {"display_name": "…"}。
# 不是 voice.md 的欄位（那是創作者手寫的，畫面不改它）；資料夾名稱才是身分，這個檔只管畫面上顯示什麼
META_PART = PartSpec(
    "meta",
    "顯示名稱",
    "persona.json",
    "畫面上顯示的名字；只有角色頁寫它，資料夾名稱才是角色的身分",
    False,
    "畫面顯示資料夾名稱",
)
META_FILE = META_PART.path
# 其餘程式要這些檔名，都從 `PARTS` 拿，不再自己寫一份字串
VOICE_FILE = PART_BY_KEY["voice"].path
THEMES_FILE = PART_BY_KEY["themes"].path
LESSONS_FILE = PART_BY_KEY["lessons"].path
BRAND_FILE = PART_BY_KEY["brand"].path
GENRES_DIR = PART_BY_KEY["genres"].path.rstrip("/")
CRAFT_DIR = PART_BY_KEY["craft"].path.rstrip("/")
# 文體風格檔只認 7 種（ADR-0026 §3.1）；舊的 12 種不支援
GENRE_SLUGS: tuple[str, ...] = tuple(GENRE_NAME)
# 寫稿用得到的手藝（ADR-0026 §3.3「只送這一篇用得到的」）：開場、標題、金句與收尾、圖文編排。
# 配圖的 figures、charts 這類是配圖工單的事（ADR-0027），寫稿不送
WRITING_CRAFT: tuple[str, ...] = ("openings.md", "titles.md", "golden-lines-and-endings.md", "layout.md")
# 角色有其中一份就不再讀共用的標題與金句規則（`style/headline-punchline-system.md`，ADR-0026 §3.3）
HEADLINE_CRAFT: tuple[str, ...] = ("titles.md", "golden-lines-and-endings.md")


def genre_file(slug: str) -> str:
    """某一種文體的風格檔，相對於角色資料夾：`genres/<slug>.md`。"""
    return f"{GENRES_DIR}/{slug}.md"


