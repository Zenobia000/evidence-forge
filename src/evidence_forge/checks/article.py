"""重構段的程式檢查（PRD v3.0 §6 第 4 點、§7、ADR-0020 §3.4）：只看格式與出處，不判斷寫得好不好。

- 擋（C5）：`article.md` 是標準 Markdown；最後一節是「參考來源」，裡面列了原文出處
  （原文有網址就要有那個網址，貼上的文字、上傳的檔沒有網址就要有原文的標題）；
  `article.json` 符合 `rewrite` 的 schema。
- 只顯示、不擋（C3）：文章正文跟原文、跟每一份來源的逐字重疊，各算一份（`overlap.py`）。
  參考來源那一節不算：它本來就照抄標題與網址。

全部是純函式：檔案由執行器讀好傳進來，不讀檔、不寫檔、不呼叫模型。
"""

import re
from collections.abc import Mapping
from typing import Any

from evidence_forge.checks.analysis import headings
from evidence_forge.checks.format import check_markdown, check_schema
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import normalize_for_match, normalize_url

# 文章結尾那一節的標題；`harness/skills/rewrite/SKILL.md` 照這個寫
REFERENCES_HEADING = "參考來源"
# 參考來源裡的網址：到空白、全形標點或 Markdown 的括號為止
_URL = re.compile(r"https?://[^\s<>\]）】」，。、；]+")


def split_references(article: str) -> tuple[str, str | None, int | None]:
    """把文章切成（正文, 參考來源那一節, 那一節標題的行號）。

    參考來源是**最後一個**標題文字以「參考來源」開頭的那一節，到文章結尾；沒有就是（全文, None, None）。
    """
    line = next(
        (
            number
            for number, text in reversed(headings(article))
            if text.startswith(REFERENCES_HEADING)
        ),
        None,
    )
    if line is None:
        return article, None, None
    lines = article.split("\n")
    return "\n".join(lines[: line - 1]), "\n".join(lines[line:]), line


def _fail(reason: str, file: str, line: int | None = None) -> CheckEntry:
    return CheckEntry(
        check="C5",
        status="fail",
        reason=reason,
        location=Location(file=file, line=line),
    )


def check_references(
    article: str, *, file: str, original_url: str | None, original_title: str
) -> list[CheckEntry]:
    """結尾的「參考來源」有沒有列原文出處。對就一條 pass，不對就一條 fail。"""
    _, references, line = split_references(article)
    if references is None:
        return [_fail(f"文章結尾沒有「{REFERENCES_HEADING}」一節", file)]
    later = [
        number for number, _ in headings(article) if line is not None and number > line
    ]
    if later:
        return [
            _fail(
                f"「{REFERENCES_HEADING}」要在文章結尾，後面不能再有別的標題",
                file,
                later[0],
            )
        ]
    if original_url:
        wanted = original_url.strip().rstrip("/")
        # 跟分析報告的來源清單用同一套網址比對（`normalize_url`）：主機大小寫、# 片段、結尾的 / 不算差別
        listed = {
            normalize_url(url.rstrip(").,"))
            for url in _URL.findall(references)
        }
        if normalize_url(wanted) not in listed and wanted not in references:
            return [
                _fail(
                    f"「{REFERENCES_HEADING}」沒有列原文出處：要有原文的網址 {wanted}",
                    file,
                    line,
                )
            ]
    elif normalize_for_match(original_title) not in normalize_for_match(references):
        return [
            _fail(
                f"「{REFERENCES_HEADING}」沒有列原文出處：原文沒有網址，要有原文的標題「{original_title}」",
                file,
                line,
            )
        ]
    return [CheckEntry(check="C5", status="pass", reason="結尾的參考來源列了原文出處")]


def article_checks(
    *,
    article: str,
    article_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
    original_url: str | None,
    original_title: str,
    texts: Mapping[str, str],
    thresholds: OverlapThresholds,
) -> list[CheckEntry]:
    """重構段的產出：格式、原文出處（擋）與跟每一份文字的逐字重疊（只顯示）。

    `texts` 是要比重疊的文字：給人看的名字（「原文」「S2」）→ 正文，照給的順序各算一份。
    """
    body, _, _ = split_references(article)
    return [
        *check_markdown(article, file=article_file),
        *check_references(
            article,
            file=article_file,
            original_url=original_url,
            original_title=original_title,
        ),
        *check_schema(output, schema, file=output_file),
        *(
            entry
            for label, text in texts.items()
            for entry in check_overlap(
                body, text, thresholds, file=article_file, label=label
            )
        ),
    ]
