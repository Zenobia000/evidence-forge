"""只在一個資料夾「裡面」讀與寫：沿路不跟隨任何符號連結（POSIX 的 `O_NOFOLLOW` 加資料夾 fd）。

為什麼不用「先 resolve、再檢查有沒有跑出資料夾、然後開檔」：檢查與開檔之間有空檔（TOCTOU），
這時把檔案或資料夾換成連結，開出來的就是連結指到的地方。這裡改成一路用 fd：
先開角色資料夾本身（最後一節是連結就失敗），再用 `dir_fd` 一節一節往下開，每一節都帶 `O_NOFOLLOW`，
開好之後用 `fstat` 看它真的是資料夾或一般檔案，之後只對 fd 操作。檢查的對象與使用的對象是同一個 fd，沒有空檔。

用在創作者私人的角色資料夾（ADR-0014）：角色清單、預設文體、「角色」頁、複製、改顯示名稱都走這裡。
連結一律不跟隨，不管它指到資料夾外面還是裡面；要用連結的人請自己改成真的檔案。

只支援 POSIX（Linux、WSL；專案的資料目錄就在那裡）。平台沒有 `O_NOFOLLOW` 或 `dir_fd` 時直接說不支援，
不退回「先檢查再開」的做法。
"""

import errno
import os
import secrets
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

# 一節路徑是什麼：一般檔案、資料夾、符號連結、其他（FIFO、裝置…），或根本不存在
# `large` 只有 `read_text` 會給：是一般檔案，但超過 `MAX_TEXT_BYTES`，不讀
Kind = Literal["file", "dir", "link", "other", "missing", "large"]

SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)

# O_NONBLOCK：開到 FIFO 時不會卡住（開完用 fstat 就把它擋掉）
_OPEN = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
# 角色檔都是小文字檔；超過這個大小就不讀、也不顯示（畫面說「太大」，不是悄悄當成讀不出來）
MAX_TEXT_BYTES = 5_000_000


class Unsupported(RuntimeError):
    """這個平台沒有 `O_NOFOLLOW` 或 `dir_fd`。"""


def _require() -> None:
    if not SUPPORTED:
        raise Unsupported("這個平台不支援無連結的檔案存取（需要 O_NOFOLLOW 與 dir_fd）")


def _kind_of(mode: int) -> Kind:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "link"
    return "other"


def open_entry(name: str, dir_fd: int) -> tuple[int | None, Kind]:
    """在 `dir_fd` 底下開一節（只有一節，不含 `/`）。回（fd, 種類）：是一般檔案或資料夾才有 fd。

    連結（`O_NOFOLLOW` 開不了，ELOOP）回 `link`；不存在回 `missing`；FIFO、裝置這類回 `other`（fd 已關）。
    """
    _require()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        return None, "other"
    try:
        fd = os.open(name, _OPEN, dir_fd=dir_fd)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return None, "link"
        return None, "other"
    try:
        kind = _kind_of(os.fstat(fd).st_mode)
    except BaseException:
        os.close(fd)  # 開好之後任何一步失敗，fd 都不能漏
        raise
    if kind in ("file", "dir"):
        return fd, kind
    os.close(fd)
    return None, kind


@contextmanager
def open_root(folder: Path) -> Iterator[int | None]:
    """開資料夾本身，給一個資料夾 fd；它是連結、不存在或不是資料夾就給 None。

    資料夾路徑上更前面的節（例如 `authors/` 本身是連結，整個資料目錄搬家）照一般方式解析；
    只有最後一節（角色資料夾）不准是連結。
    """
    _require()
    try:
        fd = os.open(folder, _OPEN | os.O_DIRECTORY)
    except OSError:
        yield None
        return
    try:
        yield fd
    finally:
        os.close(fd)


def _walk(root_fd: int, relative: str) -> tuple[int | None, Kind]:
    """從 `root_fd` 沿著 `relative`（POSIX 相對路徑）一節一節往下開；每一節都不跟隨連結。"""
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/"):
        return None, "other"
    current = os.dup(root_fd)
    kind: Kind = "dir"
    for position, part in enumerate(parts):
        try:
            fd, kind = open_entry(part, current)
        except BaseException:
            os.close(current)
            raise
        os.close(current)
        if fd is None:
            return None, kind
        if position < len(parts) - 1 and kind != "dir":
            os.close(fd)
            return None, "other"
        current = fd
    return current, kind


def kind_of(folder: Path, relative: str) -> Kind:
    """`folder` 底下這個相對路徑是什麼；資料夾本身開不了（是連結、不存在）回 `missing`。"""
    with open_root(folder) as root:
        if root is None:
            return "missing"
        fd, kind = _walk(root, relative)
        if fd is not None:
            os.close(fd)
        return kind


def read_text(folder: Path, relative: str) -> tuple[Kind, str | None]:
    """讀 `folder` 底下的文字檔。回（種類, 內容）：內容只有在它是一般檔案、不太大、是 UTF-8 時才有。

    種類 `large`：是一般檔案但超過 `MAX_TEXT_BYTES`（讀了前面一段就停，不會把整個檔讀進記憶體）。

    BOM 去掉、CRLF 換成 LF（跟 `documents` 讀檔一致）。
    """
    with open_root(folder) as root:
        if root is None:
            return "missing", None
        fd, kind = _walk(root, relative)
        if fd is None:
            return kind, None
        if kind != "file":
            os.close(fd)
            return kind, None
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES + 1)
    if len(raw) > MAX_TEXT_BYTES:
        return "large", None
    try:
        return "file", raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return "file", None


