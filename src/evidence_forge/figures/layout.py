"""圖文節奏檢查：量文章裡圖與圖之間隔了多少字，旗標程式量得出來的排版規則。

字數的算法：中日韓字一個字算一，英文一個詞算一；標題、圖說、參考資料不算。
手機上一個畫面大約 450 字。程式只量位置與距離，判斷不了這張圖該不該存在，
那是大綱階段「圖文分鏡」的工作。

規則（門檻是創作者依自己前三十篇文章的量測訂的，不是學術數字）：

- L1 第一張圖要早：前 1000 字內。
- L2 不要長段無圖：圖或表之間最多 1300 字。
- L3 圖不要擠在一起：兩張圖之間至少 250 字。
- L4 圖不直接放在小標下：小標和圖之間至少一段。
- L5 圖不當一節的最後一個元素：圖後至少一段。
- L6 每張圖有替代文字與一行 `*圖說*`。

確實需要例外時用 `allow={"L3@143"}`（規則@行號）豁免那一處，理由另外記在文章的 brief。
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# 門檻，單位是正文字數
FIRST_MAX = 1000
GAP_MAX = 1300
CLUSTER_MIN = 250
# 到這幾個小節就停止計算
END_SECTIONS = ("參考資料", "延伸閱讀", "References")

LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.'%-]*")
CJK = re.compile(r"[㐀-鿿豈-﫿]")


@dataclass(frozen=True)
class Block:
    kind: str  # heading、figure、table、text
    text: str
    line: int


@dataclass(frozen=True)
class Problem:
    rule: str  # L1～L6
    line: int
    message: str

    @property
    def key(self) -> str:
        """豁免用的鍵，例：`L3@143`。"""
        return f"{self.rule}@{self.line}"

    def __str__(self) -> str:
        return f"{self.key} {self.message}"


@dataclass(frozen=True)
class LayoutStats:
    chars: int
    figures: int
    tables: int
    first_figure_at: int | None
    longest_gap: int
    chars_per_figure: int | None


@dataclass(frozen=True)
class LayoutReport:
    problems: list[Problem]
    stats: LayoutStats

    @property
    def ok(self) -> bool:
        return not self.problems


def count(text: str) -> int:
    """正文字數：連結只留連結文字，不算網址。"""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return len(CJK.findall(text)) + len(LATIN_WORD.findall(text))


CAPTION = re.compile(r"^\*[^*].*\*$")


def _line_kind(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("#"):
        return "heading"
    if stripped.startswith("!["):
        return "figure"
    if stripped.startswith("|"):
        return "table"
    return "text"


def blocks(md: str) -> list[Block]:
    """把 Markdown 依「行」的結構切成標題、圖、表、文字區塊，記錄起始行號（front matter 也算進行號）。

    圖片行自己就是一個區塊，前後不必空行；緊接在它後面（中間可隔空行）的 `*圖說*` 那一行算它的圖說。
    標題也是一行一塊。連續的文字行、連續的表格行各自併成一塊，空行隔開文字。
    """
    body = re.sub(r"^---\n[\s\S]*?\n---\n", "", md, count=1)
    offset = md[: len(md) - len(body)].count("\n")
    lines = body.splitlines()
    out: list[Block] = []
    pending: list[str] = []  # 正在累積的文字或表格行
    pending_kind = ""
    pending_line = 0

    def flush() -> None:
        nonlocal pending, pending_kind
        if pending:
            out.append(Block(pending_kind, "\n".join(pending), pending_line))
        pending, pending_kind = [], ""

    i = 0
    while i < len(lines):
        line, number = lines[i], offset + i + 1
        kind = _line_kind(line)
        if kind == "blank":
            flush()
        elif kind == "heading":
            flush()
            if line.strip().lstrip("#").strip() in END_SECTIONS:
                break
            out.append(Block("heading", line.strip(), number))
        elif kind == "figure":
            flush()
            text = line.strip()
            nxt = i + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines) and CAPTION.match(lines[nxt].strip()):
                text += "\n" + lines[nxt].strip()
                i = nxt
            out.append(Block("figure", text, number))
        else:  # text 或 table
            if pending_kind != kind:
                flush()
                pending_kind, pending_line = kind, number
            pending.append(line.strip())
        i += 1
    flush()
    return out


def check_text(md: str, allow: Collection[str] = frozenset()) -> LayoutReport:
    """檢查一篇文章的 Markdown 文字；`allow` 是要豁免的 `規則@行號`。"""
    bl = blocks(md)
    problems: list[Problem] = []
    figures = [i for i, b in enumerate(bl) if b.kind == "figure"]
    visuals = [i for i, b in enumerate(bl) if b.kind in ("figure", "table")]
    total = sum(count(b.text) for b in bl if b.kind == "text")

    def chars_between(a: int, b: int) -> int:
        return sum(count(x.text) for x in bl[a + 1 : b] if x.kind == "text")

    first = chars_between(-1, figures[0]) if figures else total
    if figures and first > FIRST_MAX:
        problems.append(Problem(
            "L1", bl[figures[0]].line,
            f"first figure arrives after {first} chars (max {FIRST_MAX}); "
            "open with the article's core visual sooner",
        ))

    gaps: list[int] = []
    marks = [-1, *visuals, len(bl)]
    for a, b in zip(marks, marks[1:], strict=False):
        gap = chars_between(a, b)
        gaps.append(gap)
        if gap > GAP_MAX:
            where = bl[a + 1].line if a + 1 < len(bl) else bl[-1].line
            problems.append(Problem(
                "L2", where, f"{gap} chars with no figure or table from line {where} (max {GAP_MAX})"
            ))

    for a, b in zip(figures, figures[1:], strict=False):
        between = chars_between(a, b)
        if between < CLUSTER_MIN:
            problems.append(Problem(
                "L3", bl[b].line,
                f"figures at lines {bl[a].line} and {bl[b].line} are {between} chars apart "
                f"(min {CLUSTER_MIN}); merge into one multi-panel figure or move one to where its claim is",
            ))

    for i in figures:
        fig = bl[i]
        prev = bl[i - 1] if i > 0 else None
        nxt = bl[i + 1] if i + 1 < len(bl) else None
        if prev is None or prev.kind == "heading":
            problems.append(Problem(
                "L4", fig.line,
                f"figure at line {fig.line} sits right under a heading; say what to look at first, then show it",
            ))
        if nxt is None or nxt.kind == "heading":
            problems.append(Problem(
                "L5", fig.line,
                f"figure at line {fig.line} ends its section; follow it with the paragraph that reads it",
            ))
        alt = re.match(r"!\[([^\]]*)\]", fig.text)
        if not alt or not alt.group(1).strip():
            problems.append(Problem("L6", fig.line, f"figure at line {fig.line} has no alt text"))
        if not any(CAPTION.match(x) for x in fig.text.splitlines()[1:]):
            problems.append(Problem("L6", fig.line, f"figure at line {fig.line} has no *caption* line"))

    problems = [p for p in problems if p.key not in allow]
    stats = LayoutStats(
        chars=total,
        figures=len(figures),
        tables=len(visuals) - len(figures),
        first_figure_at=first if figures else None,
        longest_gap=max(gaps) if gaps else total,
        chars_per_figure=round(total / len(figures)) if figures else None,
    )
    return LayoutReport(problems, stats)


def check_file(path: Path, allow: Collection[str] = frozenset()) -> LayoutReport:
    return check_text(path.read_text(encoding="utf-8"), allow)
