"""重構段的程式檢查（PRD v3.0 §6 第 4 點、§7、ADR-0020 §3.4、ADR-0024 §3.3）：只看格式與出處，不判斷寫得好不好。

- 擋（C5）：`article.md` 是標準 Markdown；最後一節是「參考來源」，裡面有一筆同時列了原文的
  標題與網址（貼上的文字、上傳的檔沒有網址就只看標題），素材有作者時也要有作者；
  任何一層標題都不是文類骨架的字（結論、論據、反方、建議）；參考來源以外的內文
  不出現原文的標題與原作者名（文章用角色自己的聲音寫，不回應原文）；
  `article.json` 符合 `rewrite` 的 schema。
- 只顯示、不擋（C3）：文章正文跟原文、跟每一份來源的逐字重疊，各算一份（`overlap.py`）。
  參考來源那一節不算：它本來就照抄標題與網址。

全部是純函式：檔案由執行器讀好傳進來，不讀檔、不寫檔、不呼叫模型。
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

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
# 「原文」只在當成「那篇原文」用時才擋（原文說、原文提到、在原文裡）；「英文原文書」「照原文翻譯」不算
_REPLY_PHRASE = re.compile(
    r"原作者|這篇(?:文章)?談的是|原文(?:說|提到|認為|指出|中|裡|寫道|談到|作者)"
)


def check_reply_phrases(article: str, *, file: str) -> list[CheckEntry]:
    """參考來源以外的內文不能有回應原文的說法：「原作者」「這篇談的是」「原文提到」這類。每種說法報一次。"""
    body, _, _ = split_references(article)
    failures: list[CheckEntry] = []
    reported: set[str] = set()
    for number, line in enumerate(mask_code(body).split("\n"), start=1):
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


def article_checks(
    *,
    article: str,
    article_file: str,
    output: object,
    output_file: str,
    schema: Mapping[str, Any],
    original_url: str | None,
    original_title: str,
    original_author: str | None,
    texts: Mapping[str, str],
    thresholds: OverlapThresholds,
    skeleton: Sequence[str] = (),
) -> list[CheckEntry]:
    """重構段的產出：格式、原文出處（擋）與跟每一份文字的逐字重疊（只顯示）。

    `skeleton` 是目標文類骨架的每一項（`genre_skeleton`）；標題剛好等於其中一項就擋。

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
            original_author=original_author,
        ),
        *check_skeleton_headings(article, file=article_file, skeleton=skeleton),
        *check_original_not_in_body(
            article,
            file=article_file,
            original_title=original_title,
            original_author=original_author,
            original_url=original_url,
        ),
        *check_reply_phrases(article, file=article_file),
        *check_schema(output, schema, file=output_file),
        *(
            entry
            for label, text in texts.items()
            for entry in check_overlap(
                body, text, thresholds, file=article_file, label=label
            )
        ),
    ]
