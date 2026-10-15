"""訪談畫面的資料（#108、ADR-0025、FR-IW-03）：把編輯卡與決策紀錄整理成畫面要的樣子。

輸入是主機給畫面的 JSON（`ConversationHost.list_cards`／`card`：`cards.CardState` 與每一筆事件），不另外讀檔、
不另外算規則：關卡的狀態、哪一組問題是現在的、哪一次送出是現在的決定，全照 `cards.py` 算出來的那份。
這裡只做兩件事：

1. 對話串流裡的項目（`thread_items`）：現在要答的那一關是一張問題卡（`ActiveSet`）、送出過的收成摘要
   （`AnsweredSet`，標「點選」或「從你打的字整理」），按了沿用、重問是置中的一行系統小字（`Line`）。
   點選不會在對話裡多一則像創作者打的字的訊息；項目只照決策紀錄的時間插進紀錄裡。
2. 右側面板的「決策」分頁（`decision_view`）：每一關問過的每張卡、答案、說明、備註、沒選的，
   六種狀態（選好、改過、沿用、要重確認、正在問、還沒到），再加上第 3 關「尚待作者提供」。

互動（一次一張、鍵盤、送出）在 `static/js/interview.js`；這裡產的資料都是純文字，由模板跳脫。
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from evidence_forge import cards
from evidence_forge.harness import default_harness
from evidence_forge.ui.format import clock, format_usd, genre_name

# 選項的種類：agent 列的、第 3 關系統給的「沒有例子」、一定有的「其他」
OPTION = "option"
NO_EXAMPLE = "no_example"
OTHER = "other"

PICK_SOURCE = "pick"
CLICK_SOURCE = "click"
TYPED_SOURCE = "typed"
SOURCE_LABEL = {
    CLICK_SOURCE: "點選",
    TYPED_SOURCE: "從你打的字整理",
    PICK_SOURCE: "你說交給 AI 選",
}
SOURCE_ICON = {CLICK_SOURCE: "check", TYPED_SOURCE: "pencil", PICK_SOURCE: "pencil"}

PENDING_ANSWER = "尚待你提供"
STATION_COUNT = len(cards.STATIONS)


# --- 小工具 -----------------------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def hm(value: Any) -> str:
    moment = parse_time(value)
    return clock(moment) if moment is not None else ""


def answer_text(answer: dict[str, Any]) -> str:
    """一筆回答在畫面上的字：選的選項、「其他」寫的字、沒有例子、尚待提供；引用作者素材另外附上。"""
    if answer.get("no_example"):
        text = cards.NO_EXAMPLE_LABEL
    elif answer.get("pending"):
        text = PENDING_ANSWER
    elif answer.get("other_text"):
        text = f"{cards.OTHER_LABEL}：{answer['other_text']}"
    elif answer.get("choice"):
        text = str(answer["choice"])
    else:
        text = ""
    material = answer.get("material")
    if material:
        text = f"{text}（引用作者素材 {material}）" if text else f"作者素材 {material}"
    return text


def source_of(submission: dict[str, Any]) -> str:
    if submission.get("source") != "agent":
        return CLICK_SOURCE
    return PICK_SOURCE if submission.get("mode") == "pick_for_me" else TYPED_SOURCE


def _questions(station: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """這一關問過的每張卡（舊的也在）；問題卡 id 在一張編輯卡裡不重複。"""
    return {q["id"]: q for q in station.get("questions", [])}


def _find_answer(
    answers: list[dict[str, Any]], question: dict[str, Any], questions: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """舊的回答裡，對得上這張問題卡的那一筆：同一個 id，或重新提出之後同一個標籤。"""
    for answer in answers:
        if answer.get("question") == question["id"]:
            return answer
    for answer in answers:
        before = questions.get(str(answer.get("question")))
        if before is not None and before.get("header") == question.get("header"):
            return answer
    return None


def _complete(station: dict[str, Any]) -> list[dict[str, Any]]:
    """算數的送出（不含第 3 關「尚待作者提供」），照時間順序。"""
    return [s for s in station.get("submissions", []) if not s.get("pending")]


def _decision(station: dict[str, Any]) -> dict[str, Any] | None:
    """現在的決定那一次送出；沒有決定的關（正在問、尚待作者提供）給最後一次，列出來看。"""
    wanted = station.get("decision")
    submissions = station.get("submissions", [])
    found = next((s for s in submissions if s["submission"] == wanted), None) if wanted else None
    return found or (submissions[-1] if submissions else None)


# --- 對話串流 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QOption:
    label: str
    description: str
    kind: str = OPTION


@dataclass(frozen=True)
class QCard:
    id: str
    header: str
    question: str
    options: tuple[QOption, ...]
    # 預設：重問時原本的答案（選項的編號、「其他」的字、備註）
    choice: int | None = None
    other_text: str = ""
    note: str = ""


@dataclass(frozen=True)
class ActiveSet:
    """現在要答的一關：一次一張，答完整關一次送出。"""

    card_id: str
    station: int
    name: str
    cards: tuple[QCard, ...]
    # 這一關是重問的（原本有答案）
    reask: bool = False
    # 一個對話有兩張以上的編輯卡時，標出是哪一張
    title: str = ""
    kind: str = "active"


@dataclass(frozen=True)
class Row:
    chip: str
    question: str
    answer: str
    note: str = ""


@dataclass(frozen=True)
class AnsweredSet:
    """送出過的一關：收成摘要，只剩標籤、答案與備註；來源標「點選」或「從你打的字整理」。"""

    card_id: str
    station: int
    name: str
    rows: tuple[Row, ...]
    source: str
    time: str
    reask: bool = False
    title: str = ""
    kind: str = "answered"

    @property
    def source_label(self) -> str:
        return SOURCE_LABEL[self.source]

    @property
    def source_icon(self) -> str:
        return SOURCE_ICON[self.source]


@dataclass(frozen=True)
class Line:
    """置中的一行系統小字：創作者在決策分頁按了沿用、重問。不是誰說的話。"""

    text: str
    icon: str
    kind: str = "line"


Item = ActiveSet | AnsweredSet | Line


def _structured(question: dict[str, Any]) -> bool:
    """是不是文體、主題、角色卡：選項的 value 是系統認得的值，不能用「其他」自己寫。"""
    return question.get("kind") is not None or all(o.get("value") is not None for o in question["options"])


def _options(station: dict[str, Any], card: dict[str, Any], question: dict[str, Any]) -> tuple[QOption, ...]:
    options = [QOption(o["label"], o.get("description", "")) for o in question["options"]]
    # 第 3 關的選項只是提示：作者自己的例子寫在「其他」或備註；系統另外給「沒有例子」
    if station["station"] == cards.EXAMPLE_STATION and cards.no_example_allowed(
        card.get("genre"), cards.Origin.model_validate(card["origin"]) if card.get("origin") else None
    ):
        options.append(QOption(cards.NO_EXAMPLE_LABEL, "我手上沒有可以講的例子。", NO_EXAMPLE))
    # 第 1 關的文體、主題、角色卡（`kind`；舊紀錄沒有 kind 時每個選項都帶 value 的是文體卡）一定要選列出的選項：
    # 沒有「其他」（`cards._parse_answers` 會拒絕）
    if not _structured(question):
        options.append(QOption(cards.OTHER_LABEL, "自己寫一個答案。", OTHER))
    return tuple(options)


def _qcard(
    station: dict[str, Any],
    card: dict[str, Any],
    question: dict[str, Any],
    before: dict[str, Any] | None,
) -> QCard:
    options = _options(station, card, question)
    choice: int | None = None
    other_text = ""
    note = ""
    if before is not None:
        note = str(before.get("note") or "")
        if before.get("other_text"):
            choice = next((i for i, o in enumerate(options) if o.kind == OTHER), None)
            other_text = str(before["other_text"])
        elif before.get("no_example"):
            choice = next((i for i, o in enumerate(options) if o.kind == NO_EXAMPLE), None)
        elif before.get("choice"):
            choice = next(
                (i for i, o in enumerate(options) if o.kind == OPTION and o.label == before["choice"]), None
            )
        if choice is None:
            note = ""
    return QCard(
        id=question["id"],
        header=question["header"],
        question=question["question"],
        options=options,
        choice=choice,
        other_text=other_text,
        note=note,
    )


def _active(card: dict[str, Any], station: dict[str, Any], title: str) -> ActiveSet | None:
    current = [q for q in station.get("questions", []) if q.get("current")]
    if not current:
        return None
    questions = _questions(station)
    previous = _decision(station)
    answers = previous["answers"] if previous is not None else []
    return ActiveSet(
        card_id=card["card_id"],
        station=station["station"],
        name=station["name"],
        cards=tuple(_qcard(station, card, q, _find_answer(answers, q, questions)) for q in current),
        reask=bool(station.get("submissions")),
        title=title,
    )


def _answered(
    card: dict[str, Any],
    station: dict[str, Any],
    submission: dict[str, Any],
    *,
    reask: bool,
    title: str,
) -> AnsweredSet:
    questions = _questions(station)
    rows = []
    for answer in submission["answers"]:
        question = questions.get(str(answer.get("question")), {})
        rows.append(
            Row(
                chip=str(question.get("header", "")),
                question=str(question.get("question", "")),
                answer=answer_text(answer),
                note=str(answer.get("note") or ""),
            )
        )
    return AnsweredSet(
        card_id=card["card_id"],
        station=station["station"],
        name=station["name"],
        rows=tuple(rows),
        source=source_of(submission),
        time=hm(submission.get("ts")),
        reask=reask,
        title=title,
    )


def thread_items(
    card_list: list[dict[str, Any]], events: dict[str, list[dict[str, Any]]]
) -> list[tuple[datetime, Item]]:
    """對話串流要插的項目，每個帶時間（決策紀錄那一筆的時間），之後照時間插進 Pi 的紀錄裡。

    - 送出過的一關（算數的）：摘要卡，時間是送出的時間。
    - 沿用、重問：一行系統小字。
    - 現在要答的一關（狀態是正在問）：問題卡，時間是最後一次提出問題、或重問的時間（重問之後它回到串流最底下）。
    """
    items: list[tuple[datetime, Item]] = []
    for card in card_list:
        card_id = card["card_id"]
        title = (card.get("title") or "") if len(card_list) > 1 else ""
        stations = {s["station"]: s for s in card["stations"]}
        valid = {(s["station"], sub["submission"]) for s in card["stations"] for sub in s["submissions"]}
        answered: dict[int, int] = {}
        proposed: dict[int, datetime] = {}
        seen: set[tuple[int, str]] = set()
        for event in events.get(card_id, []):
            moment = parse_time(event.get("ts"))
            station = stations.get(event.get("station", 0))
            if moment is None or station is None:
                continue
            number = station["station"]
            kind = event.get("type")
            if kind == "questions_proposed":
                proposed[number] = moment
            elif kind == "answered":
                key = (number, str(event.get("submission")))
                if key in seen or key not in valid:
                    continue
                seen.add(key)
                submission = next(s for s in station["submissions"] if s["submission"] == key[1])
                items.append(
                    (moment, _answered(card, station, submission, reask=answered.get(number, 0) > 0, title=title))
                )
                answered[number] = answered.get(number, 0) + 1
            elif kind == "kept":
                items.append(
                    (moment, Line(f"你在決策分頁按了「沿用」：第 {number} 關・{clock(moment)}", "keep"))
                )
            elif kind == "reasked":
                # 重問之後，問題卡回到最底下（原本那組問題，原本的答案當預設）：位置照重問的時間
                proposed[number] = moment
                items.append(
                    (moment, Line(f"你在決策分頁按了「重問第 {number} 關」・{clock(moment)}", "undo"))
                )
        for number, station in stations.items():
            if station["status"] != "asking":
                continue
            active = _active(card, station, title)
            if active is not None:
                items.append((proposed.get(number) or parse_time(card.get("updated_at")) or datetime.now().astimezone(), active))
    items.sort(key=lambda pair: pair[0])
    return items


# --- 決策分頁 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRow:
    id: str
    chip: str
    question: str
    answer: str = ""
    description: str = ""
    note: str = ""
    # 「從你打的字整理」／「你說交給 AI 選」；點選的不標（免得每一行都多一個標籤）
    source: str = ""
    quote: str = ""
    quote_time: str = ""
    was: str = ""
    was_time: str = ""
    unselected: tuple[tuple[str, str], ...] = ()
    # 正在問：這張卡還沒有送出的答案，由畫面填草稿
    drafting: bool = False

    @property
    def source_label(self) -> str:
        return SOURCE_LABEL.get(self.source, "")

    @property
    def source_icon(self) -> str:
        return SOURCE_ICON.get(self.source, "check")


@dataclass(frozen=True)
class HistoryEntry:
    """這一關問過的一組問題卡的一次紀錄：哪一組、什麼時候、答了什麼（沒送出的只列問題）。"""

    title: str
    time: str
    note: str
    current: bool
    rows: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class DecisionStation:
    number: int
    name: str
    status: str
    # 樣式：done／revised／reused／reconfirm／asking／pending／todo
    state: str
    badge: str = ""
    badge_tone: str = ""
    badge_icon: str = ""
    time: str = ""
    note: str = ""
    rows: tuple[DecisionRow, ...] = ()
    keep: bool = False
    reask: bool = False
    # 重問這一關會讓這幾關變成要重確認（後面已經有決定的）
    later: tuple[int, ...] = ()
    asking: bool = False
    # 手機寬度收起來時留的一行摘要：每張卡的答案用「・」接起來
    line: str = ""
    # 這一關問過的每一組問題卡與每一次答案（含舊的），照時間；只有一筆就不列
    history: tuple[HistoryEntry, ...] = ()


@dataclass(frozen=True)
class DecisionView:
    card_id: str
    title: str
    origin: str
    started: str
    progress: str
    reconfirm: int
    writable: bool
    stations: tuple[DecisionStation, ...] = field(default_factory=tuple)
    # 第 1 關選好之後寫在標題底下的：文體、主題、角色（中文名在前，slug 在後）；還沒選的不列
    picks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CardOption:
    card_id: str
    label: str


def reconfirm_total(card_list: list[dict[str, Any]]) -> int:
    """要重確認的關數（分頁上的數字）：這個對話所有編輯卡加起來。"""
    return sum(1 for card in card_list for s in card["stations"] if s["status"] == "reconfirm")


def card_options(card_list: list[dict[str, Any]]) -> list[CardOption]:
    """編輯卡的選單：最新的在最上面，跟報告的版本選單一樣。"""
    ordered = sorted(card_list, key=lambda c: str(c.get("created_at") or ""), reverse=True)
    return [
        CardOption(c["card_id"], (c.get("title") or f"編輯卡 {hm(c.get('created_at'))}").strip())
        for c in ordered
    ]


def _origin_text(card: dict[str, Any], job_labels: dict[str, str]) -> str:
    origin = card.get("origin")
    if not origin:
        return ""
    kind = origin.get("kind")
    if kind == "report_topic":
        label = job_labels.get(str(origin.get("job_id")), "")
        topic = origin.get("topic")
        where = f"報告 {label} 的" if label else "報告的"
        return f"來自{where}研究題材{f' {topic}' if topic else ''}"
    if kind == "material":
        return "來自你給的素材"
    return "來自你的想法"


def _row(
    station: dict[str, Any],
    answer: dict[str, Any],
    submission: dict[str, Any],
    before: dict[str, Any] | None,
    status: str,
) -> DecisionRow:
    questions = _questions(station)
    question = questions.get(str(answer.get("question")), {})
    chosen = answer.get("choice")
    options = question.get("options", [])
    description = ""
    if chosen:
        description = next((o.get("description", "") for o in options if o["label"] == chosen), "")
    unselected = tuple((o["label"], o.get("description", "")) for o in options if o["label"] != chosen)
    source = source_of(submission) if submission.get("source") == "agent" else ""
    was = was_time = ""
    if status == "changed" and before is not None:
        old = answer_text(before)
        if old and old != answer_text(answer):
            was, was_time = old, hm(before.get("_ts"))
    return DecisionRow(
        id=str(answer.get("question", "")),
        chip=str(question.get("header", "")),
        question=str(question.get("question", "")),
        answer=answer_text(answer),
        description=description,
        note=str(answer.get("note") or ""),
        source=source,
        quote=str(submission.get("quote") or "") if source == TYPED_SOURCE else "",
        quote_time=hm(submission.get("ts")) if source == TYPED_SOURCE else "",
        was=was,
        was_time=was_time,
        unselected=unselected,
    )


def _history(station: dict[str, Any]) -> tuple[HistoryEntry, ...]:
    """這一關每一組問過的問題卡（同一次提出的算一組）加上每一次對它的回答，照時間順序。

    決策分頁主體只列現在的決定；舊的組、舊的答案、問了沒送出就換掉的組都在這裡，標出哪個是現在的。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for question in station.get("questions", []):
        groups.setdefault(str(question.get("proposed_at")), []).append(question)
    decision = station.get("decision")
    entries: list[HistoryEntry] = []
    for number, (proposed_at, questions) in enumerate(groups.items(), start=1):
        by_id = {q["id"]: q for q in questions}
        current_set = any(q.get("current") for q in questions)
        answered = [s for s in station.get("submissions", []) if any(a.get("question") in by_id for a in s["answers"])]
        if not answered:
            note = "現在這組，還沒送出" if current_set else "沒送出就換成別組了"
            rows = tuple((q["header"], q["question"], "") for q in questions)
            entries.append(HistoryEntry(f"第 {number} 組問題", hm(proposed_at), note, current_set, rows))
            continue
        for submission in answered:
            rows = tuple(
                (
                    str(by_id.get(a.get("question"), {}).get("header", "")),
                    str(by_id.get(a.get("question"), {}).get("question", "")),
                    answer_text(a),
                )
                for a in submission["answers"]
            )
            is_decision = submission["submission"] == decision
            note = "現在的決定" if is_decision else ("尚待作者提供" if submission.get("pending") else "之前的答案")
            if current_set and not is_decision:
                note += "（現在這組問題）"
            entries.append(HistoryEntry(f"第 {number} 組問題", hm(submission.get("ts")), note, is_decision, rows))
    return tuple(entries) if len(entries) > 1 else ()


