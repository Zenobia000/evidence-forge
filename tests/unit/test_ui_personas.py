"""角色頁讀資料夾的那一層（`ui/personas.py`）：每一部分有沒有、缺了用什麼預設、唯讀顯示、寫過幾篇、最近的 lessons。

ADR-0014：全是為測試編的假角色；`CANARY` 放在不該被讀到的地方，出現在畫面的 HTML 裡就是外洩。
"""

from pathlib import Path

import pytest
from chat_folder import RECIPES, ChatFolder

from evidence_forge import persona
from evidence_forge.chat.desk import list_personas
from evidence_forge.paths import DataDir
from evidence_forge.persona import PARTS
from evidence_forge.ui import personas

CANARY = "暗號-不該被讀到"


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    world = ChatFolder(tmp_path / "data")
    world.data.authors.mkdir(parents=True, exist_ok=True)
    return world


def _write(data: DataDir, name: str, files: dict[str, str]) -> Path:
    root = data.authors / name
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


FULL = {
    "voice.md": "---\nid: me\ndefault_genre: essay\ncalibrated: false\n---\n\n## 語氣\n\n冷靜，**不誇張**。\n",
    "genres/essay.md": "---\ncalibrated: true\n---\n\n## 要多帶的價值\n\n假。\n",
    "genres/trend.md": "---\ncalibrated: false\n---\n\n## 要多帶的價值\n\n假。\n",
    "genres/old-type.md": "---\ncalibrated: true\n---\n\n舊的 12 種之一。\n",
    "craft/titles.md": "# 標題\n\n假規則。\n",
    "craft/openings.md": "# 開場\n\n假規則。\n",
    "themes.md": "---\nthemes: [education]\n---\n\n## 用哪幾個主題\n",
    "lessons.md": "# 規則\n\n- 2026-10-01｜第 1 篇｜甲 → 乙\n- 2026-10-02｜第 2 篇｜丙 → 丁\n",
    "brand.yaml": 'name: "假品牌"\npalette:\n  accent: "#123456"\nfont:\n  dirs: []\n',
}


def _by_key(parts: tuple[personas.PartView, ...]) -> dict[str, personas.PartView]:
    return {p.spec.key: p for p in parts}


# --- 有沒有、缺了什麼 ---------------------------------------------------------


