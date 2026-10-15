"""角色資料夾的規格與建立、複製、改顯示名稱（`persona.py`，#116、ADR-0026 §3.3）。

ADR-0014：測試進公開 repo，所以這裡的角色全是為測試編的（假名字、假內容）；
`CANARY` 是放在「不該被讀到」的檔裡的暗號，結果裡出現它就是外洩。
"""

import json
import os
from pathlib import Path

import pytest

from evidence_forge import persona
from evidence_forge.documents import read_frontmatter
from evidence_forge.harness import default_harness
from evidence_forge.paths import DataDir
from evidence_forge.persona import PARTS, PersonaError
from evidence_forge.persona_import import SourceTree, build_persona
from evidence_forge.ui.format import GENRE_NAME

CANARY = "暗號-不該被讀到"
HARNESS = default_harness()


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    found = DataDir(tmp_path / "data")
    found.authors.mkdir(parents=True)
    return found


def _persona(data: DataDir, name: str, files: dict[str, str] | None = None) -> Path:
    folder = data.authors / name
    for relative, text in (files or {"voice.md": f"---\nid: {name}\nname: 假角色\n---\n\n## 語氣\n\n冷靜。\n"}).items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


# --- 規格 ---------------------------------------------------------------------


def test_規格_只有聲音是必要的_每一部分都說了沒有時怎麼辦() -> None:
    assert [p.key for p in PARTS] == ["voice", "genres", "craft", "themes", "lessons", "brand"]
    assert [p.key for p in PARTS if p.required] == ["voice"]
    assert all(p.fallback and p.holds and p.label for p in PARTS)
    assert len({p.path for p in PARTS}) == len(PARTS)


def test_規格_文體只認_7_種_跟_harness_的定義檔是同一份() -> None:
    assert set(persona.GENRE_SLUGS) == {p.stem for p in HARNESS.genre_files()}
    assert len(persona.GENRE_SLUGS) == 7


def test_規格_每一部分都有格式範本() -> None:
    templates = HARNESS.root / "templates"
    wanted = {
        "voice": "author-voice.md",
        "genres": "author-genre-style.md",
        "craft": "author-craft.md",
        "themes": "author-themes.md",
        "lessons": "author-lessons.md",
        "brand": "author-brand.yaml",
    }
    assert set(wanted) == {p.key for p in PARTS}
    for name in wanted.values():
        assert (templates / name).is_file(), name


def test_規格_匯入腳本寫出來的每個檔都在規格裡_不是兩份規格() -> None:
    source = SourceTree(
        origin="假來源",
        files={
            "SKILL.md": "# 假\n\n## 0. 甲\n\n假任務。\n",
            "references/genres/README.md": "# 假\n",
            "references/genres/essay.md": "# 假隨筆\n\n## 價值\n\n假。\n",
            "references/craft/titles.md": "# 假標題\n",
            "references/lessons-learnt.md": "| 日期 | 篇 | 事 | 改 | 規則 |\n|---|---|---|---|---|\n"
            "| 2026-10-13 | 1 | 假 | 假 | `x` |\n",
            "scripts/chart_style.py": 'C = {"accent": "#123456"}\n',
        },
    )
    written = build_persona(source, "fake", default_genre="essay", now=__import__("datetime").datetime(2026, 10, 15))

    def covered(relative: str) -> bool:
        return any(relative == p.path or (p.path.endswith("/") and relative.startswith(p.path)) for p in PARTS)

    assert written
    assert all(covered(relative) for relative in written), sorted(written)


# --- 角色名與資料夾 -----------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["..", "../x", "a/b", "a\\b", "", ".hidden", "_template", "a b", "a..b", "x" * 200, "小明", "a\x00b"]
)
def test_角色名不合規則_一律_400_什麼都沒建(data: DataDir, tmp_path: Path, name: str) -> None:
    _persona(data, "src")
    before = sorted(p.name for p in data.authors.iterdir())
    for call in (
        lambda: persona.folder_of(data, name),
        lambda: persona.create_from_template(data, HARNESS, name),
        lambda: persona.copy_persona(data, "src", name),
        lambda: persona.rename(data, name, "新名字"),
    ):
        with pytest.raises(PersonaError) as caught:
            call()
        assert caught.value.status == 400
    assert sorted(p.name for p in data.authors.iterdir()) == before
    assert not [p for p in tmp_path.rglob("*") if p.name in {"x" * 200}]


def test_沒有這個角色_404(data: DataDir) -> None:
    with pytest.raises(PersonaError) as caught:
        persona.folder_of(data, "ghost")
    assert caught.value.status == 404


