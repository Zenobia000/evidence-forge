"""執行器在段與段之間呼叫的入口（PRD §7、測試藍圖 SEAM-06）。

- 整理段之後：`digest_checks` 跑 C1、C5。沒過算這段這次失敗，走重試與升級。
- 二創段之後：`compose_checks` 跑 C2～C5。沒過帶著結果回二創段修，到 `auto_fix` 上限。

兩個都是純函式：檔案由執行器讀好傳進來，結果由執行器收進 `ChecksReport`、
用 `write_checks_report` 寫出去。這裡不讀檔、不寫檔、不呼叫模型。

跟 skill 的約定（S0.8 的 `digest`、`compose` 照這個寫 schema）：
- 整理段的引句放在叫 `quote` 的欄位，在 JSON 裡的哪一層都可以；
- 二創段除了稿子，還要交一份 JSON，把稿子裡的事實句（照稿子原樣、連引用編號）
  放在叫 `fact` 的欄位；引用編號寫成 `[^<來源 id>]`。
"""

from collections.abc import Collection, Mapping
from typing import Any

from evidence_forge.checks.assets import check_images
from evidence_forge.checks.citations import check_citations
from evidence_forge.checks.format import check_markdown, check_schema
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.quotes import check_quotes
from evidence_forge.checks.report import CheckEntry, collect_located

QUOTE_FIELD = "quote"
FACT_FIELD = "fact"


def digest_checks(
    *,
    claims: object,
    claims_file: str,
    source_text: str,
    schema: Mapping[str, Any],
) -> list[CheckEntry]:
    """整理段的產出：C1（引句都在原文）、C5（符合 `digest` 的 schema）。"""
    quotes = collect_located(claims, QUOTE_FIELD, claims_file)
    return [
        *check_quotes(quotes, source_text),
        *check_schema(claims, schema, file=claims_file),
    ]


def compose_checks(
    *,
    draft: str,
    draft_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
    source_text: str,
    source_ids: Collection[str],
    asset_urls: Collection[str],
    thresholds: OverlapThresholds,
) -> list[CheckEntry]:
    """二創段的產出：C2 引用、C3 照抄、C4 圖、C5（JSON 符合 `compose` 的 schema、稿子是標準 Markdown）。"""
    facts = collect_located(output, FACT_FIELD, output_file)
    return [
        *check_citations(draft, source_ids, facts, file=draft_file),
        *check_overlap(draft, source_text, thresholds, file=draft_file),
        *check_images(draft, asset_urls, file=draft_file),
        *check_schema(output, schema, file=output_file),
        *check_markdown(draft, file=draft_file),
    ]
