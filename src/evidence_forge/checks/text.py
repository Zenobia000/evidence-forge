"""各項檢查共用的文字處理：比對前的正規化、斷詞、位置換算、遮掉程式碼。"""

import re
import unicodedata
from dataclasses import dataclass

# Markdown 標記用到、但 Unicode 不算標點的符號：引言的 >、表格的 |、刪除線的 ~、
# 行內程式碼的 `、上標的 ^。原文（content.md）是 Markdown，比對時要當成不存在。
_MARKUP_SYMBOLS = frozenset("`~^|>")


def _ignorable(char: str) -> bool:
    category = unicodedata.category(char)
    # Z*：空白；P*：標點；Cc：換行、tab 等控制字元；Cf：零寬空白、BOM、軟連字號。
    return (
        char.isspace()
        or category[0] in ("Z", "P")
        or category in ("Cc", "Cf")
        or char in _MARKUP_SYMBOLS
    )


# 寫在數字前面當負號用的橫線。
_MINUS_SIGNS = frozenset("-‐‑‒–")


def _inside_number(text: str, index: int) -> bool:
    """這個標點是不是數字的一部分：3.5 的點、1,000 的逗號、2020-2021 的橫線、-5 的負號。"""
    if index + 1 >= len(text) or not text[index + 1].isdigit():
        return False
    return (index > 0 and text[index - 1].isdigit()) or text[index] in _MINUS_SIGNS


def normalize_for_match(text: str) -> str:
    """逐字比對前的正規化：全形半形統一（NFKC），拿掉所有空白、標點與 Markdown 標記符號。

    大小寫、數字、文字本身一律保留：換一個字、改一個數字、改大小寫都不算同一句。
    數字裡的小數點、千分位、負號也保留，不然 3.5 會跟 35、-5 會跟 5 對上。
    空白整個拿掉（不是縮成一個），因為中文原文的斷行、中英之間有沒有空白都不該影響比對。
    """
    chars = unicodedata.normalize("NFKC", text)
    return "".join(
        # 各種橫線當負號或範圍時一律寫成 -，2020–2021 與 2020-2021 算同一句
        "-" if char in _MINUS_SIGNS else char
        for index, char in enumerate(chars)
        if not _ignorable(char) or _inside_number(chars, index)
    )


# ---------------------------------------------------------------------------
# 位置與程式碼區塊
# ---------------------------------------------------------------------------

# CommonMark 的程式碼圍欄：行首最多 3 個空白，接 3 個以上的 ` 或 ~。
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# 行內程式碼：同樣長度的反引號包起來的一段（不跨行，夠用）。
# 開頭的反引號前面不能是反斜線：\` 是字面上的反引號，不開行內程式碼（CommonMark）。
# 寧可少遮（多檢查），不要把真的圖或引用編號當成程式碼藏起來。
_CODE_SPAN = re.compile(r"(?<![`\\])(`+)(?!`)[^\n]*?(?<!`)\1(?!`)")


@dataclass(frozen=True)
class Fence:
    """一個程式碼圍欄區塊：開頭與結尾在第幾行（從 1 起算）；沒關上時 `close_line` 是 None。"""

    open_line: int
    close_line: int | None


def find_fences(text: str) -> list[Fence]:
    """找出所有程式碼圍欄區塊。沒關上的那個一路算到檔尾（CommonMark 的規則）。"""
    fences: list[Fence] = []
    opener: tuple[int, str] | None = None
    for number, line in enumerate(text.split("\n"), start=1):
        match = _FENCE.match(line)
        if opener is None:
            # 反引號開頭的圍欄，後面的說明文字不能再有反引號（CommonMark）；
            # 「```js``` 是寫法」是一行行內程式碼，不是圍欄。
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end() :]):
                opener = (number, match.group(1))
            continue
        # 結尾圍欄：同一種字元、長度不短於開頭，後面只能有空白。
        open_number, marker = opener
        if (
            match
            and match.group(1)[0] == marker[0]
            and len(match.group(1)) >= len(marker)
            and not line[match.end() :].strip()
        ):
            fences.append(Fence(open_number, number))
            opener = None
    if opener is not None:
        fences.append(Fence(opener[0], None))
    return fences


def mask_code(text: str) -> str:
    """把程式碼區塊與行內程式碼換成空白，換行與長度不變，所以位置照樣對得上。

    程式碼裡的 `![](...)`、`[^S1]` 是在講語法，不是真的嵌圖或引用。
    """
    lines = text.split("\n")
    for fence in find_fences(text):
        last = fence.close_line if fence.close_line is not None else len(lines)
        for index in range(fence.open_line - 1, last):
            lines[index] = " " * len(lines[index])
    masked = "\n".join(lines)
    return _CODE_SPAN.sub(lambda match: " " * len(match.group(0)), masked)


# GFM 註腳定義：行首（最多 3 個空白）的 `[^編號]:`，到行尾
_FOOTNOTE_DEFINITION = re.compile(r"^ {0,3}\[\^[^\]\s]+\]:.*$", re.MULTILINE)


def mask_footnote_definitions(text: str) -> str:
    """把註腳定義行（`[^S1]: <來源標題>（網址）`）換成空白，換行與長度不變。

    註腳本來就該照抄來源的標題；C3 算照抄時把它算進去，標題一長就誤擋（#67）。
    """
    return _FOOTNOTE_DEFINITION.sub(lambda match: " " * len(match.group(0)), text)


def line_column(text: str, offset: int) -> tuple[int, int]:
    """字元位移換成（行, 欄），都從 1 起算；欄以字元計，一個中文字算一欄。"""
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, column


# ---------------------------------------------------------------------------
# 斷詞（C3 用）
# ---------------------------------------------------------------------------

# 中日韓文沒有空白分詞，一個字算一個詞：漢字（含擴充 A、相容）、平假名、片假名、韓文音節。
_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
_TOKEN = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+")


@dataclass(frozen=True)
class Token:
    """一個詞：正規化後的字（NFKC、不分大小寫）與它在原文的起訖字元位移。"""

    text: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """把文字切成詞。標點與空白不算詞；全形半形、大小寫都統一。

    位移以原文為準（先切再正規化），所以報出來的位置對得回稿子。
    """
    return [
        Token(unicodedata.normalize("NFKC", match.group(0)).casefold(), *match.span())
        for match in _TOKEN.finditer(text)
    ]
