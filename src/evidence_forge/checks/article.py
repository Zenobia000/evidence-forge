"""重構段的程式檢查（PRD v3.0 §6 第 4 點、§7、ADR-0020 §3.4、ADR-0024 §3.3）：只看格式與出處，不判斷寫得好不好。

- 擋（C5）：`article.md` 是標準 Markdown；最後一節是「參考來源」，裡面有一筆同時列了原文的
  標題與網址（貼上的文字、上傳的檔沒有網址就只看標題），素材有作者時也要有作者；
  任何一層標題都不是文類骨架的字（結論、論據、反方、建議）；參考來源以外的內文
  不出現原文的標題與原作者名（文章用角色自己的聲音寫，不回應原文）；
  `article.json` 符合 `rewrite` 的 schema。
- 只顯示、不擋（C3）：文章正文跟原文、跟每一份來源的逐字重疊，各算一份（`overlap.py`）。
  參考來源那一節不算：它本來就照抄標題與網址。
- 照編輯卡寫的文章（`ArticleRules`，ADR-0025 §3.7、ADR-0026 §3.2、#110）另外擋：
  front matter 有 `title`、`description`、`genre`、`theme`，文體與主題各一個、是合法的 slug、跟編輯卡一樣；
  參考來源每一筆有網址，網址是這張單真的有的（原文、分析抓過的來源、作者素材裡寫的），不能自己編；
  角色資料列出的句型（`voice.md` front matter 的 `sentence_patterns`，規則內容在角色資料，程式只照它數）；
  圖文分鏡的每一張概念圖、數據圖都有圖位（`./images/fig-N.webp`，附圖說），沒有分鏡以外的圖。
  沒有原文（從創作者自己的想法或作者素材開始）時，原文出處與「不提原文」都不查；
  文體是 `essay` 而且從報告開始時，內文可以引用並回應原文，原文照樣要在參考來源第一條（ADR-0025 §3.4）。

全部是純函式：檔案由執行器讀好傳進來，不讀檔、不寫檔、不呼叫模型。
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from evidence_forge.checks.analysis import headings, strip_heading
from evidence_forge.checks.format import check_markdown, check_schema
from evidence_forge.checks.overlap import OverlapThresholds, check_overlap
from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import mask_code, normalize_for_match, normalize_url

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


# 參考來源一筆的開頭：清單記號（-、*、+、1.、1)）
_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])\s+")


def _entries(references: str) -> list[str]:
    """參考來源那一節拆成一筆一筆：清單的每一項（縮排的續行算同一項）；沒有清單就一行一筆。"""
    lines = [line for line in references.split("\n") if line.strip()]
    if not any(_ITEM.match(line) for line in lines):
        return lines
    found: list[str] = []
    for line in lines:
        if _ITEM.match(line) or not found:
            found.append(line)
        else:
            found[-1] += "\n" + line
    return found


# 網頁 <title> 常見的「文章標題 | 站名」分隔；站名那一段可以不照抄
_SITE_SUFFIX = re.compile(r"\s+[|｜–—-]\s+[^|｜–—-]+$")


def _title_forms(title: str) -> list[str]:
    """認得的原文標題寫法：完整的，與去掉結尾站名的（「文章標題 | 站名」的「文章標題」）。"""
    forms = [title]
    shorter = _SITE_SUFFIX.sub("", title).strip()
    if shorter and shorter != title.strip():
        forms.append(shorter)
    return forms


def _missing(
    entry: str, url: str | None, title: str, author: str | None
) -> list[str]:
    """這一筆少了原文出處的哪幾樣（網址、標題、作者）；都有就是空的。"""
    missing: list[str] = []
    if url:
        # 跟分析報告的來源清單同一套網址比對（`normalize_url`）：主機大小寫、# 片段、結尾的 / 不算差別；
        # 只比整個網址，前綴相同的另一篇（…/post 與 …/post-2）不算
        listed = {normalize_url(found.rstrip(").,")) for found in _URL.findall(entry)}
        if normalize_url(url) not in listed:
            missing.append(f"網址 {url.strip()}")
    # 標題與作者只在網址以外的文字裡找：網址本身常含著標題的字（…/night-library），不能算數
    text = normalize_for_match(_URL.sub(" ", entry))
    if not any(normalize_for_match(name) in text for name in _title_forms(title)):
        missing.append(f"標題「{title}」")
    if author and normalize_for_match(author) not in text:
        missing.append(f"作者「{author}」")
    return missing


def check_references(
    article: str,
    *,
    file: str,
    original_url: str | None,
    original_title: str,
    original_author: str | None = None,
) -> list[CheckEntry]:
    """結尾的「參考來源」有沒有列原文出處。對就一條 pass，不對就一條 fail。

    要有一筆同時寫了原文的標題與網址（原文沒有網址就只看標題），素材項目有作者時也要有作者
    （PRD §6 第 4 點、`rewrite.feature`）。不在同一筆、或網址只是前綴相同，都不算。
    """
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
    gaps = [
        _missing(entry, original_url, original_title, original_author)
        for entry in _entries(references)
    ]
    # 原文出處要是第一條（ADR-0024 §3.2 第 4 點）；有列但不在第一條也擋
    later_index = next((i for i, gap in enumerate(gaps) if not gap), None)
    if later_index is not None and later_index > 0:
        return [
            _fail(
                f"原文出處要放在「{REFERENCES_HEADING}」的第一條，現在在第 {later_index + 1} 條",
                file,
                line,
            )
        ]
    if any(not gap for gap in gaps):
        return [
            CheckEntry(check="C5", status="pass", reason="結尾的參考來源列了原文出處")
        ]
    # 報最接近的那一筆缺什麼（缺得最少的）；一筆都沒有就是全部都缺
    closest = min(
        gaps,
        key=len,
        default=_missing("", original_url, original_title, original_author),
    )
    return [
        _fail(
            f"「{REFERENCES_HEADING}」沒有一筆完整列出原文出處：同一筆裡少了"
            + "、".join(closest),
            file,
            line,
        )
    ]


# 任何一層的 Markdown 標題：（# 的個數, 標題文字）
_ANY_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$")
# 文類骨架的字當標題（ADR-0024 §3.2）：「先講結論」「論據一：…」「反方怎麼說」「我的建議」「結論與行動」。
# 結論、論據、建議後面接的是編號、標點、或「與／說法／怎麼說／回應／先講」才算；「建議先別急著買」這種有實際內容的不算
_PUNCT = r"[：:，,、。！？!?（(]"
_SKELETON = re.compile(
    # 「反方」後面接觀點、說法、怎麼說、的、意見或標點才算（「反方向思考」不算）
    rf"^(?:反方(?:$|{_PUNCT}|觀點|說法|怎麼說|的|意見|與|和)"
    # 「論點一」「論點 2」
    r"|論點\s*[一二三四五六七八九十\d]+"
    rf"|(?:先講|先說|我的|最後的?)?(?:結論|論據|建議|總結|行動建議)"
    r"(?:\s*[一二三四五六七八九十\d]+)?\s*"
    rf"(?:$|{_PUNCT}|與|和|說法|怎麼說|的?回應|先講))"
)
# 目標文類骨架的一項：`## 骨架` 那一節裡的有編號清單
_SKELETON_ITEM = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")


def genre_skeleton(definition: str) -> list[str]:
    """從文類定義（`input/target-genre.md`）讀出骨架的每一項。

    「論據 1～3（各附證據）」「情境：發生了什麼」這種帶說明的，另外放一份去掉說明的（「論據 1～3」「情境」）。
    """
    found: list[str] = []
    inside = False
    for line in definition.split("\n"):
        if line.startswith("## "):
            inside = line[3:].strip().startswith("骨架")
            continue
        match = _SKELETON_ITEM.match(line) if inside else None
        if match is None:
            continue
        item = match.group(1)
        found.append(item)
        base = re.split(r"[（(：:]", item, maxsplit=1)[0].strip()
        if base and base != item:
            found.append(base)
    return found


def check_skeleton_headings(
    article: str, *, file: str, skeleton: Sequence[str] = ()
) -> list[CheckEntry]:
    """文章的標題不能是文類骨架的字：骨架只是思考順序，標題寫這一節實際在講什麼（ADR-0024）。

    擋兩種：結論、論據、反方、建議這類骨架字（`_SKELETON`），與剛好等於目標文類骨架某一項的標題
    （`skeleton`，由 `genre_skeleton` 讀出；案例拆解的「背景」「做法」「結果」這類）。
    每個這樣的標題一條 fail；程式碼區塊裡的不算。
    """
    items = {normalize_for_match(item) for item in skeleton}
    failures: list[CheckEntry] = []
    for number, line in enumerate(mask_code(article).split("\n"), start=1):
        match = _ANY_HEADING.match(line)
        if match is None:
            continue
        text = strip_heading(match.group(2))
        if not _SKELETON.match(text) and normalize_for_match(text) not in items:
            continue
        failures.append(
            CheckEntry(
                check="C5",
                status="fail",
                reason=(
                    f"標題「{match.group(2)}」是文類骨架的字（結論、論據、反方、建議）："
                    "骨架只是思考順序，標題要寫這一節實際在講什麼"
                ),
                location=Location(file=file, line=number),
                excerpt=match.group(2),
            )
        )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="標題沒有用文類骨架的字")]


# 原文標題短到這個字數以內就不比：太短的標題（「夜間閱讀」）在正常內文裡也會出現，比了會誤擋
MIN_TITLE_CHARS = 7
# 作者名至少這幾個字才比
MIN_AUTHOR_CHARS = 2


def _first_line_with(body: str, needle: str) -> int | None:
    """正規化之後含著 `needle` 的第一行；跨行才湊得出來的回 None。"""
    for number, line in enumerate(body.split("\n"), start=1):
        if needle in normalize_for_match(line):
            return number
    return None


# 只有拉丁字母、數字與空白的名字（「Sam Lee」）：要整個字對上，「Sam」不能算到「Samsung」裡
_LATIN_NAME = re.compile(r"[A-Za-z0-9 .'-]+")


def _mentions(body: str, normalized_body: str, name: str) -> bool:
    """內文有沒有提到這個名字。中文的名字比正規化後的字串；拉丁字母的名字要前後不黏著英數字。"""
    words = name.split()
    if _LATIN_NAME.fullmatch(name.strip()) and words:
        pattern = r"(?<![A-Za-z0-9])" + r"\s*".join(map(re.escape, words)) + r"(?![A-Za-z0-9])"
        return re.search(pattern, body) is not None
    return normalize_for_match(name) in normalized_body


def _other_sources(references: str | None, url: str | None, title: str) -> list[str]:
    """參考來源裡原文以外的每一筆（網址拿掉）；原文那一筆是列了原文網址或原文標題的。"""
    if references is None:
        return []
    forms = [normalize_for_match(form) for form in _title_forms(title)]
    others: list[str] = []
    for entry in _entries(references):
        urls = {normalize_url(found.rstrip(").,")) for found in _URL.findall(entry)}
        text = _URL.sub(" ", entry)
        if (url and normalize_url(url) in urls) or any(f in normalize_for_match(text) for f in forms):
            continue
        others.append(text)
    return others


def check_original_not_in_body(
    article: str,
    *,
    file: str,
    original_title: str,
    original_author: str | None,
    original_url: str | None = None,
) -> list[CheckEntry]:
    """參考來源以外的內文不能出現原文的標題與原作者名（ADR-0024 §3.2 第 2 點）。

    原文只列在參考來源。標題認完整的與去掉結尾站名的（`_title_forms`）；太短的標題、作者不比。
    作者名在參考來源裡別的來源也出現（同一個品牌發的文件、同一位作者的系列文、別篇的標題）時不擋：
    那是在講那份來源，rewrite 的 SKILL 也要文章用發布者的名字講來源。原文標題照擋。
    """
    body, references, _ = split_references(article)
    text = normalize_for_match(body)
    named: list[tuple[str, str]] = [
        ("標題", form)
        for form in _title_forms(original_title)
        if len(normalize_for_match(form)) >= MIN_TITLE_CHARS
    ]
    others = _other_sources(references, original_url, original_title)
    if (
        original_author
        and len(normalize_for_match(original_author)) >= MIN_AUTHOR_CHARS
        and not any(_mentions(entry, normalize_for_match(entry), original_author) for entry in others)
    ):
        named.append(("作者", original_author))
    failures: list[CheckEntry] = []
    reported: set[str] = set()
    for kind, name in named:
        needle = normalize_for_match(name)
        # 完整標題與去掉站名的標題都對上時只報一次
        if kind in reported or not _mentions(body, text, name):
            continue
        reported.add(kind)
        failures.append(
            CheckEntry(
                check="C5",
                status="fail",
                reason=(
                    f"內文提到了原文的{kind}「{name.strip()}」：文章用角色自己的聲音寫這個主題，"
                    "不提原文與原作者，原文只列在參考來源"
                ),
                location=Location(file=file, line=_first_line_with(body, needle)),
                excerpt=name.strip(),
            )
        )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="內文沒有提原文的標題與原作者")]


# 回應原文的說法（ADR-0024 §3.2 第 2 點）：內文不提原文、不提原作者。
# 「原文」一律擋，只放過幾個跟「那篇原文」無關的固定說法（`_BENIGN_ORIGINAL`）
_REPLY_PHRASE = re.compile(r"原作者|這篇(?:文章)?談的是|原文")
# 不是在講「那篇原文」的「原文」：原文書、英文原文、照原文、依原文、原文翻譯、原文照登
_BENIGN_ORIGINAL = re.compile(r"(?:英|日|中|德|法|韓)文原文|[照依]原文|原文(?:書|翻譯|照登)")


def check_reply_phrases(article: str, *, file: str) -> list[CheckEntry]:
    """參考來源以外的內文不能有回應原文的說法：「原作者」「這篇談的是」與「原文」（幾個固定說法除外）。每種說法報一次。"""
    body, _, _ = split_references(article)
    failures: list[CheckEntry] = []
    reported: set[str] = set()
    masked = _BENIGN_ORIGINAL.sub(lambda match: " " * len(match.group(0)), mask_code(body))
    for number, line in enumerate(masked.split("\n"), start=1):
        for match in _REPLY_PHRASE.finditer(line):
            phrase = match.group(0)
            if phrase in reported:
                continue
            reported.add(phrase)
            failures.append(
                CheckEntry(
                    check="C5",
                    status="fail",
                    reason=(
                        f"內文寫了「{phrase}」：文章用角色自己的聲音寫這個主題，"
                        "不提原文、不回應原文，原文只列在參考來源"
                    ),
                    location=Location(file=file, line=number, column=match.start() + 1),
                    excerpt=phrase,
                )
            )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="內文沒有回應原文的說法")]


# --- 照編輯卡寫的文章（#110）------------------------------------------------------------------


@dataclass(frozen=True)
class SentencePattern:
    """角色資料列的一條句型規則：名字、怎麼認、最多幾次（`voice.md` 的 `sentence_patterns`）。"""

    name: str
    pattern: re.Pattern[str]
    max: int


@dataclass(frozen=True)
class ArticleRules:
    """照編輯卡寫的文章多查的幾條（執行器從工單的編輯卡快照與角色資料讀好傳進來）。

    - `genres`、`themes`：合法的文體與主題 slug；`genre`、`theme`：編輯卡定的（None 就只查合法）。
    - `figures`：分鏡裡要有圖位的編號（`cards.figure_slots`）；None 是沒有結構化的分鏡，圖位不查。
    - `patterns`：角色的句型規則。`known_urls`：參考來源准列的網址（正規化過）；None 就不查。
    - `answers_original`：文體是 essay 而且從報告開始，內文可以引用並回應原文。
    """

    genres: frozenset[str]
    themes: frozenset[str]
    genre: str | None = None
    theme: str | None = None
    figures: tuple[int, ...] | None = None
    patterns: tuple[SentencePattern, ...] = ()
    known_urls: frozenset[str] | None = None
    answers_original: bool = False


# 角色資料的句型規則：一條的 pattern 最長幾個字（是創作者自己寫的正規表示式，長了就是寫錯）
MAX_PATTERN = 200
FRONT_MATTER_FIELDS = ("title", "description", "genre", "theme")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_sentence_patterns(meta: Mapping[str, Any]) -> tuple[SentencePattern, ...]:
    """角色 `voice.md` front matter 的 `sentence_patterns`（ADR-0025 §3.7、ADR-0026 §3.3）。

    格式：清單，每一條 `{name: 給人看的名字, pattern: 正規表示式, max: 最多幾次}`。沒有這一欄是空的。
    寫錯（不是清單、少欄位、正規表示式不合法、max 不是 0 以上的整數）丟 `ValueError` 說是哪一條：
    重寫文章修不好，要創作者改角色資料。
    """
    raw = meta.get("sentence_patterns")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("角色 voice.md 的 sentence_patterns 要是清單")
    found: list[SentencePattern] = []
    for number, item in enumerate(raw, start=1):
        where = f"角色 voice.md 的 sentence_patterns 第 {number} 條"
        if not isinstance(item, dict):
            raise ValueError(f"{where}要是 name、pattern、max 三欄")
        name, pattern, limit = item.get("name"), item.get("pattern"), item.get("max")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}少了 name")
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
            raise ValueError(f"{where}的 pattern 要是 1～{MAX_PATTERN} 字的正規表示式")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"{where}的 max 要是 0 以上的整數")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{where}的 pattern 不是合法的正規表示式：{exc}") from None
        found.append(SentencePattern(name=name.strip(), pattern=compiled, max=limit))
    return tuple(found)


def check_front_matter(
    article: str,
    *,
    file: str,
    genres: frozenset[str],
    themes: frozenset[str],
    genre: str | None = None,
    theme: str | None = None,
) -> list[CheckEntry]:
    """文章開頭的 front matter（ADR-0025 §3.7、ADR-0026 §3.2）：四欄都有，文體與主題各一個、合法、跟編輯卡一樣。"""
    match = _FRONT.match(article)
    if match is None:
        return [_fail("文章開頭沒有 front matter（--- 包起來的 title、description、genre、theme）", file, 1)]
    raw = match.group(1)
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        return [_fail("文章的 front matter 讀不懂（不是合法的 YAML）", file, 1)]
    if not isinstance(meta, dict):
        return [_fail("文章的 front matter 要是 title、description、genre、theme 這幾欄", file, 1)]
    failures: list[CheckEntry] = []
    for name in FRONT_MATTER_FIELDS:
        if not isinstance(meta.get(name), str) or not str(meta[name]).strip():
            failures.append(_fail(f"front matter 少了 {name}（要是一段文字）", file, 1))
    # YAML 同一欄寫兩次只留最後一個：照原文數，每篇只有一個文體、一個主題
    for name in ("genre", "theme"):
        count = len(re.findall(rf"^{name}\s*:", raw, flags=re.MULTILINE))
        if count > 1:
            failures.append(_fail(f"front matter 的 {name} 寫了 {count} 次：每篇只有一個", file, 1))
    for name, allowed, wanted in (("genre", genres, genre), ("theme", themes, theme)):
        value = meta.get(name)
        if not isinstance(value, str):
            continue
        if value not in allowed:
            failures.append(
                _fail(f"front matter 的 {name} 是 {value}，不是合法的值（{'、'.join(sorted(allowed))}）", file, 1)
            )
        elif wanted is not None and value != wanted:
            failures.append(_fail(f"front matter 的 {name} 要是編輯卡定的 {wanted}，現在是 {value}", file, 1))
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="front matter 齊全，文體與主題各一個、跟編輯卡一樣")]


def _is_original(entry: str, url: str | None, title: str | None) -> bool:
    if title is None:
        return False
    if url:
        urls = {normalize_url(found.rstrip(").,")) for found in _URL.findall(entry)}
        return normalize_url(url) in urls
    text = normalize_for_match(_URL.sub(" ", entry))
    return any(normalize_for_match(form) in text for form in _title_forms(title))


def check_reference_urls(
    article: str,
    *,
    file: str,
    original_url: str | None,
    original_title: str | None,
    known_urls: frozenset[str] | None,
) -> list[CheckEntry]:
    """參考來源一節要有；清單的每一筆有網址，而且網址是這張單真的有的（不能自己編來源）。

    原文是貼上的文字（沒有網址）時，原文那一筆不用網址。沒有原文、也沒用到外面的來源（照作者自己的經驗寫）時，
    這一節可以只有一句說明、沒有清單。`known_urls` 是正規化過的網址；None 就只查有沒有網址。
    """
    _, references, line = split_references(article)
    if references is None:
        return [_fail(f"文章結尾沒有「{REFERENCES_HEADING}」一節", file)]
    lines = [text for text in references.split("\n") if text.strip()]
    entries = _entries(references) if any(_ITEM.match(text) for text in lines) else []
    failures: list[CheckEntry] = []
    for entry in entries:
        urls = [found.rstrip(").,") for found in _URL.findall(entry)]
        if not urls:
            if original_url is None and _is_original(entry, None, original_title):
                continue
            failures.append(
                _fail(f"「{REFERENCES_HEADING}」的這一筆沒有網址：{entry.strip()[:60]}", file, line)
            )
            continue
        if known_urls is None:
            continue
        for url in urls:
            if normalize_url(url) not in known_urls:
                failures.append(
                    _fail(
                        f"「{REFERENCES_HEADING}」列的 {url} 不是這篇的來源（原文、分析抓過的來源、作者素材裡都沒有）："
                        "只列真的有的來源，不要自己編",
                        file,
                        line,
                    )
                )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="參考來源每一筆都有網址，而且都是這篇真的有的來源")]


def check_sentence_patterns(
    article: str, *, file: str, patterns: Sequence[SentencePattern]
) -> list[CheckEntry]:
    """角色資料列的句型，正文（參考來源與程式碼以外）出現的次數不能超過它寫的上限。"""
    if not patterns:
        return []
    body, _, _ = split_references(article)
    match = _FRONT.match(body)
    text = mask_code(body[match.end() :] if match else body)
    failures: list[CheckEntry] = []
    for rule in patterns:
        found = list(rule.pattern.finditer(text))
        if len(found) <= rule.max:
            continue
        failures.append(
            CheckEntry(
                check="C5",
                status="fail",
                reason=f"句型 {rule.name} 用了 {len(found)} 次，角色的規則最多 {rule.max} 次",
                location=Location(file=file),
                excerpt=found[rule.max].group(0)[:80],
            )
        )
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="角色列的句型都在次數以內")]


# 文章裡的圖：![圖說](路徑)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")


def figure_path(number: int) -> str:
    """第 N 張圖的圖位（跟 `cards.figure_path` 同一個寫法；配圖照這個檔名產圖）。"""
    return f"./images/fig-{number}.webp"


def check_figure_slots(article: str, *, file: str, slots: Sequence[int] | None) -> list[CheckEntry]:
    """圖文分鏡的每一張圖都有圖位（`![圖說](./images/fig-N.webp)`，圖說不能空）；沒有分鏡以外的圖。

    `slots` 是 None（分鏡不是結構化的，例如選「其他」自己寫）就不查。
    """
    if slots is None:
        return []
    body, _, _ = split_references(article)
    images = [(m.group(1).strip(), m.group(2)) for m in _IMAGE.finditer(mask_code(body))]
    wanted = {figure_path(n): n for n in slots}
    failures: list[CheckEntry] = []
    for path, number in wanted.items():
        placed = [alt for alt, target in images if target == path]
        if not placed:
            failures.append(_fail(f"分鏡的第 {number} 張圖沒有圖位：要有一行 ![圖說]({path})", file))
        elif len(placed) > 1:
            failures.append(_fail(f"第 {number} 張圖的圖位 {path} 放了 {len(placed)} 次，只放一次", file))
        elif not placed[0]:
            failures.append(_fail(f"第 {number} 張圖的圖位 {path} 沒有圖說：寫在 ![ ] 的方括號裡", file))
    for _, target in images:
        if target not in wanted:
            failures.append(_fail(f"文章裡的圖 {target} 不在圖文分鏡裡：只照分鏡留圖位，表格寫成 Markdown 表格", file))
    if failures:
        return failures
    return [CheckEntry(check="C5", status="pass", reason="圖文分鏡的每一張圖都有圖位")]


def article_checks(
    *,
    article: str,
    article_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
    original_url: str | None,
    original_title: str | None,
    original_author: str | None,
    texts: Mapping[str, str],
    thresholds: OverlapThresholds,
    skeleton: Sequence[str] = (),
    rules: ArticleRules | None = None,
) -> list[CheckEntry]:
    """重構段的產出：格式、原文出處（擋）與跟每一份文字的逐字重疊（只顯示）。

    `skeleton` 是目標文類骨架的每一項（`genre_skeleton`）；標題剛好等於其中一項就擋。

    `texts` 是要比重疊的文字：給人看的名字（「原文」「S2」）→ 正文，照給的順序各算一份。

    `original_title` 是 None：沒有原文（照創作者自己的想法或作者素材寫），原文出處與「不提原文」都不查。
    `rules` 是照編輯卡寫的文章多查的幾條（`ArticleRules`）；None 是舊的寫法（沒有編輯卡）。
    """
    body, _, _ = split_references(article)
    answers_original = rules is not None and rules.answers_original
    credit: list[CheckEntry] = []
    voice: list[CheckEntry] = []
    if original_title is not None:
        credit = check_references(
            article,
            file=article_file,
            original_url=original_url,
            original_title=original_title,
            original_author=original_author,
        )
        if not answers_original:
            # 從報告開始的文章用角色自己的聲音寫，不提原文；只有 essay 可以回應原文（ADR-0025 §3.4）
            voice = [
                *check_original_not_in_body(
                    article,
                    file=article_file,
                    original_title=original_title,
                    original_author=original_author,
                    original_url=original_url,
                ),
                *check_reply_phrases(article, file=article_file),
            ]
    card: list[CheckEntry] = []
    if rules is not None:
        card = [
            *check_front_matter(
                article, file=article_file, genres=rules.genres, themes=rules.themes,
                genre=rules.genre, theme=rules.theme,
            ),
            *check_reference_urls(
                article, file=article_file, original_url=original_url,
                original_title=original_title, known_urls=rules.known_urls,
            ),
            *check_sentence_patterns(article, file=article_file, patterns=rules.patterns),
            *check_figure_slots(article, file=article_file, slots=rules.figures),
        ]
    # 順序照舊（沒過的條目照這個順序寫進「回去修」的任務說明）：新的幾條接在後面
    return [
        *check_markdown(article, file=article_file),
        *credit,
        *check_skeleton_headings(article, file=article_file, skeleton=skeleton),
        *voice,
        *card,
        *check_schema(output, schema, file=output_file),
        *(
            entry
            for label, text in texts.items()
            for entry in check_overlap(
                body, text, thresholds, file=article_file, label=label
            )
        ),
    ]
