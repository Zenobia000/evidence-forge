"""角色資料夾的規格，與「角色」頁（#116）用到的建立、複製、改顯示名稱（ADR-0026 §3.3）。

一個角色就是 `$EF_DATA/authors/<角色>/`，每個角色同一種樣子。**那個樣子的唯一規格在 `persona_spec.py`**
（這裡原樣再匯出）：`PARTS` 列出每一部分叫什麼、放哪裡、沒有的時候怎麼辦；畫面標「缺」與「用通用預設」
都從那裡來，不另寫第二份。檔案內容的格式範本在 `harness/templates/author-*.md`（只寫格式，不放品牌內容）；
把創作者現有的寫作系統拆進這個樣子的匯入腳本是 `persona_import.py`，寫出來的檔名跟這裡一樣。
`PARTS` 之外還有一個 `META_PART`（`persona.json`，顯示名稱）：它不是創作者寫的內容，是角色頁記的小檔，
所以不列在 `PARTS`（畫面不把它當成「缺了要補」的一部分）；資料夾名稱才是角色的身分。

不進 repo：角色資料夾裡是創作者的私人寫作系統（ADR-0014）。這個檔只知道檔名與欄位，不碰內容。

安全：
- 角色名先過 `jobkey.check_name`（英數字開頭，之後英數字、點、底線、連字號；擋 `..`、`/`、空白），
  再過 `figures.brand.persona_dir`（角色資料夾本身若是符號連結一律拒絕）。
- **建立與複製不覆蓋**：目標只要已經有東西（含壞掉的符號連結）就拒絕；用 `mkdir` 搶位，
  兩個請求同時建同一個名字，只有一個會成功。
- 角色資料夾裡的讀、複製、寫都走 `confined`（`O_NOFOLLOW` 加資料夾 fd）：沿路不跟隨任何符號連結，
  檢查與使用同一個 fd，所以「檢查完、動手前被換成連結」也沒有空檔。複製遇到連結一律略過並回報。
- 改顯示名稱只寫角色資料夾裡的 `persona.json`，不改資料夾名字：資料夾名字是角色的身分
  （工單的 `persona` 欄、帳本、對話都記它）。
"""

import json
import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from evidence_forge import confined
from evidence_forge.figures.brand import BrandError, persona_dir
from evidence_forge.harness import Harness
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.persona_spec import (
    BRAND_FILE,
    CRAFT_DIR,
    GENRE_SLUGS,
    GENRES_DIR,
    LESSONS_FILE,
    META_FILE,
    META_PART,
    PART_BY_KEY,
    PARTS,
    THEMES_FILE,
    VOICE_FILE,
    PartSpec,
    genre_file,
)

__all__ = [
    "BRAND_FILE",
    "CRAFT_DIR",
    "GENRES_DIR",
    "GENRE_SLUGS",
    "LESSONS_FILE",
    "META_FILE",
    "META_PART",
    "PARTS",
    "PART_BY_KEY",
    "THEMES_FILE",
    "VOICE_FILE",
    "PartSpec",
    "genre_file",
]

# 複製時不帶過去的檔：顯示名稱是這個角色自己的
_NOT_COPIED = (META_FILE,)

MAX_DISPLAY_NAME = 40
_VOICE_TEMPLATE = "author-voice.md"


