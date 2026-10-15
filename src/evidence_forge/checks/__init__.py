"""程式檢查（PRD v3.0 §7、ADR-0020 §3.4）：只看格式與出處，不判斷內容對不對。

全部是純函式加一層讀檔：只看傳進來的文字與資料，不呼叫模型、不碰網路，只有 `write_checks_report` 寫檔。
執行器在段之後呼叫 `run_brief_checks`（brief 段）、`run_report_checks`（查證與找題材段）、
`run_article_checks`（重構段）：給工單資料夾與產出路徑，它們照約定讀檔、交給純函式判斷，
把條目收成 `ChecksReport`，再用 `write_checks_report` 寫成 `checks/checks.vN.json`。

C5 是格式與出處，沒過就算那一段那一次失敗；C3 是重構文章跟原文與來源的逐字重疊，只顯示、不擋。
C1（引句真實）、C2（引用完整）、C4（素材使用）已經退場（ADR-0020）。
"""

from evidence_forge.checks.analysis import (
    BRIEF_SECTIONS,
    REPORT_SECTIONS,
    brief_checks,
    check_claim_coverage,
    check_fact_alignment,
    check_internal_ids,
    check_lead,
    check_sections,
    check_source_list_urls,
    check_source_refs,
    check_topic_alignment,
    check_topics_numbered,
    check_verdict_order,
    report_checks,
)
from evidence_forge.checks.article import (
    REFERENCES_HEADING,
    ArticleRules,
    SentencePattern,
    article_checks,
    check_figure_slots,
    check_front_matter,
    check_reference_urls,
    check_sentence_patterns,
    parse_sentence_patterns,
    check_original_not_in_body,
    check_references,
    check_reply_phrases,
    check_skeleton_headings,
    genre_skeleton,
    split_references,
)
from evidence_forge.checks.format import SchemaError, check_markdown, check_schema
from evidence_forge.checks.job import (
    run_article_checks,
    run_brief_checks,
    run_report_checks,
)
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.report import (
    CheckEntry,
    CheckId,
    ChecksReport,
    CheckStatus,
    Location,
    SoftCheckEntry,
    SoftCheckReport,
    checks_report_schema,
    parse_soft_checks,
    write_checks_report,
)
from evidence_forge.checks.settings import CheckSettings, load_check_settings

__all__ = [
    "ArticleRules",
    "BRIEF_SECTIONS",
    "CheckEntry",
    "CheckId",
    "CheckSettings",
    "CheckStatus",
    "ChecksReport",
    "Location",
    "OverlapThresholds",
    "REFERENCES_HEADING",
    "REPORT_SECTIONS",
    "SchemaError",
    "SentencePattern",
    "SoftCheckEntry",
    "SoftCheckReport",
    "article_checks",
    "brief_checks",
    "check_claim_coverage",
    "check_fact_alignment",
    "check_figure_slots",
    "check_front_matter",
    "check_internal_ids",
    "check_lead",
    "check_markdown",
    "check_original_not_in_body",
    "check_overlap",
    "check_reference_urls",
    "check_references",
    "check_reply_phrases",
    "check_schema",
    "check_sentence_patterns",
    "check_sections",
    "check_skeleton_headings",
    "check_source_list_urls",
    "check_source_refs",
    "check_topic_alignment",
    "check_topics_numbered",
    "check_verdict_order",
    "checks_report_schema",
    "genre_skeleton",
    "load_check_settings",
    "parse_sentence_patterns",
    "parse_soft_checks",
    "report_checks",
    "run_article_checks",
    "run_brief_checks",
    "run_report_checks",
    "split_references",
    "write_checks_report",
]