def text(folder: Path, relative: str) -> str | None:
    """`read_text` 只要內容：不是一般檔案（含連結）、讀不到、不是 UTF-8 都是 None。"""
    return read_text(folder, relative)[1]


def listdir(folder: Path, relative: str) -> tuple[Kind, list[tuple[str, Kind]]]:
    """`folder` 底下一個資料夾裡的東西（名字排序）與各自的種類（不跟隨連結）。

    資料夾本身是連結、不存在或不是資料夾：回它的種類與空清單。
    """
    with open_root(folder) as root:
        if root is None:
            return "missing", []
        fd, kind = _walk(root, relative)
        if fd is None:
            return kind, []
        try:
            if kind != "dir":
                return kind, []
            found: list[tuple[str, Kind]] = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    try:
                        found.append((entry.name, _kind_of(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)))
                    except OSError:
                        found.append((entry.name, "other"))
            return "dir", sorted(found)
        finally:
            os.close(fd)


def write_text_atomic(folder: Path, name: str, content: str) -> None:
    """在 `folder` 底下寫 `name`：先寫同一個資料夾裡的暫存檔，再用 `rename` 換上去。

    全程用 `folder` 的 fd：`folder` 在檢查之後被換成連結，這裡開不起來（丟 `FolderChanged`），
    不會寫到連結指到的地方。`name` 本身若是連結，換上去的是連結本身，不是它指的檔案。
    """
    with open_root(folder) as root:
        if root is None:
            raise FolderChanged(f"{folder.name} 不是一個真的資料夾（被換成連結或移走了）")
        write_atomic_at(root, name, content)


def write_atomic_at(dir_fd: int, name: str, content: str) -> None:
    """同 `write_text_atomic`，但資料夾已經是開好的 fd。"""
    _require()
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"檔名不對：{name!r}")
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(temporary, flags, 0o666, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(content)
        os.rename(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def append_text(folder: Path, name: str, content: str) -> None:
    """在 `folder` 底下的 `name` 檔尾附加 `content`（沒有就建）：只增不改，不重寫整個檔。

    全程用 `folder` 的 fd，檔本身用 `O_NOFOLLOW` 開、`fstat` 確認是一般檔案：
    `folder` 或 `name` 是（或剛被換成）符號連結，就不寫、也不寫到連結指到的地方。
    檔尾不是換行（上一筆寫到一半，或手改過）先補一個換行，這一筆才不會黏在上一行後面；
    `content` 用一次 `O_APPEND` 的 write 寫完。
    """
    _require()
    if not name or "/" in name or name in (".", "..") or "\x00" in name:
        raise ValueError(f"檔名不對：{name!r}")
    with open_root(folder) as root:
        if root is None:
            raise FolderChanged(f"{folder.name} 不是一個真的資料夾（被換成連結或移走了）")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(name, flags, 0o666, dir_fd=root)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise EntryRefused(f"{name} 是符號連結，不寫") from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise EntryRefused(f"{name} 不是一般檔案，不寫")
            size = os.fstat(fd).st_size
            data = content.encode("utf-8")
            if size > 0 and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data
            written = os.write(fd, data)
        finally:
            os.close(fd)
    if written != len(data):
        raise OSError(f"只寫進 {written}／{len(data)} bytes：{name}")


def make_dir(dir_fd: int, name: str) -> int:
    """在 `dir_fd` 底下建一個新資料夾（`mkdir` 搶位：已經有任何東西就丟 `FileExistsError`），回它的 fd。

    建好之後用 `O_NOFOLLOW` 開：建與開之間被換成連結，這裡開不起來，丟 `FolderChanged`。
    """
    _require()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise ValueError(f"資料夾名稱不對：{name!r}")
    os.mkdir(name, 0o777, dir_fd=dir_fd)
    try:
        return os.open(name, _OPEN | os.O_DIRECTORY, dir_fd=dir_fd)
    except OSError as exc:
        raise FolderChanged(f"{name} 剛建好就被換掉了") from exc


def create_file(dir_fd: int, name: str) -> BinaryIO:
    """在 `dir_fd` 底下新建一個檔，回可寫的二進位檔案物件。`O_EXCL|O_NOFOLLOW`：已經有東西（含連結）就失敗，不覆蓋。"""
    _require()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise ValueError(f"檔名不對：{name!r}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    return os.fdopen(os.open(name, flags, 0o666, dir_fd=dir_fd), "wb")


def remove_entry(dir_fd: int, name: str) -> None:
    """移除 `dir_fd` 底下的一個東西（資料夾整個移除；連結只移除連結本身，不碰它指到的地方）。不存在就算了。"""
    _require()
    try:
        mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except OSError:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(name, ignore_errors=True, dir_fd=dir_fd)
    else:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass


class FolderChanged(OSError):
    """檢查過的資料夾在動手之前被換掉了。"""


class EntryRefused(OSError):
    """要寫的檔是符號連結或不是一般檔案，沒有寫。"""
