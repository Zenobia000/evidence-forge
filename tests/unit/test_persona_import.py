"""角色匯入（`scripts/import_persona.py`，#106、ADR-0026 §3.3）：把創作者自己的寫作系統拆進角色資料夾。

ADR-0014：測試進公開 repo，所以這裡的「寫作系統」全部是為測試寫的假內容（假的品牌、假的色票、
假的封面用語），只照真的那份的檔案結構排。真的內容只在創作者的 zip 與 `EF_DATA`，不進 repo。
"""

import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from evidence_forge.figures.brand import Brand, BrandError, load_brand, load_brand_file
from evidence_forge.paths import DataDir
from evidence_forge.persona_import import (
    PersonaImportError,
    build_persona,
    import_persona,
    main,
    read_source,
)

NOW = datetime(2026, 10, 15, 21, 30, 5, tzinfo=ZoneInfo("Asia/Taipei"))

SKILL = """---
name: fake-writing
description: 測試用的假寫作系統。
---

# Fake Writing Skill

## 0. Mission

假任務：把測試寫清楚。

## 1. When to Use

這一節是給 Claude Code 判斷什麼時候用的，不是聲音。

## 2. Brand Thesis

假品牌主張：測試先行。

## 4. Author Voice

### 4.1 整體人格

像一個寫測試的人。

## 5. Reasoning Structure

假推論：問題 → 機制 → 邊界。

## 6. Evidence & Truthfulness

不虛構測試結果。

## 7. Genres（文體）

文體表在別處，不進聲音。

## 12. Quality Gates

### Thesis Gate
- [ ] 有一句主張

### AI-Smell Gate
若出現以下模式，重寫：
- 假句型一號

## 15. Non-negotiables

1. 不虛構假資料。
"""

GENRES_README = """# Genres

## 5. 主題（`theme`）

| slug | 主題 | 常見文體 | 通常落在的網站 TOPIC |
|---|---|---|---|
| `education` | 假的教學主題 | `explainer` | 假分類 |
| `tools` | 假的工具主題 | `benchmark` | 假分類 |
| `cooking` | 不在 8 個主題裡 | `essay` | 假分類 |

一篇只有一個主題。

## 6. 文體判定

不進主題檔。
"""

ESSAY = """# essay：隨筆

> 文體總表見 `README.md`。

## 一句話

假的一句話。

## 假品牌 要加的價值

說出測試怎麼改變看法。

## CTA

同主題的下一篇測試。

## 實戰紀錄

01、02 篇。
"""

EXPLAINER = """# explainer：理論教學

> 文體總表見 `README.md`。

## 假品牌 要加的價值

講清楚會改變哪個決定。

## 實戰紀錄

尚無已發布文章，以上是起始規範。
"""

LESSONS = """# Lessons Learnt

> 新的寫在最上面。

| 日期 | 文章 | 發生什麼 | 改成什麼 | 規則在哪 |
|---|---|---|---|---|
| 2026-10-14 | 2 | 第二次退件 | 改用第二條規則 | `craft/titles.md` |
| 2026-10-13 | 1 | 第一次退件 | 改用第一條規則 | `genres/essay.md` |
"""

STRATEGY = """# Content Strategy

## 北極星

不進主題檔。

## 主題的典型主張（原 SKILL §3 Pillars）

### P1 — 假主題

假主張一。

## 月主題與裂變

不進主題檔。
"""

CHART_STYLE = """import matplotlib

C = {
    "paper": "#FAFAF0",
    "ink": "#101010",
    "graphite": "#303030",
    "accent": "#123456",
    "warn": "#A05050",
    "mist": "#C0C0C0",
    "grid": "#E0E0E0",
}


def exhibit(fig):
    fig.text(0.04, 0.95, f"圖 {1}", color=C["accent"])
    fig.text(0.96, 0.025, "Fake Brand", color=C["ink"])
"""

MAKE_COVER = """FORMS = {
    "painting": "A fake painting form.",
    "photography": "A fake photography form.",
}

MEDIA = {
    "oil": (("painting",), "Fake oil."),
    "film": (("photography",), "Fake film."),
}

STYLES = {
    "realism": "Fake realism.",
}

PRESETS = {
    "fake-photo": ("photography", "film", "realism", (
        "Fake preset preamble, "
        "in two parts.")),
}

SHARED = (
    "Fake shared rules. "
    "Quiet and fake."
)

MARKET_PREAMBLE = "Fake market preamble."
SHARED_MARKET = SHARED.replace("Quiet and fake.", "Direct and fake.")
"""

