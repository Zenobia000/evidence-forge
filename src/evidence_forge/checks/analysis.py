"""分析配方兩段的程式檢查（PRD v3.0 §7、ADR-0020 §3.4、ADR-0024 §3.3）：只看格式與出處，不判斷內容對不對。

- brief 段：`brief.md` 是標準 Markdown、規定的六節都在（順序不拘）；`brief.json` 符合 `brief` 的 schema。
- 查證與找題材段（US-22、`analyze.feature`）：
  - `report.md` 是標準 Markdown、第一個 `##` 之前有一句話、五節依序都在（PRD §5，標題固定是為了之後累積成知識庫）；
  - 報告裡沒有裸露的 `K<n>`、`S<n>` 編號（ADR-0024：編號只留在 `sources.json`，報告裡來源用名字）；
  - 「可以寫的題材」至少 2 個、從 1 起連續編號（創作者說「寫第 N 個」就是第 N 個）；
  - 「來源清單」一節的網址都是這一段這一次真的抓成功的網頁，或原文的網址——報告本身不能列憑記憶寫的來源；
  - `sources.json` 符合 `investigate` 的 schema（有爭議要附至少兩個來源）；查證與題材引用的編號都在清單上；
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
from evidence_forge.checks.text import line_column, mask_code, normalize_for_match, normalize_url

# brief.md 規定的幾節（FR-AN-01）；`harness/skills/brief/SKILL.md` 照這幾個標題寫
BRIEF_SECTIONS = (
    "論點與立場",
    "事實與數據",
    "觀點",
    "文類",
    "主題標籤",
    "可以延伸的方向",
)
# 分析報告的五節，依序（PRD §5、ADR-0024）；`harness/skills/investigate/SKILL.md` 照這幾個標題寫
REPORT_SECTIONS = ("作者在說什麼", "查證", "作者的看法", "可以寫的題材", "來源清單")
TOPICS_SECTION = "可以寫的題材"
# 可以寫的題材至少幾個（ADR-0024：通常 3 個，少於 2 個就沒得挑）
MIN_TOPICS = 2

# 只認 `##` 這一層：報告的 `#` 是文章標題，標題剛好以「查證」之類的節名開頭時不能算成一節
_HEADING = re.compile(r"^ {0,3}##[ \t]+(.*?)[ \t#]*$")
# 標題前面的編號（「1.」「2)」「三、」）與粗體記號不算標題的一部分
_NUMBERING = re.compile(r"^(?:\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")


def strip_heading(text: str) -> str:
    """標題文字去掉粗體記號與前面的編號（「2. **反方**」→「反方」）。"""
    return _NUMBERING.sub("", text.replace("**", "").strip()).strip()


def headings(markdown: str) -> list[tuple[int, str]]:
    """Markdown 的 `##` 標題：（行號, 去掉編號與粗體的標題文字）。程式碼區塊裡的不算。"""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(markdown).split("\n"), start=1):
        match = _HEADING.match(line)
        if match is None:
            continue
        found.append((number, strip_heading(match.group(1))))
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
    """查證（`fact_checks`）與可以寫的題材（`topics`）引用的編號都要在來源清單（`sources`）上。

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
    for group, label in (("fact_checks", "查證"), ("topics", "可以寫的題材")):
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


SUPPORTED = "supported"
SEED_ID = "S1"

# 任何一層的 Markdown 標題（`#`～`######`）
_ANY_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
# 長得像內部編號的字：K1、S12。前後不能黏著英數字（K8s、KS2023、SKU12 不算）
_INTERNAL_ID = re.compile(r"(?<![A-Za-z0-9_])([KS][0-9]+)(?![A-Za-z0-9_])")


# 「▶ 下一步：下面有 3 個可以寫的題材…」那一行
_NEXT_STEP = re.compile(r"^(?:▶\s*)?下一步")
_NEXT_STEP_COUNT = re.compile(r"([0-9]+|[一二兩三四五六七八九十])\s*個")
_CHINESE_DIGITS = {c: n for n, c in enumerate("一二三四五六七八九十", start=1)} | {"兩": 2}


