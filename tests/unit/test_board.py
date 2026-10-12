"""告示板的後端故障：每個後端各記各的，一個後端恢復不會洗掉另一個的故障。"""

from datetime import UTC, datetime

from evidence_forge.gateway.board import JobBoard, Outage
from evidence_forge.jobkey import JobKey

KEY = JobKey("20261012-120000-abcdef12", "research", 1)


def _board() -> JobBoard:
    return JobBoard(lambda: datetime(2026, 10, 12, tzinfo=UTC))


def test_模型呼叫成功不會洗掉_Tavily_的故障() -> None:
    board = _board()
    board.report_outage(KEY, "tavily", "Tavily 出錯（HTTP 500）")
    # 搜尋失敗之後 agent 照樣問模型，模型好好回了
    board.recovered(KEY, "ollama")
    assert board.outage(KEY) == Outage("tavily", "Tavily 出錯（HTTP 500）")


def test_同一個後端恢復了_它的故障不算數() -> None:
    board = _board()
    board.report_outage(KEY, "tavily", "Tavily 出錯（HTTP 500）")
    board.recovered(KEY, "tavily")
    assert board.outage(KEY) is None


def test_兩個後端都故障_回最早碰到的_恢復一個還剩另一個() -> None:
    board = _board()
    board.report_outage(KEY, "tavily", "Tavily 出錯")
    board.report_outage(KEY, "ollama", "模型後端連不上")
    # 同一個後端第二次的故障不蓋掉第一次的原因
    board.report_outage(KEY, "tavily", "Tavily 沒回應")
    assert board.outage(KEY) == Outage("tavily", "Tavily 出錯")
    board.recovered(KEY, "tavily")
    assert board.outage(KEY) == Outage("ollama", "模型後端連不上")
    # 別的段、別次不受影響
    assert board.outage(JobKey(KEY.job_id, "research", 2)) is None