SOURCE: dict[str, str] = {
    "SKILL.md": SKILL,
    "references/genres/README.md": GENRES_README,
    "references/genres/essay.md": ESSAY,
    "references/genres/explainer.md": EXPLAINER,
    "references/genres/poem.md": "# poem：不在 7 種裡\n",
    "references/craft/voice.md": "# Fake Voice Reference\n\n## 核心感覺\n\n冷靜。\n",
    "references/craft/openings.md": "# Openings\n\n開場用一個問題。\n",
    "references/craft/titles.md": "# Titles\n\n標題寫完才訂。\n",
    "references/craft/golden-lines-and-endings.md": "# Golden Lines\n\n收在一句話。\n",
    "references/craft/layout.md": "# Layout\n\n圖文分鏡。\n",
    "references/craft/figures.md": "# Figures\n\n封面三層。\n",
    "references/strategy/content-strategy.md": STRATEGY,
    "references/strategy/examples.md": "# Examples\n\n假標題範例。\n",
    "references/process/writing-pipeline.md": "# Pipeline\n\n不匯入。\n",
    "references/lessons-learnt.md": LESSONS,
    "scripts/chart_style.py": CHART_STYLE,
    "scripts/make_cover.py": MAKE_COVER,
}

EXPECTED_FILES = [
    "brand.yaml",
    "craft/figures.md",
    "craft/golden-lines-and-endings.md",
    "craft/layout.md",
    "craft/openings.md",
    "craft/title-examples.md",
    "craft/titles.md",
    "genres/essay.md",
    "genres/explainer.md",
    "lessons.md",
    "themes.md",
    "voice.md",
]


def _folder(root: Path) -> Path:
    """照真的那份的樣子排：repo 根目錄底下的 `.claude/skills/<名字>/`。"""
    skill = root / "pipeline" / ".claude" / "skills" / "fake-writing"
    for relative, text in SOURCE.items():
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "pipeline" / "README.md").write_text("不是 skill 的檔\n", encoding="utf-8")
    return root / "pipeline"


def _zip(root: Path) -> Path:
    folder = _folder(root / "unzipped")
    archive = root / "handoff.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zipped.write(path, "pipeline/" + path.relative_to(folder).as_posix())
    return archive


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    return DataDir(tmp_path / "data")


def _built(tmp_path: Path) -> dict[str, str]:
    return build_persona(read_source(_zip(tmp_path)), "me", default_genre="explainer", now=NOW)


def test_zip_與資料夾讀出一樣的內容(tmp_path: Path) -> None:
    from_zip = build_persona(read_source(_zip(tmp_path / "a")), "me", default_genre="explainer", now=NOW)
    from_folder = build_persona(
        read_source(_folder(tmp_path / "b")), "me", default_genre="explainer", now=NOW
    )
    # 只有檔頭註記的來源名不同（zip 檔名、資料夾名）
    assert {k: v.replace("handoff.zip", "pipeline") for k, v in from_zip.items()} == from_folder
    assert sorted(from_zip) == EXPECTED_FILES


def test_聲音_只取聲音相關的節_附上_craft_的聲音細節(tmp_path: Path) -> None:
    voice = _built(tmp_path)["voice.md"]
    meta = read_frontmatter_text(voice)
    assert (meta["id"], meta["name"], meta["default_genre"], meta["calibrated"]) == (
        "me",
        "Fake Brand",
        "explainer",
        True,
    )
    for kept in ("假任務", "假品牌主張", "像一個寫測試的人", "假推論", "不虛構測試結果", "假句型一號", "不虛構假資料", "冷靜。"):
        assert kept in voice
    # 給 Claude Code 判斷用途的節、文體表、其他品質關卡不是聲音
    for dropped in ("什麼時候用", "文體表在別處", "有一句主張"):
        assert dropped not in voice
    # craft 的聲音細節降一級，掛在聲音底下，不再有第二個一級標題
    assert "\n# " not in voice.split("---", 2)[2]
    assert "\n### 核心感覺" in voice


