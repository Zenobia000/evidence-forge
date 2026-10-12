"""C3 不照抄：稿子跟原文的逐字重疊（最長共同片段、n-gram 重疊率）不能超過門檻（PRD §7）。

門檻 Q-28 還沒定（測試藍圖 G-02），這裡的測試都自己給門檻，不依賴預設值。
詞的算法：中日韓文一個字算一個詞，其他文字一串字母數字算一個詞；標點與大小寫不算。
"""

import pytest

from evidence_forge.checks import Location, OverlapThresholds, check_overlap

pytestmark = pytest.mark.promise("FR-VF-01")

FILE = "drafts/draft.v1.md"
STRICT = OverlapThresholds(ngram_size=5, max_ngram_overlap=0.5, max_common_tokens=12)

SOURCE_EN = (
    "The real cost of running agents is not the tokens. It is the hours engineers "
    "spend reading transcripts to find out why the agent picked the wrong file and "
    "then rewrote half of the module without asking anyone first."
)

SOURCE_ZH = "大型語言模型的推論成本在過去一年降了九成，但真正貴的始終是工程師花在讀紀錄上的時間。"


def test_rewrite_in_own_words_passes() -> None:
    draft = (
        "# 錢花在哪\n\nTokens got cheap. What still hurts is people: someone has to "
        "read every agent log when it edits the wrong thing.\n"
    )
    entries = check_overlap(draft, SOURCE_EN, STRICT, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].check == "C3"


def test_copied_english_sentence_fails_and_shows_where() -> None:
    copied = (
        "It is the hours engineers spend reading transcripts to find out why the agent "
        "picked the wrong file"
    )
    draft = f"# 觀點\n\nMy take: {copied}, which nobody budgets for.\n"
    entries = check_overlap(draft, SOURCE_EN, STRICT, file=FILE)
    spans = [entry for entry in entries if entry.location is not None]
    assert {entry.status for entry in entries} == {"fail"}
    assert [entry.excerpt for entry in spans] == [copied]
    assert spans[0].location == Location(file=FILE, line=3, column=10)


def test_copied_chinese_passage_fails() -> None:
    copied = "但真正貴的始終是工程師花在讀紀錄上的時間"
    draft = f"我的看法是：{copied}。所以要先把紀錄整理好。\n"
    entries = check_overlap(draft, SOURCE_ZH, STRICT, file=FILE)
    spans = [entry for entry in entries if entry.location is not None]
    assert [entry.excerpt for entry in spans] == [copied]
    assert all(entry.status == "fail" for entry in entries)


def test_changing_case_punctuation_or_width_does_not_hide_copying() -> None:
    draft = (
        "THE HOURS ENGINEERS SPEND—reading transcripts—to find out WHY the agent "
        "picked the ｗｒｏｎｇ file\n"
    )
    entries = check_overlap(draft, SOURCE_EN, STRICT, file=FILE)
    assert {entry.status for entry in entries} == {"fail"}


def test_many_short_copied_phrases_fail_on_the_overlap_rate() -> None:
    # 每段只抄 6 個詞，沒有一段超過 12 個詞；但 5-gram 有 8／32＝25% 在原文裡
    draft = (
        "the real cost of running agents alpha beta gamma "
        "engineers spend reading transcripts to find delta epsilon zeta "
        "the agent picked the wrong file eta theta iota "
        "half of the module without asking kappa lambda mu\n"
    )
    loose_span = OverlapThresholds(
        ngram_size=5, max_ngram_overlap=0.2, max_common_tokens=12
    )
    entries = check_overlap(draft, SOURCE_EN, loose_span, file=FILE)
    spans = [entry for entry in entries if entry.location is not None]
    assert {entry.status for entry in entries} == {"fail"}
    assert len(spans) == 4


def test_same_phrases_pass_when_the_rate_limit_is_higher() -> None:
    draft = (
        "the real cost of running agents alpha beta gamma "
        "engineers spend reading transcripts to find delta epsilon zeta\n"
    )
    loose = OverlapThresholds(ngram_size=5, max_ngram_overlap=0.5, max_common_tokens=12)
    entries = check_overlap(draft, SOURCE_EN, loose, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]


def test_zero_overlap_limit_rejects_any_shared_ngram() -> None:
    # UAT TC-CHECK-03 把上限設成 0，讓任何一段相同的 n-gram 都擋下
    zero = OverlapThresholds(ngram_size=5, max_ngram_overlap=0.0, max_common_tokens=100)
    draft = "我想說的是 the real cost of running 這件事，以及其他一百件事。\n"
    entries = check_overlap(draft, SOURCE_EN, zero, file=FILE)
    assert {entry.status for entry in entries} == {"fail"}


def test_draft_shorter_than_one_ngram_passes() -> None:
    entries = check_overlap("短。\n", SOURCE_ZH, STRICT, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]


def test_result_says_the_thresholds_are_provisional() -> None:
    entries = check_overlap(
        "完全不同的一段話，跟原文沒有關係。\n", SOURCE_ZH, STRICT, file=FILE
    )
    assert "暫定" in entries[0].reason


def test_long_copy_shorter_than_one_ngram_still_shows_where() -> None:
    # 最長共同片段的上限比 n-gram 還短時，fail 的那段也要列出位置，不能只有一條總結
    thresholds = OverlapThresholds(
        ngram_size=8, max_ngram_overlap=1.0, max_common_tokens=3
    )
    draft = "Honestly, the real cost of running agents surprised me.\n"

    entries = check_overlap(draft, SOURCE_EN, thresholds, file=FILE)

    located = [entry for entry in entries if entry.location is not None]
    assert [entry.status for entry in entries] == ["fail"] * len(entries)
    assert [entry.excerpt for entry in located] == ["the real cost of running agents"]
    assert located[0].location == Location(file=FILE, line=1, column=11)


# #67：註腳定義行本來就該照抄來源標題，不算照抄
LONG_TITLE = (
    "The real cost of running agents is not the tokens: it is the hours engineers "
    "spend reading transcripts"
)
DEFAULTS = OverlapThresholds(ngram_size=8, max_ngram_overlap=0.1, max_common_tokens=30)


def test_footnote_definition_with_a_long_source_title_is_not_counted() -> None:
    draft = (
        "# 我的看法\n\n"
        "跑代理人最貴的不是模型費，是人去翻紀錄的時間[^S1]。我認為該先把紀錄整理好。\n\n"
        f"[^S1]: {LONG_TITLE}（https://example.org/agents）\n"
    )
    source = f"# {LONG_TITLE}\n\n{SOURCE_EN}"

    entries = check_overlap(draft, source, DEFAULTS, file=FILE)

    assert [entry.status for entry in entries] == ["pass"]


def test_same_title_copied_into_the_body_still_fails() -> None:
    # 只放過註腳定義那一行；同一段話寫進正文照樣算照抄
    draft = f"# 我的看法\n\n{LONG_TITLE}, and here is why it matters.\n"
    source = f"# {LONG_TITLE}\n\n{SOURCE_EN}"

    entries = check_overlap(draft, source, DEFAULTS, file=FILE)

    assert {entry.status for entry in entries} == {"fail"}


def test_footnote_lookalike_inside_a_sentence_is_still_checked() -> None:
    # 「[^S1]:」不在行首就不是註腳定義（GFM），整行照樣比對
    draft = f"# 觀點\n\n他說 [^S1]: {LONG_TITLE}\n"
    source = f"# {LONG_TITLE}\n\n{SOURCE_EN}"

    entries = check_overlap(draft, source, DEFAULTS, file=FILE)

    assert {entry.status for entry in entries} == {"fail"}
