"""訪談的畫面（#108）：對話串流裡的問題卡與摘要卡、右側面板的決策分頁、寫稿前的最後確認。

跟 `test_chat_ui.py` 一樣：真的開在 127.0.0.1 的空埠上，Pi 是假 Pi，資料夾是手寫的假資料。
編輯卡用 `cards.py` 的公開入口蓋出來（提出問題、送出、沿用、重問），畫面的片段從決策紀錄長出來，
所以這裡看的是「紀錄是什麼樣子，畫面就是什麼樣子」，不重測 `cards.py` 的規則。
互動（一次一張、鍵盤、送出）在 JS，見 `tests/js/interview.test.mjs`。
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_folder import RECIPES, ChatFolder, at
from chat_world import fake_chat_harness

from evidence_forge import cards
from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.chat.pi_rpc import session_dir
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.ui import interview

GENRE_CARD = {
    "header": "文體",
    "question": "這篇要寫成哪一種？",
    "options": [
        {"label": "explainer", "description": "把計價方式講清楚就有價值。", "value": "explainer"},
        {"label": "benchmark", "description": "要自己實測一次才站得住。", "value": "benchmark"},
        {"label": "trend", "description": "寫成兩年內這筆帳會怎麼變。", "value": "trend"},
    ],
}
THEME_CARD = {
    "header": "主題",
    "question": "放在哪個主題底下？",
    "options": [
        {"label": "[主題 A]", "description": "最貼近原文。"},
        {"label": "[主題 B]", "description": "比較冷門。"},
    ],
}
NAME_CARD = {
    "header": "命名",
    "question": "這個觀點要叫什麼名字？",
    "options": [
        {"label": "「價目會降，架構不會」", "description": "一句話講完。"},
        {"label": "「長上下文稅」", "description": "最好記，但口氣偏負面。"},
        {"label": "「架構帳」", "description": "最短，但要解釋。"},
    ],
}


def _station(n: int) -> list[dict[str, Any]]:
    if n == 1:
        return [GENRE_CARD, THEME_CARD]
    return [
        {
            "header": f"關{n}",
            "question": f"第 {n} 關要怎麼定？",
            "options": [
                {"label": f"關{n}甲", "description": f"關{n}甲的說明"},
                {"label": f"關{n}乙", "description": f"關{n}乙的說明"},
            ],
        }
    ]


@dataclass
class World:
    url: str
    folder: ChatFolder
    host: ConversationHost

    def conversation(self, title: str = "長上下文會不會取代檢索") -> str:
        return self.folder.conversation(title)

    def propose(
        self, cid: str, card_id: str | None, station: int, questions: list[dict[str, Any]] | None = None,
        minutes: float = 1, origin: dict[str, Any] | None = None,
    ) -> str:
        written = cards.propose_questions(
            self.folder.data, cid, card_id, station, questions or _station(station), now=at(minutes), origin=origin
        )
        return written.card_id

    def answers(self, cid: str, card_id: str, station: int, picks: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """每張現在的問題卡選第一個選項（第 3 關寫自己的例子）；`picks`：標籤 → 選項名稱。"""
        state = cards.read_card(self.folder.data, cid, card_id).stations[station - 1]
        found = []
        for q in (q for q in state.questions if q.current):
            if station == cards.EXAMPLE_STATION:
                found.append({"question": q.id, "other_text": "客服機器人改成先檢索"})
            else:
                label = (picks or {}).get(q.header, q.options[0].label)
                found.append({"question": q.id, "choice": label})
        return found

    def submit(
        self, cid: str, card_id: str, station: int, minutes: float = 2, picks: dict[str, str] | None = None,
        note: str | None = None,
    ) -> None:
        answers = self.answers(cid, card_id, station, picks)
        if note:
            answers[0]["note"] = note
        cards.submit_station(self.folder.data, cid, card_id, station, answers, now=at(minutes))

    def through(self, cid: str, last: int, picks: dict[int, dict[str, str]] | None = None) -> str:
        """問並送出前 `last` 關；回編輯卡 id。"""
        card_id = ""
        minute = 1.0
        for n in range(1, last + 1):
            card_id = self.propose(cid, card_id or None, n, minutes=minute, origin={"kind": "idea", "title": "長上下文的實際成本"} if n == 1 else None)
            self.submit(cid, card_id, n, minutes=minute + 0.5, picks=(picks or {}).get(n))
            minute += 1
        return card_id

    def thread(self, cid: str) -> str:
        return httpx.get(f"{self.url}/ui/c/{cid}/thread", timeout=15).text

    def panel(self, cid: str, tab: str = "decisions", **params: str) -> httpx.Response:
        return httpx.get(f"{self.url}/ui/c/{cid}/panel", params={"tab": tab, **params}, timeout=15)


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[World]:
    folder = ChatFolder(tmp_path / "data")
    host = ConversationHost(
        data=folder.data,
        harness=fake_chat_harness(tmp_path / "harness"),
        stages=ActiveStages(),
        gateway_url="http://127.0.0.1:9",
        recipes=RECIPES,
        clock=lambda: at(30),
    )
    host.start()
    try:
        with serve(create_app(host, data=folder.data, recipes=RECIPES)) as url:
            yield World(url, folder, host)
    finally:
        host.close()


def _seed(folder: ChatFolder, cid: str, messages: list[dict[str, Any]]) -> None:
    directory = session_dir(folder.data, cid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{cid}.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages), encoding="utf-8"
    )


def _ms(minutes: float) -> int:
    return int(at(minutes).timestamp() * 1000)


# --- 對話串流：問題卡 --------------------------------------------------------------------


def test_現在要答的一關是問題卡_一張一個_fieldset_加單選鈕_最後有其他(world: World) -> None:
    cid = world.conversation()
    card_id = world.propose(cid, None, 1)

    html = world.thread(cid)

    assert f'data-card="{card_id}"' in html and 'data-station="1"' in html
    assert html.count('<fieldset class="ch-q"') == 2
    # 一次只出一張：第一張看得到，第二張藏著
    first, second = re.findall(r'<fieldset class="ch-q"[^>]*>', html)
    assert "hidden" not in first and "hidden" in second
    assert "第 1 張／共 2 張" in html
    assert "第 1 關・文體＋主題" in html
    # 進度標籤列出每張卡的標籤
    assert re.findall(r'data-qtab="[^"]+">([^<]+)<', html) == ["文體", "主題"]
    # 無障礙：legend 帶出第幾關第幾張；每個選項是 label 包著的 radio；說明是選項名字的一部分
    assert "第 1 關，第 1 張共 2 張，" in html and "第 1 關，第 2 張共 2 張，" in html
    assert 'type="radio"' in html and 'class="ch-qopt"' in html


def test_文體卡沒有其他_一般的卡最後一個選項一定是其他(world: World) -> None:
    cid = world.conversation()
    card_id = world.propose(cid, None, 1)
    world.submit(cid, card_id, 1)
    world.propose(cid, card_id, 2, [NAME_CARD])

    first = world.thread(cid)
    # 第 1 關已送出，收成摘要；現在要答的是第 2 關的命名卡：三個選項加「其他」
    assert "ch-qset" in first
    kinds = re.findall(r'data-kind="([^"]+)" data-label="([^"]*)"', first)
    assert [k for k, _ in kinds] == ["option", "option", "option", "other"]
    assert kinds[-1][1] == "其他"
    # 「其他」那一列帶一個預設藏著的輸入框，名字講清楚是哪一張的
    assert re.search(r'<input class="ch-qopt__other" type="text" data-other aria-label="命名：自己寫的答案"[^>]* hidden>', first)
    # 備註框預設藏著（選了選項才出現，由前端決定）
    assert "data-note-label" in first and "data-note-input" in first


def test_文體卡一定要選列出的文體_沒有其他的輸入框(world: World) -> None:
    cid = world.conversation()
    world.propose(cid, None, 1)

    html = world.thread(cid)
    genre = html.split('data-q="q1"')[1].split("</fieldset>")[0]

    assert 'data-kind="other"' not in genre
    assert "explainer" in genre and "benchmark" in genre


def test_第_3_關多一個沒有例子_作者證據型的文體不給(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 2, picks={1: {"文體": "explainer"}})
    world.propose(cid, card_id, 3, [{"header": "例子", "question": "哪一段經驗只有你寫得出來？",
                                       "options": [{"label": "你自己的 API 帳單", "description": "提示。"},
                                                   {"label": "幫客戶估過的成本", "description": "提示。"}]}], minutes=5)

    html = world.thread(cid)

    assert re.findall(r'data-kind="([^"]+)" data-label', html) == ["option", "option", "no_example", "other"]

    # 文體選了 benchmark：主證據是作者自己的，不能答沒有例子
    other = world.conversation("乙")
    other_card = world.through(other, 2, picks={1: {"文體": "benchmark"}})
    world.propose(other, other_card, 3, [{"header": "例子", "question": "哪一段經驗？",
                                           "options": [{"label": "甲", "description": "提示。"},
                                                       {"label": "乙", "description": "提示。"}]}], minutes=5)
    assert "no_example" not in world.thread(other)


def test_重問的一關_原本的答案是預設_選項跟備註都接得回來(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    world.submit(cid, card_id, 2, minutes=5, picks={"命名": "「架構帳」"}, note="標題別用稅")
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(6))
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=7)

    html = world.thread(cid)
    active = html.split("data-qset")[1]

    assert "（重問）" in active
    checked = re.findall(r'<input type="radio"[^>]*data-label="([^"]*)" checked>', active)
    assert checked == ["「架構帳」"]
    assert ">標題別用稅</textarea>" in active


# --- 對話串流：送出之後 ------------------------------------------------------------------


def test_送出之後收成摘要卡_標點選與已記下的時間_沒有假的使用者訊息(world: World) -> None:
    cid = world.conversation()
    card_id = world.propose(cid, None, 1)
    # 畫面送出：真的走 API（source: ui），主機再對 Pi 送 /ef-card（紀錄裡多一則 custom 訊息）
    state = cards.read_card(world.folder.data, cid, card_id).stations[0]
    answers = [{"question": q.id, "choice": q.options[0].label} for q in state.questions]
    response = httpx.post(
        f"{world.url}/api/conversations/{cid}/cards/{card_id}/stations/1/submit", json={"answers": answers}, timeout=15
    )
    assert response.status_code == 200
    _seed(world.folder, cid, [
        {"role": "custom", "customType": "ef-card", "content": "[ef-card] 創作者送出了第 1 關", "timestamp": _ms(2)},
    ])

    html = world.thread(cid)

    assert "data-answered" in html and 'data-src="click"' in html and "點選" in html
    assert "2 張" in html and "已記下" in html
    assert re.search(r"\d\d:\d\d 已記下", html)
    assert "ch-me" not in html and "[ef-card]" not in html
    # 收成摘要之後不能再點：沒有單選鈕
    assert 'type="radio"' not in html


def test_備註只在摘要卡上出現一次_答案用選項的名字(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    world.submit(cid, card_id, 2, minutes=5, note="標題裡不要出現「稅」")

    html = world.thread(cid)

    assert html.count("備註：標題裡不要出現「稅」") == 1
    assert "→ <b>「價目會降，架構不會」</b>" in html


def test_沿用和重問在對話裡是置中的一行小字_不是誰說的話(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 3)
    # 回頭改第 2 關：第 3 關變要重確認
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(10))
    world.propose(cid, card_id, 2, minutes=11)
    world.submit(cid, card_id, 2, minutes=12, picks={"關2": "關2乙"})
    cards.keep_decision(world.folder.data, cid, card_id, 3, now=at(13))

    html = world.thread(cid)

    lines = re.findall(r'<span class="ch-event">.*?</span>', html, re.S)
    assert [re.sub(r"<[^>]+>", "", line) for line in lines] == [
        "你在決策分頁按了「重問第 2 關」・15:10",
        "你在決策分頁按了「沿用」：第 3 關・15:13",
    ]
    # 重問之後再送出的那一關，摘要標「（重問）」
    assert "第 2 關・核心觀點（重問）" in html


def test_重問之後問題卡回到串流最底下_在重問那一行後面(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 2)
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(10))

    html = world.thread(cid)

    # 第 1、2 關原本的摘要，然後重問的那一行，最後是現在要答的卡（原本那組問題，原本的答案當預設）
    assert html.count("data-answered") == 2
    assert html.rindex("data-answered") < html.index("ch-event") < html.index("data-qset")


def test_打字回答是_agent_記的_摘要標從你打的字整理_交給_AI_選另外標(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    state = cards.read_card(world.folder.data, cid, card_id).stations[1]
    q = next(q.id for q in state.questions if q.current)
    cards.record_interpreted_answers(
        world.folder.data, cid, card_id, 2, [{"question": q, "choice": "「架構帳」"}],
        now=at(5), quote="名字就叫架構帳。",
    )
    world.propose(cid, card_id, 3, minutes=6)
    cards.record_interpreted_answers(
        world.folder.data, cid, card_id, 3, pick_for_me=True, now=at(7)
    )

    html = world.thread(cid)

    assert 'data-src="typed"' in html and "從你打的字整理" in html
    # 第 3 關「交給 AI 選」：不是點的、也不是打的原話，另外標出來；第 3 關不替作者答，記尚待提供
    assert 'data-src="pick"' in html and "你說交給 AI 選" in html
    assert "尚待你提供" in html


def test_問題卡照決策紀錄的時間插在對話裡_不是全堆在最後(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    _seed(world.folder, cid, [
        {"role": "user", "content": [{"type": "text", "text": "第一句"}], "timestamp": _ms(0)},
        {"role": "assistant", "content": [{"type": "text", "text": "好，一關一關來。"}], "timestamp": _ms(0.5)},
        {"role": "assistant", "content": [{"type": "text", "text": "第二關的問題在上面。"}], "timestamp": _ms(9)},
    ])

    html = world.thread(cid)

    order = [html.index("好，一關一關來。"), html.index("data-answered"), html.index("data-qset"), html.index("第二關的問題在上面。")]
    assert order == sorted(order)


def test_agent_的工具呼叫有中文名字(world: World) -> None:
    cid = world.conversation()
    _seed(world.folder, cid, [
        {"role": "assistant", "content": [{"type": "toolCall", "id": "t", "name": "propose_questions", "arguments": {"station": 1}}]},
    ])

    assert "出這一關的問題" in world.thread(cid)


def test_兩張編輯卡時_摘要與問題卡標出是哪一張(world: World) -> None:
    cid = world.conversation()
    world.propose(cid, None, 1, origin={"kind": "idea", "title": "題目甲"})
    world.propose(cid, None, 1, origin={"kind": "idea", "title": "題目乙"}, minutes=2)

    html = world.thread(cid)

    assert "題目甲・第 1 關" in html and "題目乙・第 1 關" in html


# --- 決策分頁 ----------------------------------------------------------------------------


def test_分頁的順序是報告_決策_文章_花費(world: World) -> None:
    cid = world.conversation()

    html = world.panel(cid, "report").text

    assert re.findall(r'data-tab="([a-z]+)"', html) == ["report", "decisions", "article", "cost"]


def test_沒有編輯卡有說明_沒有數字徽章(world: World) -> None:
    cid = world.conversation()

    html = world.panel(cid).text

    assert "這個對話還沒有編輯卡" in html
    assert "s0-tab__n" not in html


def test_決策分頁_六種狀態各有標示_徽章數字是要重確認的關數(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 4)
    # 第 5 關正在問；第 6 關還沒到
    world.propose(cid, card_id, 5, minutes=10)
    # 回頭改第 2 關（選不同的）：第 3、4 關要重確認
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(11))
    world.propose(cid, card_id, 2, minutes=12)
    world.submit(cid, card_id, 2, minutes=13, picks={"關2": "關2乙"})
    # 第 3 關沿用
    cards.keep_decision(world.folder.data, cid, card_id, 3, now=at(14))

    html = world.panel(cid).text
    by_station = {int(n): s for s, n in re.findall(r'data-state="([a-z]+)" data-station="(\d)"', html)}

    assert by_station == {1: "done", 2: "revised", 3: "reused", 4: "reconfirm", 5: "asking", 6: "todo"}
    assert "改過 1 次" in html and ">沿用<" in html and "要重確認" in html and "還沒到" in html
    # 分頁上的數字：要重確認的關數（只有第 4 關）
    assert re.search(r'aria-label="決策，1 關要重確認">決策 <span class="s0-tab__n">1</span>', html)


def test_改過的答案留著原本那一行加刪除線_沒選的收起來(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    world.submit(cid, card_id, 2, minutes=5, picks={"命名": "「長上下文稅」"})
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(6))
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=7)
    world.submit(cid, card_id, 2, minutes=8, picks={"命名": "「價目會降，架構不會」"}, note="標題別用稅")

    html = world.panel(cid).text

    assert "<del>「長上下文稅」</del>" in html
    assert re.search(r"原本（<span class=\"s0-mono\">15:05</span>）", html)
    assert "<b>命名</b>這個觀點要叫什麼名字？" in html
    assert "一句話講完。" in html  # 選的那個的說明
    assert "你的備註" in html and "標題別用稅" in html
    # 沒選的兩個收在 details 裡，附說明
    assert "沒選的 2 個" in html
    assert "「架構帳」：最短，但要解釋。" in html and "「長上下文稅」：最好記，但口氣偏負面。" in html
    assert "<details" in html and "<details open" not in html


def test_要重確認的關有沿用和重問兩顆按鈕_重問帶出後面有決定的關(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 3)
    cards.reask_station(world.folder.data, cid, card_id, 1, now=at(10))
    world.propose(cid, card_id, 1, minutes=11)
    world.submit(cid, card_id, 1, minutes=12, picks={"文體": "trend"})

    html = world.panel(cid).text

    assert "前面有一關改過，這一關是照改之前的答案選的。" in html
    # 第 2、3 關都要重確認：各自有沿用、重問
    assert html.count('data-dec-action="keep"') == 2
    assert 'aria-label="沿用第 2 關"' in html and 'aria-label="沿用第 3 關"' in html
    assert 'data-dec-action="reask" data-card="' in html
    # 要重確認的是黃底（--color-accent-action）：用 data-tone，不寫死顏色
    assert 'data-tone="reconfirm"' in html
    # 第 1 關的重問會讓後面有決定的關要重確認：先確認；後面沒有決定的關不用
    later = dict(re.findall(r'data-station="(\d)" data-later="([\d,]*)"', html))
    assert later["1"] == ""  # 第 2、3 關已經是要重確認，不是「有決定」，所以再問第 1 關不會多標


def test_按鈕上的屬性剛好是_API_要的_沿用和重問按下去決策分頁與對話立刻變(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 3)
    cards.reask_station(world.folder.data, cid, card_id, 2, now=at(10))
    world.propose(cid, card_id, 2, minutes=11)
    world.submit(cid, card_id, 2, minutes=12, picks={"關2": "關2乙"})
    before = world.panel(cid).text
    keep = re.search(r'data-dec-action="keep" data-card="([^"]+)" data-station="(3)"', before)
    assert keep is not None

    # 前端的 cardRequest 組出來的路徑：/api/conversations/<對話>/cards/<卡>/stations/<關>/<動作>，body 是 {}
    kept = httpx.post(
        f"{world.url}/api/conversations/{cid}/cards/{keep.group(1)}/stations/{keep.group(2)}/keep", json={}, timeout=15
    )
    assert kept.status_code == 200
    after = world.panel(cid).text
    states = {int(n): s for s, n in re.findall(r'data-state="([a-z]+)" data-station="(\d)"', after)}
    assert states[3] == "reused" and states[2] == "revised"
    assert "你確認原本的答案照用" in after
    assert "你在決策分頁按了「沿用」：第 3 關" in world.thread(cid)

    reask = re.search(r'data-dec-action="reask" data-card="([^"]+)" data-station="(3)"', after)
    assert reask is not None
    asked = httpx.post(
        f"{world.url}/api/conversations/{cid}/cards/{reask.group(1)}/stations/{reask.group(2)}/reask", json={}, timeout=15
    )
    assert asked.status_code == 200
    assert 'data-state="asking" data-station="3"' in world.panel(cid).text
    assert "你在決策分頁按了「重問第 3 關」" in world.thread(cid)


def test_點選之後對話串流沒有假的使用者訊息_主機通知_Pi_的_custom_訊息也不顯示(world: World) -> None:
    # 畫面的點選由系統記在決策紀錄；主機再對 Pi 送 /ef-card，Pi 的紀錄裡多一則 role: custom 的通知，
    # 它是給 agent 讀的，不是創作者說的話，串流不能把它畫成「你的訊息」
    cid = world.conversation()
    card_id = world.through(cid, 2)
    _seed(world.folder, cid, [
        {"role": "custom", "customType": "ef-card", "content": "[ef-card] 創作者重問了第 2 關", "timestamp": _ms(9)},
    ])
    httpx.post(f"{world.url}/api/conversations/{cid}/cards/{card_id}/stations/2/reask", json={}, timeout=15)

    html = world.thread(cid)

    assert "ch-me" not in html and "[ef-card]" not in html
    assert "你在決策分頁按了「重問第 2 關」" in html


def test_無障礙_問題卡是一組有名字的群組_選項是真的單選鈕_按鈕有字(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD, {**THEME_CARD, "header": "強度"}], minutes=4)

    html = world.thread(cid)
    active = html[html.index("data-qset") - 40 :]

    # 一關是一個有名字的群組，念法：第 2 關・核心觀點，答題中
    assert re.search(r'role="group" aria-current="true" aria-label="第 2 關・核心觀點，答題中"', active)
    # 每張卡是 fieldset＋legend：螢幕閱讀器念出「第 2 關，第 1 張共 2 張，命名 這個觀點要叫什麼名字？」
    assert active.count("<fieldset") == 2 and active.count("<legend") == 2
    assert "第 2 關，第 1 張共 2 張，</span>" in active
    # 選項是真的 radio（不是 div）：同一張卡共用一個 name，每個包在 label 裡
    names = re.findall(r'<input type="radio" name="([^"]+)"', active)
    assert len(set(names)) == 2 and all(n.startswith("q-card-") for n in names)
    assert active.count('<label class="ch-qopt">') == active.count('type="radio"')
    # 其他的輸入框有自己的名字；備註框有 label 對著
    assert 'aria-label="命名：自己寫的答案"' in active
    assert re.search(r'<label for="note-card-[^"]+-q\d+" data-note-label>', active)
    # 鍵盤提示只給眼睛看（aria-hidden），按鈕都有字
    assert 'class="ch-keys" aria-hidden="true"' in active
    assert "確認，下一張" in active and "上一張" in active
    # 圖示都是裝飾
    assert "<svg" in active and "<svg" not in active.replace('<svg viewBox="0 0 24 24" aria-hidden="true">', "")


def test_無障礙_決策分頁_分頁標籤帶數字的名字_按鈕帶關號(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 3)
    cards.reask_station(world.folder.data, cid, card_id, 1, now=at(10))
    world.propose(cid, card_id, 1, minutes=11)
    world.submit(cid, card_id, 1, minutes=12, picks={"文體": "trend"})

    html = world.panel(cid).text

    assert 'role="tablist"' in html and html.count('role="tab"') == 4
    assert 'aria-label="決策，2 關要重確認"' in html
    assert 'aria-label="沿用第 2 關"' in html and 'aria-label="重問第 3 關"' in html
    # 六關是一份有名字的清單；狀態用文字（徽章）說，不只靠顏色
    assert '<ol class="ch-dec" aria-label="六關">' in html
    assert html.count("要重確認") >= 2
    assert "<svg" not in html.replace('<svg viewBox="0 0 24 24" aria-hidden="true">', "")


def test_重問第_2_關_後面有選好的就帶出來讓前端先確認(world: World) -> None:
    cid = world.conversation()
    world.through(cid, 3)

    html = world.panel(cid).text
    later = dict(re.findall(r'data-station="(\d)" data-later="([\d,]*)"', html))

    assert later == {"1": "2,3", "2": "3", "3": ""}


def test_沒有要重確認的關_也沒有沿用鈕(world: World) -> None:
    cid = world.conversation()
    world.through(cid, 2)

    html = world.panel(cid).text

    assert 'data-dec-action="keep"' not in html
    assert "s0-tab__n" not in html


def test_正在問的那一關列出現在的問題卡_草稿由前端填_沒有重問鈕(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, minutes=4)

    html = world.panel(cid).text
    asking = html.split('data-state="asking"')[1].split("</li>\n</ol>")[0]

    assert "data-asking" in html and "data-asking-badge" in html and "正在問" in html
    assert "data-draft" in asking and "這張還沒答……" in asking
    assert 'data-dec-action="reask"' not in asking


def test_第_3_關尚待作者提供_不算選好_有說明(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 2, picks={1: {"文體": "explainer"}})
    world.propose(cid, card_id, 3, minutes=5)
    cards.record_interpreted_answers(world.folder.data, cid, card_id, 3, pick_for_me=True, now=at(6))

    html = world.panel(cid).text
    states = {int(n): s for s, n in re.findall(r'data-state="([a-z]+)" data-station="(\d)"', html)}

    assert states[3] == "pending"
    assert "等你提供" in html and "尚待你提供" in html
    assert "你說交給 AI 選" in html


def test_agent_打字記的答案標從你打的字整理_附原話與時間(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 1)
    world.propose(cid, card_id, 2, [NAME_CARD], minutes=4)
    q = next(q.id for q in cards.read_card(world.folder.data, cid, card_id).stations[1].questions if q.current)
    cards.record_interpreted_answers(
        world.folder.data, cid, card_id, 2, [{"question": q, "choice": "「架構帳」"}], now=at(5), quote="名字就叫架構帳。"
    )

    html = world.panel(cid).text

    assert "從你打的字整理" in html
    assert "你打的：「名字就叫架構帳。」（<span class=\"s0-mono\">15:05</span>）" in html


def test_照這張編輯卡寫稿的按鈕只在能寫稿時出現(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 5)
    assert "照這張編輯卡寫稿" not in world.panel(cid).text

    world.propose(cid, card_id, 6, minutes=8)
    world.submit(cid, card_id, 6, minutes=9)
    html = world.panel(cid).text
    assert "照這張編輯卡寫稿" in html and 'data-action="write-from-card"' in html
    assert "六關都選好" in html

    # 回頭改第 3 關：第 4～6 關要重確認，按鈕收回去
    cards.reask_station(world.folder.data, cid, card_id, 3, now=at(10))
    world.propose(cid, card_id, 3, minutes=11)
    world.submit(cid, card_id, 3, minutes=12)
    assert "照這張編輯卡寫稿" not in world.panel(cid).text


def test_標題與起點寫在上面_兩張卡有選單_網址上的卡不是這個對話的就給最新的(world: World) -> None:
    cid = world.conversation()
    old = world.propose(cid, None, 1, origin={"kind": "idea", "title": "舊的題目"}, minutes=1)
    new = world.propose(cid, None, 1, origin={"kind": "idea", "title": "新的題目"}, minutes=5)

    html = world.panel(cid).text
    assert "這張編輯卡在寫" in html and "新的題目" in html and "來自你的想法" in html
    assert re.findall(r"<option value=\"([^\"]+)\"( selected)?>", html)[0][0] == new

    picked = world.panel(cid, card=old).text
    assert "舊的題目" in picked.split("ch-card-topic__v")[1].split("</span>")[0]

    other = world.conversation("乙")
    foreign = world.propose(other, None, 1, origin={"kind": "idea", "title": "別人的"}, minutes=2)
    ignored = world.panel(cid, card=foreign).text
    assert "別人的" not in ignored and "新的題目" in ignored


def test_起點是報告的題材_寫出是哪一版報告的第幾個題材(world: World) -> None:
    cid = world.conversation()
    world.folder.analysis(cid, "a1")
    world.propose(cid, None, 1, origin={"kind": "report_topic", "job_id": "a1", "topic": 2, "title": "第一個字為什麼越來越慢"})

    html = world.panel(cid).text

    assert re.search(r"來自報告 v1 的研究題材 2", html)


def test_讀不到編輯卡_說明原因_不拖垮分頁(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = world.conversation()
    world.propose(cid, None, 1)

    def broken(self: ConversationHost, conversation_id: str) -> list[dict[str, Any]]:
        raise OSError("磁碟讀不到")

    monkeypatch.setattr(ConversationHost, "list_cards", broken)
    html = world.panel(cid).text

    assert "讀不到這張編輯卡的決策紀錄" in html and "磁碟讀不到" in html
    assert 'data-tab="decisions"' in html


def test_不存在的對話_決策分頁_404(world: World) -> None:
    assert world.panel("nope").status_code == 404


# --- 寫稿前的最後確認 --------------------------------------------------------------------


def _writable(world: World, cid: str) -> str:
    card_id = world.through(cid, 5, picks={1: {"文體": "explainer"}})
    world.propose(cid, card_id, 6, minutes=8)
    world.submit(cid, card_id, 6, minutes=9)
    return card_id


def test_最後確認卡_預估金額與上限_沒有其他也沒有備註(world: World) -> None:
    cid = world.conversation()
    card_id = _writable(world, cid)

    response = httpx.get(f"{world.url}/ui/c/{cid}/write-confirm", params={"card": card_id}, timeout=15)

    assert response.status_code == 200
    html = response.text
    assert "要照這張編輯卡開始寫了嗎？" in html
    assert re.findall(r'data-label="([^"]*)"', html) == ["照這張寫", "先不要"]
    assert 'data-kind="other"' not in html and "ch-qnote" not in html
    # 還沒寫過文章：老實說沒有預估可以參考，不編數字；上限是費用設定的每張寫稿預算（預設 US$0.50）
    assert "還沒寫過文章" in html and "這張工單最多 US$0.50" in html
    assert "角色 me" in html and "確認之後就開寫稿工單，開始花錢。" in html


def test_最後確認卡_寫過文章就拿上一篇花的錢當預估(world: World) -> None:
    cid = world.conversation()
    card_id = _writable(world, cid)
    world.folder.analysis(cid, "a1")
    world.folder.article(cid, "w1", parent="a1")
    world.folder.ledger("w1", [{"stage": "rewrite", "tier": "strong", "model": "claude", "cost_usd": 0.21}])

    html = httpx.get(f"{world.url}/ui/c/{cid}/write-confirm", params={"card": card_id}, timeout=15).text

    assert "預估 US$0.21（照上一篇文章花的錢）" in html


def test_最後確認卡_還有關沒選好或要重確認_不給開始(world: World) -> None:
    cid = world.conversation()
    card_id = world.through(cid, 3)

    response = httpx.get(f"{world.url}/ui/c/{cid}/write-confirm", params={"card": card_id}, timeout=15)

    assert response.status_code == 409
    assert "現在不能照這張編輯卡寫稿" in response.text and 'data-kind' not in response.text


def test_最後確認卡_別的對話的卡_跟沒有這張卡一樣(world: World) -> None:
    cid = world.conversation()
    other = world.conversation("乙")
    foreign = _writable(world, other)

    mine = httpx.get(f"{world.url}/ui/c/{cid}/write-confirm", params={"card": foreign}, timeout=15)
    missing = httpx.get(f"{world.url}/ui/c/{cid}/write-confirm", params={"card": "card-nope"}, timeout=15)

    assert mine.status_code == missing.status_code
    # 說的話只差在回聲的卡 id：不說這張卡存不存在、在哪個對話
    assert mine.text.replace(foreign, "X") == missing.text.replace("card-nope", "X")


# --- 純資料：interview.py -----------------------------------------------------------------


def test_沒有例子的規則跟_cards_的規則對得上() -> None:
    allowed = interview.no_example_allowed
    assert allowed({"genre": "explainer"}) is True
    assert allowed({"genre": "essay", "origin": {"kind": "report_topic"}}) is True
    assert allowed({"genre": "essay", "origin": {"kind": "idea"}}) is False
    assert allowed({"genre": "benchmark"}) is False
    assert allowed({"genre": None}) is False


def test_答案的字_其他_沒有例子_尚待提供_素材() -> None:
    assert interview.answer_text({"choice": "甲"}) == "甲"
    assert interview.answer_text({"other_text": "自己寫"}) == "其他：自己寫"
    assert interview.answer_text({"no_example": True}) == "沒有例子"
    assert interview.answer_text({"pending": True}) == "尚待你提供"
    assert interview.answer_text({"choice": "甲", "material": "M1"}) == "甲（引用作者素材 M1）"
    assert interview.answer_text({"material": "M2"}) == "作者素材 M2"
