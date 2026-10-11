"""C5 格式：產出符合該段 skill 的 `schema.json`；稿子是標準 Markdown（PRD §7）。"""

from typing import Any

import pytest

from evidence_forge.checks import Location, SchemaError, check_markdown, check_schema

pytestmark = pytest.mark.promise("FR-VF-01")

CLAIMS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["genre", "claims"],
    "properties": {
        "genre": {"type": "string"},
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "quote"],
                "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
            },
        },
    },
}

FILE = "drafts/draft.v1.md"


def test_output_matching_the_schema_passes() -> None:
    payload = {"genre": "opinion", "claims": [{"id": "c1", "quote": "原文的一句"}]}
    entries = check_schema(payload, CLAIMS_SCHEMA, file="claims.json")
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].check == "C5"


def test_each_schema_violation_fails_at_its_pointer() -> None:
    payload = {"genre": "opinion", "claims": [{"id": "c1"}, {"id": 2, "quote": "q"}]}
    entries = check_schema(payload, CLAIMS_SCHEMA, file="claims.json")
    assert {entry.status for entry in entries} == {"fail"}
    pointers = [entry.location.pointer for entry in entries if entry.location]
    assert sorted(pointer or "" for pointer in pointers) == [
        "/claims/0",
        "/claims/1/id",
    ]


def test_missing_top_level_field_points_at_the_root() -> None:
    entries = check_schema({"claims": []}, CLAIMS_SCHEMA, file="claims.json")
    pointers = {entry.location.pointer for entry in entries if entry.location}
    assert "" in pointers
    assert {entry.status for entry in entries} == {"fail"}


def test_broken_schema_is_a_setup_error_not_a_failed_output() -> None:
    # schema 壞了是 skill 寫錯，不是這次產出不合格；不能讓執行器拿它去重試或升級
    with pytest.raises(SchemaError):
        check_schema({}, {"type": "no-such-type"}, file="claims.json")


def test_plain_markdown_draft_passes() -> None:
    draft = (
        "---\nversion: 1\n---\n# 標題\n\n一段文字[^S1]。\n\n"
        "```python\nprint('hi')\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n[^S1]: 來源\n"
    )
    entries = check_markdown(draft, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].check == "C5"


@pytest.mark.parametrize(
    "draft",
    [
        "",
        "  \n\n",
        "```markdown\n# 標題\n\n內文\n```\n",
        "~~~md\n# 標題\n~~~",
    ],
)
def test_empty_or_fully_fenced_draft_fails(draft: str) -> None:
    entries = check_markdown(draft, file=FILE)
    assert [entry.status for entry in entries] == ["fail"]


def test_unclosed_code_fence_fails_at_its_opening_line() -> None:
    draft = "# 標題\n\n```python\nprint('hi')\n\n後面的內文全被吃進程式碼\n"
    entries = check_markdown(draft, file=FILE)
    assert [(entry.status, entry.location) for entry in entries] == [
        ("fail", Location(file=FILE, line=3, column=1))
    ]


@pytest.mark.parametrize(
    ("draft", "line"),
    [
        ("<!DOCTYPE html>\n<html><body><p>內文</p></body></html>\n", 1),
        ("# 標題\n\n<script>alert(1)</script>\n", 3),
        ("import Chart from './Chart'\n\n# 標題\n", 1),
        ('# 標題\n\n<Callout type="info">提醒</Callout>\n', 3),
        ("# 標題\n\nexport const meta = {}\n", 3),
    ],
)
def test_html_documents_and_mdx_are_not_standard_markdown(
    draft: str, line: int
) -> None:
    entries = check_markdown(draft, file=FILE)
    assert {entry.status for entry in entries} == {"fail"}
    assert entries[0].location is not None
    assert entries[0].location.line == line


def test_mdx_looking_text_inside_code_is_fine() -> None:
    draft = "# 標題\n\n```jsx\nimport Chart from './Chart'\n<Chart />\n```\n"
    entries = check_markdown(draft, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]


def test_ordinary_sentence_starting_with_import_is_fine() -> None:
    draft = "# 關稅\n\nimport duties rose again this quarter.\n"
    entries = check_markdown(draft, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]
