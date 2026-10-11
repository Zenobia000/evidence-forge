"""C4 素材使用，S0 最小版（PRD §7、ADR-0016）：稿子裡嵌入的每張圖，網址都要在工單的
`assets/assets.json` 裡，不在就 fail 並列出位置。

S0 還沒有素材下載，`assets.json` 是空的（或不存在），等於 S0 的稿子不能嵌入任何圖，
避免二創把原文的圖直接抄進稿子。完整的 C4（授權、`quote` 的出處與附近段落、
`reference` 不能出現）在 S3。

認得的嵌圖寫法：`![alt](網址)`、`![alt][參照]`／`![參照]` 配 `[參照]: 網址`、HTML 的
`<img src=…>`。程式碼區塊與行內程式碼裡的不算。
"""

import json
import re
from collections.abc import Collection
from pathlib import Path

from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import line_column, mask_code

# 網址可以用 <> 包起來；後面可以接 "標題"、'標題' 或 (標題)。
_DESTINATION = r"(<[^>\n]*>|[^\s)]+)"
_TITLE = r"""(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\)))?"""
_INLINE_IMAGE = re.compile(rf"!\[[^\]\n]*\]\(\s*{_DESTINATION}{_TITLE}\s*\)")
# ![alt][label]、![label][]、![label]（後面不能緊接 [ 或 (，那是另外兩種寫法）
_REFERENCE_IMAGE = re.compile(r"!\[([^\]\n]*)\](?:\[([^\]\n]*)\]|(?![\[(]))")
_REFERENCE_DEFINITION = re.compile(
    r"^ {0,3}\[([^\]\n]+)\]:\s*(<[^>\n]*>|\S+)", re.MULTILINE
)
_HTML_IMAGE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE
)


def _unwrap(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        return destination[1:-1]
    return destination


def _label_key(label: str) -> str:
    # CommonMark：參照標籤不分大小寫、連續空白視為一個。
    return " ".join(label.split()).casefold()


def find_images(draft: str) -> list[tuple[int, str]]:
    """稿子裡所有嵌入的圖：（在原文的字元位移, 網址），照出現順序。"""
    text = mask_code(draft)
    images: list[tuple[int, str]] = []
    for match in _INLINE_IMAGE.finditer(text):
        images.append((match.start(), _unwrap(match.group(1))))
    definitions = {
        _label_key(match.group(1)): _unwrap(match.group(2))
        for match in _REFERENCE_DEFINITION.finditer(text)
    }
    for match in _REFERENCE_IMAGE.finditer(text):
        label = match.group(2) or match.group(1)
        url = definitions.get(_label_key(label))
        # 對不到定義的 ![x][y] 在 Markdown 裡只是普通文字，不是圖。
        if url is not None:
            images.append((match.start(), url))
    for match in _HTML_IMAGE.finditer(text):
        url = next(group for group in match.groups() if group is not None)
        images.append((match.start(), url))
    return sorted(images)


def check_images(
    draft: str, asset_urls: Collection[str], *, file: str
) -> list[CheckEntry]:
    """每張網址不在素材清單裡的圖一條 fail；都在（或沒有圖）就一條 pass。"""
    failures: list[CheckEntry] = []
    images = find_images(draft)
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