class PersonaError(ValueError):
    """角色名不合規則、找不到、或會蓋掉已有的東西。`status` 是對應的 HTTP 狀態碼。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def folder_of(data: DataDir, name: str) -> Path:
    """已經存在的角色資料夾（`authors/<名字>/`）。名字不合規則 400、沒有這個角色 404。

    資料夾若是符號連結（指到外面或別的角色）一律拒絕，理由跟 `persona_dir` 一樣。
    """
    path = _checked(data, name)
    if not path.is_dir():
        raise PersonaError(f"沒有角色 {name}", 404)
    return path


def _checked(data: DataDir, name: str) -> Path:
    try:
        return persona_dir(data, name)
    except InvalidJobKey as exc:
        raise PersonaError(str(exc), 400) from exc
    except BrandError as exc:
        # 原訊息帶著資料目錄的絕對路徑；畫面只需要知道是哪個角色、為什麼
        raise PersonaError(f"角色資料夾 {name} 是符號連結，不接受", 400) from exc


def name_is_valid(name: str) -> bool:
    """這個資料夾名字能不能在畫面上開（手動建的資料夾可能不合規則，例如有空白或中文）。"""
    try:
        check_name("角色", name)
    except InvalidJobKey:
        return False
    return True


# --- 顯示名稱 -----------------------------------------------------------------


def clean_display_name(raw: object) -> str:
    """顯示名稱：去頭尾空白，1 到 40 字，不准換行與控制字元。"""
    if not isinstance(raw, str):
        raise PersonaError("顯示名稱要是文字")
    text = raw.strip()
    if not text:
        raise PersonaError("顯示名稱不能是空的")
    if len(text) > MAX_DISPLAY_NAME:
        raise PersonaError(f"顯示名稱最多 {MAX_DISPLAY_NAME} 字")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise PersonaError("顯示名稱不能有換行或控制字元")
    return text


def display_name(data: DataDir, name: str) -> str | None:
    """畫面上顯示的名字：`persona.json` 的 `display_name`；沒設、讀不懂都是 None（畫面改顯示資料夾名字）。

    `persona.json` 是連結（不管指到哪）就當沒設。
    """
    try:
        folder = persona_dir(data, name)
    except (InvalidJobKey, BrandError):
        return None
    text = confined.text(folder, META_FILE)
    if text is None:
        return None
    try:
        loaded = json.loads(text)
    except ValueError:
        return None
    value = loaded.get("display_name") if isinstance(loaded, dict) else None
    try:
        return clean_display_name(value)
    except PersonaError:
        return None


def rename(data: DataDir, name: str, new_display_name: object) -> str:
    """改顯示名稱，回存下去的名字。只寫 `persona.json`（先寫暫存檔再換上去），不動資料夾名字。

    寫的時候用角色資料夾的 fd（`confined.write_text_atomic`）：檢查完、動手前資料夾被換成連結，
    就寫不進去（409），不會寫到連結指到的地方。`persona.json` 若是連結，換上去的是連結本身。
    """
    folder = folder_of(data, name)
    cleaned = clean_display_name(new_display_name)
    _write_meta(folder, cleaned)
    return cleaned


def _meta_payload(shown: str) -> str:
    return json.dumps({"display_name": shown}, ensure_ascii=False, indent=2) + "\n"


def _write_meta(folder: Path, shown: str) -> None:
    try:
        confined.write_text_atomic(folder, META_FILE, _meta_payload(shown))
    except confined.FolderChanged as exc:
        raise PersonaError(f"角色資料夾 {folder.name} 在存檔前被換掉了，沒有寫", 409) from exc


def _write_meta_at(dir_fd: int, shown: str) -> None:
    confined.write_atomic_at(dir_fd, META_FILE, _meta_payload(shown))


# --- 建立 ---------------------------------------------------------------------


@contextmanager
def _claimed(data: DataDir, name: str) -> Iterator[tuple[Path, int]]:
    """搶下 `authors/<名字>/`（已經有任何東西、含壞掉的符號連結，就拒絕，不覆蓋），給新資料夾的路徑與 fd。

    用 `authors/` 的 fd 做 `mkdir`（搶位：兩個請求同時建同一個名字，只有一個會成功），再用 `O_NOFOLLOW` 開新資料夾。
    之後所有的寫都對這個 fd，不再用路徑：建好之後路徑被換成連結，寫的還是剛建的那個資料夾。
    區塊裡丟例外，剛建的資料夾整個移除（這個資料夾是剛剛才搶下來的，沒有別人的東西）。
    """
    try:
        check_name("角色", name)
    except InvalidJobKey as exc:
        raise PersonaError(str(exc), 400) from exc
    data.authors.mkdir(parents=True, exist_ok=True)
    # authors/ 本身可以是連結（整個資料目錄搬家）；只有角色資料夾那一節不准
    parent = os.open(data.authors, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        try:
            fd = confined.make_dir(parent, name)
        except FileExistsError as exc:
            raise PersonaError(f"已經有角色 {name}，不會蓋掉它；換一個名字", 409) from exc
        except confined.FolderChanged as exc:
            confined.remove_entry(parent, name)
            raise PersonaError(f"角色資料夾 {name} 剛建好就被換掉了，沒有建", 409) from exc
        try:
            yield data.authors / name, fd
        except BaseException:
            confined.remove_entry(parent, name)
            raise
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _set_front_matter_key(text: str, key: str, value: str) -> str:
    """只改開頭 front matter 裡 `key:` 那一行；沒有 front matter 或沒有這個欄位就原樣回傳。

    值用 JSON 字串寫（YAML 的子集），中文與冒號都安全。
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    head, rest = text[:end], text[end:]
    line = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    if not line.search(head):
        return text
    head = line.sub(lambda _: f"{key}: {json.dumps(value, ensure_ascii=False)}", head, count=1)
    return head + rest


def create_from_template(
    data: DataDir, harness: Harness, name: str, shown: object | None = None
) -> list[str]:
    """用範本建一個新角色，回寫了哪些檔。

    只建必要的 `voice.md`（範本的格式與欄位說明，`id` 填資料夾名稱，`name` 填顯示名稱，沒給就用資料夾名稱）
    加顯示名稱；其他部分缺的時候用通用預設（`PARTS`），畫面標出來。範本裡的例子是為了示範格式虛構的，
    所以不整包帶過去，免得別的角色的檔案長得像一個假品牌。
    """
    cleaned = clean_display_name(shown) if shown is not None else name
    template = harness.root / "templates" / _VOICE_TEMPLATE
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersonaError(f"讀不到範本 {_VOICE_TEMPLATE}：{exc}", 500) from exc
    text = _set_front_matter_key(text, "id", name)
    text = _set_front_matter_key(text, "name", cleaned)
    with _claimed(data, name) as (_, fd):
        confined.write_atomic_at(fd, VOICE_FILE, text)
        _write_meta_at(fd, cleaned)
    return [VOICE_FILE, META_FILE]


