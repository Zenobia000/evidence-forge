"""文體與主題的通用定義（ADR-0026 §3.1、§3.2）：`harness/genres/` 是 7 種文體加一份主題清單。

清單照 ADR 獨立寫一份，不從程式拿：程式或檔案飄開時，這裡會先紅。
"""

import re

import pytest

from evidence_forge.checks.article import genre_skeleton
from evidence_forge.documents import read_frontmatter
from evidence_forge.harness import default_harness
from evidence_forge.ui.format import GENRE_NAME, genre_name

HARNESS = default_harness()

# ADR-0026 §3.1：slug → 文體、主證據要不要作者自己做過
SEVEN = {
    "essay": ("隨筆", True),
    "research-review": ("研究評論", False),
    "product-case": ("產品個案", True),
    "benchmark": ("實證評測", True),
    "postmortem": ("工程復盤", True),
    "explainer": ("理論教學", False),
    "trend": ("趨勢解讀", False),
}

# ADR-0026 §3.2
THEMES = [
    "worldview",
    "builder-method",
    "startup",
    "tech-selection",
    "adoption",
    "education",
    "tools",
    "open-source",
]

# 每一份文體定義都要有的節（ADR-0026 §3.1「通用定義」）
SECTIONS = [
    "一句話",
    "辨識訊號",
    "主證據",
    "讀者讀完要做的決定",
    "必要內容",
    "搜尋策略",
    "推薦的結構公式",
    "骨架（目標是這一種時）",
]


def test_文體定義正好是_7_種_主題清單不算() -> None:
    assert [p.stem for p in HARNESS.genre_files()] == sorted(SEVEN)
    assert HARNESS.themes.is_file()
    assert HARNESS.themes not in HARNESS.genre_files()


def test_舊的轉換說明資料夾不在了() -> None:
    # 舊的轉換說明用舊 12 種 id 寫，新程式不讀（ADR-0026 §3.4）
    assert not (HARNESS.root / "conversions").exists()


@pytest.mark.parametrize("slug", sorted(SEVEN))
def test_每一種的_front_matter_與節都齊(slug: str) -> None:
    path = HARNESS.genres / f"{slug}.md"
    meta = read_frontmatter(path)
    name, needs_author = SEVEN[slug]
    assert (meta["id"], meta["name"], meta["needs_author_evidence"]) == (slug, name, needs_author)
    assert str(meta["main_evidence"]).strip()
    text = path.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert headings == SECTIONS
    # 骨架讀得出來：重構的程式檢查拿它擋「標題剛好是骨架的一項」
    assert len(genre_skeleton(text)) >= 4


def test_畫面與工作台的文體名稱跟定義同一份() -> None:
    assert GENRE_NAME == {slug: name for slug, (name, _) in SEVEN.items()}


def test_舊工單的文類_id_照原字顯示_不當掉() -> None:
    # 新程式不做舊 12 種的對照（ADR-0026 §3.4）：認不得的照原字
    assert genre_name("opinion") == "opinion"
    assert genre_name("research-review") == "研究評論"
    assert genre_name(None) == ""


def test_主題清單是_8_個_slug_各有一句說明() -> None:
    rows = re.findall(
        r"^\| `([a-z-]+)` \| (.+?) \|$", HARNESS.themes.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert [slug for slug, _ in rows] == THEMES
    assert all(description.strip() for _, description in rows)