def test_每一部分都有_沒有缺的(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", FULL)
    parts = personas.part_views(root)
    assert [p.spec.key for p in parts] == [p.key for p in PARTS]
    assert all(p.present for p in parts)
    assert [f.path for f in _by_key(parts)["craft"].files] == ["craft/openings.md", "craft/titles.md"]


def test_缺的部分標出來_只有聲音的角色缺其餘五個(folder: ChatFolder) -> None:
    root = _write(folder.data, "bare", {"voice.md": "## 語氣\n\n冷靜。\n"})
    missing = [p.spec.key for p in personas.part_views(root) if not p.present]
    assert missing == ["genres", "craft", "themes", "lessons", "brand"]
    detail = personas.build_detail(folder.data, "bare", {})
    assert [m.key for m in detail.missing] == missing
    # 缺的每一部分都說得出用什麼預設（畫面照字顯示）
    assert all(m.fallback for m in detail.missing)


def test_沒有聲音的角色_聲音標必要缺(folder: ChatFolder) -> None:
    root = _write(folder.data, "novoice", {"themes.md": "x"})
    voice = _by_key(personas.part_views(root))["voice"]
    assert not voice.present and voice.spec.required


def test_craft_裡只算_md_檔_空資料夾算缺(folder: ChatFolder) -> None:
    root = _write(folder.data, "c", {"voice.md": "x", "craft/notes.txt": "不是 md"})
    assert not _by_key(personas.part_views(root))["craft"].present


def test_文體列_7_種各一列_舊的_12_種檔不算(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", FULL)
    rows = {r.slug: r for r in personas.genre_rows(root)}
    assert len(rows) == 7
    assert (rows["essay"].written, rows["essay"].calibrated) == (True, True)
    assert (rows["trend"].written, rows["trend"].calibrated) == (True, False)
    assert (rows["explainer"].written, rows["explainer"].calibrated) == (False, False)
    assert "old-type" not in rows


def test_文體校準_front_matter_壞掉當成沒校準(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x", "genres/essay.md": "---\ncalibrated: [\n---\n"})
    assert not next(r for r in personas.genre_rows(root) if r.slug == "essay").calibrated


def test_文體校準_跟對話的_list_personas_算法一致(folder: ChatFolder) -> None:
    _write(folder.data, "me", FULL)
    listed = list_personas(folder.data)["personas"][0]
    root = folder.data.authors / "me"
    rows = personas.genre_rows(root)
    assert [r.slug for r in rows if r.written] == listed["written_genres"]
    assert [r.slug for r in rows if r.calibrated] == listed["calibrated_genres"]


# --- 唯讀顯示 -----------------------------------------------------------------


def test_檔案轉成安全的_HTML_front_matter_另外列_原始_HTML_不放行(folder: ChatFolder) -> None:
    root = _write(
        folder.data,
        "me",
        {"voice.md": "---\ncalibrated: false\n---\n\n## 語氣\n\n<script>alert(1)</script> **粗**\n"},
    )
    view = personas.part_views(root)[0].files[0]
    assert view.state == "ok"
    assert "<h2>語氣</h2>" in view.html and "<strong>粗</strong>" in view.html
    assert "<script>" not in view.html
    assert dict(view.meta) == {"calibrated": "false"}
    assert "---" not in view.html


def test_連到角色資料夾外面的檔_不讀_標成不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(CANARY, encoding="utf-8")
    root = _write(folder.data, "me", {"voice.md": "x"})
    (root / "themes.md").symlink_to(outside)
    (root / "genres").mkdir()
    (root / "genres" / "essay.md").symlink_to(outside)

    parts = _by_key(personas.part_views(root))

    for key in ("themes", "genres"):
        assert not parts[key].present and parts[key].blocked
        assert [f.state for f in parts[key].files] == ["unsafe"]
        assert all(CANARY not in str(f.html) for f in parts[key].files)
    assert not next(r for r in personas.genre_rows(root) if r.slug == "essay").written


def test_craft_資料夾本身連到外面_整個不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "titles.md").write_text(CANARY, encoding="utf-8")
    root = _write(folder.data, "me", {"voice.md": "x"})
    (root / "craft").symlink_to(outside, target_is_directory=True)

    craft = _by_key(personas.part_views(root))["craft"]

    assert not craft.present and craft.blocked
    assert all(CANARY not in str(f.html) for f in craft.files)


def test_craft_裡的連結檔_不讀_其他照讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(CANARY, encoding="utf-8")
    root = _write(folder.data, "me", {"voice.md": "x", "craft/ok.md": "# 好的\n"})
    (root / "craft" / "leak.md").symlink_to(outside)

    craft = _by_key(personas.part_views(root))["craft"]

    assert {f.path: f.state for f in craft.files} == {"craft/leak.md": "unsafe", "craft/ok.md": "ok"}
    assert craft.present and craft.blocked
    assert all(CANARY not in str(f.html) for f in craft.files)


def test_不是_UTF8_的檔_標讀不出來_不丟例外(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x"})
    (root / "themes.md").write_bytes(b"\xff\xfe\x00")
    themes = _by_key(personas.part_views(root))["themes"]
    assert [f.state for f in themes.files] == ["unreadable"]
    assert not themes.present


# --- lessons ------------------------------------------------------------------


def test_最近的_lessons_新的在前_最多_10_筆_總數照實(folder: ChatFolder) -> None:
    items = "\n".join(f"- 規則 {n:02d}" for n in range(1, 16))
    root = _write(folder.data, "me", {"voice.md": "x", "lessons.md": f"# 規則\n\n<!-- 註解\n- 不算我 -->\n\n{items}\n"})
    recent, total = personas.recent_lessons(root)
    assert total == 15
    assert recent == tuple(f"規則 {n:02d}" for n in range(15, 5, -1))


def test_lessons_沒有檔或沒有條目(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x"})
    assert personas.recent_lessons(root) == ((), 0)
    (root / "lessons.md").write_text("# 只有標題\n", encoding="utf-8")
    assert personas.recent_lessons(root) == ((), 0)


def test_lessons_是連到外面的連結_不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(f"- {CANARY}\n", encoding="utf-8")
    root = _write(folder.data, "me", {"voice.md": "x"})
    (root / "lessons.md").symlink_to(outside)
    assert personas.recent_lessons(root) == ((), 0)


# --- brand.yaml ---------------------------------------------------------------


def test_brand_攤成欄位對值的表_只列檔案裡寫了的(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", FULL)
    rows, problem = personas.brand_table(root)
    assert problem is None
    assert dict(rows) == {"name": "假品牌", "palette.accent": "#123456", "font.dirs": ""}


def test_brand_欄位不合格_照樣列出_另外說原因(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x", "brand.yaml": 'palette:\n  accent: "紅色"\n'})
    rows, problem = personas.brand_table(root)
    assert dict(rows) == {"palette.accent": "紅色"}
    assert problem is not None and "欄位不合格" in problem
    assert str(folder.data.root) not in problem  # 不把資料目錄的絕對路徑端到畫面上


def test_brand_不是_YAML_說原因_沒有表(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x", "brand.yaml": "a: [\n"})
    assert personas.brand_table(root) == ((), "不是合法的 YAML")


def test_brand_沒有檔_沒有表也沒有錯(folder: ChatFolder) -> None:
    root = _write(folder.data, "me", {"voice.md": "x"})
    assert personas.brand_table(root) == ((), None)


# --- 寫過幾篇 -----------------------------------------------------------------


def test_寫過幾篇_同一條版本鏈算一篇_換角色另算_沒交出文章的不算(folder: ChatFolder) -> None:
    cid = folder.conversation("對話一")
    folder.article(cid, "w1", persona="me")
    folder.article(cid, "w2", persona="me", parent="w1", version=2)
    folder.article(cid, "w3", persona="other")
    failed = folder.article(cid, "w4", persona="me", status="done", outcome="failed", reason="測試")
    (failed / "stages" / "rewrite" / "1" / "article.md").unlink()  # 失敗的單沒交出文章
    other = folder.conversation("對話二", minutes=5)
    folder.article(other, "w5", persona="me", minutes=5)
    folder.analysis(cid, "a1")  # 分析工單不算文章

    counts = personas.article_counts(folder.data, RECIPES)

    assert counts == {"me": (2, 3), "other": (1, 1)}


def test_寫過幾篇_不在對話裡的工單也算(folder: ChatFolder) -> None:
    folder.article(None, "loose-1", persona="me")
    assert personas.article_counts(folder.data, RECIPES) == {"me": (1, 1)}


def test_寫過幾篇_沒有任何工單_空的(folder: ChatFolder) -> None:
    assert personas.article_counts(folder.data, RECIPES) == {}


def test_舊工單沒寫角色_當成_me(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.job(cid, "old", recipe="rewrite", persona=None)
    folder.write("old", "stages/rewrite/1/article.md", "# 舊文章\n")
    assert personas.article_counts(folder.data, RECIPES) == {"me": (1, 1)}


# --- 清單與詳細 ---------------------------------------------------------------


def test_清單就是對話的_list_personas_多加顯示名稱篇數與缺什麼(folder: ChatFolder) -> None:
    _write(folder.data, "me", FULL)
    _write(folder.data, "bare", {"voice.md": "x"})
    persona.rename(folder.data, "me", "我自己")

    rows = personas.build_rows(folder.data, {"me": (2, 3)})

    listed = list_personas(folder.data)["personas"]
    assert [r["name"] for r in rows] == [p["name"] for p in listed] == ["bare", "me"]
    me = next(r for r in rows if r["name"] == "me")
    assert {k: me[k] for k in listed[1]} == listed[1]  # 對話那份的欄位一個不少、值一樣
    assert (me["shown"], me["articles"], me["versions"], me["missing"]) == ("我自己", 2, 3, [])
    bare = next(r for r in rows if r["name"] == "bare")
    assert (bare["shown"], bare["articles"]) == ("bare", 0)
    assert [m.key for m in bare["missing"]] == ["genres", "craft", "themes", "lessons", "brand"]


def test_清單_資料夾名字不合規則的也列出_但標打不開(folder: ChatFolder) -> None:
    _write(folder.data, "小明", {"voice.md": "x"})
    row = personas.build_rows(folder.data, {})[0]
    assert row["name"] == "小明" and not row["openable"] and row["problem"]


def test_清單_符號連結的角色資料夾_標打不開_不讀它的內容(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.md").write_text(CANARY, encoding="utf-8")
    (folder.data.authors / "evil").symlink_to(outside, target_is_directory=True)
    row = personas.build_rows(folder.data, {})[0]
    assert not row["openable"] and row["problem"]
    assert CANARY not in repr(row)


def test_詳細_角色不存在_丟_404(folder: ChatFolder) -> None:
    with pytest.raises(persona.PersonaError) as caught:
        personas.build_detail(folder.data, "ghost", {})
    assert caught.value.status == 404