def test_角色資料夾是符號連結_一律拒絕_指到外面或別的角色都一樣(data: DataDir, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.md").write_text(CANARY, encoding="utf-8")
    (data.authors / "evil").symlink_to(outside, target_is_directory=True)
    _persona(data, "real")
    (data.authors / "alias").symlink_to(data.authors / "real", target_is_directory=True)

    for name in ("evil", "alias"):
        with pytest.raises(PersonaError):
            persona.folder_of(data, name)
        with pytest.raises(PersonaError):
            persona.rename(data, name, "x")
        with pytest.raises(PersonaError):
            persona.copy_persona(data, name, "copy-of-" + name)
    assert not (outside / "persona.json").exists()
    assert not (data.authors / "copy-of-evil").exists()


# --- 顯示名稱 -----------------------------------------------------------------


def test_顯示名稱_去頭尾空白_擋空的_太長_控制字元() -> None:
    assert persona.clean_display_name("  小乙  ") == "小乙"
    for bad in ["", "   ", "字" * 41, "a\nb", "a\x00b", "a\x7fb", 5, None]:
        with pytest.raises(PersonaError):
            persona.clean_display_name(bad)
    assert persona.clean_display_name("字" * 40) == "字" * 40


def test_改顯示名稱_只寫_persona_json_資料夾名字與其他檔都不動(data: DataDir) -> None:
    folder = _persona(data, "me")
    before = (folder / "voice.md").read_bytes()
    assert persona.display_name(data, "me") is None

    assert persona.rename(data, "me", " 我自己 ") == "我自己"

    assert persona.display_name(data, "me") == "我自己"
    assert json.loads((folder / "persona.json").read_text(encoding="utf-8")) == {"display_name": "我自己"}
    assert (folder / "voice.md").read_bytes() == before
    assert sorted(p.name for p in data.authors.iterdir()) == ["me"]
    assert not list(folder.glob(".persona-*"))  # 暫存檔沒留下


def test_改顯示名稱_再改一次是換掉_不是疊上去(data: DataDir) -> None:
    _persona(data, "me")
    persona.rename(data, "me", "甲")
    persona.rename(data, "me", "乙")
    assert persona.display_name(data, "me") == "乙"


def test_改顯示名稱_persona_json_是連到外面的連結_不寫到連結指的地方(data: DataDir, tmp_path: Path) -> None:
    folder = _persona(data, "me")
    outside = tmp_path / "outside.json"
    outside.write_text('{"display_name": "外面的"}', encoding="utf-8")
    (folder / "persona.json").symlink_to(outside)

    assert persona.display_name(data, "me") is None  # 連結指到外面：不讀
    persona.rename(data, "me", "裡面的")

    assert json.loads(outside.read_text(encoding="utf-8")) == {"display_name": "外面的"}
    assert persona.display_name(data, "me") == "裡面的"


@pytest.mark.parametrize("content", ["不是 json", "[1, 2]", '{"display_name": 5}', '{"display_name": ""}', '{"x": 1}'])
def test_persona_json_壞掉_當成沒設_不丟例外(data: DataDir, content: str) -> None:
    folder = _persona(data, "me")
    (folder / "persona.json").write_text(content, encoding="utf-8")
    assert persona.display_name(data, "me") is None


# --- 從範本建立 ---------------------------------------------------------------


def test_從範本建立_只寫必要的聲音檔與顯示名稱_id_與_name_填好(data: DataDir) -> None:
    written = persona.create_from_template(data, HARNESS, "writer-2", " 第二支筆 ")

    folder = data.authors / "writer-2"
    assert written == ["voice.md", "persona.json"]
    assert sorted(p.name for p in folder.iterdir()) == ["persona.json", "voice.md"]
    front = read_frontmatter(folder / "voice.md")
    assert front["id"] == "writer-2"
    assert front["name"] == "第二支筆"
    assert front["calibrated"] is False
    assert persona.display_name(data, "writer-2") == "第二支筆"


def test_從範本建立_沒給顯示名稱就用資料夾名稱(data: DataDir) -> None:
    persona.create_from_template(data, HARNESS, "plain")
    assert persona.display_name(data, "plain") == "plain"


def test_從範本建立_名字含冒號與引號_front_matter_仍然讀得回來(data: DataDir) -> None:
    persona.create_from_template(data, HARNESS, "quoted", '甲: "乙" # 丙')
    assert read_frontmatter(data.authors / "quoted" / "voice.md")["name"] == '甲: "乙" # 丙'


def test_建立_不覆蓋_已經有的角色一個字都不動(data: DataDir) -> None:
    folder = _persona(data, "me", {"voice.md": f"我的聲音 {CANARY}", "lessons.md": "- 一條規則\n"})
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    with pytest.raises(PersonaError) as caught:
        persona.create_from_template(data, HARNESS, "me", "新的")

    assert caught.value.status == 409
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_建立_目標是壞掉的符號連結也算已經有東西_不蓋掉它(data: DataDir, tmp_path: Path) -> None:
    (data.authors / "dangling").symlink_to(tmp_path / "nowhere", target_is_directory=True)
    with pytest.raises(PersonaError) as caught:
        persona.create_from_template(data, HARNESS, "dangling")
    assert caught.value.status == 409
    assert (data.authors / "dangling").is_symlink()
    assert not (tmp_path / "nowhere").exists()


def test_建立_目標是同名的檔案_也不覆蓋(data: DataDir) -> None:
    (data.authors / "file").write_text("我是檔案", encoding="utf-8")
    with pytest.raises(PersonaError) as caught:
        persona.create_from_template(data, HARNESS, "file")
    assert caught.value.status == 409
    assert (data.authors / "file").read_text(encoding="utf-8") == "我是檔案"


def test_建立_authors_還不存在也能建(tmp_path: Path) -> None:
    empty = DataDir(tmp_path / "fresh")
    persona.create_from_template(empty, HARNESS, "first")
    assert (empty.authors / "first" / "voice.md").is_file()


def test_建立_寫到一半失敗_剛搶下的資料夾整個移除_下次可以再建(
    data: DataDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(folder: Path, shown: str) -> None:
        raise OSError("磁碟滿了")

    monkeypatch.setattr(persona, "_write_meta", boom)
    with pytest.raises(OSError):
        persona.create_from_template(data, HARNESS, "half")
    assert not (data.authors / "half").exists()

    monkeypatch.undo()
    persona.create_from_template(data, HARNESS, "half")
    assert (data.authors / "half" / "voice.md").is_file()


def test_建立_範本讀不到_不留空資料夾(data: DataDir, tmp_path: Path) -> None:
    empty_harness = type(HARNESS).at(tmp_path / "no-harness")
    with pytest.raises(PersonaError) as caught:
        persona.create_from_template(data, empty_harness, "tmpl")
    assert caught.value.status == 500
    assert not (data.authors / "tmpl").exists()


# --- 複製 ---------------------------------------------------------------------

SOURCE_FILES = {
    "voice.md": "---\nid: src\nname: 原角色\ndefault_genre: essay\ncalibrated: true\n---\n\n## 語氣\n\n冷靜。\n",
    "genres/essay.md": "---\nauthor: src\ngenre: essay\ncalibrated: true\n---\n\n## 要多帶的價值\n\n假價值。\n",
    "themes.md": "---\nauthor: src\nthemes: [education]\n---\n\n## 用哪幾個主題\n",
    "craft/titles.md": "# 標題\n\n假規則。\n",
    "lessons.md": "# 規則\n\n- 2026-10-01｜第 1 篇｜假 → 假\n",
    "brand.yaml": 'name: "假品牌"\n',
}


def test_複製_帶走所有檔_內容一個字節都一樣_除了寫著原角色名字的欄位(data: DataDir) -> None:
    source = _persona(data, "src", SOURCE_FILES)
    persona.rename(data, "src", "原本的")

    copied = persona.copy_persona(data, "src", "dup", "複本")

    target = data.authors / "dup"
    assert copied.files == len(SOURCE_FILES)
    assert copied.skipped_links == ()
    for relative in ("craft/titles.md", "lessons.md", "brand.yaml"):
        assert (target / relative).read_bytes() == (source / relative).read_bytes()
    assert read_frontmatter(target / "voice.md")["id"] == "dup"
    assert read_frontmatter(target / "voice.md")["name"] == "複本"
    assert read_frontmatter(target / "voice.md")["default_genre"] == "essay"
    assert read_frontmatter(target / "genres" / "essay.md")["author"] == "dup"
    assert read_frontmatter(target / "genres" / "essay.md")["calibrated"] is True
    assert read_frontmatter(target / "themes.md")["author"] == "dup"
    # 顯示名稱是各自的：原角色的不帶
    assert persona.display_name(data, "dup") == "複本"
    assert persona.display_name(data, "src") == "原本的"
    # 原角色一個字都沒動
    assert read_frontmatter(source / "voice.md")["id"] == "src"
    assert read_frontmatter(source / "genres" / "essay.md")["author"] == "src"


def test_複製_沒給顯示名稱就用新資料夾名稱_不帶原角色的顯示名稱(data: DataDir) -> None:
    _persona(data, "src", SOURCE_FILES)
    persona.rename(data, "src", "原本的")
    persona.copy_persona(data, "src", "dup")
    assert persona.display_name(data, "dup") == "dup"


def test_複製_不覆蓋_目標已經有的角色拒絕_一個字都不動(data: DataDir) -> None:
    _persona(data, "src", SOURCE_FILES)
    existing = _persona(data, "taken", {"voice.md": f"別人的聲音 {CANARY}"})
    before = {str(p): p.read_bytes() for p in existing.rglob("*") if p.is_file()}

    with pytest.raises(PersonaError) as caught:
        persona.copy_persona(data, "src", "taken")

    assert caught.value.status == 409
    assert {str(p): p.read_bytes() for p in existing.rglob("*") if p.is_file()} == before
    assert (existing / "voice.md").read_text(encoding="utf-8").endswith(CANARY)


def test_複製_自己複製成自己_也是已經有_拒絕(data: DataDir) -> None:
    folder = _persona(data, "src", SOURCE_FILES)
    before = (folder / "voice.md").read_bytes()
    with pytest.raises(PersonaError) as caught:
        persona.copy_persona(data, "src", "src")
    assert caught.value.status == 409
    assert (folder / "voice.md").read_bytes() == before


def test_複製_符號連結不跟隨_也不帶過去_並回報略過了哪些(data: DataDir, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(CANARY, encoding="utf-8")
    source = _persona(data, "src", SOURCE_FILES)
    (source / "craft" / "leak.md").symlink_to(outside / "secret.md")
    (source / "leakdir").symlink_to(outside, target_is_directory=True)
    (source / "dangling.md").symlink_to(tmp_path / "nowhere.md")

    copied = persona.copy_persona(data, "src", "dup")

    target = data.authors / "dup"
    assert set(copied.skipped_links) == {"craft/leak.md", "leakdir", "dangling.md"}
    assert not any(p.is_symlink() for p in target.rglob("*"))
    assert all(CANARY not in p.read_text(encoding="utf-8") for p in target.rglob("*") if p.is_file())
    assert not (target / "leakdir").exists()


def test_複製_不是_UTF8_的檔照樣複製_原樣的位元組(data: DataDir) -> None:
    source = _persona(data, "src", SOURCE_FILES)
    (source / "craft" / "odd.md").write_bytes(b"\xff\xfe\x00bad")
    (source / "genres" / "trend.md").write_bytes(b"---\n\xff\n---\n")

    persona.copy_persona(data, "src", "dup")

    assert (data.authors / "dup" / "craft" / "odd.md").read_bytes() == b"\xff\xfe\x00bad"
    assert (data.authors / "dup" / "genres" / "trend.md").read_bytes() == b"---\n\xff\n---\n"


def test_複製_沒有_front_matter_的聲音檔_原樣複製(data: DataDir) -> None:
    _persona(data, "src", {"voice.md": "只有內文，沒有 front matter\n"})
    persona.copy_persona(data, "src", "dup")
    assert (data.authors / "dup" / "voice.md").read_text(encoding="utf-8") == "只有內文，沒有 front matter\n"


def test_複製_來源不存在_404_不建目標(data: DataDir) -> None:
    with pytest.raises(PersonaError) as caught:
        persona.copy_persona(data, "ghost", "dup")
    assert caught.value.status == 404
    assert not (data.authors / "dup").exists()


def test_複製_複製到一半失敗_剛建的資料夾整個移除_原角色不動(data: DataDir, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _persona(data, "src", SOURCE_FILES)
    before = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("磁碟滿了")

    monkeypatch.setattr(persona, "_retarget", boom)
    with pytest.raises(OSError):
        persona.copy_persona(data, "src", "dup")

    assert not (data.authors / "dup").exists()
    assert {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()} == before


def test_建立_同時建同一個名字_只有一個成功(data: DataDir) -> None:
    import threading

    results: list[str] = []

    def attempt() -> None:
        try:
            persona.create_from_template(data, HARNESS, "race")
            results.append("ok")
        except PersonaError as exc:
            results.append(str(exc.status))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["409"] * 7 + ["ok"]
    assert os.path.isfile(data.authors / "race" / "voice.md")


def test_規格_GENRE_NAME_就是_7_種() -> None:
    assert tuple(GENRE_NAME) == persona.GENRE_SLUGS