def _opening_lines(report: str) -> list[tuple[int, str]]:
    """第一個 `##` 之前、不是空行也不是標題的每一行：（行號, 去掉前後空白的字）。"""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(report).split("\n"), start=1):
        if _HEADING.match(line):
            break
        if line.strip() and not _ANY_HEADING.match(line):
            found.append((number, line.strip()))
    return found


def check_lead(report: str, *, file: str) -> list[CheckEntry]:
    """第一個 `##` 之前（ADR-0024 §3.1）：第一行是一句話講這篇在講什麼；另有一行「▶ 下一步」，
    裡面寫的題材數要跟「可以寫的題材」實際的數目一樣。"""
    lines = _opening_lines(report)
    failures: list[CheckEntry] = []

    def fail(reason: str, line: int = 1) -> None:
        failures.append(
            CheckEntry(check="C5", status="fail", reason=reason, location=Location(file=file, line=line))
        )

    if not lines:
        fail("第一個「##」標題之前要先有一句話，講這篇在講什麼")
    elif _NEXT_STEP.match(lines[0][1]):
        fail("第一個「##」標題之前的第一行要是一句話講這篇在講什麼，不是下一步", lines[0][0])
    step = next(((n, text) for n, text in lines if _NEXT_STEP.match(text)), None)
    if step is None:
        fail("第一個「##」標題之前要有一行「▶ 下一步：下面有 N 個可以寫的題材…」")
    else:
        topics = len(topic_entries(report))
        match = _NEXT_STEP_COUNT.search(step[1])
        said = None if match is None else _CHINESE_DIGITS.get(match.group(1)) or int(match.group(1))
        if said != topics:
            fail(f"下一步那一行說有 {said if said is not None else '幾'} 個題材，「{TOPICS_SECTION}」實際有 {topics} 個", step[0])
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="第一個標題之前有一句話與下一步，題材數對得上")]


def check_internal_ids(
    report: str, *, file: str, known: Collection[str] | None = None
) -> list[CheckEntry]:
    """報告裡不能有 `K<n>`、`S<n>` 這類內部編號（ADR-0024）：創作者讀報告時沒有對照表。

    `known` 是這一份真的用到的編號（原文 S1、來源清單、關鍵主張）：給了就只擋這幾個，
    `Amazon S3`、`Galaxy S24` 這類產品名剛好不在清單上就不會誤擋；沒給就凡是長得像的都擋。
    程式碼裡的與網址裡的不算。同一個編號只報第一次出現的位置。
    """
    masked = _URL.sub(lambda match: " " * len(match.group(0)), mask_code(report))
    failures: list[CheckEntry] = []
    reported: set[str] = set()
    for match in _INTERNAL_ID.finditer(masked):
        found = match.group(1)
        if found in reported or (known is not None and found not in known):
            continue
        reported.add(found)
        line, column = line_column(report, match.start())
        failures.append(
            CheckEntry(
                check="C5",
                status="fail",
                reason=f"報告裡出現內部編號 {found}：K、S 編號只留在 sources.json，報告裡來源用名字",
                location=Location(file=file, line=line, column=column),
                excerpt=found,
            )
        )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="報告裡沒有裸露的內部編號")]


# 可以寫的題材一個：`### 1. 暫定標題`（標題可以加粗）。畫面與檢查都只認這一種寫法
TOPIC_HEADING = re.compile(r"^ {0,3}###[ \t]+(?:\*\*)?(\d+)[.)、][ \t]*(.*?)(?:\*\*)?[ \t#]*$")
# 查證一條開頭的標記（ADR-0023 的四種）
MARK = re.compile(r"^(?:\*\*)?【(有爭議|過時|查不到|有佐證)】(?:\*\*)?\s*")
# sources.json 的 verdict → 報告裡的標記
VERDICT_MARKS = {"disputed": "有爭議", "outdated": "過時", "not_found": "查不到", "supported": "有佐證"}
# 一節裡不縮排的清單項目（縮排的是它的續行或子項目）
_TOP_ITEM = re.compile(r"^ {0,1}(?:[-*+]|\d+[.)])[ \t]+(.*)$")


