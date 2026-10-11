"""C1 引句真實：整理段的引句要逐字出現在原文（PRD §7、架構草案 v0.3 §6）。"""

import pytest

from evidence_forge.checks import LocatedText, Location, check_quotes

pytestmark = pytest.mark.promise("FR-VF-01")

SOURCE_ZH = """# AI 代理的成本

大型語言模型的推論成本，在過去一年降了九成。

但是，「真正貴的是工程師的時間」，作者在結尾這樣寫。
"""

SOURCE_EN = """# Why agents fail

Most agent failures are not model failures.
They are context failures: the model never saw the right file.
"""

SOURCE_MIXED = """Anthropic 的 Claude Code 把 agent loop 做成 CLI，
讓 PM 也能跑 coding agent。"""


def _quote(text: str, index: int = 0) -> LocatedText:
    return LocatedText(
        text=text,
        location=Location(file="claims.json", pointer=f"/claims/{index}/quote"),
    )


@pytest.mark.parametrize(
    ("source", "quote"),
    [
        # 一模一樣
        (SOURCE_ZH, "大型語言模型的推論成本，在過去一年降了九成。"),
        # 全形標點換成半形、標點拿掉
        (SOURCE_ZH, "大型語言模型的推論成本, 在過去一年降了九成"),
        # 引號換成另一種、前後多了空白
        (SOURCE_ZH, "  “真正貴的是工程師的時間”  "),
        # 原文換行處，引句寫成一行
        (SOURCE_EN, "not model failures. They are context failures"),
        # 英文的引號與標點不同
        (SOURCE_EN, "They are context failures — the model never saw the right file"),
        # 全形英數字
        (SOURCE_MIXED, "Ｃｌａｕｄｅ　Ｃｏｄｅ 把 agent loop 做成 CLI"),
        # 中英混排、原文跨行
        (SOURCE_MIXED, "做成 CLI，讓 PM 也能跑 coding agent"),
        # 中英之間的空白有無不同
        (SOURCE_MIXED, "讓PM也能跑coding agent"),
        # 數字裡的小數點與負號照原文，周圍的標點與空白照樣忽略
        ("成長了 3.5 倍，氣溫是 -5 度。", "成長了3.5倍、氣溫是-5度"),
        # 範圍的橫線長短不同
        ("從 2020–2021 年開始。", "從 2020-2021 年開始"),
    ],
)
def test_quote_found_after_normalising_passes(source: str, quote: str) -> None:
    entries = check_quotes([_quote(quote)], source)
    assert [entry.status for entry in entries] == ["pass"]
    assert all(entry.check == "C1" for entry in entries)


@pytest.mark.parametrize(
    ("source", "quote"),
    [
        # 數字被改了
        (SOURCE_ZH, "大型語言模型的推論成本，在過去一年降了八成。"),
        # 少了一個字
        (SOURCE_ZH, "大型語言模型的推論成本，在過去一年降了成。"),
        # 換了同義詞
        (SOURCE_ZH, "真正昂貴的是工程師的時間"),
        # 把兩句接起來、中間省略（省略號不算逐字）
        (SOURCE_ZH, "大型語言模型的推論成本……真正貴的是工程師的時間"),
        # 英文換掉一個字
        (SOURCE_EN, "Most agent failures are not model errors."),
        # 英文大小寫不同也不算逐字
        (SOURCE_EN, "most agent failures are not model failures"),
        # 英文多一個字
        (SOURCE_EN, "They are mostly context failures"),
        # 混排：英文詞換掉
        (SOURCE_MIXED, "讓 PM 也能跑 coding assistant"),
        # 完全捏造
        (SOURCE_MIXED, "Claude Code 是 OpenAI 做的"),
        # 小數點不見了：3.5 倍變成 35 倍
        ("成長了 3.5 倍。", "成長了 35 倍"),
        # 負號不見了：-5 度變成 5 度
        ("氣溫是 -5 度。", "氣溫是 5 度"),
    ],
)
def test_near_miss_or_fabricated_quote_fails(source: str, quote: str) -> None:
    entries = check_quotes([_quote(quote)], source)
    assert [entry.status for entry in entries] == ["fail"]


def test_failure_points_at_the_quote_and_shows_it() -> None:
    quotes = [
        _quote("大型語言模型的推論成本，在過去一年降了九成。", 0),
        _quote("真正便宜的是工程師的時間", 1),
    ]
    entries = check_quotes(quotes, SOURCE_ZH)
    failed = [entry for entry in entries if entry.status == "fail"]
    assert len(failed) == 1
    assert failed[0].location == Location(file="claims.json", pointer="/claims/1/quote")
    assert failed[0].excerpt == "真正便宜的是工程師的時間"
    assert failed[0].reason


def test_empty_quote_fails() -> None:
    entries = check_quotes([_quote("「……」")], SOURCE_ZH)
    assert [entry.status for entry in entries] == ["fail"]


def test_no_quotes_at_all_passes_with_a_reason() -> None:
    entries = check_quotes([], SOURCE_ZH)
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].reason
