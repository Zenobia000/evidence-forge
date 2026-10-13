"""不在對話裡的工單（命令列開的）的帳：當月總表與 `/c/loose` 的花費分頁看的是同一批單。"""

import json
from pathlib import Path

import pytest
from chat_folder import RECIPES, ChatFolder, at

from evidence_forge.ui import bills

MONTH = "2026-10"


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    return ChatFolder(tmp_path / "data")


def test_花費分頁列出每一張有花費的單_合計跟當月總數對得上(folder: ChatFolder) -> None:
    # 舊的 echo 單沒有報告也沒有文章：面板沒有東西可以開，但它的錢在當月總數裡，花費分頁就要列出來
    folder.analysis(None, "20261013-100000-aaaa")
    folder.ledger("20261013-100000-aaaa", [{"stage": "investigate", "tier": "strong", "model": "c", "cost_usd": 0.2}])
    folder.job(None, "20261013-090000-echo", recipe="echo")
    folder.ledger("20261013-090000-echo", [{"stage": "echo", "tier": "strong", "model": "c", "cost_usd": 0.05}])

    cost = bills.conversation_cost(folder.data, RECIPES, "loose")
    month = bills.month_view(folder.data, RECIPES, MONTH, now=at())
    loose_row = [r for r in month.conversations if r.conversation_id == "loose"][0]

    assert cost.total_usd == pytest.approx(0.25)
    assert cost.total_usd == pytest.approx(loose_row.usd)
    assert cost.total_usd == pytest.approx(month.total_usd)
    labels = [r.label for r in cost.rows]
    assert "分析 v1" in labels
    assert any("20261013-090000-echo" in label and "echo" in label for label in labels)


def test_讀不懂_job_md_的單有花費也列出來_不讓錢憑空消失(folder: ChatFolder) -> None:
    folder.job(None, "20261013-080000-bad", recipe="echo")
    (folder.data.job_dir("20261013-080000-bad") / "job.md").write_text("壞掉了", encoding="utf-8")
    folder.ledger("20261013-080000-bad", [{"stage": "echo", "tier": "strong", "model": "c", "cost_usd": 0.07}])

    cost = bills.conversation_cost(folder.data, RECIPES, "loose")

    assert cost.total_usd == pytest.approx(0.07)
    assert [r.label for r in cost.rows] == ["工單 20261013-080000-bad（job.md 讀不懂）"]


def test_指到_jobs_外面的資料夾不算進不在對話裡的帳(folder: ChatFolder, tmp_path: Path) -> None:
    folder.analysis(None, "ok1")
    folder.ledger("ok1", [{"stage": "brief", "tier": "strong", "model": "c", "cost_usd": 0.1}])
    outside = tmp_path / "outside-ledger"
    outside.mkdir()
    row = {
        "ts": at().isoformat(),
        "job_id": "x",
        "stage": "brief",
        "attempt": 1,
        "kind": "llm",
        "tier": "strong",
        "model": "c",
        "cost_usd": 99.0,
        "latency_ms": 1,
        "status": "ok",
        "request_hash": "b" * 64,
    }
    (outside / "ledger.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (folder.data.jobs / "evil").symlink_to(outside, target_is_directory=True)

    month = bills.month_view(folder.data, RECIPES, MONTH, now=at())
    cost = bills.conversation_cost(folder.data, RECIPES, "loose")

    assert month.total_usd == pytest.approx(0.1)
    assert cost.total_usd == pytest.approx(0.1)