def test_文體風格_只寫_7_種裡的_價值改成通用的節名_沒有實戰的標未校準(tmp_path: Path) -> None:
    files = _built(tmp_path)
    essay = read_frontmatter_text(files["genres/essay.md"])
    explainer = read_frontmatter_text(files["genres/explainer.md"])
    assert (essay["author"], essay["genre"], essay["calibrated"]) == ("me", "essay", True)
    assert (explainer["genre"], explainer["calibrated"]) == ("explainer", False)
    assert "## 要多帶的價值\n\n說出測試怎麼改變看法。" in files["genres/essay.md"]
    assert "## CTA" in files["genres/essay.md"]
    assert "要加的價值" not in files["genres/essay.md"]
    assert "genres/poem.md" not in files


def test_手藝_每個主題一份(tmp_path: Path) -> None:
    files = _built(tmp_path)
    assert files["craft/openings.md"].endswith(SOURCE["references/craft/openings.md"])
    assert files["craft/title-examples.md"].endswith(SOURCE["references/strategy/examples.md"])


def test_主題_只列_8_個主題裡的_附典型主張(tmp_path: Path) -> None:
    themes = _built(tmp_path)["themes.md"]
    assert read_frontmatter_text(themes)["themes"] == ["education", "tools"]
    assert "假的教學主題" in themes and "假主張一。" in themes
    for dropped in ("不進主題檔", "cooking"):
        assert dropped not in themes


def test_lessons_一筆一行_舊的在前(tmp_path: Path) -> None:
    lessons = _built(tmp_path)["lessons.md"]
    entries = [line for line in lessons.splitlines() if line.startswith("- ")]
    assert entries == [
        "- 2026-10-13｜第 1 篇｜第一次退件 → 改用第一條規則（規則在 `genres/essay.md`）",
        "- 2026-10-14｜第 2 篇｜第二次退件 → 改用第二條規則（規則在 `craft/titles.md`）",
    ]


def test_brand_yaml_從腳本的字面值取色票與封面用語_讀得回來(tmp_path: Path, data: DataDir) -> None:
    import_persona(data, "me", _built(tmp_path), now=NOW)
    brand = load_brand(data, "me")
    assert brand.name == "Fake Brand"
    assert (brand.palette.accent, brand.palette.paper) == ("#123456", "#FAFAF0")
    assert brand.cover.forms["painting"] == "A fake painting form."
    assert brand.cover.media["film"].forms == ("photography",)
    preset = brand.cover.presets["fake-photo"]
    assert (preset.form, preset.medium, preset.style) == ("photography", "film", "realism")
    assert preset.preamble == "Fake preset preamble, in two parts."
    assert brand.cover.shared_rules == "Fake shared rules. Quiet and fake."
    assert brand.cover.market_preamble == "Fake market preamble."
    assert brand.cover.market_rules == "Fake shared rules. Direct and fake."


def test_沒有角色資料夾_直接寫_不做備份(tmp_path: Path, data: DataDir) -> None:
    result = import_persona(data, "me", _built(tmp_path), now=NOW)
    assert result.backup is None
    assert result.written == EXPECTED_FILES
    assert sorted(
        p.relative_to(data.authors / "me").as_posix()
        for p in (data.authors / "me").rglob("*")
        if p.is_file()
    ) == EXPECTED_FILES


def _existing(data: DataDir) -> Path:
    me = data.authors / "me"
    (me / "genres").mkdir(parents=True)
    (me / "voice.md").write_text("舊的聲音", encoding="utf-8")
    (me / "genres" / "_template.md").write_text("舊的範本", encoding="utf-8")
    (me / "genres" / "essay.md").write_text("舊的隨筆風格", encoding="utf-8")
    return me


def test_已有角色資料夾_先整份備份_再換掉_不刪任何檔(tmp_path: Path, data: DataDir) -> None:
    me = _existing(data)
    result = import_persona(data, "me", _built(tmp_path), now=NOW)

    backup = data.authors / ".backup" / "me-20261015-213005"
    assert result.backup == backup
    assert (backup / "voice.md").read_text(encoding="utf-8") == "舊的聲音"
    assert (backup / "genres" / "essay.md").read_text(encoding="utf-8") == "舊的隨筆風格"
    assert (backup / "genres" / "_template.md").read_text(encoding="utf-8") == "舊的範本"
    # 換掉的是匯入寫的檔；匯入沒有的檔原樣留著
    assert (me / "voice.md").read_text(encoding="utf-8") != "舊的聲音"
    assert (me / "genres" / "_template.md").read_text(encoding="utf-8") == "舊的範本"


