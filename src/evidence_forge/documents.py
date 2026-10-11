"""「YAML frontmatter＋Markdown 內文」的檔：`job.md`（v0.3 §2.3）與配方（§3.1）。

檔案長這樣：

    ---
    <YAML 對應表>
    ---
    <內文，原封不動>

frontmatter 用模型驗證；內文是結尾分隔線之後的所有字元，`parse_*`／`render_job` 一個字元都不改。
從檔案讀時（`read_job`、`read_recipe`）會去掉開頭的 UTF-8 BOM、把 CRLF 換成 LF，
所以 Windows 編輯器存的檔也讀得進來；寫回去一律是 LF、沒有 BOM。
手寫的 YAML 註解與排版不會保留——讀進來再寫出去，保證的是欄位值與內文不變。
"""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from evidence_forge.models import Job, Recipe

_FENCE = "---\n"


class FrontmatterError(ValueError):
    """檔案沒有可以讀的 frontmatter（缺分隔線，或內容不是 YAML 對應表）。"""


def _read_text(path: Path) -> str:
    # utf-8-sig 去掉 BOM；read_text 預設把 CRLF 換成 LF。
    return path.read_text(encoding="utf-8-sig")


def _split(text: str) -> tuple[dict[str, Any], str]:
    """把文字拆成 frontmatter 對應表與內文。"""
    if not text.startswith(_FENCE):
        raise FrontmatterError("檔案開頭不是 frontmatter 分隔線 ---")
    rest = text[len(_FENCE) :]
    # 結尾分隔線是單獨一行的 ---；內文裡後面再出現的 --- 都算內文。
    if rest.startswith(_FENCE):
        header, body = "", rest[len(_FENCE) :]
    elif (end := rest.find("\n" + _FENCE)) != -1:
        header, body = rest[: end + 1], rest[end + 1 + len(_FENCE) :]
    elif rest.endswith("\n---"):
        header, body = rest[: -len("---")], ""
    else:
        raise FrontmatterError("找不到 frontmatter 的結尾分隔線 ---")

    try:
        data = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter 不是合法的 YAML：{exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter 必須是 YAML 對應表（key: value）")
    return data, body


def _join(model: BaseModel, body: str) -> str:
    """把模型寫成 frontmatter，接上原封不動的內文。"""
    header = yaml.safe_dump(
        model.model_dump(mode="json"),
        sort_keys=False,  # 照模型欄位的順序寫，跟 v0.3 的範例一樣好讀
        allow_unicode=True,  # 中文直接寫出來，不轉成 \u 跳脫
        width=10_000,  # 長的目標不折行
    )
    return f"{_FENCE}{header}{_FENCE}{body}"


class JobDocument(BaseModel):
    """一份 `job.md`：frontmatter 的工單欄位，加上給 agent 的補充說明（內文）。"""

    model_config = ConfigDict(frozen=True)

    job: Job
    notes: str = ""


def parse_job(text: str) -> JobDocument:
    """讀 `job.md` 的文字；格式不對丟 `FrontmatterError`，欄位不對丟 pydantic 的 `ValidationError`。"""
    data, body = _split(text)
    return JobDocument(job=Job.model_validate(data), notes=body)


def render_job(doc: JobDocument) -> str:
    """把工單寫成 `job.md` 的文字；`parse_job(render_job(doc)) == doc`。"""
    return _join(doc.job, doc.notes)


def read_job(path: Path) -> JobDocument:
    return parse_job(_read_text(path))


def parse_recipe(text: str) -> Recipe:
    """讀配方的文字；只取 frontmatter，內文是給人看的說明，執行器不用。"""
    data, _ = _split(text)
    return Recipe.model_validate(data)


def read_recipe(path: Path) -> Recipe:
    return parse_recipe(_read_text(path))


def read_frontmatter(path: Path) -> dict[str, Any]:
    """只讀 frontmatter 的對應表，不套模型（例：skill 的 `SKILL.md`）。"""
    data, _ = _split(_read_text(path))
    return data


def write_job(path: Path, doc: JobDocument) -> None:
    """寫入（或改寫）`job.md`。

    `job.md` 是資料夾裡唯一會改寫的檔（狀態欄由執行器更新，v0.3 §2.2）。
    先寫到同目錄的暫存檔再整個換上去，UI 同時在讀也只會讀到舊版或新版，不會讀到一半。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(render_job(doc).encode("utf-8"))
    os.replace(temporary, path)
