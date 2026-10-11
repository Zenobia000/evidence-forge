"""自動檢查 C1～C7（PRD §7、架構草案 v0.3 §6、FR-VF-01）。

硬檢查 C1～C5 是純函式：只看傳進來的文字與資料，不呼叫模型、不碰網路、不寫檔。
執行器在段與段之間呼叫 `run_digest_checks`（整理段後 C1、C5）、`run_compose_checks`（二創段後 C2～C5）：
給工單資料夾與產出路徑，它們照約定讀檔、交給純函式判斷，
把條目收成 `ChecksReport`，再用 `write_checks_report` 寫成 `checks/checks.vN.json`。
軟檢查 C6、C7 由 `verify` 段的模型判斷，這裡只定結果格式（`parse_soft_checks`）。
"""

from evidence_forge.checks.assets import check_images, load_asset_urls
from evidence_forge.checks.citations import check_citations
from evidence_forge.checks.format import SchemaError, check_markdown, check_schema
from evidence_forge.checks.job import run_compose_checks, run_digest_checks
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.quotes import check_quotes
from evidence_forge.checks.report import (
    CheckEntry,
    CheckId,
    ChecksReport,
    CheckStatus,
    LocatedText,
    Location,
    SoftCheckEntry,
    SoftCheckReport,
    checks_report_schema,
    collect_located,
    parse_soft_checks,
    write_checks_report,
)
from evidence_forge.checks.settings import CheckSettings, load_check_settings
from evidence_forge.checks.stages import compose_checks, digest_checks

__all__ = [
    "CheckEntry",
    "CheckId",
    "CheckSettings",
    "CheckStatus",
    "ChecksReport",
    "LocatedText",
    "Location",
    "OverlapThresholds",
    "SchemaError",
    "SoftCheckEntry",
    "SoftCheckReport",
    "check_citations",
    "check_images",
    "check_markdown",
    "check_overlap",
    "check_quotes",
    "check_schema",
    "checks_report_schema",
    "collect_located",
    "compose_checks",
    "digest_checks",
    "load_asset_urls",
    "load_check_settings",
    "parse_soft_checks",
    "run_compose_checks",
    "run_digest_checks",
    "write_checks_report",
]