# --- 複製 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Copied:
    files: int
    # 沒複製的符號連結（相對路徑）：連結不跟隨，也不帶過去
    skipped_links: tuple[str, ...]


def copy_persona(data: DataDir, source: str, name: str, shown: object | None = None) -> Copied:
    """複製一個既有角色成新角色。新名字已經有東西就拒絕（不覆蓋）。

    - 只改「這是誰」的身分欄位成新資料夾名稱：`voice.md` 的 `id`、`themes.md` 與 `genres/*.md` 的 `author`。
      創作者寫的 `voice.md` 的 `name`（筆名）與 `brand.yaml` 的 `name` 一個字都不動；其餘內容位元組相同。
    - **lessons 的決定：** `lessons.md` 連同其他檔一起帶過去，當新角色的起點（那些規則是對這個聲音累積的，
      複製出來的聲音一開始就該守著它們）；之後兩邊各自累積，而且只有閘道寫（ADR-0026 §3.3，這一頁不寫）。
    - `persona.json`（顯示名稱）不帶，新角色用自己的：給了就用給的，沒給就用資料夾名稱。
    - 來源與目的地全程用資料夾 fd、不跟隨連結（`confined`）：連結一律略過並回報；檢查完被換成連結也不會讀到別處，
      新資料夾被換成連結也不會寫到別處。身分欄位在記憶體裡改好，每個檔只寫一次，不用路徑讀回來再寫。
    - 複製到一半失敗，剛建的資料夾整個移除。
    """
    origin = folder_of(data, source)
    cleaned = clean_display_name(shown) if shown is not None else name
    with confined.open_root(origin) as root:
        if root is None:
            raise PersonaError(f"角色資料夾 {source} 在複製前被換掉了，沒有複製", 409)
        skipped: list[str] = []
        try:
            with _claimed(data, name) as (_, fd):
                count = _copy_dir(root, fd, "", name, skipped)
                _write_meta_at(fd, cleaned)
        except confined.FolderChanged as exc:
            raise PersonaError(f"複製 {source} 到 {name} 的途中資料夾被換掉了，沒有複製", 409) from exc
    return Copied(files=count, skipped_links=tuple(skipped))


def _identity_key(relative: str) -> str | None:
    """這個檔開頭哪個欄位寫著「這是誰」；不是這幾個檔就是 None。"""
    if relative == VOICE_FILE:
        return "id"
    if relative == THEMES_FILE:
        return "author"
    parts = relative.split("/")
    if len(parts) == 2 and parts[0] == GENRES_DIR and parts[1].endswith(".md"):
        return "author"
    return None


def _copy_dir(src_fd: int, dst_fd: int, prefix: str, new_name: str, skipped: list[str]) -> int:
    """把 `src_fd` 這個資料夾的內容複製到 `dst_fd`，回複製了幾個檔。

    來源每一節都用 `O_NOFOLLOW` 相對於 fd 開、`fstat` 確認，再只對開好的 fd 讀；目的地用 `mkdir`／`O_EXCL|O_NOFOLLOW`
    相對於目的地 fd 建。列出來之後才被換成連結的來源，開的時候就會被擋下（記成略過）。
    """
    with os.scandir(src_fd) as entries:
        names = sorted(entry.name for entry in entries)
    count = 0
    for entry_name in names:
        relative = f"{prefix}{entry_name}"
        fd, kind = confined.open_entry(entry_name, src_fd)
        if kind == "link":
            skipped.append(relative)
            continue
        if fd is None:
            continue  # 列出來之後消失了，或是 FIFO、裝置這類：不是要複製的東西
        try:
            if kind == "dir":
                sub = confined.make_dir(dst_fd, entry_name)
                try:
                    count += _copy_dir(fd, sub, f"{relative}/", new_name, skipped)
                finally:
                    os.close(sub)
            elif prefix == "" and entry_name in _NOT_COPIED:
                pass
            else:
                with os.fdopen(fd, "rb") as source:
                    fd = -1  # 交給 fdopen 管了
                    _copy_file(source, dst_fd, entry_name, _identity_key(relative), new_name)
                count += 1
        finally:
            if fd >= 0:
                os.close(fd)
    return count


def _copy_file(source: BinaryIO, dst_fd: int, name: str, key: str | None, new_name: str) -> None:
    """複製一個檔。身分檔在記憶體裡改好再寫一次（不是 UTF-8 的原樣寫）；其他檔串流複製。"""
    with confined.create_file(dst_fd, name) as out:
        if key is None:
            shutil.copyfileobj(source, out)
            return
        raw = source.read()
        try:
            changed = _set_front_matter_key(raw.decode("utf-8"), key, new_name).encode("utf-8")
        except UnicodeDecodeError:
            changed = raw
        out.write(changed)