def _station_view(station: dict[str, Any], card: dict[str, Any]) -> DecisionStation:
    status = station["status"]
    number = station["station"]
    if status == "not_reached":
        return DecisionStation(number, station["name"], status, "todo", badge="還沒到", badge_tone="pending")
    if status == "asking":
        drafting = tuple(
            DecisionRow(id=q["id"], chip=q["header"], question=q["question"], drafting=True)
            for q in station.get("questions", [])
            if q.get("current")
        )
        return DecisionStation(
            number,
            station["name"],
            status,
            "asking",
            badge="正在問",
            badge_tone="complete",
            rows=drafting,
            asking=True,
            history=_history(station),
        )

    decision = _decision(station)
    complete = _complete(station)
    previous: dict[str, Any] | None = None
    if decision is not None and decision in complete:
        index = complete.index(decision)
        previous = complete[index - 1] if index > 0 else None
    questions = _questions(station)
    rows = []
    for answer in decision["answers"] if decision is not None else []:
        before = None
        if previous is not None:
            question = questions.get(str(answer.get("question")))
            found = _find_answer(previous["answers"], question, questions) if question else None
            if found is not None:
                before = {**found, "_ts": previous.get("ts")}
        rows.append(_row(station, answer, decision or {}, before, status))
    later = tuple(
        s["station"] for s in card["stations"] if s["station"] > number and s["status"] in {"decided", "changed", "kept"}
    )
    shown = DecisionStation(
        number,
        station["name"],
        status,
        "done",
        time=hm((decision or {}).get("ts")),
        rows=tuple(rows),
        reask=bool(station.get("questions")),
        later=later,
        line="・".join(r.answer for r in rows if r.answer),
        history=_history(station),
    )
    if status == "changed":
        return replace(shown, state="revised", badge=f"改過 {len(complete) - 1} 次", badge_icon="undo")
    if status == "kept":
        kept = station.get("kept") or {}
        return replace(
            shown,
            state="reused",
            badge="沿用",
            badge_icon="keep",
            time=hm(kept.get("ts")) or shown.time,
            note=f"你確認原本的答案照用（{shown.time} 答的）。",
        )
    if status == "reconfirm":
        return replace(
            shown,
            state="reconfirm",
            badge="要重確認",
            badge_tone="reconfirm",
            badge_icon="warn",
            note="前面有一關改過，這一關是照改之前的答案選的。",
            keep=True,
        )
    if status == "pending_author":
        return replace(
            shown,
            state="pending",
            badge="等你提供",
            badge_tone="pending",
            note="這一關還缺你自己的例子，不算選好；agent 會在對話裡再問。",
        )
    return shown


