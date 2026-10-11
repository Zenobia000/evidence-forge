"""C2 引用完整（PRD §7）：

1. 稿子裡每個引用編號都對得到來源清單；
2. 二創產出列出的每一句事實句都附引用編號，而且那句（連同編號）真的在稿子裡。

引用編號寫成 GFM 註腳：`[^<來源 id>]`，例：`推論成本一年降了九成[^S1]。`
註腳定義那一行（`[^S1]: …`）的編號也算，一樣要對得到來源。
程式碼區塊與行內程式碼裡的編號不算。

事實句要照稿子原樣抄、連編號一起（二創 skill 的 schema 這樣要求），
比對時忽略空白與標點；清單說有編號、稿子那句卻沒附，以稿子為準判不合格。
"""

import re
from collections.abc import Collection, Sequence

from evidence_forge.checks.report import CheckEntry, LocatedText, Location
from evidence_forge.checks.text import line_column, mask_code, normalize_for_match

CITATION = re.compile(r"\[\^([^\]\s]+)\]")


def check_citations(
    draft: str,
    source_ids: Collection[str],
    facts: Sequence[LocatedText],
    *,
    file: str,
) -> list[CheckEntry]:
    """每個對不到來源的編號、每句沒附編號或不在稿子裡的事實句，各一條 fail。"""
    failures: list[CheckEntry] = []

    for match in CITATION.finditer(mask_code(draft)):
        if match.group(1) in source_ids:
            continue
        line, column = line_column(draft, match.start())
        failures.append(
            CheckEntry(
                check="C2",
                status="fail",
                reason=f"引用編號 {match.group(1)} 對不到來源清單裡的任何來源",
                location=Location(file=file, line=line, column=column),
                excerpt=match.group(0),
            )
        )

    normalized_draft = normalize_for_match(draft)
    for fact in facts:
        if not CITATION.search(fact.text):
            reason = "事實句沒有附引用編號"
        elif normalize_for_match(fact.text) not in normalized_draft:
            reason = "事實句（連同引用編號）在稿子裡找不到"
        else:
            continue
        failures.append(
            CheckEntry(
                check="C2",
                status="fail",
                reason=reason,
                location=fact.location,
                excerpt=fact.text,
            )
        )

    if failures:
        return failures
    return [
        CheckEntry(
            check="C2",
            status="pass",
            reason=f"引用編號都對得到來源，{len(facts)} 句事實句都附了編號",
        )
    ]
