"""開段之前，執行器把 agent 要讀的參考資料放進工單的 `input/`（ADR-0015 §3.7、ADR-0018）。

agent 只看得到系統提示裡的 `SKILL.md`，與工單資料夾裡的檔（`read_file` 只能讀工單資料夾）。
文類定義（`harness/genres/`）、轉換說明（`harness/conversions/`）、作家設定
（`$EF_DATA/authors/<作家>/`）都不在工單裡，所以由這裡複製進去：

- `input/genres.md`：12 種文類的定義接成一份，整理段用來判斷文類、照抽取欄位整理；
- `input/target-genre.md`：目標文類的定義（骨架），重構段用；
- `input/author/voice.md`：作家聲音；
- `input/author/genre-style.md`：作家在目標文類的風格；作家沒寫這一類就沒有這個檔（未校準）；
- `input/conversions/<來源>--<目標>.md`：轉換說明，全部放進去，重構段照 brief 的主文類挑。

- `input/headline-punchline.md`：標題與金句規則（`$EF_DATA/style/headline-punchline-system.md`，
  創作者自己的寫作系統，跨角色共用、不進 repo），重構段用；
- 從上一張單接著做（`ef new --from`，ADR-0019 §3.6）：上一張單過了程式檢查的產出
  （`input/report.md`、`input/brief.md`、`input/brief.json`、上一版文章 `input/article.md`）
  與它的來源正文 `input/sources/S<n>.md`。上一張單自己也是接著做的，它帶來的也一起帶過來。
  原文全文（`input/content.md`）不帶：重構不讀原文全文（FR-RW-01）。

查證段找到的來源正文（`input/sources/`）在分析工單裡不在這裡放：那一段結束後才知道有哪些，見 `sources.py`。

整份參考資料只放一次（只增不改）：全部放完才寫 `input/.prepared.json`，有這份就整段跳過，
所以重跑、退件重跑、當機重開都沿用第一次那一份，連「當時沒有的檔」也一起凍結；
請求逐位元組不變，閘道的快取才對得上，不會重新付錢。每個檔先寫暫存檔再用 `os.link`
換成正式檔名（原子動作、已存在就失敗），當機只會留下暫存檔，不會留下半截的正式檔。
`SKILL.md` 只准提到 `PROVIDED` 裡的路徑（`tests/unit/test_skills.py`）。
"""

import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from evidence_forge.checks.report import ChecksReport
from evidence_forge.documents import FrontmatterError, read_frontmatter
from evidence_forge.harness import Harness
from evidence_forge.models import Job
from evidence_forge.paths import DataDir
from evidence_forge.write_once import write_once

# 工單沒指定角色時用創作者自己（舊的 job.md 沒有 persona 欄）
AUTHOR = "me"

CONTENT = "input/content.md"
GENRES = "input/genres.md"
TARGET_GENRE = "input/target-genre.md"
AUTHOR_DIR = "input/author/"
VOICE = AUTHOR_DIR + "voice.md"
GENRE_STYLE = AUTHOR_DIR + "genre-style.md"
CONVERSIONS = "input/conversions/"
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
    CONVERSIONS,
    SOURCES_DIR,
    HEADLINE_RULES,
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
        f"未校準：角色 {persona} 還沒有校準過的 {genre} 文類風格"
        f"（authors/{persona}/genres/{genre}.md），照文類骨架與聲音寫"
    )


def default_genre(data: DataDir, author: str = AUTHOR) -> str | None:
    """作家設定的預設文類（`voice.md` frontmatter 的 `default_genre`）；沒有作家設定就是 None。"""
    voice = author_dir(data, author) / "voice.md"
    if not voice.is_file():
        return None
    try:
        value = read_frontmatter(voice).get("default_genre")
    except FrontmatterError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


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


def prepare_inputs(harness: Harness, data: DataDir, job_dir: Path, job: Job) -> None:
    """把這張單用得到的參考資料放進 `input/`；放過一次（有 `PREPARED`）就整段跳過。"""
    if (job_dir / PREPARED).is_file():
        return
    placed: list[str] = []

    def put(relative: str, content: str | bytes) -> None:
        put_once(job_dir, relative, content)
        placed.append(relative)

    definitions = sorted(harness.genres.glob("*.md"))
    if definitions:
        put(
            GENRES,
            "\n\n".join(path.read_text(encoding="utf-8") for path in definitions),
        )
    for conversion in sorted(harness.conversions.glob("*.md")):
        put(CONVERSIONS + conversion.name, conversion.read_bytes())

    target = job.target_genre
    if target is not None:
        definition = harness.genres / f"{target}.md"
        if not definition.is_file():
            raise InputError(f"目標文類 {target} 不在 harness/genres/ 裡")
        put(TARGET_GENRE, definition.read_bytes())

    author = author_dir(data, job_persona(job))
    voice = author / "voice.md"
    if voice.is_file():
        put(VOICE, voice.read_bytes())
    if target is not None:
        style = author / "genres" / f"{target}.md"
        if style.is_file():
            put(GENRE_STYLE, style.read_bytes())
    if data.headline_rules.is_file():
        put(HEADLINE_RULES, data.headline_rules.read_bytes())

    if job.parent is not None:
        parent = data.job_dir(job.parent)
        if not parent.is_dir():
            raise InputError(f"找不到上一張工單 {job.parent}")
        for relative, source in carried_files(parent).items():
            if relative == PREVIOUS_ARTICLE and not job.feedback:
                # 沒有意見就不是再改（例：換角色重寫）：不放上一版，免得照著上一版抄
                continue
            put(relative, source.read_bytes())

    # 最後才寫：寫之前當機，下次重開會從頭再放一次（已經放好的檔沿用）
    write_once(job_dir / PREPARED, json.dumps({"files": placed}, ensure_ascii=False))
