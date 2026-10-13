"""帳單（`ui/bills.py`）：從帳本算出本月總表與每個對話的花費，`conv:` 的帳列成「對話」（IA §6）。

金額都是手寫的字面值，不是用被測程式的算法再算一次。
"""

from pathlib import Path

import pytest
from chat_folder import RECIPES, ChatFolder, at

from evidence_forge.ui import bills

MONTH = "2026-10"


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    return ChatFolder(tmp_path / "data")


def _world(folder: ChatFolder) -> str:
    """一個對話：一份報告（本機 brief＋雲端查證＋搜尋）、文章 v1 與 v2、對話本身的花費。"""
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.ledger(
        "a1",
        [
            {"stage": "brief", "tier": "local", "cost_usd": 0.0, "gpu_seconds": 12.0},
            {"stage": "investigate", "tier": "strong", "model": "claude", "cost_usd": 0.20},
            {"stage": "investigate", "kind": "search", "tier": None, "model": None, "cost_usd": 0.05, "credits": 4},
        ],
    )
    folder.article(cid, "w1", parent="a1", persona="me", minutes=10)
    folder.ledger("w1", [{"stage": "rewrite", "tier": "strong", "model": "claude", "cost_usd": 0.10}])
    folder.article(cid, "w2", parent="w1", persona="me", version=2, minutes=20, reason="未校準：角色 me")
    folder.ledger(
        "w2",
        [{"stage": "rewrite", "tier": "max", "model": "claude", "escalated_from": "strong", "cost_usd": 0.15}],
    )
    folder.ledger(cid, [{"stage": "chat", "attempt": 1, "tier": "strong", "model": "claude", "cost_usd": 0.04}], conversation=True)
    return cid


def test_本月總花費是對話本身加所有工單(folder: ChatFolder) -> None:
    _world(folder)

    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())

    assert view.total_usd == pytest.approx(0.54)
    assert view.conversation_count == 1


def test_報告幾份文章幾篇_同一條版本鏈算一篇_所有版本的錢都算進去(folder: ChatFolder) -> None:
    _world(folder)

    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())

    assert (view.reports, view.articles) == (1, 1)
    assert view.report_average == pytest.approx(0.25)  # brief 0 + 查證 0.20 + 搜尋 0.05
    assert view.article_average == pytest.approx(0.25)  # v1 0.10 + v2 0.15


def test_依_skill_把對話本身列成一項(folder: ChatFolder) -> None:
    _world(folder)

    by_skill = {bar.label: bar.usd for bar in bills.month_view(folder.data, RECIPES, MONTH, now=at()).by_skill}

    assert by_skill == pytest.approx(
        {"重構 rewrite": 0.25, "查證與找題材": 0.25, "對話本身": 0.04, "brief": 0.0}
    )


def test_依模型分本機_雲端_升級_搜尋(folder: ChatFolder) -> None:
    _world(folder)

    by_model = {bar.label: bar.usd for bar in bills.month_view(folder.data, RECIPES, MONTH, now=at()).by_model}

    assert by_model == pytest.approx(
        {"雲端（強）": 0.34, "Tavily 搜尋": 0.05, "雲端（升級）": 0.15, "本機": 0.0}
    )


def test_依角色_文章數_版本數_未校準數(folder: ChatFolder) -> None:
    _world(folder)

    persona = bills.month_view(folder.data, RECIPES, MONTH, now=at()).by_persona

    assert [(p.persona, p.articles, p.versions, p.uncalibrated) for p in persona] == [("me", 1, 2, 1)]
    assert persona[0].usd == pytest.approx(0.25)


def test_本機雲端表_GPU_秒與_credits(folder: ChatFolder) -> None:
    _world(folder)

    where = {row.label: row for row in bills.month_view(folder.data, RECIPES, MONTH, now=at()).by_where}

    assert (where["本機"].calls, where["本機"].gpu_seconds) == (1, 12.0)
    assert where["雲端"].calls == 4  # 查證、文章 v1、v2、對話本身
    assert where["搜尋（Tavily）"].calls == 4  # credits
    assert where["搜尋（Tavily）"].usd == pytest.approx(0.05)