def topic_entries(report: str) -> list[tuple[int, int, str, str]]:
    """「可以寫的題材」裡的每個題材：（行號, 編號, 暫定標題, 底下的內文）。程式碼區塊裡的不算。"""
    masked = dict(_section_lines(mask_code(report), TOPICS_SECTION))
    original = dict(_section_lines(report, TOPICS_SECTION))
    found: list[tuple[int, int, str, list[str]]] = []
    for number, line in masked.items():
        match = TOPIC_HEADING.match(line)
        if match is not None:
            found.append((number, int(match.group(1)), match.group(2).replace("**", "").strip(), []))
        elif found:
            found[-1][3].append(original[number])
    return [(line, n, title, "\n".join(body).strip()) for line, n, title, body in found]


def section_items(report: str, title: str) -> list[tuple[int, str]]:
    """一節裡不縮排的清單項目：（行號, 項目的字，縮排的續行與子項目接在後面）。"""
    found: list[tuple[int, list[str]]] = []
    masked = _section_lines(mask_code(report), title)
    original = dict(_section_lines(report, title))
    for number, line in masked:
        match = _TOP_ITEM.match(line)
        if match is not None:
            found.append((number, [original[number][match.start(1) :]]))
        elif found and line.strip() and line[:1] in " \t":
            found[-1][1].append(original[number].strip())
        elif found and not line.strip():
            continue
        elif line.strip():
            # 不在清單裡的段落：之後的行不再算前一條的續行
            found.append((number, [original[number].strip()]))
    return [(number, "\n".join(lines)) for number, lines in found]


def verify_marks(report: str) -> list[str | None]:
    """「查證」一節每一條開頭的標記（有爭議、過時、查不到、有佐證）；沒有標記的是 None。"""
    marks: list[str | None] = []
    for _, text in section_items(report, "查證"):
        match = MARK.match(text)
        marks.append(match.group(1) if match else None)
    return marks


def fact_check_marks(output: object) -> list[str] | None:
    """sources.json 的 fact_checks 照順序換成報告的標記；格式不對（交給 schema 報）是 None。"""
    entries = output.get("fact_checks") if isinstance(output, Mapping) else None
    if not isinstance(entries, list):
        return None
    return [
        VERDICT_MARKS.get(str(entry.get("verdict")), "?") if isinstance(entry, Mapping) else "?"
        for entry in entries
    ]


def check_topics_numbered(report: str, *, file: str) -> list[CheckEntry]:
    """「可以寫的題材」至少 `MIN_TOPICS` 個、從 1 起連續編號（ADR-0024）。只認 `### 1. 暫定標題`。"""
    entries = topic_entries(report)
    numbers = [number for _, number, _, _ in entries]
    lines = _section_lines(report, TOPICS_SECTION)
    where = Location(file=file, line=lines[0][0] - 1 if lines else None)
    if len(numbers) < MIN_TOPICS:
        return [
            CheckEntry(
                check="C5",
                status="fail",
                reason=(
                    f"「{TOPICS_SECTION}」要至少 {MIN_TOPICS} 個有編號的題材（### 1. 暫定標題），"
                    f"現在有 {len(numbers)} 個"
                ),
                location=where,
            )
        ]
    if numbers != list(range(1, len(numbers) + 1)):
        return [
            CheckEntry(
                check="C5",
                status="fail",
                reason="可以寫的題材的編號要從 1 起連續，現在是 " + "、".join(map(str, numbers)),
                location=where,
            )
        ]
    return [
        CheckEntry(check="C5", status="pass", reason=f"可以寫的題材有 {len(numbers)} 個，編號連續")
    ]


