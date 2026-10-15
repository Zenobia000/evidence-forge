"""對話串流的畫面資料（`ui/thread.py`）：Pi 的完整紀錄加上工單，整理成一塊一塊要顯示的東西（IA §4）。

訊息的形狀照 Pi 的 `get_messages`：user、assistant（內容有 text 與 toolCall）、toolResult、custom。
"""

from pathlib import Path
from typing import Any

import pytest
from chat_folder import RECIPES, ChatFolder

from evidence_forge.ui import artifacts, thread


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    return ChatFolder(tmp_path / "data")


def _user(text: str, ts: int = 1_791_900_000_000) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": ts}


def _agent(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "toolCall", "id": "t1", "name": name, "arguments": arguments}],
    }


def _result(name: str, text: str) -> dict[str, Any]:
    return {"role": "toolResult", "toolCallId": "t1", "toolName": name, "content": [{"type": "text", "text": text}]}


def _build(folder: ChatFolder, cid: str, messages: list[dict[str, Any]]) -> list[thread.Block]:
    jobs = artifacts.conversation_jobs(folder.data, RECIPES, cid)
    return thread.build(folder.data, jobs, messages)


def _kinds(blocks: list[thread.Block]) -> list[str]:
    return [b.kind for b in blocks]


def test_你的訊息_agent_的回答_工具呼叫各是一塊(folder: ChatFolder) -> None:
    cid = folder.conversation()
    blocks = _build(
        folder,
        cid,
        [_user("幫我分析這篇"), _agent("收到。**先讀全文**。"), _call("read_report", {"job_id": "a1"})],
    )

    assert _kinds(blocks) == ["me", "agent", "tool"]
    assert blocks[0].text == "幫我分析這篇"
    assert "<strong>先讀全文</strong>" in str(blocks[1].html)
    assert (blocks[2].label, blocks[2].args) == ("讀報告", '{\n  "job_id": "a1"\n}')


def test_網址顯示成連結卡_抓到之後用素材項目的標題(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    blocks = _build(folder, cid, [_user("分析這篇 https://seed.example/post 謝謝")])

    me = blocks[0]
    assert me.url == "https://seed.example/post"
    assert me.domain == "seed.example"
    assert me.link_title == "長上下文會不會取代檢索"


def test_還沒抓到的網址只顯示網域(folder: ChatFolder) -> None:
    cid = folder.conversation()
    blocks = _build(folder, cid, [_user("https://new.example/x")])

    assert blocks[0].domain == "new.example" and blocks[0].link_title is None


def test_附檔顯示檔名和字數_不把全文貼進對話泡泡(folder: ChatFolder) -> None:
    cid = folder.conversation()
    body = "這是一篇文章。" * 30
    blocks = _build(folder, cid, [_user(f"請分析\n\n〔附檔 note.md〕\n{body}\n〔附檔結束〕")])

    me = blocks[0]
    assert me.text == "請分析"
    assert [(a.name, a.chars) for a in me.attachments] == [("note.md", len(body))]


def test_模型的標記不會變成標記_agent_回答裡的_script(folder: ChatFolder) -> None:
    cid = folder.conversation()
    blocks = _build(folder, cid, [_agent("<script>alert(1)</script>好")])

    assert "<script>" not in str(blocks[0].html)


def test_開工的工具結果帶工單_結果卡接在它後面(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    blocks = _build(
        folder,
        cid,
        [
            _user("分析這篇"),
            _call("analyze", {"text": "x"}),
            _result("analyze", "job_id: a1\nstatus: queued\nrecipe: analyze with 2 stage(s)"),
            _agent("好了"),
        ],
    )

    assert _kinds(blocks) == ["me", "tool", "job", "agent"]
    assert blocks[2].job is not None and blocks[2].job.job_id == "a1"


def test_沒對到訊息的工單接在最後_每張工單都看得到(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    blocks = _build(folder, cid, [_user("嗨")])

    assert _kinds(blocks) == ["me", "job"]


def test_沒開成工的呼叫是一行錯誤_原因照原樣_再補能做什麼(folder: ChatFolder) -> None:
    cid = folder.conversation()
    blocks = _build(
        folder,
        cid,
        [_call("analyze", {"url": "https://wall.example"}), _result("analyze", "NOT STARTED: 讀不到網址（付費牆）")],
    )

    error = blocks[-1]
    assert error.kind == "error"
    assert error.text == "讀不到網址（付費牆）"
    assert "改貼文字" in (error.help or "")


def test_結果卡_完成的分析_疑慮與佐證與查不到的數字(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    job = _build(folder, cid, [])[0].job

    assert job is not None
    assert (job.state, job.outcome) == ("done", "complete")
    assert (job.doubtful, job.supported, job.unfound, job.topics) == (1, 1, 1, 2)
    assert job.lead.startswith("長上下文還撐不起")
    assert job.can_open


def test_結果卡_文章帶角色文類字數重疊率_未校準也標(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", overlaps={"原文": 6.0}, reason="未校準：角色 me 還沒有")
    job = [b.job for b in _build(folder, cid, []) if b.job and b.job.kind == "rewrite"][0]

    assert job is not None
    assert (job.persona, job.genre, job.title) == ("me", "理論教學", "長上下文不會取代檢索")
    assert job.uncalibrated and job.overlap is not None and job.overlap.rate == 6.0


def test_結果卡_未完成和失敗各有原因_失敗沒有產出就不能開(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1", outcome="incomplete", reason="預算用完")
    folder.job(cid, "a2", outcome="failed", reason="Ollama 沒回應", parent="a1", minutes=5)
    first, second = [b.job for b in _build(folder, cid, []) if b.job]

    assert (first.outcome, first.reason, first.can_open) == ("incomplete", "預算用完", True)
    assert (second.outcome, second.reason, second.can_open) == ("failed", "Ollama 沒回應", False)


def test_跑著的工單是進度列的位置_由畫面照事件填(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.job(cid, "a1", status="running", outcome=None)
    job = _build(folder, cid, [])[0].job

    assert job is not None and job.state == "running" and not job.can_open


def test_最後一張以外的結果卡是精簡版(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.analysis(cid, "a2", parent="a1", version=2, minutes=5)
    first, second = [b.job for b in _build(folder, cid, []) if b.job]

    assert (first.compact, second.compact) == (True, False)


def test_看不懂的訊息跳過_不丟例外(folder: ChatFolder) -> None:
    cid = folder.conversation()
    blocks = _build(folder, cid, [{"role": "weird"}, {"role": "custom", "customType": "ef-job"}, {"nothing": 1}])

    assert blocks == []
