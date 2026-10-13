"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020 §3.4）：只看格式與出處，不判斷內容對不對。

- brief 段：`brief.md` 是標準 Markdown、規定的六節都在（順序不拘）；`brief.json` 符合 `brief` 的 schema。
- 查證與找題材段（US-22、`analyze.feature`）：
  - `report.md` 是標準 Markdown、五節依序都在（PRD §5，標題固定是為了之後累積成知識庫）；
  - 報告內文引用的 `[S<n>]` 都在來源清單上（或是原文 S1）；「來源清單」一節的網址都是這一段這一次
    真的抓成功的網頁，或原文的網址——報告本身不能列憑記憶寫的來源；
  - `sources.json` 符合 `investigate` 的 schema（有爭議要附至少兩個來源）；查證與研究題材引用的編號都在清單上；
  - brief 的每條關鍵主張剛好一筆查證；有疑慮的（有爭議、過時、查不到）排在有佐證的前面。
  `sources.json` 清單上的網址是不是這一段真的抓過的，由執行器的 `runner/sources.py` 查；
  「這一段抓過哪些網頁」也由執行器從閘道的帳本算好傳進來（`allowed_urls`），這裡不讀帳本。

全部是純函式：檔案由 `job.py` 讀好傳進來，不讀檔、不寫檔、不呼叫模型。
條目都算 C5（格式）：ADR-0020 拿掉了 C1、C2、C4，留下的只有格式與出處。
"""

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from evidence_forge.checks.format import check_markdown, check_schema
from evidence_forge.checks.report import CheckEntry, Location, json_pointer
from evidence_forge.checks.text import line_column, mask_code, normalize_url

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

# 只認 `##` 這一層：報告的 `#` 是文章標題，標題剛好以「查證」之類的節名開頭時不能算成一節
_HEADING = re.compile(r"^ {0,3}##[ \t]+(.*?)[ \t#]*$")
# 標題前面的編號（「1.」「2)」「三、」）與粗體記號不算標題的一部分
_NUMBERING = re.compile(r"^(?:\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")


def _headings(markdown: str) -> list[tuple[int, str]]:
    """Markdown 的 `##` 標題：（行號, 去掉編號與粗體的標題文字）。程式碼區塊裡的不算。"""
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
    headings = _headings(markdown)
    failures: list[CheckEntry] = []
    previous: tuple[str, int] | None = None
    for title in required:
        line = next((n for n, text in headings if text.startswith(title)), None)
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


# 報告內文的引用：[S2]、[S2, S3]、[S2、S3]
_CITATION = re.compile(r"\[(S\d+(?:\s*[,、，]\s*S\d+)*)\]")
_CITED_ID = re.compile(r"S\d+")
# 來源清單裡的網址：碰到空白、角括號、方括號、全形標點就結束。半形括號留在裡面
# （`…/Example_(film)` 是網址的一部分），結尾多出來、沒配對的 `)` 再由 `_trim_url` 拿掉
_URL = re.compile(r"https?://[^\s<>\[\]「」『』，、。；：）（]+")
_URL_TRAILING = ".,;:!?'\""


def _trim_url(raw: str) -> str:
    """拿掉網址結尾不屬於它的字：句尾標點，與沒配對的 `)`（Markdown 連結 `[t](url)` 的收尾、
    句子裡 `(url)` 的右括號）。網址自己帶的成對括號留著。"""
    url = raw
    while True:
        trimmed = url.rstrip(_URL_TRAILING)
        if trimmed.endswith(")") and trimmed.count(")") > trimmed.count("("):
            trimmed = trimmed[:-1]
        if trimmed == url:
            return url
        url = trimmed
SEED_ID = "S1"
SUPPORTED = "supported"


def _listed_ids(output: object) -> set[str]:
    if not isinstance(output, Mapping):
        return set()
    return {
        str(source.get("id"))
        for source in output.get("sources") or []
        if isinstance(source, Mapping)
    }


def check_report_citations(
    report: str, listed: Collection[str], *, file: str
) -> list[CheckEntry]:
    """報告內文引用的每個來源編號都要在來源清單上，或是原文 S1。同一個編號只報一次。"""
    allowed = {*listed, SEED_ID}
    failures: list[CheckEntry] = []
    reported: set[str] = set()
    for match in _CITATION.finditer(mask_code(report)):
        for cited in _CITED_ID.findall(match.group(1)):
            if cited in allowed or cited in reported:
                continue
            reported.add(cited)
            line, column = line_column(report, match.start())
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=f"報告引用的 {cited} 不在來源清單上",
                    location=Location(file=file, line=line, column=column),
                    excerpt=match.group(0),
                )
            )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="報告引用的編號都在來源清單上")]


def _section_lines(markdown: str, title: str) -> list[tuple[int, str]]:
    """以 `title` 開頭的那一節（到下一個 `##` 為止）的每一行：（行號, 原文）。沒有這一節是空的。"""
    headings = _headings(markdown)
    start = next((n for n, text in headings if text.startswith(title)), None)
    if start is None:
        return []
    end = next((n for n, _ in headings if n > start), None)
    lines = markdown.split("\n")
    stop = end - 1 if end is not None else len(lines)
    return [(number, lines[number - 1]) for number in range(start + 1, stop + 1)]


def check_source_list_urls(
    report: str, allowed_urls: Collection[str], *, file: str
) -> list[CheckEntry]:
    """「來源清單」一節列的每個網址，都要是這一段這一次真的抓成功的網頁，或原文的網址。

    `allowed_urls` 由執行器給，已經用 `normalize_url` 正規化（要求的與轉址後的網址都算）。
    """
    allowed = set(allowed_urls)
    failures: list[CheckEntry] = []
    for number, line in _section_lines(report, REPORT_SECTIONS[-1]):
        for match in _URL.finditer(line):
            url = _trim_url(match.group(0))
            if normalize_url(url) in allowed:
                continue
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=f"來源清單列了 {url}：這一段沒有用 fetch_url 抓到它，也不是原文",
                    location=Location(file=file, line=number, column=match.start() + 1),
                    excerpt=url,
                )
            )
    if failures:
        return failures
    return [
        CheckEntry(
            check="C5", status="pass", reason="來源清單的網址都是這一段抓過的網頁或原文"
        )
    ]


def _fact_checks(output: object) -> list[tuple[int, Mapping[str, Any]]]:
    entries = output.get("fact_checks") if isinstance(output, Mapping) else None
    if not isinstance(entries, list):
        return []
    return [
        (index, entry) for index, entry in enumerate(entries) if isinstance(entry, Mapping)
    ]


def check_claim_coverage(
    output: object, key_claims: Sequence[str], *, file: str
) -> list[CheckEntry]:
    """brief 的每條關鍵主張剛好一筆查證：少的、重複的、brief 沒有的各一條 fail。"""
    seen: dict[str, int] = {}
    failures: list[CheckEntry] = []
    for index, entry in _fact_checks(output):
        claim = str(entry.get("claim"))
        where = Location(file=file, pointer=json_pointer(["fact_checks", index, "claim"]))
        if claim not in key_claims:
            reason = f"查證了 {claim}，brief 沒有這條關鍵主張"
        elif claim in seen:
            reason = (
                f"{claim} 查證了兩次（第 {seen[claim] + 1} 筆與第 {index + 1} 筆），"
                "每條關鍵主張只能一筆"
            )
        else:
            seen[claim] = index
            continue
        failures.append(
            CheckEntry(
                check="C5", status="fail", reason=reason, location=where, excerpt=claim
            )
        )
    for claim in key_claims:
        if claim not in seen:
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=f"brief 的關鍵主張 {claim} 沒有查證結果",
                    location=Location(file=file, pointer="/fact_checks"),
                    excerpt=claim,
                )
            )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="每條關鍵主張剛好一筆查證")]


def check_verdict_order(output: object, *, file: str) -> list[CheckEntry]:
    """有疑慮的查證（有爭議、過時、查不到）要排在有佐證的前面（`analyze.feature`）。"""
    first_supported: str | None = None
    failures: list[CheckEntry] = []
    for index, entry in _fact_checks(output):
        claim = str(entry.get("claim"))
        if entry.get("verdict") == SUPPORTED:
            first_supported = first_supported or claim
            continue
        if first_supported is None:
            continue
        failures.append(
            CheckEntry(
                check="C5",
                status="fail",
                reason=(
                    f"{claim}（{entry.get('verdict')}）有疑慮，"
                    f"要排在有佐證的 {first_supported} 前面"
                ),
                location=Location(file=file, pointer=json_pointer(["fact_checks", index])),
                excerpt=claim,
            )
        )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="有疑慮的查證排在有佐證的前面")]

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
    allowed_urls: Collection[str],
    key_claims: Sequence[str] | None,
) -> list[CheckEntry]:
    """查證與找題材段的產出，見模組說明。

    `allowed_urls`：這一段這一次抓成功的網頁與原文的網址（正規化過）。
    `key_claims`：brief 的關鍵主張編號；前面沒有 brief 段（例：之後只跑查證段的補查）時是 None，不查涵蓋。
    """
    coverage = (
        check_claim_coverage(output, key_claims, file=output_file)
        if key_claims is not None
        else []
    )
    return [
        *check_markdown(report, file=report_file),
        *check_sections(report, REPORT_SECTIONS, file=report_file, ordered=True),
        *check_report_citations(report, _listed_ids(output), file=report_file),
        *check_source_list_urls(report, allowed_urls, file=report_file),
        *check_schema(output, schema, file=output_file),
        *check_source_refs(output, file=output_file),
        *coverage,
        *check_verdict_order(output, file=output_file),
    ]