def check_fact_alignment(report: str, output: object, *, file: str) -> list[CheckEntry]:
    """「查證」一條一條的標記，要跟 sources.json 的 fact_checks 同樣多、每個位置同一種結果。

    報告沒有編號了，畫面靠這個順序把每一條的來源接回去；對不上就算這一次不合格（重試）。
    """
    expected = fact_check_marks(output)
    if expected is None:
        return []
    marks = verify_marks(report)
    if marks == expected:
        return [CheckEntry(check="C5", status="pass", reason="查證的標記跟 fact_checks 一條一條對得上")]

    def show(found: Sequence[str | None]) -> str:
        return "、".join(mark or "沒有標記" for mark in found) or "（沒有）"

    return [
        CheckEntry(
            check="C5",
            status="fail",
            reason=(
                "查證一節的標記要跟 sources.json 的 fact_checks 一條一條對上："
                f"報告是 {show(marks)}，sources.json 是 {show(expected)}"
            ),
            location=Location(file=file),
        )
    ]


def topic_titles(output: object) -> list[str] | None:
    """sources.json 的 topics 的標題；格式不對（交給 schema 報）是 None。"""
    entries = output.get("topics") if isinstance(output, Mapping) else None
    if not isinstance(entries, list):
        return None
    return [str(entry.get("title", "")) if isinstance(entry, Mapping) else "" for entry in entries]


def check_topic_alignment(report: str, output: object, *, file: str) -> list[CheckEntry]:
    """「可以寫的題材」要跟 sources.json 的 topics 一樣多、標題一樣（正規化後比）、順序一樣。"""
    expected = topic_titles(output)
    if expected is None:
        return []
    found = [title for _, _, title, _ in topic_entries(report)]
    prefix = "可以寫的題材要跟 sources.json 的 topics 一樣多、標題一樣、順序一樣："
    if len(found) != len(expected):
        reason = f"{prefix}報告有 {len(found)} 個，sources.json 有 {len(expected)} 個"
    else:
        wrong = next(
            (
                i
                for i, (a, b) in enumerate(zip(found, expected, strict=True))
                if normalize_for_match(a) != normalize_for_match(b)
            ),
            None,
        )
        if wrong is None:
            return [CheckEntry(check="C5", status="pass", reason="可以寫的題材跟 topics 對得上")]
        reason = f"{prefix}第 {wrong + 1} 個報告是「{found[wrong]}」，sources.json 是「{expected[wrong]}」"
    return [CheckEntry(check="C5", status="fail", reason=reason, location=Location(file=file))]


def _section_lines(markdown: str, title: str) -> list[tuple[int, str]]:
    """以 `title` 開頭的那一節（到下一個 `##` 為止）的每一行：（行號, 原文）。沒有這一節是空的。"""
    found_headings = headings(markdown)
    start = next((n for n, text in found_headings if text.startswith(title)), None)
    if start is None:
        return []
    end = next((n for n, _ in found_headings if n > start), None)
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


def _known_ids(output: object, key_claims: Sequence[str] | None) -> set[str]:
    """這一份分析用到的編號：原文 S1、來源清單的、查證過的主張與 brief 的關鍵主張。"""
    known = {SEED_ID, *(key_claims or [])}
    if isinstance(output, Mapping):
        for group, key in (("sources", "id"), ("fact_checks", "claim")):
            for entry in output.get(group) or []:
                if isinstance(entry, Mapping) and isinstance(entry.get(key), str):
                    known.add(entry[key])
    return known


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
        *check_lead(report, file=report_file),
        *check_sections(report, REPORT_SECTIONS, file=report_file, ordered=True),
        *check_internal_ids(report, file=report_file, known=_known_ids(output, key_claims)),
        *check_topics_numbered(report, file=report_file),
        *check_fact_alignment(report, output, file=report_file),
        *check_topic_alignment(report, output, file=report_file),
        *check_source_list_urls(report, allowed_urls, file=report_file),
        *check_schema(output, schema, file=output_file),
        *check_source_refs(output, file=output_file),
        *coverage,
        *check_verdict_order(output, file=output_file),
    ]