def test_每個對話一列_點進去是那個對話的花費(folder: ChatFolder) -> None:
    cid = _world(folder)

    row = bills.month_view(folder.data, RECIPES, MONTH, now=at()).conversations[0]

    assert row.conversation_id == cid
    assert (row.title, row.reports, row.articles, row.versions) == ("長上下文會不會取代檢索", 1, 1, 2)
    assert row.usd == pytest.approx(0.54)


def test_別的月份的帳不算進來(folder: ChatFolder) -> None:
    cid = _world(folder)
    folder.ledger("a1", [{"stage": "investigate", "tier": "strong", "model": "claude", "cost_usd": 9.0, "ts": "2026-09-30T23:00:00+08:00"}])
    del cid

    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())
    september = bills.month_view(folder.data, RECIPES, "2026-09", now=at())

    assert view.total_usd == pytest.approx(0.54)
    assert september.total_usd == pytest.approx(9.0)


def test_台北的月界線_十月一日零點算十月(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.ledger("a1", [{"stage": "brief", "tier": "strong", "model": "c", "cost_usd": 1.0, "ts": "2026-09-30T16:30:00+00:00"}])

    assert bills.month_view(folder.data, RECIPES, "2026-10", now=at()).total_usd == pytest.approx(1.0)
    assert bills.month_view(folder.data, RECIPES, "2026-09", now=at()).total_usd == 0


def test_這個月沒有花費就是空的_三個數字都是零(folder: ChatFolder) -> None:
    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())

    assert view.empty
    assert (view.total_usd, view.reports, view.articles) == (0, 0, 0)
    assert view.conversations == []


def test_帳本壞掉的一行標檔案和行號_其餘照算(folder: ChatFolder) -> None:
    _world(folder)
    ledger = folder.data.job_dir("a1") / "ledger.jsonl"
    ledger.write_text(ledger.read_text(encoding="utf-8") + "這不是 json\n", encoding="utf-8")

    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())

    assert view.total_usd == pytest.approx(0.54)
    assert [(b.where, b.line) for b in view.bad_lines] == [("jobs/a1/ledger.jsonl", 4)]


def test_不屬於任何對話的工單單獨列一行(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.ledger("a1", [{"stage": "brief", "tier": "strong", "model": "c", "cost_usd": 0.5}])
    # 命令列開的單：沒有對話
    folder.analysis(cid, "cli1")
    path = folder.data.conversation_dir(cid) / "jobs.jsonl"
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if "cli1" not in line) + "\n", encoding="utf-8")
    folder.ledger("cli1", [{"stage": "brief", "tier": "strong", "model": "c", "cost_usd": 0.25}])

    view = bills.month_view(folder.data, RECIPES, MONTH, now=at())

    assert view.total_usd == pytest.approx(0.75)
    assert [r.title for r in view.conversations] == ["長上下文會不會取代檢索", "不在對話裡的工單"]


def test_月份參數不合法就用這個月(folder: ChatFolder) -> None:
    assert bills.parse_month("../../x", at()) == "2026-10"
    assert bills.parse_month("2026-13", at()) == "2026-10"
    assert bills.parse_month("2026-09", at()) == "2026-09"
    assert bills.parse_month(None, at()) == "2026-10"


def test_上個月下個月_未來的月份不能按(folder: ChatFolder) -> None:
    view = bills.month_view(folder.data, RECIPES, "2026-10", now=at())
    assert (view.previous_month, view.next_month) == ("2026-09", None)
    earlier = bills.month_view(folder.data, RECIPES, "2026-01", now=at())
    assert (earlier.previous_month, earlier.next_month) == ("2025-12", "2026-02")


# --- 對話的「花費」分頁 -------------------------------------------------------------


def test_對話花費每次工作一列_對話本身另列_合計是全部(folder: ChatFolder) -> None:
    cid = _world(folder)

    cost = bills.conversation_cost(folder.data, RECIPES, cid)

    assert [(r.label, r.models) for r in cost.rows] == [
        ("分析 v1", "本機＋雲端（強）＋Tavily"),
        ("文章 v1", "雲端（強）"),
        ("文章 v2", "雲端（升級）"),
        ("對話本身", "雲端（強）"),
    ]
    assert [r.usd for r in cost.rows] == pytest.approx([0.25, 0.10, 0.15, 0.04])
    assert cost.rows[2].escalation == "雲端（強）→雲端（最強）"
    assert cost.total_usd == pytest.approx(0.54)
    assert cost.budget_usd == 1.0
