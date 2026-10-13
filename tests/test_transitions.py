"""工單狀態只能照 ADR-0019 §3.4 的圖轉換：排隊、執行中、結束（ADR-0010 原本的五種已收成三種）。"""

import itertools

import pytest

from evidence_forge.transitions import InvalidTransition, transition

STATUSES = ["queued", "running", "done"]

# 直接從 ADR-0019 §3.4 抄下來，不從被測程式讀。
ALLOWED = {
    ("queued", "running"),  # 執行器有空位
    ("running", "running"),  # 下一段
    ("running", "done"),  # 做完、預算用完、出錯
    ("running", "queued"),  # 當機後放回排隊（ADR-0019 §3.4）
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
    with pytest.raises(InvalidTransition, match="done.*running"):
        transition("done", "running")


@pytest.mark.promise("NFR-RUN-02")
def test_unknown_status_is_refused() -> None:
    with pytest.raises(InvalidTransition):
        transition("done", "published")  # type: ignore[arg-type]
