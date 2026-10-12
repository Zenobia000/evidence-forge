"""執行器在段與段之間呼叫的兩個入口：整理段後 C1、C5；二創段後 C2～C5（PRD §7、SEAM-06）。"""

from typing import Any

import pytest

from evidence_forge.checks import OverlapThresholds, compose_checks, digest_checks

pytestmark = pytest.mark.promise("FR-VF-01")

SOURCE = """# 推論變便宜了

大型語言模型的推論成本，在過去一年降了九成。
但是，真正貴的是工程師的時間。
"""

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {"type": "object", "required": ["quote"]},
        }
    },
}

COMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["facts"],
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "required": ["fact"]}}
    },
}

THRESHOLDS = OverlapThresholds(
    ngram_size=5, max_ngram_overlap=0.3, max_common_tokens=12
)


def _statuses(entries: list[Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for entry in entries:
        result.setdefault(entry.check, set()).add(entry.status)
    return result


def test_digest_with_a_fabricated_quote_fails_c1_only() -> None:
    claims = {
        "claims": [
            {"quote": "推論成本，在過去一年降了九成"},
            {"quote": "工程師的時間一點都不貴"},
        ]
    }
    entries = digest_checks(
        claims=claims,
        claims_file="claims.json",
        source_text=SOURCE,
        schema=DIGEST_SCHEMA,
    )
    assert _statuses(entries) == {"C1": {"fail"}, "C5": {"pass"}}


def test_digest_that_breaks_the_schema_fails_c5() -> None:
    entries = digest_checks(
        claims={"claims": "沒有"},
        claims_file="claims.json",
        source_text=SOURCE,
        schema=DIGEST_SCHEMA,
    )
    assert _statuses(entries)["C5"] == {"fail"}


def test_clean_draft_passes_c2_to_c5() -> None:
    draft = "# 錢花在哪\n\n模型的帳單一年縮成一成[^S1]，人的時間卻沒變便宜。\n\n[^S1]: 原文\n"
    output = {"facts": [{"fact": "模型的帳單一年縮成一成[^S1]"}]}
    entries = compose_checks(
        draft=draft,
        draft_file="drafts/draft.v1.md",
        output=output,
        output_file="facts.json",
        schema=COMPOSE_SCHEMA,
        source_text=SOURCE,
        source_ids={"S1"},
        asset_urls=frozenset(),
        thresholds=THRESHOLDS,
    )
    assert _statuses(entries) == {
        "C2": {"pass"},
        "C3": {"pass"},
        "C4": {"pass"},
        "C5": {"pass"},
    }
    assert [entry.check for entry in entries] == ["C2", "C3", "C4", "C5", "C5"]


def test_draft_problems_show_up_under_the_check_that_caught_them() -> None:
    draft = (
        "# 錢花在哪\n\n![原文的圖](https://example.com/a.png)\n\n"
        "大型語言模型的推論成本，在過去一年降了九成[^S7]。\n"
    )
    output = {"facts": [{"fact": "大型語言模型的推論成本，在過去一年降了九成[^S7]"}]}
    entries = compose_checks(
        draft=draft,
        draft_file="drafts/draft.v1.md",
        output=output,
        output_file="facts.json",
        schema=COMPOSE_SCHEMA,
        source_text=SOURCE,
        source_ids={"S1"},
        asset_urls=frozenset(),
        thresholds=THRESHOLDS,
    )
    assert _statuses(entries) == {
        "C2": {"fail"},
        "C3": {"fail"},
        "C4": {"fail"},
        "C5": {"pass"},
    }


MULTI_SOURCE_DRAFT = (
    "# 錢花在哪\n\n模型的帳單一年縮成一成[^S1]，人的時間卻沒變便宜。\n\n[^S1]: 原文\n"
)


@pytest.mark.parametrize(
    ("source_ids", "c2"),
    [({"S1"}, "pass"), ({"S1", "S2"}, "fail")],
    ids=["只有種子", "補料放了S2"],
)
def test_compose_needs_two_cited_sources_only_when_research_placed_some(
    source_ids: set[str], c2: str
) -> None:
    # 只有種子一個來源的配方不受影響；補料段放了 S2 以後，正文只引 S1 就是沒有綜合
    entries = compose_checks(
        draft=MULTI_SOURCE_DRAFT,
        draft_file="drafts/draft.v1.md",
        output={"facts": [{"fact": "模型的帳單一年縮成一成[^S1]"}]},
        output_file="facts.json",
        schema=COMPOSE_SCHEMA,
        source_text=SOURCE,
        source_ids=source_ids,
        asset_urls=frozenset(),
        thresholds=THRESHOLDS,
    )
    assert _statuses(entries)["C2"] == {c2}
