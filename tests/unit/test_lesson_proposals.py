"""lessons 的提議與同意（`lesson_proposals.py`，ADR-0026 §3.3、決定 G、FR-RW-04、#114）。

- agent 只能提議：提議只記在對話自己的 `lessons-proposed.jsonl`，不碰角色資料夾。
- 創作者同意，才由閘道把一行附加到 `authors/<角色>/lessons.md`；不要什麼都不寫。
- 只增不改；同意兩次只附加一次。
- 角色不存在、text 有換行／HTML 註解標記／太長：拒絕，什麼都不寫。
- 別的對話的提議、不存在的提議一律 403。

只用虛構的角色 `nightowl`；不碰 `data/`，不連網路。
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evidence_forge import confined, conversations, lesson_proposals
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.lessons import latest_lessons
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)
RULE = "第 2 篇｜開頭用了問句 → 開頭直接講結論"
WHY = "創作者說：我不要問句開頭"


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.data = DataDir(tmp_path / "data")
        self.data.ensure()
        self.cid = conversations.create(self.data, NOW).id
        self.other = conversations.create(self.data, NOW).id
        self.folder = self.data.authors / "nightowl"
        self.folder.mkdir(parents=True)
        (self.folder / "voice.md").write_text("夜貓的聲音\n", encoding="utf-8")
        self.lessons = self.folder / "lessons.md"

    def propose(self, text: object = RULE, persona: object = "nightowl", cid: str | None = None) -> lesson_proposals.Written:
        return lesson_proposals.propose(self.data, cid or self.cid, persona, text, WHY, now=NOW)

    def raw(self, cid: str | None = None) -> list[dict[str, object]]:
        path = self.data.conversation_dir(cid or self.cid) / lesson_proposals.PROPOSED_FILE
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _rejected(call: object) -> ToolRejected:
    with pytest.raises(ToolRejected) as caught:
        call()  # type: ignore[operator]
    return caught.value


@pytest.mark.promise("FR-RW-04")
def test_提議只記進對話自己的檔_沒同意之前角色資料夾一個字都沒動(world: World) -> None:
    written = world.propose()

    assert written.created is True
    assert written.proposal.status == "pending"
    assert [(e["type"], e["source"], e["persona"], e["text"]) for e in world.raw()] == [
        ("proposed", "agent", "nightowl", RULE)
    ]
    assert not world.lessons.exists()
    assert sorted(p.name for p in world.folder.iterdir()) == ["voice.md"]
    # 別的對話看不到這筆
    assert lesson_proposals.read_proposals(world.data, world.other) == []


@pytest.mark.promise("FR-RW-04")
def test_同意才寫_附加一行_格式照範本_latest_lessons_讀得到(world: World) -> None:
    proposal = world.propose().proposal
    world.lessons.write_text("# 規則\n\n<!-- 格式說明 -->\n\n- 2026-01-01｜舊的一筆\n", encoding="utf-8")

    approved = lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER)

    assert approved.proposal.status == "approved"
    assert world.lessons.read_text(encoding="utf-8") == (
        "# 規則\n\n<!-- 格式說明 -->\n\n- 2026-01-01｜舊的一筆\n- 2026-10-16｜" + RULE + "\n"
    )
    assert latest_lessons(world.data, "nightowl") == ["2026-01-01｜舊的一筆", "2026-10-16｜" + RULE]
    assert [(e["type"], e["source"]) for e in world.raw()] == [("proposed", "agent"), ("approved", "ui")]


def test_角色還沒有_lessons_md_同意時建立它(world: World) -> None:
    proposal = world.propose().proposal

    lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER)

    assert world.lessons.read_text(encoding="utf-8") == "- 2026-10-16｜" + RULE + "\n"


def test_檔尾沒有換行_先補一個換行_不黏在上一行後面(world: World) -> None:
    proposal = world.propose().proposal
    world.lessons.write_text("- 舊的一筆", encoding="utf-8")

    lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER)

    assert world.lessons.read_text(encoding="utf-8") == "- 舊的一筆\n- 2026-10-16｜" + RULE + "\n"


@pytest.mark.promise("FR-RW-04")
def test_只增不改_同意後原有的內容位元組不變(world: World) -> None:
    before = "# 規則\n\n- 舊的一筆\n- 另一筆\n"
    world.lessons.write_text(before, encoding="utf-8")
    first = world.propose().proposal
    second = world.propose("第 3 篇｜結尾寫了口號 → 結尾收在一個動作").proposal

    lesson_proposals.approve(world.data, world.cid, first.id, now=LATER)
    lesson_proposals.approve(world.data, world.cid, second.id, now=LATER)

    after = world.lessons.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.splitlines()[-2:] == ["- 2026-10-16｜" + RULE, "- 2026-10-16｜第 3 篇｜結尾寫了口號 → 結尾收在一個動作"]


@pytest.mark.promise("FR-RW-04")
def test_同意兩次只附加一次_也只記一筆事件(world: World) -> None:
    proposal = world.propose().proposal

    first = lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER)
    second = lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER + timedelta(minutes=5))

    assert len(first.events) == 1 and second.events == []
    assert second.proposal.status == "approved"
    assert world.lessons.read_text(encoding="utf-8").count(RULE) == 1
    assert [e["type"] for e in world.raw()] == ["proposed", "approved"]


def test_寫進_lessons_之後當機_重來只補記事件_不再附加(world: World) -> None:
    proposal = world.propose().proposal
    # 模擬：那一行已經寫進去了，approved 事件還沒記
    world.lessons.write_text("- 2026-10-16｜" + RULE + "\n", encoding="utf-8")

    approved = lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER)

    assert world.lessons.read_text(encoding="utf-8").count(RULE) == 1
    assert approved.proposal.status == "approved"
    assert world.raw()[-1]["already_present"] is True


@pytest.mark.promise("FR-RW-04")
def test_不要什麼都不寫_冪等_已經不要的不能再同意_已經同意的不能再不要(world: World) -> None:
    rejected_one = world.propose().proposal
    approved_one = world.propose("第 3 篇｜結尾寫了口號 → 結尾收在一個動作").proposal

    first = lesson_proposals.reject(world.data, world.cid, rejected_one.id, now=LATER)
    again = lesson_proposals.reject(world.data, world.cid, rejected_one.id, now=LATER)
    lesson_proposals.approve(world.data, world.cid, approved_one.id, now=LATER)

    assert first.proposal.status == "rejected" and again.events == []
    assert RULE not in (world.lessons.read_text(encoding="utf-8"))
    assert _rejected(lambda: lesson_proposals.approve(world.data, world.cid, rejected_one.id, now=LATER)).status == 409
    assert _rejected(lambda: lesson_proposals.reject(world.data, world.cid, approved_one.id, now=LATER)).status == 409
    assert world.lessons.read_text(encoding="utf-8").count("結尾收在一個動作") == 1


@pytest.mark.promise("FR-RW-04")
def test_別的對話的提議_與不存在的提議_一律_403_什麼都不寫(world: World) -> None:
    proposal = world.propose().proposal

    wrong_conversation = _rejected(lambda: lesson_proposals.approve(world.data, world.other, proposal.id, now=LATER))
    missing = _rejected(lambda: lesson_proposals.approve(world.data, world.cid, "L99", now=LATER))
    cannot_reject = _rejected(lambda: lesson_proposals.reject(world.data, world.other, proposal.id, now=LATER))

    assert (wrong_conversation.status, missing.status, cannot_reject.status) == (403, 403, 403)
    # 不說存不存在：兩種 403 的說法一樣
    assert wrong_conversation.message == missing.message
    assert not world.lessons.exists()
    assert world.raw(world.other) == []
    assert lesson_proposals.read_proposals(world.data, world.cid)[0].status == "pending"


@pytest.mark.promise("FR-RW-04")
@pytest.mark.parametrize("persona", ["nobody", "Nightowl", "me", "../nightowl", "", 7, None])
def test_角色不存在_或名稱不合_不記提議_什麼都不寫(world: World, persona: object) -> None:
    caught = _rejected(lambda: world.propose(persona=persona))

    assert caught.status in (400, 404)
    assert world.raw() == []
    assert not world.lessons.exists()


def test_沒有這個角色的訊息_列出現有的(world: World) -> None:
    caught = _rejected(lambda: world.propose(persona="nobody"))

    assert caught.status == 404
    assert "nightowl" in caught.message


def test_提議之後角色資料夾不見了_同意拒絕_提議留著_什麼都不寫(world: World) -> None:
    proposal = world.propose().proposal
    (world.folder / "voice.md").unlink()
    world.folder.rmdir()

    caught = _rejected(lambda: lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER))

    assert caught.status == 404
    assert not world.folder.exists()
    assert lesson_proposals.read_proposals(world.data, world.cid)[0].status == "pending"


@pytest.mark.promise("FR-RW-04")
@pytest.mark.parametrize(
    "text",
    [
        "第一行\n第二行",
        "第一行\r\n第二行",
        "第一行 第二行",
        "一行\t帶 tab",
        "<!-- 註解開頭 -->規則",
        "規則 --> 後面",
        "",
        "   ",
        "規" * (lesson_proposals.MAX_TEXT + 1),
        123,
    ],
)
def test_text_有換行_註解標記_空的_太長都拒絕_什麼都不寫(world: World, text: object) -> None:
    caught = _rejected(lambda: world.propose(text=text))

    assert caught.status == 400
    assert world.raw() == []
    assert not world.lessons.exists()


def test_text_剛好到上限可以_前後空白會去掉(world: World) -> None:
    exactly = "規" * lesson_proposals.MAX_TEXT

    written = world.propose(text=f"  {exactly}  ")

    assert written.proposal.text == exactly


def test_why_也是一行_有換行或太長就拒絕(world: World) -> None:
    assert _rejected(lambda: lesson_proposals.propose(world.data, world.cid, "nightowl", RULE, "兩\n行", now=NOW)).status == 400
    too_long = "字" * (lesson_proposals.MAX_WHY + 1)
    assert _rejected(lambda: lesson_proposals.propose(world.data, world.cid, "nightowl", RULE, too_long, now=NOW)).status == 400
    assert world.raw() == []


def test_同一行字還在等_再提議拿回同一筆_不多記(world: World) -> None:
    first = world.propose()
    again = world.propose()

    assert again.created is False and again.events == []
    assert again.proposal.id == first.proposal.id
    assert len(world.raw()) == 1


def test_在等的提議太多_409_請創作者先處理(world: World) -> None:
    for n in range(lesson_proposals.MAX_PENDING):
        world.propose(text=f"第 {n} 篇｜問題 → 改法")

    caught = _rejected(lambda: world.propose(text="再一筆"))

    assert caught.status == 409
    assert len(world.raw()) == lesson_proposals.MAX_PENDING


def test_角色資料夾是符號連結_提議與同意都拒絕(world: World, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = world.data.authors / "linked"
    link.symlink_to(outside, target_is_directory=True)

    caught = _rejected(lambda: world.propose(persona="linked"))

    assert caught.status == 403
    assert list(outside.iterdir()) == []


def test_lessons_md_是符號連結_同意拒絕_不寫到連結指到的地方_提議留著(world: World, tmp_path: Path) -> None:
    proposal = world.propose().proposal
    target = tmp_path / "elsewhere.md"
    target.write_text("別人的檔\n", encoding="utf-8")
    world.lessons.symlink_to(target)

    caught = _rejected(lambda: lesson_proposals.approve(world.data, world.cid, proposal.id, now=LATER))

    assert caught.status == 403
    assert target.read_text(encoding="utf-8") == "別人的檔\n"
    assert lesson_proposals.read_proposals(world.data, world.cid)[0].status == "pending"


def test_殘行與壞掉的行跳過_編號接著最大的往下(world: World) -> None:
    world.propose()
    path = world.data.conversation_dir(world.cid) / lesson_proposals.PROPOSED_FILE
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 2, "type": "propo')  # 寫到一半被砍

    second = world.propose(text="第 3 篇｜另一個問題 → 另一個改法")

    assert second.proposal.id == "L2"
    assert [p.id for p in lesson_proposals.read_proposals(world.data, world.cid)] == ["L1", "L2"]


def test_沒有這個對話_404(world: World) -> None:
    assert _rejected(lambda: lesson_proposals.read_proposals(world.data, "c-nope")).status == 404
    assert _rejected(lambda: world.propose(cid="c-nope")).status == 404


# --- confined.append_text（只增不改的附加；不跟隨連結）------------------------------------


def test_append_text_只在檔尾加_檔不存在就建(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    folder.mkdir()

    confined.append_text(folder, "a.md", "一\n")
    confined.append_text(folder, "a.md", "二\n")

    assert (folder / "a.md").read_text(encoding="utf-8") == "一\n二\n"


def test_append_text_資料夾是連結或檔是連結都不寫(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep.md").write_text("原樣\n", encoding="utf-8")
    folder_link = tmp_path / "folder-link"
    folder_link.symlink_to(real, target_is_directory=True)
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.md").symlink_to(real / "keep.md")

    with pytest.raises(confined.FolderChanged):
        confined.append_text(folder_link, "keep.md", "不該寫\n")
    with pytest.raises(confined.EntryRefused):
        confined.append_text(folder, "a.md", "不該寫\n")
    with pytest.raises(ValueError):
        confined.append_text(folder, "../x.md", "不該寫\n")
    assert (real / "keep.md").read_text(encoding="utf-8") == "原樣\n"