def _picks(card: dict[str, Any], persona_name: str | None) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if card.get("genre"):
        found.append(("文體", _named(genre_name(card["genre"]), card["genre"])))
    if card.get("theme"):
        label = default_harness().theme_labels().get(card["theme"], "")
        found.append(("主題", _named(label, card["theme"])))
    if card.get("persona"):
        # 角色的顯示名稱（角色管理，`persona.display_name`）；沒設就是資料夾名
        found.append(("角色", _named(persona_name or "", card["persona"])))
    elif card.get("genre"):
        # 第 1 關選好了、角色卡沒問：寫稿用 me（ADR-0026）
        found.append(("角色", "me（沒問，用預設）"))
    return tuple(found)


def _named(name: str, slug: str) -> str:
    return f"{name}（{slug}）" if name and name != slug else slug


def decision_view(
    card: dict[str, Any], job_labels: dict[str, str], *, persona_name: str | None = None
) -> DecisionView:
    stations = tuple(_station_view(s, card) for s in card["stations"])
    current = card.get("current_station")
    writable = bool(card.get("writable"))
    if writable:
        progress = "六關都選好"
    else:
        progress = f"到第 {current or STATION_COUNT}／{STATION_COUNT} 關"
    return DecisionView(
        card_id=card["card_id"],
        title=(card.get("title") or "").strip(),
        origin=_origin_text(card, job_labels),
        started=hm(card.get("created_at")),
        progress=progress,
        reconfirm=sum(1 for s in stations if s.status == "reconfirm"),
        writable=writable,
        stations=stations,
        picks=_picks(card, persona_name),
    )


