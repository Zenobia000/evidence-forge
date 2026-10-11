"""工單狀態只能照架構草案 v0.3 §2.4 的圖轉換。"""

import itertools

import pytest

from evidence_forge.transitions import InvalidTransition, transition

STATUSES = ["queued", "running", "review", "approved", "discarded"]

# 直接從 v0.3 §2.4 的狀態圖抄下來，不從被測程式讀。
ALLOWED = {
    ("queued", "running"),  # 執行器有空位
    ("running", "running"),  # 下一段
    ("running", "review"),  # 做完、預算用完、出錯
    ("review", "approved"),  # 通過
    ("review", "queued"),  # 退件、失敗重試
    ("review", "discarded"),  # 丟棄
    ("approved", "queued"),  # 通過後又想改
}
FORBIDDEN = [
    pair for pair in itertools.product(STATUSES, STATUSES) if pair not in ALLOWED
]


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transition_returns_the_new_status(current: str, target: str) -> None:
    assert transition(current, target) == target  # type: ignore[arg-type]


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_any_other_transition_is_refused(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition):
        transition(current, target)  # type: ignore[arg-type]


def test_refusal_names_both_statuses() -> None:
    with pytest.raises(InvalidTransition, match="discarded.*running"):
        transition("discarded", "running")


@pytest.mark.promise("NFR-RUN-02")
def test_unknown_status_is_refused() -> None:
    with pytest.raises(InvalidTransition):
        transition("review", "published")  # type: ignore[arg-type]
