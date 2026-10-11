"""C5 格式（PRD §7）：

- 段的 JSON 產出符合該段 skill 的 `schema.json`（JSON Schema；沒寫 `$schema` 時用 2020-12 版）；
- 稿子是標準 Markdown（CommonMark＋GFM 的表格、註腳、刪除線）。

「標準 Markdown」任何文字都能解析，所以這裡擋的是模型常犯、會讓稿子壞掉或變成別種格式的寫法：
空的稿子、整份包在程式碼區塊裡、程式碼區塊沒關（後面全被吃掉）、
HTML 文件（`<html>`、`<body>`、`<script>`…）、MDX（`import`／`export` 敘述、大寫開頭的元件標籤）。
開頭的 YAML frontmatter 允許（執行器可能在稿子上記版本資訊）。行內的小段 HTML 不擋，
`<img>` 由 C4 管。
"""

import re
from collections.abc import Mapping
from typing import Any, cast

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema import validators

from evidence_forge.checks.report import CheckEntry, Location
from evidence_forge.checks.text import find_fences, line_column, mask_code

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_NOT_MARKDOWN = (
    (
        re.compile(
            r"<!DOCTYPE\b|</?(?:html|head|body|script|style|iframe)\b", re.IGNORECASE
        ),
        "稿子裡有 HTML 文件或程式的標籤，不是標準 Markdown",
    ),
    (
        re.compile(
            r"^import\s+(?:['\"]|.+\s+from\s+['\"])"
            r"|^export\s+(?:default|const|let|var|function|async|class)\b",
            re.MULTILINE,
        ),
        "稿子裡有 MDX 的 import／export 敘述，不是標準 Markdown",
    ),
    (
        re.compile(r"</?[A-Z][\w.]*(?=[\s/>])"),
        "稿子裡有 MDX／JSX 元件標籤，不是標準 Markdown",
    ),
)


class SchemaError(ValueError):
    """skill 的 `schema.json` 本身寫錯了。這是設定錯誤，不是這次產出不合格。"""


def _pointer(path: Any) -> str:
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def check_schema(
    payload: object, schema: Mapping[str, Any], *, file: str
) -> list[CheckEntry]:
    """每個不符合 schema 的地方一條 fail（位置是 JSON pointer）；全部符合就一條 pass。

    schema 本身不合法時丟 `SchemaError`：那是 skill 寫錯，重試或升級模型都修不好。
    """
    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(dict(schema))
    except jsonschema_exceptions.SchemaError as exc:
        raise SchemaError(f"schema 不合法：{exc.message}") from exc
    errors = sorted(
        # payload 是 json.loads 讀進來的任意 JSON；jsonschema 的型別標註比實際能吃的窄。
        validator_class(dict(schema)).iter_errors(cast(Any, payload)),
        key=lambda error: (_pointer(error.absolute_path), error.message),
    )
    if not errors:
        return [CheckEntry(check="C5", status="pass", reason="產出符合 schema")]
    return [
        CheckEntry(
            check="C5",
            status="fail",
            reason=f"產出不符合 schema：{error.message}",
            location=Location(file=file, pointer=_pointer(error.absolute_path)),
        )
        for error in errors
    ]


def _fail(
    reason: str, file: str, line: int | None = None, column: int | None = None
) -> CheckEntry:
    location = Location(file=file, line=line, column=column)
    return CheckEntry(check="C5", status="fail", reason=reason, location=location)


def check_markdown(draft: str, *, file: str) -> list[CheckEntry]:
    """稿子不是標準 Markdown 的地方各一條 fail；沒問題就一條 pass。"""
    if not draft.strip():
        return [_fail("稿子是空的", file)]

    frontmatter = _FRONTMATTER.match(draft)
    body_first_line = draft.count("\n", 0, frontmatter.end()) + 1 if frontmatter else 1
    lines = draft.split("\n")
    content_lines = [
        number
        for number, line in enumerate(lines, start=1)
        if number >= body_first_line and line.strip()
    ]

    failures: list[CheckEntry] = []
    for fence in find_fences(draft):
        if fence.close_line is None:
            failures.append(
                _fail(
                    "程式碼區塊沒有關上，後面的內文都會變成程式碼",
                    file,
                    fence.open_line,
                    1,
                )
            )
        elif (fence.open_line, fence.close_line) == (
            content_lines[0],
            content_lines[-1],
        ):
            return [_fail("整份稿子包在程式碼區塊裡", file, fence.open_line, 1)]

    masked = mask_code(draft)
    flagged_lines: set[int] = set()
    for pattern, reason in _NOT_MARKDOWN:
        for match in pattern.finditer(masked):
            line, column = line_column(draft, match.start())
            if line in flagged_lines:
                continue
            flagged_lines.add(line)
            failures.append(_fail(reason, file, line, column))

    if failures:
        # 照在稿子裡出現的順序；每條 fail 都有行號
        return sorted(
            failures,
            key=lambda entry: (entry.location.line or 0) if entry.location else 0,
        )
    return [CheckEntry(check="C5", status="pass", reason="稿子是標準 Markdown")]