def test_同一秒匯入兩次_備份不互相蓋掉(tmp_path: Path, data: DataDir) -> None:
    _existing(data)
    first = import_persona(data, "me", _built(tmp_path / "a"), now=NOW)
    second = import_persona(data, "me", _built(tmp_path / "b"), now=NOW)
    assert first.backup is not None and second.backup is not None
    assert first.backup != second.backup
    assert (first.backup / "voice.md").read_text(encoding="utf-8") == "舊的聲音"
    assert (second.backup / "voice.md").is_file()


def test_角色資料夾是符號連結_拒絕_什麼都不寫(tmp_path: Path, data: DataDir) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    data.authors.mkdir(parents=True)
    (data.authors / "me").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(BrandError):
        import_persona(data, "me", _built(tmp_path), now=NOW)
    assert list(elsewhere.iterdir()) == []
    assert not (data.authors / ".backup").exists()


def test_子資料夾連到角色資料夾之外_拒絕_什麼都不寫(tmp_path: Path, data: DataDir) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    me = data.authors / "me"
    me.mkdir(parents=True)
    (me / "voice.md").write_text("舊的聲音", encoding="utf-8")
    (me / "genres").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PersonaImportError, match="genres"):
        import_persona(data, "me", _built(tmp_path), now=NOW)
    assert list(elsewhere.iterdir()) == []
    assert (me / "voice.md").read_text(encoding="utf-8") == "舊的聲音"


@pytest.mark.parametrize("persona", ["../me", "", "a/b"])
def test_角色名不合格_拒絕(tmp_path: Path, data: DataDir, persona: str) -> None:
    with pytest.raises(ValueError):
        import_persona(data, persona, _built(tmp_path), now=NOW)


def test_找不到寫作系統_說清楚找的是什麼(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(PersonaImportError, match="references/genres/README.md"):
        read_source(tmp_path / "empty")


def test_來源裡有兩份寫作系統_拒絕(tmp_path: Path) -> None:
    root = _folder(tmp_path)
    copy = root / ".claude" / "skills" / "another"
    (copy / "references" / "genres").mkdir(parents=True)
    (copy / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (copy / "references" / "genres" / "README.md").write_text(GENRES_README, encoding="utf-8")
    with pytest.raises(PersonaImportError, match="兩份以上"):
        read_source(root)


def test_SKILL_md_找不到聲音的節_拒絕_不寫一份空的聲音(tmp_path: Path) -> None:
    # 換錯來源時不能用一份只有檔頭的 voice.md 蓋掉原本的聲音
    root = _folder(tmp_path)
    (root / ".claude" / "skills" / "fake-writing" / "SKILL.md").write_text("# 沒有編號的節\n", encoding="utf-8")
    with pytest.raises(PersonaImportError, match="聲音"):
        build_persona(read_source(root), "me", default_genre="explainer", now=NOW)


def test_預設文體不在_7_種裡_拒絕(tmp_path: Path) -> None:
    with pytest.raises(PersonaImportError, match="opinion"):
        build_persona(read_source(_zip(tmp_path)), "me", default_genre="opinion", now=NOW)


def test_命令列_dry_run_只列檔案不寫(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    monkeypatch.setenv("EF_DATA", str(root))
    archive = _zip(tmp_path)
    assert main([str(archive), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "voice.md" in out and "brand.yaml" in out
    assert not root.exists()


def test_命令列_真的匯入_印出備份位置與寫了哪些檔_不印內容(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    monkeypatch.setenv("EF_DATA", str(root))
    _existing(DataDir(root))
    assert main([str(_zip(tmp_path)), "--persona", "me"]) == 0
    out = capsys.readouterr().out
    assert ".backup" in out and "genres/essay.md" in out
    # 輸出會貼進報告與 PR：只列檔名，不印品牌內容
    for secret in ("假品牌主張", "#123456", "Fake Brand"):
        assert secret not in out
    assert (root / "authors" / "me" / "brand.yaml").is_file()
    assert os.listdir(root / "authors" / ".backup")


def read_frontmatter_text(text: str) -> dict[str, Any]:
    """還在記憶體裡的檔：取開頭兩條 `---` 之間的 YAML。"""
    assert text.startswith("---\n")
    loaded = yaml.safe_load(text.split("---\n", 2)[1])
    assert isinstance(loaded, dict)
    return loaded


def test_角色資料夾的格式範本_brand_yaml_讀得進來_就是中性預設值() -> None:
    template = Path(__file__).parents[2] / "harness" / "templates" / "author-brand.yaml"
    brand = load_brand_file(template)
    assert brand.palette == Brand().palette
    assert brand.name == ""
