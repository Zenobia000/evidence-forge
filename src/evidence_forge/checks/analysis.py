"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020 §3.4）：只看格式與出處，不判斷內容對不對。

- brief 段：`brief.md` 是標準 Markdown、規定的六節都在（順序不拘）；`brief.json` 符合 `brief` 的 schema。
- 查證與找題材段：`report.md` 是標準 Markdown、五節依序都在（PRD §5，標題固定是為了之後累積成知識庫）；
  `sources.json` 符合 `investigate` 的 schema；查證與研究題材引用的來源編號都在來源清單上。
  清單上的網址是不是這一段真的抓過的，要看閘道的帳本，由執行器的 `runner/sources.py` 查。

全部是純函式：檔案由 `job.py` 讀好傳進來，不讀檔、不寫檔、不呼叫模型。
條目都算 C5（格式）：ADR-0020 拿掉了 C1、C2、C4，留下的只有格式與出處。
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from evidence_forge.checks.format import check_markdown, check_schema
from evidence_forge.checks.report import CheckEntry, Location, json_pointer
from evidence_forge.checks.text import mask_code

# brief.md 規定的幾節（FR-AN-01）；`harness/skills/brief/SKILL.md` 照這幾個標題寫
BRIEF_SECTIONS = (
    "論點與立場",
    "事實與數據",
    "觀點",
    "文類",
    "主題標籤",
    "可以延伸的方向",
)
# 分析報告的五節，依序（PRD §5）；`harness/skills/investigate/SKILL.md` 照這幾個標題寫
REPORT_SECTIONS = ("作者在說什麼", "事實與觀點", "查證", "研究題材", "來源清單")

_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)[ \t#]*$")
# 標題前面的編號（「1.」「2)」「三、」）與粗體記號不算標題的一部分
_NUMBERING = re.compile(r"^(?:\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")


def headings(markdown: str) -> list[tuple[int, str]]:
    """Markdown 的 ATX 標題：（行號, 去掉編號與粗體的標題文字）。程式碼區塊裡的不算。"""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(markdown).split("\n"), start=1):
        match = _HEADING.match(line)
        if match is None:
            continue
        text = match.group(1).replace("**", "").strip()
        found.append((number, _NUMBERING.sub("", text).strip()))
    return found


def check_sections(
    markdown: str, required: Sequence[str], *, file: str, ordered: bool
) -> list[CheckEntry]:
    """每個規定的標題都要是一節的標題（標題文字以它開頭，例：「查證（有疑慮的放最前面）」也算）。

    `ordered` 時還要照 `required` 的順序出現。少一節或順序不對各一條 fail；都對就一條 pass。
    """
    found = headings(markdown)
    failures: list[CheckEntry] = []
    previous: tuple[str, int] | None = None
    for title in required:
        line = next((n for n, text in found if text.startswith(title)), None)
        if line is None:
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=f"少了「{title}」一節（要有一個標題是「{title}」）",
                    location=Location(file=file),
                )
            )
            continue
        if ordered and previous is not None and line < previous[1]:
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=f"「{title}」一節要排在「{previous[0]}」後面",
                    location=Location(file=file, line=line),
                )
            )
        previous = (title, line)
    if failures:
        return failures
    return [
        CheckEntry(
            check="C5",
            status="pass",
            reason=f"規定的 {len(required)} 節都在" + ("，順序也對" if ordered else ""),
        )
    ]


def check_source_refs(output: object, *, file: str) -> list[CheckEntry]:
    """查證（`fact_checks`）與研究題材（`topics`）引用的編號都要在來源清單（`sources`）上。

    格式不對的地方（不是物件、少欄位）交給 schema 檢查，這裡略過不重複報。
    """
    if not isinstance(output, Mapping):
        return []
    listed = {
        source.get("id")
        for source in output.get("sources") or []
        if isinstance(source, Mapping)
    }
    failures: list[CheckEntry] = []
    for group, label in (("fact_checks", "查證"), ("topics", "研究題材")):
        entries = output.get(group)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            refs = entry.get("sources") if isinstance(entry, Mapping) else None
            if not isinstance(refs, list):
                continue
            for position, ref in enumerate(refs):
                if ref in listed:
                    continue
                failures.append(
                    CheckEntry(
                        check="C5",
                        status="fail",
                        reason=f"{label}引用的 {ref} 不在來源清單上",
                        location=Location(
                            file=file,
                            pointer=json_pointer([group, index, "sources", position]),
                        ),
                        excerpt=str(ref),
                    )
                )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="引用的來源編號都在清單上")]


def brief_checks(
    *,
    brief: str,
    brief_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
) -> list[CheckEntry]:
    """brief 段的產出：`brief.md` 是 Markdown、六節都在；`brief.json` 符合 schema。"""
    return [
        *check_markdown(brief, file=brief_file),
        *check_sections(brief, BRIEF_SECTIONS, file=brief_file, ordered=False),
        *check_schema(output, schema, file=output_file),
    ]


def report_checks(
    *,
    report: str,
    report_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
) -> list[CheckEntry]:
    """查證與找題材段的產出：報告是 Markdown、五節依序都在；來源清單符合 schema、引用的編號都在清單上。"""
    return [
        *check_markdown(report, file=report_file),
        *check_sections(report, REPORT_SECTIONS, file=report_file, ordered=True),
        *check_schema(output, schema, file=output_file),
        *check_source_refs(output, file=output_file),
    ]
