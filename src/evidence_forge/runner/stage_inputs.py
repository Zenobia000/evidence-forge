"""開段之前，執行器把 agent 要讀的參考資料放進工單的 `input/`（ADR-0015 §3.7、ADR-0018）。

agent 只看得到系統提示裡的 `SKILL.md`，與工單資料夾裡的檔（`read_file` 只能讀工單資料夾）。
文體定義（`harness/genres/`）與角色設定（`$EF_DATA/authors/<角色>/`）都不在工單裡，所以由這裡複製進去：

- `input/genres.md`：7 種文體的通用定義接成一份（ADR-0026；同資料夾的主題清單 `themes.md` 不算），
  brief 段用來判斷原文的文體、查證段替題材建議文體；
- `input/target-genre.md`：目標文體的定義（主證據、必要內容、骨架），重構段用；
- `input/author/voice.md`：角色的聲音；
- `input/author/genre-style.md`：角色在目標文體的風格；角色沒寫這一種就沒有這個檔（未校準）。

舊的轉換說明（`harness/conversions/`）是用舊 12 種文類寫的，ADR-0026 §3.4 之後不再放。

- `input/headline-punchline.md`：標題與金句規則（`$EF_DATA/style/headline-punchline-system.md`，
  創作者自己的寫作系統，跨角色共用、不進 repo），重構段用；
- 從上一張單接著做（`ef new --from`，ADR-0019 §3.6）：上一張單過了程式檢查的產出
  （`input/report.md`、`input/brief.md`、`input/brief.json`、上一版文章 `input/article.md`）
  與它的來源正文 `input/sources/S<n>.md`。上一張單自己也是接著做的，它帶來的也一起帶過來。
  原文全文（`input/content.md`）不帶：重構不讀原文全文（FR-RW-01）。
- 照編輯卡寫稿（#110）：編輯卡快照 `input/card/` 是開單時放的（`intake.open_job_from_card`；改稿也是開單時
  拿一份新的，上一版的不再帶過來，只在新工單沒有快照時當備援）。這種單只放這一篇用得到的（ADR-0026 §3.3），
  本機的上下文才塞得下：
  - 從報告的題材開始：報告，與報告「可以寫的題材」那一個題材列的來源（分析的 `sources.json` 的 `topics`）；
    找不到那份清單就照舊帶全部。brief 不帶（報告已經講了寫稿要的）。
  - 角色：寫稿用的 craft（`persona_spec.WRITING_CRAFT`；分鏡整篇不配圖時不帶圖文編排 `layout.md`）、
    這篇的主題在角色 `themes.md` 裡的那一節（`input/author/theme.md`）、最近十筆 lessons（`input/author/lessons.md`）。
    角色有自己的標題或金句 craft 時不再放共用的標題與金句規則。角色資料一律不跟隨符號連結讀（`confined`）。

查證段找到的來源正文（`input/sources/`）在分析工單裡不在這裡放：那一段結束後才知道有哪些，見 `sources.py`。

整份參考資料只放一次（只增不改）：全部放完才寫 `input/.prepared.json`，有這份就整段跳過，
所以重跑、退件重跑、當機重開都沿用第一次那一份，連「當時沒有的檔」也一起凍結；
請求逐位元組不變，閘道的快取才對得上，不會重新付錢。每個檔先寫暫存檔再用 `os.link`
換成正式檔名（原子動作、已存在就失敗），當機只會留下暫存檔，不會留下半截的正式檔。
`SKILL.md` 只准提到 `PROVIDED` 裡的路徑（`tests/unit/test_skills.py`）。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evidence_forge import confined
from evidence_forge.checks.report import ChecksReport
from evidence_forge.documents import FrontmatterError, read_frontmatter, split_frontmatter
from evidence_forge.harness import Harness
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.models import Job
from evidence_forge.paths import DataDir
from evidence_forge.persona_spec import (
    CRAFT_DIR,
    HEADLINE_CRAFT,
    LAYOUT_CRAFT,
    THEMES_FILE,
    VOICE_FILE,
    WRITING_CRAFT,
    genre_file,
)
from evidence_forge.write_once import write_once

_log = logging.getLogger(__name__)

# 拿不到題材的來源清單、只好帶全部來源時交回給執行器的說明（記進事件紀錄）
ALL_SOURCES_NOTE = "題材的來源對不上，帶了全部來源"

# 工單沒指定角色時用創作者自己（舊的 job.md 沒有 persona 欄）
AUTHOR = "me"

CONTENT = "input/content.md"
GENRES = "input/genres.md"
TARGET_GENRE = "input/target-genre.md"
AUTHOR_DIR = "input/author/"
VOICE = AUTHOR_DIR + "voice.md"
GENRE_STYLE = AUTHOR_DIR + "genre-style.md"
# 照編輯卡寫稿才放的角色資料（#110）
AUTHOR_CRAFT = AUTHOR_DIR + "craft/"
AUTHOR_THEME = AUTHOR_DIR + "theme.md"
AUTHOR_LESSONS = AUTHOR_DIR + "lessons.md"
# 編輯卡快照（ADR-0025 §3.4）：開單時放，寫稿讀 `card.md`，另外兩份是留底；作者素材照 sha256 放在 materials/
CARD_DIR = "input/card/"
CARD_BRIEF = CARD_DIR + "card.md"
CARD_JSON = CARD_DIR + "card.json"
CARD_EVENTS = CARD_DIR + "decisions.jsonl"
# 查證段之後，執行器照 sources.json 放的來源正文（`sources.py`，#70）
SOURCES_DIR = "input/sources/"
HEADLINE_RULES = "input/headline-punchline.md"
# 從上一張單帶過來的產出：檔名 → 放在新工單的哪裡（`carried_files`）
REPORT = "input/report.md"
BRIEF = "input/brief.md"
BRIEF_JSON = "input/brief.json"
PREVIOUS_ARTICLE = "input/article.md"
CARRIED = {
    "report.md": REPORT,
    "brief.md": BRIEF,
    "brief.json": BRIEF_JSON,
    "article.md": PREVIOUS_ARTICLE,
}
# 參考資料放完的紀錄；不給 agent 讀，所以不在 PROVIDED 裡
PREPARED = "input/.prepared.json"

# agent 讀得到的工單路徑（`content.md` 由開單時寫進去）；結尾是 / 的表示整個資料夾
PROVIDED = (
    CONTENT,
    GENRES,
    TARGET_GENRE,
    AUTHOR_DIR,
    SOURCES_DIR,
    HEADLINE_RULES,
    CARD_DIR,
    *CARRIED.values(),
)

_CHECKS_FILE = re.compile(r"^checks\.v([0-9]+)\.json$")


class InputError(ValueError):
    """參考資料放不進去（例：目標文類不存在）；重跑模型修不好。"""


def author_dir(data: DataDir, author: str = AUTHOR) -> Path:
    return data.authors / author


def job_persona(job: Job) -> str:
    """這張單用哪個角色；沒寫就是創作者自己。"""
    return job.persona or AUTHOR


def personas(data: DataDir) -> list[str]:
    """現有的角色：`authors/` 底下每個資料夾一個（`_`、`.` 開頭的是範本或暫存，不算）。"""
    if not data.authors.is_dir():
        return []
    return sorted(
        path.name
        for path in data.authors.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def _passed_reports(job_dir: Path) -> list[ChecksReport]:
    """一張單過了的檢查結果，照版本由舊到新；讀不懂的略過。"""
    directory = job_dir / "checks"
    if not directory.is_dir():
        return []
    numbered = sorted(
        (int(match.group(1)), path)
        for path in directory.iterdir()
        if (match := _CHECKS_FILE.match(path.name))
    )
    found: list[ChecksReport] = []
    for _, path in numbered:
        try:
            report = ChecksReport.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            continue
        if report.passed:
            found.append(report)
    return found


def parent_dir(data: DataDir, job_id: str) -> Path:
    """上一張工單（`--from`、`Job.parent`）的資料夾；id 不合規則或跑出 `jobs/` 就丟 `InputError`。

    id 跟工單鑰匙用同一條字元規則（`jobkey.check_name`：沒有 `/`、`..`、空白）；
    P3 以後這個 id 會來自對話的模型，所以解析出來的路徑（連結也照著走）還要確認在 `jobs/` 底下。
    """
    try:
        check_name("工單 id", job_id)
    except InvalidJobKey as exc:
        raise InputError(str(exc)) from None
    directory = data.job_dir(job_id)
    if not directory.resolve().is_relative_to(data.jobs.resolve()):
        raise InputError(f"工單 {job_id} 的資料夾不在 {data.jobs} 底下")
    return directory


def carried_files(parent_dir: Path) -> dict[str, Path]:
    """上一張單交得出、要帶進新工單的檔：新工單裡的位置 → 上一張單裡的檔。

    - 段產出只帶**過了程式檢查**的那一次（執行器寫的 `checks.vN.json`）；同一個檔名有好幾份時取最新的。
      沒過的產出（例：查證段第一次少一節的報告）不算。
    - 上一張單自己從更上一張帶來的（`input/report.md` 等）也帶：再改的工單從上一版拿報告。
      上一張單有自己的產出時，以產出為準。上一版文章例外：只認上一張單自己過了檢查的文章，
      它沒寫成就沒有，不會拿更早那一版充數（再改的意見是對上一張的文章寫的）。
    - 來源正文 `input/sources/S<n>.md` 全部帶。原文全文 `input/content.md` 不帶。
    """
    found: dict[str, Path] = {}
    for report in _passed_reports(parent_dir):
        for relative in report.files:
            path = parent_dir / relative
            target = CARRIED.get(path.name)
            if target is not None and path.is_file() and not path.is_symlink():
                found[target] = path
    for target in CARRIED.values():
        inherited = parent_dir / target
        # 上一版文章只算上一張單自己寫、過了檢查的那一份；它沒寫成，不拿更早那一版充數
        if target != PREVIOUS_ARTICLE and target not in found and inherited.is_file():
            found[target] = inherited
    card = parent_dir / CARD_DIR
    if card.is_dir() and not card.is_symlink():
        # 編輯卡快照整份帶：再改照編輯卡寫的文章，讀的是同一張卡的同一份快照（#110）
        for path in sorted(card.rglob("*")):
            if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
                found[path.relative_to(parent_dir).as_posix()] = path
    sources = parent_dir / SOURCES_DIR
    if sources.is_dir():
        for path in sorted(sources.iterdir()):
            if (
                path.is_file()
                and path.name.endswith(".md")
                and not path.name.startswith(".")
            ):
                found[SOURCES_DIR + path.name] = path
    return found


def uncalibrated(job_dir: Path, job: Job) -> str | None:
    """這張單的角色在目標文類還沒校準時，給人看的一句話；校準過或沒有目標文類是 None（ADR-0015 §3.6）。

    看的是工單裡實際放了什麼：開段時放進來的文類風格有寫 `calibrated: true` 才算校準過。
    """
    genre = job.target_genre
    if genre is None:
        return None
    style = job_dir / GENRE_STYLE
    calibrated = False
    if style.is_file():
        try:
            calibrated = read_frontmatter(style).get("calibrated") is True
        except FrontmatterError:
            calibrated = False
    if calibrated:
        return None
    persona = job_persona(job)
    return (
        f"未校準：角色 {persona} 還沒有校準過的 {genre} 文體風格"
        f"（authors/{persona}/genres/{genre}.md），照文體的通用定義與聲音寫"
    )


def default_genre(data: DataDir, author: str = AUTHOR) -> str | None:
    """作家設定的預設文類（`voice.md` frontmatter 的 `default_genre`）；沒有作家設定就是 None。

    不跟隨符號連結（`confined`）：角色資料夾或 `voice.md` 是連結就當沒有，不讀連結指到的檔案。
    """
    text = confined.text(author_dir(data, author), VOICE_FILE)
    if text is None:
        return None
    try:
        value = split_frontmatter(text)[0].get("default_genre")
    except FrontmatterError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _card_meta(job_dir: Path) -> dict[str, Any] | None:
    """照編輯卡寫的單（有 `input/card/card.json`）的編輯卡快照；不是這種單、或讀不懂是 None。"""
    path = job_dir / CARD_JSON
    if path.is_symlink() or not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _report_topic(card: dict[str, Any]) -> int | None:
    """從報告第 N 個題材開始的卡的 N；不是從報告開始是 None。"""
    origin = card.get("origin")
    topic = origin.get("topic") if isinstance(origin, dict) and origin.get("kind") == "report_topic" else None
    if isinstance(topic, bool) or not isinstance(topic, int) or topic < 1:
        return None
    return topic


def _topic_sources(parent: Path, card: dict[str, Any]) -> set[str] | None:
    """從報告第 N 個題材開始的卡：分析 `sources.json` 的 `topics` 裡那個題材列的來源編號。

    不是從報告開始、或上一張單沒有過了檢查的來源清單、或題材編號對不上：None（照舊帶全部的來源）。
    """
    topic = _report_topic(card)
    if topic is None:
        return None
    listed: Path | None = None
    for report in _passed_reports(parent):
        for relative in report.files:
            if Path(relative).name == "sources.json":
                listed = parent / relative
    if listed is None or not listed.is_file() or listed.is_symlink():
        return None
    try:
        topics = json.loads(listed.read_text(encoding="utf-8")).get("topics")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(topics, list) or topic > len(topics) or not isinstance(topics[topic - 1], dict):
        return None
    ids = topics[topic - 1].get("sources")
    if not isinstance(ids, list):
        return None
    return {i for i in ids if isinstance(i, str)}


def _latest_lessons(data: DataDir, persona: str) -> list[str]:
    """角色最近十筆 lessons；沒有、或是符號連結（`lessons.py` 擋下）就是空的。"""
    # lessons.py 從這個模組拿角色清單：在函式裡匯入，模組之間不互相卡住
    from evidence_forge.gateway.scope import ToolRejected
    from evidence_forge.lessons import latest_lessons

    try:
        return latest_lessons(data, persona)
    except ToolRejected:
        return []


_HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*$")


def theme_stance(themes: str, theme: str) -> str | None:
    """角色 `themes.md` 裡跟這個主題有關的部分：表格裡這個主題的那一列，與標題提到它的那一節。

    格式見 `harness/templates/author-themes.md`（`## 用哪幾個主題` 的表格、`## 對每個主題的主張` 底下
    一個主題一節）。標題或表格第一格寫 slug 才認得；都沒有就是 None（不整份送，ADR-0026 §3.3）。
    """
    try:
        body = split_frontmatter(themes)[1]
    except FrontmatterError:
        body = themes
    marker = f"`{theme}`"
    found: list[str] = []
    table_head: list[str] = []
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[index + 1]):
            table_head = [line, lines[index + 1]]
        if line.startswith("|") and marker in line.split("|")[1]:
            found += [*table_head, line, ""]
    section: list[str] | None = None
    for line in lines:
        heading = _HEADING.match(line)
        if heading is not None:
            if section:
                found += [*section, ""]
            words = heading.group(2).replace("`", " ").split()
            section = [line] if len(heading.group(1)) == 3 and theme in words else None
            continue
        if section is not None:
            section.append(line)
    if section:
        found += section
    text = "\n".join(found).strip()
    if not text:
        return None
    return f"# 角色對主題 `{theme}` 的看法\n\n{text}\n"


def put_once(job_dir: Path, relative: str, data: str | bytes) -> None:
    """放一個檔進工單：先寫暫存檔再用 `os.link` 換成正式檔名；已經有了就沿用那一份。"""
    target = job_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    # 固定的暫存檔名：上次當機留下的半截暫存檔，這次直接蓋掉
    partial = target.with_name(f".{target.name}.partial")
    partial.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    try:
        os.link(partial, target)
    except FileExistsError:
        # 上次當機前已經放好（link 是原子的，所以是完整的）：沿用那一份
        pass
    finally:
        partial.unlink()


def prepare_inputs(harness: Harness, data: DataDir, job_dir: Path, job: Job) -> list[str]:
    """把這張單用得到的參考資料放進 `input/`；放過一次（有 `PREPARED`）就整段跳過。

    回傳要讓人知道的事（執行器記進事件紀錄），例如拿不到題材的來源清單、只好帶全部來源：
    多帶的來源可能讓本機模型的上下文放不下，寫稿就會升級到雲端。
    """
    notes: list[str] = []
    if (job_dir / PREPARED).is_file():
        return notes
    placed: list[str] = []

    def put(relative: str, content: str | bytes) -> None:
        put_once(job_dir, relative, content)
        placed.append(relative)

    definitions = harness.genre_files()
    if definitions:
        put(
            GENRES,
            "\n\n".join(path.read_text(encoding="utf-8") for path in definitions),
        )

    target = job.target_genre
    if target is not None:
        # 只認文體定義檔：同資料夾的主題清單、舊工單的 12 種文類 id 都不是（ADR-0026 §3.4）
        definition = next((p for p in definitions if p.stem == target), None)
        if definition is None:
            raise InputError(f"目標文體 {target} 不在 harness/genres/ 的 7 種裡")
        put(TARGET_GENRE, definition.read_bytes())

    # 開單時放的編輯卡快照（照編輯卡寫稿、或改這種稿）
    own_card = _card_meta(job_dir)
    if job.parent is not None:
        parent = parent_dir(data, job.parent)
        if not parent.is_dir():
            raise InputError(f"找不到上一張工單 {job.parent}")
        carried = carried_files(parent)
        card_job = own_card is not None or CARD_JSON in carried
        topic = _topic_sources(parent, own_card) if own_card is not None else None
        if topic is None and own_card is not None and _report_topic(own_card) is not None:
            # 內容比預算重要：照舊帶全部的來源，但說出來
            note = f"{ALL_SOURCES_NOTE}：分析工單 {job.parent} 沒有第 {_report_topic(own_card)} 個題材的來源清單"
            _log.warning("工單 %s：%s", job_dir.name, note)
            notes.append(note)
        for relative, source in carried.items():
            if relative == PREVIOUS_ARTICLE and not job.feedback:
                # 沒有意見就不是再改（例：換角色重寫）：不放上一版，免得照著上一版抄
                continue
            if own_card is not None and relative.startswith(CARD_DIR):
                # 新工單開單時拿了自己的快照：上一版的不蓋過去（ADR-0025 §3.4）
                continue
            if card_job and relative in (BRIEF, BRIEF_JSON):
                # 照編輯卡寫只讀報告：brief 的內容報告裡都有，省下本機的上下文
                continue
            if topic is not None and relative.startswith(SOURCES_DIR) and Path(relative).stem not in topic:
                # 只帶這個題材的來源（報告「可以寫的題材」列的）
                continue
            put(relative, source.read_bytes())

    persona = job_persona(job)
    author = author_dir(data, persona)
    # 角色資料不跟隨符號連結（`confined`）：連結可以指到資料目錄以外
    voice = confined.text(author, VOICE_FILE)
    if voice is not None:
        put(VOICE, voice)
    if target is not None:
        style = confined.text(author, genre_file(target))
        if style is not None:
            put(GENRE_STYLE, style)
    meta = _card_meta(job_dir)
    theme = meta.get("theme") if meta is not None else None
    headline_craft = False
    if isinstance(theme, str) and theme:
        # 照編輯卡寫稿：只送這一篇用得到的（ADR-0026 §3.3）；整篇不配圖就不用圖文編排
        assert meta is not None
        no_figures = meta.get("storyboard") == []
        for name in WRITING_CRAFT:
            if no_figures and name == LAYOUT_CRAFT:
                continue
            craft = confined.text(author, f"{CRAFT_DIR}/{name}")
            if craft is not None:
                put(AUTHOR_CRAFT + name, craft)
                headline_craft = headline_craft or name in HEADLINE_CRAFT
        themes = confined.text(author, THEMES_FILE)
        stance = theme_stance(themes, theme) if themes is not None else None
        if stance is not None:
            put(AUTHOR_THEME, stance)
        lessons = _latest_lessons(data, persona)
        if lessons:
            put(
                AUTHOR_LESSONS,
                "# 最近的 lessons（創作者退件或指正後累積的規則，舊的在前）\n\n"
                + "".join(f"- {entry}\n" for entry in lessons),
            )
    if not headline_craft and data.headline_rules.is_file():
        # 角色還沒有自己的標題與金句手藝：讀舊的共用規則（ADR-0026 §3.3）
        put(HEADLINE_RULES, data.headline_rules.read_bytes())

    # 最後才寫：寫之前當機，下次重開會從頭再放一次（已經放好的檔沿用）
    write_once(job_dir / PREPARED, json.dumps({"files": placed}, ensure_ascii=False))
    return notes
