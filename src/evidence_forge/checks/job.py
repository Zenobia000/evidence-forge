"""執行器呼叫硬檢查的入口：給工單資料夾與要檢查的產出路徑，拿回 `checks.vN.json` 的條目。

`stages.py` 的 `digest_checks`、`compose_checks` 是純函式，吃已經讀好的文字；這裡是包在外面的
薄薄一層，只負責「照工單資料夾的約定讀檔」，判斷全部交給純函式：

- 產出路徑（稿子、段的 JSON 產出、原文）一律寫成相對於工單資料夾的路徑，
  條目裡的位置（`Location.file`）就用這個相對路徑；
- 素材清單在 `JobDir.assets_file`（`<工單>/assets/assets.json`，架構草案 v0.3 §2.1），不存在等於沒有素材；
- skill 的 `schema.json` 在 repo 裡，不在工單資料夾，所以給完整路徑。

模型交出來的檔案（稿子、JSON 產出）不存在、或不是合法的 JSON，算這次產出不合格（fail），不丟例外：
那是模型的錯，重試或升級可能修得好。執行器自己的東西（原文、素材清單、schema）讀不到或壞掉，
才丟例外，因為重跑模型修不好。這裡只讀檔，不寫檔、不呼叫模型、不碰網路。
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evidence_forge.checks.analysis import brief_checks, report_checks
from evidence_forge.checks.assets import check_images, load_asset_urls
from evidence_forge.checks.format import SchemaError, check_markdown
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.report import CheckEntry, CheckId, Location
from evidence_forge.checks.stages import compose_checks, digest_checks
from evidence_forge.paths import JobDir


class _Unreadable(Exception):
    """模型交出來的檔案讀不出來：不存在、不是 UTF-8、不是合法的 JSON。"""


def _inside(job_dir: Path, relative: Path) -> Path:
    """相對路徑換成工單資料夾裡的完整路徑；跑出工單資料夾的路徑是執行器的錯，直接拒絕。"""
    if relative.is_absolute():
        raise ValueError(f"{relative} 要寫成相對於工單資料夾的路徑")
    full = (job_dir / relative).resolve()
    if not full.is_relative_to(job_dir.resolve()):
        raise ValueError(f"{relative} 跑出了工單資料夾 {job_dir}")
    return full


def _read_text(path: Path) -> str:
    try:
        # utf-8-sig：開頭多一個 BOM 也照樣讀，跟素材清單、設定檔的讀法一致
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise _Unreadable(f"讀不到：{exc}") from exc


def _read_json(path: Path) -> object:
    text = _read_text(path)
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _Unreadable(f"不是合法的 JSON：{exc}") from exc
    return payload


def _load_schema(path: Path) -> Mapping[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"skill 的 schema {path} 讀不出來：{exc}") from exc
    if not isinstance(schema, dict):
        raise SchemaError(f"skill 的 schema {path} 要是 JSON 物件")
    return schema


def _source_text(job_dir: Path, path: Path) -> str:
    """讀一份來源正文。是空的就丟例外：沒有原文可比，C1、C3 會變成空轉。"""
    text = _inside(job_dir, path).read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"來源（原文）{path} 是空的，沒辦法檢查")
    return text


def _unreadable(
    checks: tuple[CheckId, ...], file: str, problem: str
) -> list[CheckEntry]:
    """產出讀不出來時，靠這份產出的每一項檢查都判 fail，不能因為沒東西可查就 pass。"""
    return [
        CheckEntry(
            check=check,
            status="fail",
            reason=f"產出 {file} {problem}，沒辦法檢查",
            location=Location(file=file),
        )
        for check in checks
    ]


def run_digest_checks(
    job_dir: Path, *, output: Path, schema: Path, source: Path
) -> list[CheckEntry]:
    """整理段某次嘗試的產出：C1（引句都在原文）、C5（符合 `digest` 的 schema）。

    `output` 是整理段的 JSON 產出、`source` 是原文，都相對於 `job_dir`。
    """
    source_text = _source_text(job_dir, source)
    schema_doc = _load_schema(schema)
    output_file = output.as_posix()
    try:
        claims = _read_json(_inside(job_dir, output))
    except _Unreadable as exc:
        return _unreadable(("C1", "C5"), output_file, str(exc))
    return digest_checks(
        claims=claims,
        claims_file=output_file,
        source_text=source_text,
        schema=schema_doc,
    )


def run_compose_checks(
    job_dir: Path,
    *,
    draft: Path,
    output: Path,
    schema: Path,
    sources: Mapping[str, Path],
    thresholds: OverlapThresholds,
    footnotes: Mapping[str, Sequence[str]] | None = None,
) -> list[CheckEntry]:
    """二創段某次嘗試的產出：C2 引用、C3 照抄、C4 圖、C5 格式。

    `draft` 是稿子、`output` 是二創段的 JSON 產出（列出事實句），`sources` 是來源清單：
    引用編號 → 來源正文的路徑。都相對於 `job_dir`。C3 拿稿子跟所有來源比。
    `footnotes` 是每個來源的出處（編號 → 標題、網址）：稿子註腳裡照抄的出處 C3 不算。
    """
    if not sources:
        raise ValueError("來源清單是空的：C2 對不到任何編號、C3 沒有原文可比")
    source_text = "\n\n".join(_source_text(job_dir, path) for path in sources.values())
    asset_urls = load_asset_urls(JobDir(job_dir).assets_file)
    schema_doc = _load_schema(schema)
    draft_file = draft.as_posix()
    output_file = output.as_posix()
    try:
        draft_text = _read_text(_inside(job_dir, draft))
    except _Unreadable as exc:
        return _unreadable(("C2", "C3", "C4", "C5"), draft_file, str(exc))
    try:
        payload = _read_json(_inside(job_dir, output))
    except _Unreadable as exc:
        # JSON 壞了，稿子本身的問題（照抄、圖、格式）照樣查出來一起交回去，省一輪修正
        return [
            *_unreadable(("C2", "C5"), output_file, str(exc)),
            *check_overlap(
                draft_text,
                source_text,
                thresholds,
                file=draft_file,
                footnotes=footnotes,
            ),
            *check_images(draft_text, asset_urls, file=draft_file),
            *check_markdown(draft_text, file=draft_file),
        ]
    return compose_checks(
        draft=draft_text,
        draft_file=draft_file,
        output=payload,
        output_file=output_file,
        schema=schema_doc,
        source_text=source_text,
        source_ids=frozenset(sources),
        asset_urls=asset_urls,
        thresholds=thresholds,
        footnotes=footnotes,
    )



def _read_document(
    job_dir: Path, document: Path, output: Path
) -> tuple[str, object] | list[CheckEntry]:
    """讀一份 Markdown 加一份 JSON 的產出（brief 段、查證與找題材段）。

    讀不出來時回傳 fail 條目：兩份都讀，兩份的問題一次報，省一輪重試才發現。
    """
    problems: list[CheckEntry] = []
    text, payload = "", None
    try:
        text = _read_text(_inside(job_dir, document))
    except _Unreadable as exc:
        problems.extend(_unreadable(("C5",), document.as_posix(), str(exc)))
    try:
        payload = _read_json(_inside(job_dir, output))
    except _Unreadable as exc:
        problems.extend(_unreadable(("C5",), output.as_posix(), str(exc)))
    return problems or (text, payload)


def run_brief_checks(
    job_dir: Path, brief: Path, *, output: Path, schema: Path
) -> list[CheckEntry]:
    """brief 段某次嘗試的產出：`brief.md` 六節、`brief.json` 符合 schema。路徑都相對於 `job_dir`。"""
    schema_doc = _load_schema(schema)
    read = _read_document(job_dir, brief, output)
    if isinstance(read, list):
        return read
    text, payload = read
    return brief_checks(
        brief=text,
        brief_file=brief.as_posix(),
        output=payload,
        output_file=output.as_posix(),
        schema=schema_doc,
    )


def run_report_checks(
    job_dir: Path, report: Path, *, output: Path, schema: Path
) -> list[CheckEntry]:
    """查證與找題材段某次嘗試的產出：`report.md` 五節依序、`sources.json` 符合 schema 且引用對得上。"""
    schema_doc = _load_schema(schema)
    read = _read_document(job_dir, report, output)
    if isinstance(read, list):
        return read
    text, payload = read
    return report_checks(
        report=text,
        report_file=report.as_posix(),
        output=payload,
        output_file=output.as_posix(),
        schema=schema_doc,
    )
