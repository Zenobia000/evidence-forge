"""執行器呼叫硬檢查的入口：給工單資料夾與要檢查的產出路徑，拿回 `checks.vN.json` 的條目。

`analysis.py`、`article.py` 是純函式，吃已經讀好的文字；這裡是包在外面的
薄薄一層，只負責「照工單資料夾的約定讀檔」，判斷全部交給純函式：

- 產出路徑（報告、文章、段的 JSON 產出）一律寫成相對於工單資料夾的路徑，
  條目裡的位置（`Location.file`）就用這個相對路徑；
- skill 的 `schema.json` 在 repo 裡，不在工單資料夾，所以給完整路徑；
- 重構段要比重疊的原文與來源正文，由執行器讀好傳進來（原文在素材項目裡，不在重構工單裡）。

模型交出來的檔案（報告、文章、JSON 產出）不存在、或不是合法的 JSON，算這次產出不合格（fail），不丟例外：
那是模型的錯，重試或升級可能修得好。執行器自己的東西（schema）讀不到或壞掉，
才丟例外，因為重跑模型修不好。這裡只讀檔，不寫檔、不呼叫模型、不碰網路。
"""

import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from evidence_forge.checks.analysis import brief_checks, report_checks
from evidence_forge.checks.article import article_checks
from evidence_forge.checks.format import SchemaError
from evidence_forge.checks.overlap import OverlapThresholds
from evidence_forge.checks.report import CheckEntry, CheckId, Location


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


def _read_document(
    job_dir: Path, document: Path, output: Path
) -> tuple[str, object] | list[CheckEntry]:
    """讀一份 Markdown 加一份 JSON 的產出（brief 段、查證與找題材段、重構段）。

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


def _key_claims(job_dir: Path, brief: Path) -> list[str]:
    """前一段合格的 `brief.json` 的關鍵主張編號。它已經過 schema，讀不出來是執行器的問題，丟例外。"""
    try:
        payload = _read_json(_inside(job_dir, brief))
    except _Unreadable as exc:
        raise ValueError(f"前一段的 {brief.as_posix()} {exc}") from exc
    claims = payload.get("key_claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        raise ValueError(f"前一段的 {brief.as_posix()} 沒有關鍵主張（key_claims）")
    return [str(claim.get("id")) for claim in claims if isinstance(claim, dict)]


def run_report_checks(
    job_dir: Path,
    report: Path,
    *,
    output: Path,
    schema: Path,
    allowed_urls: Collection[str],
    brief: Path | None,
) -> list[CheckEntry]:
    """查證與找題材段某次嘗試的產出（`analysis.report_checks`）。

    `allowed_urls` 是這一段這一次抓成功的網頁與原文的網址（執行器從帳本算）；
    `brief` 是前一段合格的 `brief.json`（相對於 `job_dir`），沒有就不查關鍵主張的涵蓋。
    """
    schema_doc = _load_schema(schema)
    key_claims = _key_claims(job_dir, brief) if brief is not None else None
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
        allowed_urls=allowed_urls,
        key_claims=key_claims,
    )


def run_article_checks(
    job_dir: Path,
    article: Path,
    *,
    output: Path,
    schema: Path,
    original_url: str | None,
    original_title: str,
    texts: Mapping[str, str],
    thresholds: OverlapThresholds,
) -> list[CheckEntry]:
    """重構段某次嘗試的產出：`article.md` 的格式與原文出處、`article.json` 符合 schema（擋），
    文章跟 `texts` 裡每一份文字的逐字重疊（只顯示）。路徑都相對於 `job_dir`。"""
    schema_doc = _load_schema(schema)
    read = _read_document(job_dir, article, output)
    if isinstance(read, list):
        return read
    text, payload = read
    return article_checks(
        article=text,
        article_file=article.as_posix(),
        output=payload,
        output_file=output.as_posix(),
        schema=schema_doc,
        original_url=original_url,
        original_title=original_title,
        texts=texts,
        thresholds=thresholds,
    )
