"""角色資料夾的規格，與「角色」頁（#116）用到的建立、複製、改顯示名稱（ADR-0026 §3.3）。

一個角色就是 `$EF_DATA/authors/<角色>/`，每個角色同一種樣子。**這個檔是那個樣子的唯一規格**：
`PARTS` 列出每一部分叫什麼、放哪裡、沒有的時候怎麼辦；畫面標「缺」與「用通用預設」都從這裡來，
不另寫第二份。檔案內容的格式範本在 `harness/templates/author-*.md`（只寫格式，不放品牌內容）；
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
from dataclasses import dataclass
from pathlib import Path

from evidence_forge import confined
from evidence_forge.figures.brand import BrandError, persona_dir
from evidence_forge.harness import Harness
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.paths import DataDir
from evidence_forge.ui.format import GENRE_NAME

MAX_DISPLAY_NAME = 40
_VOICE_TEMPLATE = "author-voice.md"


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
# 複製時不帶過去的檔：顯示名稱是這個角色自己的
_NOT_COPIED = (META_FILE,)
# 文體風格檔只認 7 種（ADR-0026 §3.1）；舊的 12 種不支援
GENRE_SLUGS: tuple[str, ...] = tuple(GENRE_NAME)


def genre_file(slug: str) -> str:
    """某一種文體的風格檔，相對於角色資料夾：`genres/<slug>.md`。"""
    return f"{GENRES_DIR}/{slug}.md"


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


def _write_meta(folder: Path, shown: str) -> None:
    payload = json.dumps({"display_name": shown}, ensure_ascii=False, indent=2) + "\n"
    try:
        confined.write_text_atomic(folder, META_FILE, payload)
    except confined.FolderChanged as exc:
        raise PersonaError(f"角色資料夾 {folder.name} 在存檔前被換掉了，沒有寫", 409) from exc


# --- 建立 ---------------------------------------------------------------------


def _claim(data: DataDir, name: str) -> Path:
    """搶下 `authors/<名字>/`：已經有任何東西（含壞掉的符號連結）就拒絕，不覆蓋。"""
    try:
        check_name("角色", name)
    except InvalidJobKey as exc:
        raise PersonaError(str(exc), 400) from exc
    data.authors.mkdir(parents=True, exist_ok=True)
    path = data.authors / name
    try:
        path.mkdir()  # 搶位：兩個請求同時建同一個名字，只有一個會成功
    except FileExistsError as exc:
        raise PersonaError(f"已經有角色 {name}，不會蓋掉它；換一個名字", 409) from exc
    return _checked(data, name)


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

    只建必要的 `voice.md`（範本的格式與欄位說明，`id` 填資料夾名稱，`name` 填顯示名稱，沒給就用資料夾名稱）加顯示名稱；其他部分缺的時候
    用通用預設（`PARTS`），畫面標出來。範本裡的例子是為了示範格式虛構的，所以不整包帶過去，
    免得別的角色的檔案長得像一個假品牌。
    """
    cleaned = clean_display_name(shown) if shown is not None else name
    template = harness.root / "templates" / _VOICE_TEMPLATE
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersonaError(f"讀不到範本 {_VOICE_TEMPLATE}：{exc}", 500) from exc
    folder = _claim(data, name)
    try:
        text = _set_front_matter_key(text, "id", name)
        text = _set_front_matter_key(text, "name", cleaned)
        confined.write_text_atomic(folder, VOICE_FILE, text)
        _write_meta(folder, cleaned)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # 這個資料夾是剛剛才搶下來的，沒有別人的東西
        raise
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
    - 讀來源全程用 fd、不跟隨連結（`confined`）：連結一律略過並回報；檢查完被換成連結也不會讀到別處。
    - 複製到一半失敗，剛建的資料夾整個移除。
    """
    origin = folder_of(data, source)
    cleaned = clean_display_name(shown) if shown is not None else name
    with confined.open_root(origin) as root:
        if root is None:
            raise PersonaError(f"角色資料夾 {source} 在複製前被換掉了，沒有複製", 409)
        folder = _claim(data, name)
        skipped: list[str] = []
        try:
            count = _copy_dir(root, folder, "", skipped)
            _retarget(folder, name)
            _write_meta(folder, cleaned)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return Copied(files=count, skipped_links=tuple(skipped))


def _copy_dir(src_fd: int, target: Path, prefix: str, skipped: list[str]) -> int:
    """把 `src_fd` 這個資料夾的內容複製到 `target`。每一節都用 `O_NOFOLLOW` 相對於 fd 開、`fstat` 確認，
    再只對開好的 fd 複製：列出來之後才被換成連結的東西，這裡開的時候就會被擋下（記成略過）。"""
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
        if kind == "dir":
            try:
                (target / entry_name).mkdir()
                count += _copy_dir(fd, target / entry_name, f"{relative}/", skipped)
            finally:
                os.close(fd)
        elif prefix == "" and entry_name in _NOT_COPIED:
            os.close(fd)
        else:
            with os.fdopen(fd, "rb") as source, open(target / entry_name, "xb") as out:
                shutil.copyfileobj(source, out)
            count += 1
    return count


def _retarget(folder: Path, name: str) -> None:
    """複製出來的檔案開頭寫著原角色的資料夾名稱：改成新角色的。只動身分欄位，筆名等其他欄位不碰。

    讀不懂的檔（不是 UTF-8）原樣留著。這個資料夾是剛建的、裡面沒有連結（複製時都略過了）。
    """
    _rewrite_key(folder / VOICE_FILE, "id", name)
    _rewrite_key(folder / THEMES_FILE, "author", name)
    genres = folder / GENRES_DIR
    if genres.is_dir():
        for path in sorted(genres.glob("*.md")):
            _rewrite_key(path, "author", name)


def _rewrite_key(path: Path, key: str, value: str) -> None:
    if not path.is_file() or path.is_symlink():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    changed = _set_front_matter_key(text, key, value)
    if changed != text:
        path.write_text(changed, encoding="utf-8")
