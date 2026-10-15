"""編輯卡與決策紀錄（`cards.py`，ADR-0025 §3.3、FR-IW-03、#107）。

測試都從模組的公開入口走：提出問題、送出一關、沿用、重問、記作者素材、讀卡，
再看 `decisions.jsonl` 與算出來的編輯卡。不打模型、不連網。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evidence_forge import cards, conversations
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)


def _question(header: str, *labels: str) -> dict[str, Any]:
    return {
        "header": header,
        "question": f"{header}要怎麼定？",
        "options": [{"label": label, "description": f"{label}的說明"} for label in labels],
    }


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    found = DataDir(tmp_path / "data")
    found.ensure()
    return found


@pytest.fixture
def cid(data: DataDir) -> str:
    return conversations.create(data, NOW).id


def test_提出第_1_關的問題卡_開一張新卡_編輯卡上這一關正在問(data: DataDir, cid: str) -> None:
    written = cards.propose_questions(
        data, cid, None, 1, [_question("文體", "explainer", "trend")], now=NOW
    )

    card = cards.read_card(data, cid, written.card_id)
    first = card.stations[0]
    assert (first.station, first.status, first.decided) == (1, "asking", False)
    assert [q.header for q in first.questions] == ["文體"]
    assert [o.label for o in first.questions[0].options] == ["explainer", "trend"]
    assert [s.status for s in card.stations[1:]] == ["not_reached"] * 5
    assert card.writable is False


def _decisions(data: DataDir, cid: str, card_id: str) -> list[str]:
    path = data.conversation_dir(cid) / "cards" / card_id / "decisions.jsonl"
    return path.read_text(encoding="utf-8").splitlines()


def _start(data: DataDir, cid: str) -> str:
    """開一張卡，第 1 關問兩張卡。"""
    return cards.propose_questions(
        data,
        cid,
        None,
        1,
        [_question("文體", "explainer", "trend"), _question("主題", "主題 A", "主題 B")],
        now=NOW,
    ).card_id


def _current(data: DataDir, cid: str, card_id: str, station: int) -> list[cards.StationQuestion]:
    return [q for q in cards.read_card(data, cid, card_id).stations[station - 1].questions if q.current]


def _submit(
    data: DataDir, cid: str, card_id: str, station: int, *picks: str, at: int = 0
) -> cards.Written:
    """照順序回答這一關現在的每一張問題卡（選項名稱），一次送出。"""
    current = _current(data, cid, card_id, station)
    answers = [{"question": q.id, "choice": pick} for q, pick in zip(current, picks, strict=True)]
    return cards.submit_station(
        data, cid, card_id, station, answers, now=NOW + timedelta(minutes=at)
    )


def test_畫面送出一關_一張卡一筆回答_同一批同一個送出編號_來源是畫面(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)

    written = _submit(data, cid, card_id, 1, "explainer", "主題 B")

    assert [e["type"] for e in written.events] == ["answered", "answered"]
    assert {e["source"] for e in written.events} == {"ui"}
    assert len({e["submission"] for e in written.events}) == 1
    first = written.card.stations[0]
    assert (first.status, first.decided) == ("decided", True)
    assert [a.choice for a in first.submissions[0].answers] == ["explainer", "主題 B"]
    assert written.card.current_station == 2


def test_決策紀錄只增不改_之前寫的每一行原封不動(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    before = _decisions(data, cid, card_id)

    _submit(data, cid, card_id, 1, "explainer", "主題 B")
    cards.reask_station(data, cid, card_id, 1, now=NOW)
    _submit(data, cid, card_id, 1, "trend", "主題 A")

    after = _decisions(data, cid, card_id)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 2 + 1 + 2


def _through(data: DataDir, cid: str, card_id: str, last: int) -> None:
    """第 1 關已經問了；從第 1 關一路問、一路送出到第 `last` 關（每關一張卡，選第一個選項）。"""
    for station in range(1, last + 1):
        if station > 1:
            cards.propose_questions(
                data, cid, card_id, station, [_question(f"第{station}關", "甲", "乙")], now=NOW
            )
        current = _current(data, cid, card_id, station)
        answers = [{"question": q.id, "choice": q.options[0].label} for q in current]
        cards.submit_station(data, cid, card_id, station, answers, now=NOW)


def test_編輯卡只是快取_刪掉或寫壞都照事件重算回來(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 2)
    cache = data.conversation_dir(cid) / "cards" / card_id / "card.json"
    expected = cache.read_text(encoding="utf-8")

    cache.write_text('{"writable": true}', encoding="utf-8")
    assert cards.read_card(data, cid, card_id).current_station == 3
    assert cache.read_text(encoding="utf-8") == expected

    cache.unlink()
    cards.read_card(data, cid, card_id)
    assert cache.read_text(encoding="utf-8") == expected


def test_回頭改一關_重新送出之後_後面已經決定的關都標要重確認(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 4)

    cards.reask_station(data, cid, card_id, 2, now=NOW)
    asking = cards.read_card(data, cid, card_id)
    # 只按重問、還沒重新送出：後面不動，第 2 關回到正在問，原本的決定留著當預設
    assert [s.status for s in asking.stations[:4]] == ["decided", "asking", "decided", "decided"]
    assert asking.stations[1].decision == "sub2"

    _submit(data, cid, card_id, 2, "乙")

    card = cards.read_card(data, cid, card_id)
    assert [s.status for s in card.stations] == [
        "decided", "changed", "reconfirm", "reconfirm", "not_reached", "not_reached"
    ]
    assert [s.needs_reconfirm for s in card.stations[2:4]] == [True, True]
    # 要重確認的關，原本的決定還在（畫面拿它當預設）
    assert card.stations[2].decision == "sub3"


def test_沿用_取消要重確認_保留原本那次送出(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 3)
    cards.reask_station(data, cid, card_id, 1, now=NOW)
    _submit(data, cid, card_id, 1, "trend", "主題 A")

    written = cards.keep_decision(data, cid, card_id, 2, now=NOW)

    assert [(e["type"], e["source"], e["submission"]) for e in written.events] == [("kept", "ui", "sub2")]
    second = written.card.stations[1]
    assert (second.status, second.decided, second.needs_reconfirm) == ("kept", True, False)
    assert written.card.stations[2].status == "reconfirm"


def test_沒有要重確認的關不能沿用(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 1)

    with pytest.raises(ToolRejected) as caught:
        cards.keep_decision(data, cid, card_id, 1, now=NOW)
    assert caught.value.status == 409


def test_重問的關重新送出就取消重問_回到選好(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 1)
    written = cards.reask_station(data, cid, card_id, 1, now=NOW)
    assert [(e["type"], e["source"], e["station"]) for e in written.events] == [("reasked", "ui", 1)]

    card = _submit(data, cid, card_id, 1, "explainer", "主題 B").card

    assert card.stations[0].status == "changed"
    assert len(card.stations[0].submissions) == 2


def test_你幫我選_每張卡記排第一的選項_來源是_agent(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)

    written = cards.record_interpreted_answers(data, cid, card_id, 1, pick_for_me=True, now=NOW)

    assert [(e["source"], e["mode"], e["choice"], e["note"]) for e in written.events] == [
        ("agent", "pick_for_me", "explainer", "交給 AI 選"),
        ("agent", "pick_for_me", "主題 A", "交給 AI 選"),
    ]
    assert written.card.stations[0].decided is True


def test_第_3_關你幫我選只記尚待作者提供_不算有決定_寫稿被擋(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 2)
    cards.propose_questions(
        data, cid, card_id, 3, [_question("例子", "你遇過的帳單", "沒有例子")], now=NOW
    )

    written = cards.record_interpreted_answers(data, cid, card_id, 3, pick_for_me=True, now=NOW)

    assert [(e["pending"], e["choice"] if "choice" in e else None) for e in written.events] == [(True, None)]
    third = written.card.stations[2]
    assert (third.status, third.decided) == ("pending_author", False)
    # 卡在第 3 關：第 4 關問不了
    with pytest.raises(ToolRejected) as caught:
        cards.propose_questions(data, cid, card_id, 4, [_question("範圍", "甲", "乙")], now=NOW)
    assert caught.value.status == 409
    assert "第 3 關" in caught.value.message


def test_第_3_關的尚待作者提供_agent_不能拿來記別的關_畫面也不能送(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    q1, q2 = _current(data, cid, card_id, 1)
    pending = [{"question": q1.id, "pending": True}, {"question": q2.id, "choice": "主題 A"}]

    with pytest.raises(ToolRejected):
        cards.record_interpreted_answers(data, cid, card_id, 1, pending, quote="先跳過", now=NOW)
    _through(data, cid, card_id, 2)
    cards.propose_questions(data, cid, card_id, 3, [_question("例子", "甲", "沒有例子")], now=NOW)
    (q3,) = _current(data, cid, card_id, 3)
    with pytest.raises(ToolRejected):
        cards.submit_station(data, cid, card_id, 3, [{"question": q3.id, "pending": True}], now=NOW)


def test_作者自己回答之後第_3_關才有決定(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 2)
    cards.propose_questions(data, cid, card_id, 3, [_question("例子", "甲", "沒有例子")], now=NOW)
    cards.record_interpreted_answers(data, cid, card_id, 3, pick_for_me=True, now=NOW)
    cards.propose_questions(data, cid, card_id, 3, [_question("例子", "帳單", "沒有例子")], now=NOW)

    card = _submit(data, cid, card_id, 3, "沒有例子").card

    assert card.stations[2].status == "decided"
    # 尚待作者提供不是決定，所以補上作者的回答不算「回頭改」：後面沒有東西要重確認
    assert not any(s.needs_reconfirm for s in card.stations)


def test_打字回答_agent_整理後記下_附原話(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    q1, q2 = _current(data, cid, card_id, 1)

    written = cards.record_interpreted_answers(
        data,
        cid,
        card_id,
        1,
        [{"question": q1.id, "other_text": "寫成實測"}, {"question": q2.id, "choice": "主題 B"}],
        quote="我想寫成實測，放主題 B",
        now=NOW,
    )

    assert {(e["source"], e["mode"], e["quote"]) for e in written.events} == {
        ("agent", "typed", "我想寫成實測，放主題 B")
    }
    assert written.card.stations[0].submissions[0].answers[0].other_text == "寫成實測"


def test_一關要一次回答到每一張問題卡(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    q1, _ = _current(data, cid, card_id, 1)

    with pytest.raises(ToolRejected) as caught:
        cards.submit_station(data, cid, card_id, 1, [{"question": q1.id, "choice": "trend"}], now=NOW)
    assert caught.value.status == 400
    assert cards.read_card(data, cid, card_id).stations[0].status == "asking"


def test_選項要是問題卡上有的_其他的文字放_other_text(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    q1, q2 = _current(data, cid, card_id, 1)

    with pytest.raises(ToolRejected):
        cards.submit_station(
            data,
            cid,
            card_id,
            1,
            [{"question": q1.id, "choice": "不存在"}, {"question": q2.id, "choice": "主題 A"}],
            now=NOW,
        )


def test_六關都有決定而且沒有要重確認_才放行寫稿(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 5)
    with pytest.raises(ToolRejected) as early:
        cards.require_writable(data, cid, card_id)
    assert early.value.status == 409 and "第 6 關" in early.value.message

    _through_last(data, cid, card_id)
    assert cards.require_writable(data, cid, card_id).writable is True

    cards.reask_station(data, cid, card_id, 4, now=NOW)
    _submit(data, cid, card_id, 4, "乙")
    with pytest.raises(ToolRejected) as late:
        cards.require_writable(data, cid, card_id)
    assert "第 5 關" in late.value.message and "要重確認" in late.value.message
    assert "第 6 關" in late.value.message

    cards.keep_decision(data, cid, card_id, 5, now=NOW)
    cards.keep_decision(data, cid, card_id, 6, now=NOW)
    assert cards.require_writable(data, cid, card_id).writable is True


def _through_last(data: DataDir, cid: str, card_id: str) -> None:
    cards.propose_questions(data, cid, card_id, 6, [_question("框架", "甲", "乙")], now=NOW)
    _submit(data, cid, card_id, 6, "甲")


def test_壞掉的一行跳過_不拖垮整張卡(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _through(data, cid, card_id, 1)
    path = data.conversation_dir(cid) / "cards" / card_id / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 99, "type": "answered", "壞掉')

    card = cards.read_card(data, cid, card_id)
    assert card.skipped_lines == 1
    assert card.stations[0].status == "decided"

    # 下一筆接在殘行後面也讀得懂（殘行先被封起來）
    cards.propose_questions(data, cid, card_id, 2, [_question("觀點", "甲", "乙")], now=NOW)
    card = cards.read_card(data, cid, card_id)
    assert (card.skipped_lines, card.stations[1].status) == (1, "asking")


def test_一批回答只寫進一半_那次送出不算(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    _submit(data, cid, card_id, 1, "explainer", "主題 B")
    path = data.conversation_dir(cid) / "cards" / card_id / "decisions.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    # 模擬寫到第二張卡的回答之前被砍
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    assert cards.read_card(data, cid, card_id).stations[0].status == "asking"


def test_同一份作者素材只存一份_拿回原本的編號(data: DataDir, cid: str) -> None:
    first = cards.add_material(data, cid, None, "上個月的帳單：三千元", now=NOW)
    again = cards.add_material(data, cid, first.card_id, "上個月的帳單：三千元", now=NOW)
    other = cards.add_material(data, cid, first.card_id, "另一份筆記", now=NOW, filename="note.md")

    assert (first.material, first.created) == ("M1", True)
    assert (again.material, again.created, again.events) == ("M1", False, [])
    assert other.material == "M2"
    folder = data.conversation_dir(cid) / "cards" / first.card_id / "materials"
    assert len(list(folder.iterdir())) == 2
    content = cards.material_path(data, cid, first.card_id, "M1").read_text(encoding="utf-8")
    assert content == "上個月的帳單：三千元"
    materials = cards.read_card(data, cid, first.card_id).materials
    assert [(m.id, m.filename, m.size) for m in materials] == [
        ("M1", None, len("上個月的帳單：三千元".encode())),
        ("M2", "note.md", len("另一份筆記".encode())),
    ]


@pytest.mark.parametrize("bad", ["../other", "..", "a/b", "", ".hidden", "x:y"])
def test_卡_id_拿來拼路徑_跳出去的一律擋下(data: DataDir, cid: str, bad: str) -> None:
    with pytest.raises(ToolRejected) as caught:
        cards.read_card(data, cid, bad)
    assert caught.value.status == 400


def test_別的對話的卡碰不到_也不說存不存在(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    other = conversations.create(data, NOW).id

    with pytest.raises(ToolRejected) as read:
        cards.read_card(data, other, card_id)
    with pytest.raises(ToolRejected) as missing:
        cards.read_card(data, other, "card-20261015-000000-000000")
    assert (read.value.status, read.value.message.replace(card_id, "?")) == (
        missing.value.status,
        missing.value.message.replace("card-20261015-000000-000000", "?"),
    )
    with pytest.raises(ToolRejected):
        cards.submit_station(data, other, card_id, 1, [], now=NOW)


def test_指到別的對話的符號連結不算這個對話的卡(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)
    other = conversations.create(data, NOW).id
    root = data.conversation_dir(other) / "cards"
    root.mkdir()
    (root / card_id).symlink_to(data.conversation_dir(cid) / "cards" / card_id)

    with pytest.raises(ToolRejected) as caught:
        cards.read_card(data, other, card_id)
    assert caught.value.status == 404


def test_一關一關往下_前一關沒有決定不能問下一關(data: DataDir, cid: str) -> None:
    card_id = _start(data, cid)

    with pytest.raises(ToolRejected) as caught:
        cards.propose_questions(data, cid, card_id, 2, [_question("觀點", "甲", "乙")], now=NOW)
    assert caught.value.status == 409
    with pytest.raises(ToolRejected):
        cards.propose_questions(data, cid, None, 2, [_question("觀點", "甲", "乙")], now=NOW)


@pytest.mark.parametrize(
    "questions",
    [
        [],
        [_question("文體", "只有一個")],
        [_question("文體", "甲", "乙", "丙", "丁", "戊")],
        [_question("文體", "甲", "其他")],
        [_question("文體", "甲", "甲")],
        [_question("一", "甲", "乙")] * 5,
    ],
)
def test_問題卡_1_到_4_張_每張_2_到_4_個選項_其他不用列(
    data: DataDir, cid: str, questions: list[dict[str, Any]]
) -> None:
    with pytest.raises(ToolRejected) as caught:
        cards.propose_questions(data, cid, None, 1, questions, now=NOW)
    assert caught.value.status == 400
    # 不合格就不開卡：不留一張空的卡
    assert cards.list_cards(data, cid) == []


def test_從報告的題材開始_記下哪份報告第幾個題材(data: DataDir, cid: str) -> None:
    conversations.add_job(
        data, cid, conversations.ConversationJob(job_id="20261015-report", ts=NOW)
    )
    (data.jobs / "20261015-report").mkdir()

    written = cards.propose_questions(
        data,
        cid,
        None,
        1,
        [_question("文體", "explainer", "trend")],
        now=NOW,
        origin={"kind": "report_topic", "job_id": "20261015-report", "topic": 2, "title": "長上下文的帳"},
    )

    card = written.card
    assert card.title == "長上下文的帳"
    assert card.origin is not None
    assert (card.origin.kind, card.origin.job_id, card.origin.topic) == ("report_topic", "20261015-report", 2)
    with pytest.raises(ToolRejected) as caught:
        cards.propose_questions(
            data,
            cid,
            None,
            1,
            [_question("文體", "explainer", "trend")],
            now=NOW,
            origin={"kind": "report_topic", "job_id": "20261015-not-mine", "topic": 1},
        )
    assert caught.value.status == 403


def test_一個對話可以有好幾張卡(data: DataDir, cid: str) -> None:
    first = _start(data, cid)
    second = cards.propose_questions(
        data, cid, None, 1, [_question("文體", "essay", "trend")], now=NOW + timedelta(seconds=1)
    ).card_id

    assert [c.card_id for c in cards.list_cards(data, cid)] == sorted([first, second])