# --- 寫稿前的最後確認 -------------------------------------------------------------------


@dataclass(frozen=True)
class WriteConfirm:
    card_id: str
    persona: str
    genre: str
    estimate: str
    cap: str
    # 確認當下這張卡的決策紀錄有幾筆：之後卡又變了（筆數變多），這張確認卡就過期了
    events: int = 0


def write_confirm(
    card: dict[str, Any], *, persona: str, last_rewrite_usd: float | None, cap_usd: float
) -> WriteConfirm:
    """最後確認卡上的字：角色與文體、預估金額、這張工單的上限。

    預估：還沒有可靠的算法（寫稿配方在 #110），先拿這個對話上一篇文章實際花的錢當參考；
    沒寫過文章就老實說沒有可以參考的紀錄，不編數字。上限是費用設定的每張寫稿預算，超過閘道會擋。
    """
    estimate = (
        f"預估 {format_usd(last_rewrite_usd)}（照上一篇文章花的錢）"
        if last_rewrite_usd is not None
        else "預估金額：還沒寫過文章，沒有可以參考的紀錄"
    )
    return WriteConfirm(
        card_id=card["card_id"],
        persona=persona,
        genre=str(card.get("genre") or ""),
        estimate=estimate,
        cap=f"這張工單最多 {format_usd(cap_usd)}",
        events=int(card.get("events") or 0),
    )
