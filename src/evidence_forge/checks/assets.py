"""C4 素材使用，S0 最小版（PRD §7、ADR-0016）：稿子裡嵌入的每張圖，網址都要在工單的
`assets/assets.json` 裡，不在就 fail 並列出位置。

S0 還沒有素材下載，`assets.json` 是空的（或不存在），等於 S0 的稿子不能嵌入任何圖，
避免二創把原文的圖直接抄進稿子。完整的 C4（授權、`quote` 的出處與附近段落、
`reference` 不能出現）在 S3。

認得的嵌圖寫法：`![alt](網址)`、`![alt][參照]`／`![參照]` 配 `[參照]: 網址`（定義寫在引言區塊裡也算）、
HTML 的 `<img>`、`<image>`（含 SVG 裡的）、`<source>`、`<input>` 上的 `src`、`srcset`、`href`。
程式碼區塊與行內程式碼裡的不算。以 `![` 開頭卻認不出網址的，一律 fail（認不出就擋）。HTML 這邊寧可多抓：S0 的素材清單是空的，多抓只是多擋一張圖。
"""

import json
import re
from collections.abc import Collection
from pathlib import Path

from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import line_column, mask_code

# 說明文字（alt）可以換行，也可以有兩層成對的中括號：![圖 [1 [a]] 示意](…)
_ALT = r"(?:[^\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*"
# 網址可以用 <> 包起來，也可以有一層成對的括號：…/Foo_(bar).png
_DESTINATION = r"(<[^>\n]*>|(?:[^\s()]|\([^\s()]*\))+)"
# 網址後面可以接 "標題"、'標題' 或 (標題)。
_TITLE = r"""(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\)))?"""
_INLINE_IMAGE = re.compile(rf"!\[{_ALT}\]\(\s*{_DESTINATION}{_TITLE}\s*\)")
# ![alt][label]、![label][]、![label]（後面不能緊接 [ 或 (，那是另外兩種寫法）
_REFERENCE_IMAGE = re.compile(rf"!\[({_ALT})\](?:\[([^\]\n]*)\]|(?![\[(]))")
# 定義前面可以有引言區塊的 >（一層或多層）
_REFERENCE_DEFINITION = re.compile(
    r"^(?: {0,3}> ?)* {0,3}\[([^\]\n]+)\]:\s*(<[^>\n]*>|\S+)", re.MULTILINE
)
# 會把圖顯示出來的 HTML 標籤（<image> 瀏覽器當成 <img>；SVG 的 <image> 用 href）
_HTML_IMAGE_TAG = re.compile(r"<(?:img|image|source|input)\b[^>]*>", re.IGNORECASE)
# 屬性名稱前面不能是字母或 -：data-src 之類的屬性瀏覽器不拿來顯示圖。
# xlink:href 的 href 前面是冒號，照樣抓得到。
_HTML_IMAGE_ATTRIBUTE = re.compile(
    r"""(?<![\w-])(src|srcset|href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)


def _unwrap(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        return destination[1:-1]
    return destination


def _label_key(label: str) -> str:
    # CommonMark：參照標籤不分大小寫、連續空白視為一個。
    return " ".join(label.split()).casefold()


# 每個 ![ 都可能是一張圖的開頭
_IMAGE_OPENER = re.compile(r"!\[")


def find_images(draft: str) -> list[tuple[int, str]]:
    """稿子裡所有嵌入的圖：（在原文的字元位移, 網址），照出現順序。"""
    return _scan(draft)[0]


def _scan(draft: str) -> tuple[list[tuple[int, str]], list[int]]:
    """（認得出網址的圖, 以 `![` 開頭卻解析不出網址的位移）。程式碼裡的都不算。"""
    text = mask_code(draft)
    images: list[tuple[int, str]] = []
    for match in _INLINE_IMAGE.finditer(text):
        images.append((match.start(), _unwrap(match.group(1))))
    # 同一個參照定義兩次時第一個生效（CommonMark），所以用 setdefault。
    definitions: dict[str, str] = {}
    for match in _REFERENCE_DEFINITION.finditer(text):
        definitions.setdefault(_label_key(match.group(1)), _unwrap(match.group(2)))
    for match in _REFERENCE_IMAGE.finditer(text):
        label = match.group(2) or match.group(1)
        url = definitions.get(_label_key(label))
        # 對不到定義的不放進 images；它的 ![ 會落到下面「認不出」那一份
        if url is not None:
            images.append((match.start(), url))
    # 認不出就擋：CommonMark 的寫法比這裡的規則多（例如網址裡兩層括號、清單裡的參照定義），
    # 解析不出網址的 ![ 不能當成「沒有圖」而放行。
    parsed = {offset for offset, _ in images}
    unparsed = [
        match.start()
        for match in _IMAGE_OPENER.finditer(text)
        if match.start() not in parsed
    ]
    for tag in _HTML_IMAGE_TAG.finditer(text):
        for attribute in _HTML_IMAGE_ATTRIBUTE.finditer(tag.group(0)):
            value = next(g for g in attribute.groups()[1:] if g is not None)
            if attribute.group(1).lower() == "srcset":
                # srcset 是「網址 描述」用逗號隔開的一串候選，每一張都可能被顯示
                urls = [c.split()[0] for c in value.split(",") if c.strip()]
            else:
                urls = [value]
            images.extend((tag.start(), url) for url in urls)
    return sorted(images), unparsed


def check_images(
    draft: str, asset_urls: Collection[str], *, file: str
) -> list[CheckEntry]:
    """每張網址不在素材清單裡、或解析不出網址的圖一條 fail；都在（或沒有圖）就一條 pass。"""
    failures: list[CheckEntry] = []
    images, unparsed = _scan(draft)
    for offset in unparsed:
        line, column = line_column(draft, offset)
        end = draft.find("\n", offset)
        failures.append(
            CheckEntry(
                check="C4",
                status="fail",
                reason="這裡以 ![ 開頭像是嵌圖，但解析不出網址，沒辦法對素材清單；改成 ![說明](網址) 的寫法",
                location=Location(file=file, line=line, column=column),
                excerpt=draft[offset : end if end != -1 else len(draft)][:120],
            )
        )
    for offset, url in images:
        if url in asset_urls:
            continue
        line, column = line_column(draft, offset)
        failures.append(
            CheckEntry(
                check="C4",
                status="fail",
                reason="稿子嵌入的圖不在這張工單的素材清單（assets.json）裡",
                location=Location(file=file, line=line, column=column),
                excerpt=url,
            )
        )
    if failures:
        return failures
    reason = f"{len(images)} 張圖都在素材清單裡" if images else "稿子沒有嵌入圖"
    return [CheckEntry(check="C4", status="pass", reason=reason)]


def load_asset_urls(path: Path) -> frozenset[str]:
    """讀工單的 `assets/assets.json`，回傳裡面所有素材的網址。

    檔案不存在等於沒有素材（S0 的常態）。格式是素材的陣列，每件至少有 `url`；
    格式不對直接丟 `ValueError`，不當成空清單——壞掉的清單不能變成「全部放行」或「全部擋」而沒人知道。
    """
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"素材清單 {path} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, list) or not all(
        isinstance(asset, dict) and isinstance(asset.get("url"), str) for asset in data
    ):
        raise ValueError(f"素材清單 {path} 要是素材的陣列，每件都有字串的 url")
    return frozenset(asset["url"] for asset in data)
