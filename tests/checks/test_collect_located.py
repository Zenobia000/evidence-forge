"""從段的 JSON 產出裡找出要檢查的文字（引句、事實句），附 JSON pointer 位置。"""

import pytest

from evidence_forge.checks import LocatedText, Location, collect_located

pytestmark = pytest.mark.promise("FR-VF-01")


def test_collects_every_value_under_the_key_with_its_pointer() -> None:
    claims = {
        "genre": "opinion",
        "claims": [
            {"id": "c1", "quote": "第一句"},
            {"id": "c2", "quote": "第二句", "reported": {"quote": "轉述的話"}},
        ],
    }
    assert collect_located(claims, "quote", "claims.json") == [
        LocatedText(
            text="第一句",
            location=Location(file="claims.json", pointer="/claims/0/quote"),
        ),
        LocatedText(
            text="第二句",
            location=Location(file="claims.json", pointer="/claims/1/quote"),
        ),
        LocatedText(
            text="轉述的話",
            location=Location(file="claims.json", pointer="/claims/1/reported/quote"),
        ),
    ]


def test_pointer_escapes_slash_and_tilde_in_keys() -> None:
    payload = {"a/b": {"~x": {"quote": "q"}}}
    [found] = collect_located(payload, "quote", "out.json")
    assert found.location.pointer == "/a~1b/~0x/quote"


def test_non_string_values_under_the_key_are_reported_as_text() -> None:
    # 模型把 quote 寫成數字或 null 時不能默默跳過，要讓 C1 判它找不到
    payload = {"claims": [{"quote": None}, {"quote": 42}]}
    texts = [found.text for found in collect_located(payload, "quote", "claims.json")]
    assert texts == ["", "42"]
