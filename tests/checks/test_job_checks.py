"""執行器呼叫檢查的入口：給工單資料夾與要檢查的產出路徑，拿回 `checks.vN.json` 的條目（SEAM-06）。

用重構段（`run_article_checks`）當例子：讀檔的約定（路徑相對於工單資料夾、模型交的檔讀不出來算 fail、
執行器自己的 schema 壞掉丟例外）是分析兩段與重構段共用的。
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.checks import (
    CheckEntry,
    ChecksReport,
    OverlapThresholds,
    SchemaError,
    run_article_checks,
    write_checks_report,
)

pytestmark = pytest.mark.promise("FR-RW-01")

SCHEMA: dict[str, Any] = {"type": "object", "required": ["title"]}
THRESHOLDS = OverlapThresholds(
    ngram_size=5, max_ngram_overlap=0.3, max_common_tokens=12
)
URL = "https://seed.example/p"
ARTICLE = (
    "# 帳單縮水之後\n\n我認為省下來的錢該花在讀紀錄的人身上。\n\n"
    f"## 參考來源\n\n- 〈推論變便宜了〉，{URL}\n"
)
OUTPUT = {"title": "帳單縮水之後"}
ARTICLE_PATH = Path("stages/rewrite/1/article.md")
OUTPUT_PATH = Path("stages/rewrite/1/article.json")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _job(
    tmp_path: Path, *, article: str | None = ARTICLE, output: str | None = None
) -> tuple[Path, Path]:
    """建一個工單資料夾：文章與 JSON 產出；skill 的 schema 放在工單外面。"""
    job = tmp_path / "jobs" / "job-1"
    job.mkdir(parents=True)
    if article is not None:
        _write(job / ARTICLE_PATH, article)
    if output is None:
        output = json.dumps(OUTPUT)
    _write(job / OUTPUT_PATH, output)
    schema = _write(tmp_path / "skills" / "rewrite" / "schema.json", json.dumps(SCHEMA))
    return job, schema


def _run(job: Path, schema: Path, article: Path = ARTICLE_PATH) -> list[CheckEntry]:
    return run_article_checks(
        job,
        article,
        output=OUTPUT_PATH,
        schema=schema,
        original_url=URL,
        original_title="推論變便宜了",
        original_author=None,
        texts={"原文": "大型語言模型的推論成本，在過去一年降了九成。"},
        thresholds=THRESHOLDS,
    )


def test_合格的文章全部通過_寫成_checks_v1(tmp_path: Path) -> None:
    job, schema = _job(tmp_path)
    entries = _run(job, schema)
    assert all(entry.status != "fail" for entry in entries)

    report = ChecksReport(
        stage="rewrite",
        attempt=1,
        files=(ARTICLE_PATH.as_posix(), OUTPUT_PATH.as_posix()),
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
        entries=tuple(entries),
    )
    written = write_checks_report(job, report)
    assert written.path == job / "checks" / "checks.v1.json"
    assert ChecksReport.model_validate_json(written.path.read_text("utf-8")) == report


def test_沒交文章_算這次不合格_不丟例外(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, article=None)
    [entry] = _run(job, schema)
    assert (entry.check, entry.status) == ("C5", "fail")
    assert entry.location is not None and entry.location.file == ARTICLE_PATH.as_posix()


def test_JSON_產出壞掉_算這次不合格(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, output="{not json")
    [entry] = _run(job, schema)
    assert entry.status == "fail" and "JSON" in entry.reason


def test_JSON_開頭多一個_BOM_照樣讀得出來(tmp_path: Path) -> None:
    job, schema = _job(tmp_path, output="﻿" + json.dumps(OUTPUT))
    assert all(entry.status != "fail" for entry in _run(job, schema))


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.md"])
def test_跑出工單資料夾的路徑_直接拒絕(tmp_path: Path, path: str) -> None:
    job, schema = _job(tmp_path)
    with pytest.raises(ValueError):
        _run(job, schema, Path(path))


def test_skill_的_schema_壞掉是設定錯誤_不是產出不合格(tmp_path: Path) -> None:
    job, schema = _job(tmp_path)
    schema.write_text("{broken", encoding="utf-8")
    with pytest.raises(SchemaError):
        _run(job, schema)
