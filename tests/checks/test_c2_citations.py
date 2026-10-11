"""C2 引用完整：稿子的引用編號都對得到來源；二創產出列的事實句都附編號（PRD §7）。

引用編號寫成 GFM 註腳的樣子：`[^<來源 id>]`。
"""

import pytest

from evidence_forge.checks import LocatedText, Location, check_citations

pytestmark = pytest.mark.promise("FR-VF-01")

SOURCES = {"S1", "S2"}

DRAFT = """# 推論變便宜了，工程師沒有

推論成本一年降了九成[^S1]。
Most teams still spend more on people than on tokens[^S2].

[^S1]: 原文，第 2 段
[^S2]: 補充來源
"""


def _fact(text: str, index: int) -> LocatedText:
    return LocatedText(
        text=text,
        location=Location(file="compose.json", pointer=f"/facts/{index}/fact"),
    )


def test_every_citation_maps_to_a_source_and_every_fact_is_cited_passes() -> None:
    facts = [
        _fact("推論成本一年降了九成[^S1]。", 0),
        _fact("Most teams still spend more on people than on tokens[^S2].", 1),
    ]
    entries = check_citations(DRAFT, SOURCES, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].check == "C2"


def test_citation_to_an_unknown_source_fails_at_its_line_and_column() -> None:
    draft = "第一行\n據說市場翻倍[^S9]。\n"
    entries = check_citations(draft, SOURCES, [], file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["fail"]
    assert entries[0].location == Location(file="drafts/draft.v1.md", line=2, column=7)
    assert entries[0].excerpt == "[^S9]"


def test_fact_without_a_citation_fails_at_its_pointer() -> None:
    draft = "推論成本一年降了九成。\n"
    facts = [_fact("推論成本一年降了九成。", 3)]
    entries = check_citations(draft, SOURCES, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["fail"]
    assert entries[0].location == Location(file="compose.json", pointer="/facts/3/fact")


def test_fact_whose_citation_is_not_in_the_draft_fails() -> None:
    # 事實句清單說有編號，但稿子裡那句其實沒附：以稿子為準
    draft = "推論成本一年降了九成。\n"
    facts = [_fact("推論成本一年降了九成[^S1]。", 0)]
    entries = check_citations(draft, SOURCES, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["fail"]


def test_fact_matching_ignores_whitespace_and_punctuation() -> None:
    facts = [_fact("推論成本 一年降了九成 [^S1]", 0)]
    entries = check_citations(DRAFT, SOURCES, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["pass"]


def test_markers_inside_code_are_not_citations() -> None:
    draft = "註腳寫成 `[^S9]`。\n\n```\n[^S8]\n```\n"
    entries = check_citations(draft, SOURCES, [], file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["pass"]


def test_each_bad_citation_is_listed_separately() -> None:
    draft = "甲[^X1]，乙[^X2]。\n"
    entries = check_citations(draft, SOURCES, [], file="drafts/draft.v1.md")
    assert [entry.excerpt for entry in entries] == ["[^X1]", "[^X2]"]
    assert all(entry.status == "fail" for entry in entries)


def test_fact_whose_marker_was_dropped_in_the_draft_fails() -> None:
    # 稿子只剩 S1 兩個字、沒有 [^S1]：不能因為標點被忽略就當成有附編號
    draft = "成本降了九成 S1 晶片很強。\n"
    facts = [_fact("成本降了九成[^S1]", 0)]
    entries = check_citations(draft, SOURCES, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["fail"]


def test_fact_citing_a_different_source_than_the_draft_fails() -> None:
    # 稿子引的是 S12，事實句說是 S1：S1 不能當成 S12 的前半段對上
    draft = "成本降了九成[^S12]。\n"
    facts = [_fact("成本降了九成[^S1]", 0)]
    entries = check_citations(draft, {"S1", "S12"}, facts, file="drafts/draft.v1.md")
    assert [entry.status for entry in entries] == ["fail"]
