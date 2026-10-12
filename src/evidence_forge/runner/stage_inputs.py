"""開段之前，執行器把 agent 要讀的參考資料放進工單的 `input/`（ADR-0015 §3.7、ADR-0018）。

agent 只看得到系統提示裡的 `SKILL.md`，與工單資料夾裡的檔（`read_file` 只能讀工單資料夾）。
文類定義（`harness/genres/`）、轉換說明（`harness/conversions/`）、作家設定
（`$EF_DATA/authors/<作家>/`）都不在工單裡，所以由這裡複製進去：

- `input/genres.md`：12 種文類的定義接成一份，整理段用來判斷文類、照抽取欄位整理；
- `input/target-genre.md`：目標文類的定義（骨架），二創段用；
- `input/author/voice.md`：作家聲音；
- `input/author/genre-style.md`：作家在目標文類的風格；作家沒寫這一類就沒有這個檔（未校準）；
- `input/conversions/<來源>--<目標>.md`：轉換說明，全部放進去，二創段照整理出的主文類挑。

補料段找到的來源正文（`input/sources/`）不在這裡放：補料段結束後才知道有哪些，見 `sources.py`。

整份參考資料只放一次（只增不改）：全部放完才寫 `input/.prepared.json`，有這份就整段跳過，
所以重跑、退件重跑、當機重開都沿用第一次那一份，連「當時沒有的檔」也一起凍結；
請求逐位元組不變，閘道的快取才對得上，不會重新付錢。每個檔先寫暫存檔再用 `os.link`
換成正式檔名（原子動作、已存在就失敗），當機只會留下暫存檔，不會留下半截的正式檔。
`SKILL.md` 只准提到 `PROVIDED` 裡的路徑（`tests/unit/test_skills.py`）。
"""

import json
import os
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
# 補料段之後，執行器照 sources.json 放的來源正文（`sources.py`，#70）
SOURCES_DIR = "input/sources/"
# 參考資料放完的紀錄；不給 agent 讀，所以不在 PROVIDED 裡
PREPARED = "input/.prepared.json"

# agent 讀得到的工單路徑（`content.md` 由開單時寫進去）；結尾是 / 的表示整個資料夾
PROVIDED = (CONTENT, GENRES, TARGET_GENRE, AUTHOR_DIR, CONVERSIONS, SOURCES_DIR)


class InputError(ValueError):
    """參考資料放不進去（例：目標文類不存在）；重跑模型修不好。"""


def author_dir(data: DataDir, author: str = AUTHOR) -> Path:
    return data.authors / author


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

    author = author_dir(data)
    voice = author / "voice.md"
    if voice.is_file():
        put(VOICE, voice.read_bytes())
    if target is not None:
        style = author / "genres" / f"{target}.md"
        if style.is_file():
            put(GENRE_STYLE, style.read_bytes())

    # 最後才寫：寫之前當機，下次重開會從頭再放一次（已經放好的檔沿用）
    write_once(job_dir / PREPARED, json.dumps({"files": placed}, ensure_ascii=False))
