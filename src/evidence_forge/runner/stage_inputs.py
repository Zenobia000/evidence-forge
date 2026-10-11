"""開段之前，執行器把 agent 要讀的參考資料放進工單的 `input/`（ADR-0015 §3.7、ADR-0018）。

agent 只看得到系統提示裡的 `SKILL.md`，與工單資料夾裡的檔（`read_file` 只能讀工單資料夾）。
文類定義（`harness/genres/`）、轉換說明（`harness/conversions/`）、作家設定
（`$EF_DATA/authors/<作家>/`）都不在工單裡，所以由這裡複製進去：

- `input/genres.md`：12 種文類的定義接成一份，整理段用來判斷文類、照抽取欄位整理；
- `input/target-genre.md`：目標文類的定義（骨架），二創段用；
- `input/author/voice.md`：作家聲音；
- `input/author/genre-style.md`：作家在目標文類的風格；作家沒寫這一類就沒有這個檔（未校準）；
- `input/conversions/<來源>--<目標>.md`：轉換說明，全部放進去，二創段照整理出的主文類挑。

每個檔只在不存在時寫（只增不改）：重跑、退件重跑都沿用開單後第一次放進去的那一份，
產出永遠對得到當時用的版本。`SKILL.md` 只准提到 `PROVIDED` 裡的路徑（`tests/unit/test_skills.py`）。
"""

from pathlib import Path

from evidence_forge.documents import FrontmatterError, read_frontmatter
from evidence_forge.harness import Harness
from evidence_forge.models import Job
from evidence_forge.paths import DataDir
from evidence_forge.write_once import write_once

# S0 只有一位作家（創作者自己）；多作家時改成工單或訂閱源上的欄位
AUTHOR = "me"

CONTENT = "input/content.md"
GENRES = "input/genres.md"
TARGET_GENRE = "input/target-genre.md"
AUTHOR_DIR = "input/author/"
VOICE = AUTHOR_DIR + "voice.md"
GENRE_STYLE = AUTHOR_DIR + "genre-style.md"
CONVERSIONS = "input/conversions/"

# agent 讀得到的工單路徑（`content.md` 由開單時寫進去）；結尾是 / 的表示整個資料夾
PROVIDED = (CONTENT, GENRES, TARGET_GENRE, AUTHOR_DIR, CONVERSIONS)


class InputError(ValueError):
    """參考資料放不進去（例：目標文類不存在）；重跑模型修不好。"""


def author_dir(data: DataDir, author: str = AUTHOR) -> Path:
    return data.root / "authors" / author


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


def _put(job_dir: Path, relative: str, data: str | bytes) -> None:
    try:
        write_once(job_dir / relative, data)
    except FileExistsError:
        # 已經放過：沿用第一次那一份
        pass


def prepare_inputs(harness: Harness, data: DataDir, job_dir: Path, job: Job) -> None:
    """把這張單用得到的參考資料放進 `input/`；已經有的不動。"""
    definitions = sorted((harness.root / "genres").glob("*.md"))
    if definitions:
        _put(
            job_dir,
            GENRES,
            "\n\n".join(path.read_text(encoding="utf-8") for path in definitions),
        )
    for conversion in sorted((harness.root / "conversions").glob("*.md")):
        _put(job_dir, CONVERSIONS + conversion.name, conversion.read_bytes())

    target = job.target_genre
    if target is not None:
        definition = harness.root / "genres" / f"{target}.md"
        if not definition.is_file():
            raise InputError(f"目標文類 {target} 不在 harness/genres/ 裡")
        _put(job_dir, TARGET_GENRE, definition.read_bytes())

    author = author_dir(data)
    voice = author / "voice.md"
    if voice.is_file():
        _put(job_dir, VOICE, voice.read_bytes())
    if target is not None:
        style = author / "genres" / f"{target}.md"
        if style.is_file():
            _put(job_dir, GENRE_STYLE, style.read_bytes())
