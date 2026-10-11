"""C1 引句真實：整理段每條主張附的引句，要逐字出現在原文裡（PRD §7）。

比對前兩邊都做同樣的正規化（`normalize_for_match`），再做子字串比對。
省略號接起來的兩段不算逐字引句；引句要是原文裡連續的一段。
"""

from collections.abc import Sequence

from evidence_forge.checks.report import CheckEntry, LocatedText
from evidence_forge.checks.text import normalize_for_match


def check_quotes(quotes: Sequence[LocatedText], source_text: str) -> list[CheckEntry]:
    """每條找不到的引句一條 fail；全部找得到就一條 pass。"""
    haystack = normalize_for_match(source_text)
    failures: list[CheckEntry] = []
    for quote in quotes:
        needle = normalize_for_match(quote.text)
        if not needle:
            reason = "引句是空的（拿掉空白與標點後沒有字）"
        elif needle not in haystack:
            reason = "引句在原文裡找不到（已忽略空白、標點與全形半形的差異）"
        else:
            continue
        failures.append(
            CheckEntry(
                check="C1",
                status="fail",
                reason=reason,
                location=quote.location,
                excerpt=quote.text,
            )
        )
    if failures:
        return failures
    reason = f"{len(quotes)} 條引句都在原文裡找得到" if quotes else "這份產出沒有引句"
    return [CheckEntry(check="C1", status="pass", reason=reason)]
