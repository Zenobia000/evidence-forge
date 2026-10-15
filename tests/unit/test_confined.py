"""`confined`：只在一個資料夾裡讀與寫，沿路不跟隨符號連結（#116 審查）。全是為測試編的內容。"""

import os
from pathlib import Path

import pytest

from evidence_forge import confined

CANARY = "暗號-不該被讀到"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "persona"
    (root / "craft").mkdir(parents=True)
    (root / "voice.md").write_text("---\na: 1\n---\n聲音\n", encoding="utf-8")
    (root / "craft" / "titles.md").write_text("標題\n", encoding="utf-8")
    return root


def test_讀一般檔案與子資料夾裡的檔(folder: Path) -> None:
    assert confined.read_text(folder, "voice.md") == ("file", "---\na: 1\n---\n聲音\n")
    assert confined.text(folder, "craft/titles.md") == "標題\n"


def test_BOM_去掉_CRLF_換成_LF(folder: Path) -> None:
    (folder / "x.md").write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    assert confined.text(folder, "x.md") == "a\nb\n"


def test_不存在與不是_UTF8_與太大的檔(folder: Path) -> None:
    assert confined.read_text(folder, "nope.md") == ("missing", None)
    (folder / "bad.md").write_bytes(b"\xff\xfe\x00")
    assert confined.read_text(folder, "bad.md") == ("file", None)
    (folder / "big.md").write_bytes(b"a" * (confined.MAX_TEXT_BYTES + 1))
    assert confined.read_text(folder, "big.md") == ("large", None)  # 太大：跟「不是 UTF-8」分開說
    (folder / "edge.md").write_bytes(b"a" * confined.MAX_TEXT_BYTES)
    assert confined.read_text(folder, "edge.md")[0] == "file"  # 剛好等於上限還讀得到


def test_連結不跟隨_指到外面或指到裡面的檔都一樣(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(CANARY, encoding="utf-8")
    (folder / "out.md").symlink_to(outside)
    (folder / "in.md").symlink_to(folder / "voice.md")
    (folder / "dangling.md").symlink_to(tmp_path / "nowhere")

    for name in ("out.md", "in.md", "dangling.md"):
        assert confined.read_text(folder, name) == ("link", None), name
        assert confined.kind_of(folder, name) == "link"


def test_路徑中間有連結_也不跟隨(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text(CANARY, encoding="utf-8")
    (folder / "linkdir").symlink_to(outside, target_is_directory=True)

    assert confined.text(folder, "linkdir/secret.md") is None
    assert confined.kind_of(folder, "linkdir") == "link"
    assert confined.listdir(folder, "linkdir") == ("link", [])


def test_資料夾本身是連結_什麼都讀不到(folder: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(folder, target_is_directory=True)
    assert confined.read_text(alias, "voice.md") == ("missing", None)
    assert confined.kind_of(alias, "voice.md") == "missing"
    assert confined.listdir(alias, "craft") == ("missing", [])


def test_資料夾上層是連結_照一般方式解析_整個資料目錄搬家的情形(folder: Path, tmp_path: Path) -> None:
    parent_link = tmp_path / "authors-link"
    parent_link.symlink_to(folder.parent, target_is_directory=True)
    assert confined.text(parent_link / "persona", "voice.md") is not None


@pytest.mark.parametrize("relative", ["", "/etc/passwd", "../x", "craft/../voice.md", "a\x00b", "."])
def test_不合規則的相對路徑_讀不到(folder: Path, relative: str) -> None:
    assert confined.text(folder, relative) is None


def test_FIFO_不會卡住_種類是_other(folder: Path) -> None:
    os.mkfifo(folder / "pipe")
    assert confined.read_text(folder, "pipe") == ("other", None)


def test_listdir_給名字與種類_連結標出來(folder: Path, tmp_path: Path) -> None:
    (folder / "craft" / "leak.md").symlink_to(tmp_path / "x")
    (folder / "craft" / "sub").mkdir()
    kind, entries = confined.listdir(folder, "craft")
    assert kind == "dir"
    assert entries == [("leak.md", "link"), ("sub", "dir"), ("titles.md", "file")]
    assert confined.listdir(folder, "voice.md") == ("file", [])
    assert confined.listdir(folder, "nope") == ("missing", [])


def test_寫入_換上去_不留暫存檔(folder: Path) -> None:
    confined.write_text_atomic(folder, "persona.json", "{}\n")
    confined.write_text_atomic(folder, "persona.json", '{"a": 1}\n')
    assert (folder / "persona.json").read_text(encoding="utf-8") == '{"a": 1}\n'
    assert sorted(p.name for p in folder.iterdir()) == ["craft", "persona.json", "voice.md"]


def test_寫入_目標是連結_換掉連結本身_不寫到它指的檔(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("外面的", encoding="utf-8")
    (folder / "persona.json").symlink_to(outside)
    confined.write_text_atomic(folder, "persona.json", "裡面的")
    assert outside.read_text(encoding="utf-8") == "外面的"
    assert not (folder / "persona.json").is_symlink()


def test_寫入_資料夾是連結_丟_FolderChanged_外面沒被寫(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(confined.FolderChanged):
        confined.write_text_atomic(alias, "persona.json", "x")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["", "a/b", "..", "."])
def test_寫入_檔名不合規則(folder: Path, name: str) -> None:
    with pytest.raises(ValueError):
        confined.write_text_atomic(folder, name, "x")


def test_開檔後_fstat_失敗_fd_不漏(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    def boom(fd: int) -> os.stat_result:
        raise OSError("fstat 壞了")

    before = open_fds()
    with confined.open_root(folder) as root:
        assert root is not None
        monkeypatch.setattr(os, "fstat", boom)
        with pytest.raises(OSError):
            confined.open_entry("voice.md", root)
        with pytest.raises(OSError):
            confined._walk(root, "craft/titles.md")
        monkeypatch.undo()
    assert open_fds() == before


def test_make_dir_建好立刻被換成連結_丟_FolderChanged_外面沒被寫(folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    real_mkdir = os.mkdir

    def swap(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)
        os.rename(path, "moved", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.symlink(outside, path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", swap)
    with confined.open_root(folder) as root:
        assert root is not None
        with pytest.raises(confined.FolderChanged):
            confined.make_dir(root, "child")
    assert list(outside.iterdir()) == []


def test_create_file_已經有東西_含連結_就失敗_不覆蓋(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("外面的", encoding="utf-8")
    (folder / "link.md").symlink_to(outside)
    with confined.open_root(folder) as root:
        assert root is not None
        for name in ("link.md", "voice.md"):
            with pytest.raises(OSError):
                confined.create_file(root, name)
        with confined.create_file(root, "new.md") as out:
            out.write("新的".encode())
    assert outside.read_text(encoding="utf-8") == "外面的"
    assert (folder / "new.md").read_text(encoding="utf-8") == "新的"


def test_remove_entry_連結只移除連結_資料夾整個移除(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.md").write_text("留著", encoding="utf-8")
    (folder / "link").symlink_to(outside, target_is_directory=True)
    with confined.open_root(folder) as root:
        assert root is not None
        confined.remove_entry(root, "link")
        confined.remove_entry(root, "craft")
        confined.remove_entry(root, "nope")
    assert not (folder / "link").exists() and not (folder / "craft").exists()
    assert (outside / "keep.md").read_text(encoding="utf-8") == "留著"
