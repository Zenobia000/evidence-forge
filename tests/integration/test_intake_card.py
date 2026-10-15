"""照編輯卡開寫稿工單（`intake.open_job_from_card`，ADR-0025 §3.4、#110）。

- 工單只讀開單當下的快照：`input/card/` 放編輯卡、決策紀錄、寫稿讀的摘要與作者素材（照 sha256 放）。
- 從報告的題材開始：接著那張分析工單做（報告與來源由執行器帶過來），素材項目沿用它的。
- 從自己的想法或作者素材開始：沒有上一張單，也沒有原文（素材項目是空的）。
- 開單前就擋：卡還不能寫、角色不存在、分析交不出報告、作者素材的檔壞了；擋下就不留工單資料夾。
"""

import hashlib
from pathlib import Path

import pytest
from card_world import EXAMPLE, NOW, decided_card

from evidence_forge import cards, conversations
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.harness import default_harness
from evidence_forge.models import TIER_ORDER, JobResult
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import CardChanged, PastedText, open_job, open_job_from, open_job_from_card
from evidence_forge.runner.recipes import load_recipes
from evidence_forge.ui.format import GENRE_NAME

RECIPES = load_recipes(default_harness(), TIER_ORDER)
ANALYZE, REWRITE = RECIPES["analyze"], RECIPES["rewrite"]
NOTES = "週三晚上 19:00～21:00：第一週 31 人、第五週 18 人"


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    for name in ("me", "nightowl"):
        (root.authors / name).mkdir(parents=True)
        (root.authors / name / "voice.md").write_text("---\nid: x\n---\n\n測試用的聲音。\n", encoding="utf-8")
    return root


@pytest.fixture
def cid(data: DataDir) -> str:
    return conversations.create(data, NOW).id


def _open(data: DataDir, cid: str, card_id: str) -> str:
    taken = cards.snapshot(data, cid, card_id)
    return open_job_from_card(
        data, REWRITE, taken, now=NOW, budget_usd=0.5, conversation=cid, allowed_genres=GENRE_NAME
    ).id


def _analysis(data: DataDir, cid: str, *, report: bool = True) -> str:
    job_id = open_job(data, ANALYZE, PastedText("# 夜間圖書館\n\n原文全文。"), goal=None, now=NOW,
                      conversation=cid).job.id
    conversations.add_job(data, cid, conversations.ConversationJob(job_id=job_id, ts=NOW))
    job_dir = data.job_dir(job_id)
    if report:
        base = job_dir / "stages" / "investigate" / "1"
        base.mkdir(parents=True)
        (base / "report.md").write_text("報告", encoding="utf-8")
        write_checks_report(
            job_dir,
            ChecksReport(stage="investigate", attempt=1, files=("stages/investigate/1/report.md",),
                         created_at=NOW, entries=(CheckEntry(check="C5", status="pass", reason="測試"),)),
        )
    doc = read_job(job_dir / "job.md")
    done = doc.job.model_copy(update={"status": "done", "result": JobResult(outcome="complete")})
    write_job(job_dir / "job.md", JobDocument(job=done, notes=doc.notes))
    return job_id


@pytest.mark.promise("FR-RW-01", "FR-IW-03")
def test_從自己的想法開始_寫稿工單帶著編輯卡快照_沒有上一張單也沒有原文(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, persona="nightowl", genre="explainer", theme="tools", material=NOTES)

    job_id = _open(data, cid, card_id)

    job_dir = data.job_dir(job_id)
    job = read_job(job_dir / "job.md").job
    assert (job.recipe, job.status, job.parent, job.feed, job.card) == ("rewrite", "queued", None, None, card_id)
    assert (job.persona, job.target_genre, job.conversation) == ("nightowl", "explainer", cid)
    card = cards.read_card(data, cid, card_id)
    assert (job_dir / "input" / "card" / "card.md").read_text(encoding="utf-8") == cards.writing_brief(card)
    assert EXAMPLE in (job_dir / "input" / "card" / "card.md").read_text(encoding="utf-8")
    assert cards.CardState.model_validate_json((job_dir / "input" / "card" / "card.json").read_bytes()) == card
    assert len((job_dir / "input" / "card" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()) == card.events
    digest = hashlib.sha256(NOTES.encode()).hexdigest()
    assert (job_dir / "input" / "card" / "materials" / digest).read_text(encoding="utf-8") == NOTES
    assert not (job_dir / "input" / "content.md").exists()


@pytest.mark.promise("FR-RW-01")
def test_快照開單之後再改編輯卡_已經開出去的單不受影響(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    job_id = _open(data, cid, card_id)
    before = (data.job_dir(job_id) / "input" / "card" / "card.md").read_bytes()

    cards.reask_station(data, cid, card_id, 2, now=NOW)

    assert (data.job_dir(job_id) / "input" / "card" / "card.md").read_bytes() == before


@pytest.mark.promise("FR-RW-01")
def test_從報告的題材開始_接著那張分析做_沿用它的素材項目(data: DataDir, cid: str) -> None:
    analysis = _analysis(data, cid)
    card_id = decided_card(data, cid, origin={"kind": "report_topic", "job_id": analysis, "topic": 2})

    job = read_job(data.job_dir(_open(data, cid, card_id)) / "job.md").job

    parent = read_job(data.job_dir(analysis) / "job.md").job
    assert (job.parent, job.feed) == (analysis, parent.feed)


def test_分析交不出報告_不開單也不留資料夾(data: DataDir, cid: str) -> None:
    analysis = _analysis(data, cid, report=False)
    card_id = decided_card(data, cid, origin={"kind": "report_topic", "job_id": analysis, "topic": 1})
    before = sorted(p.name for p in data.jobs.iterdir())

    with pytest.raises(ValueError, match="report.md"):
        _open(data, cid, card_id)

    assert sorted(p.name for p in data.jobs.iterdir()) == before


def test_卡還不能寫_不開單(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    cards.reask_station(data, cid, card_id, 4, now=NOW)

    with pytest.raises(ValueError, match="第 4 關"):
        _open(data, cid, card_id)
    assert list(data.jobs.iterdir()) == []


def test_角色不存在_不開單(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, persona="nightowl")
    (data.authors / "nightowl" / "voice.md").unlink()
    (data.authors / "nightowl").rmdir()

    with pytest.raises(ValueError, match="nightowl"):
        _open(data, cid, card_id)
    assert list(data.jobs.iterdir()) == []


def _written(data: DataDir, cid: str, card_id: str) -> str:
    """照卡開一張寫稿單，當成寫好了（過了檢查的文章、結束）。"""
    first = _open(data, cid, card_id)
    job_dir = data.job_dir(first)
    base = job_dir / "stages" / "rewrite" / "1"
    base.mkdir(parents=True)
    (base / "article.md").write_text("# 第一版", encoding="utf-8")
    write_checks_report(
        job_dir,
        ChecksReport(stage="rewrite", attempt=1, files=("stages/rewrite/1/article.md",), created_at=NOW,
                     entries=(CheckEntry(check="C5", status="pass", reason="測試"),)),
    )
    doc = read_job(job_dir / "job.md")
    done = doc.job.model_copy(update={"status": "done", "result": JobResult(outcome="complete")})
    write_job(job_dir / "job.md", JobDocument(job=done, notes=doc.notes))
    return first


@pytest.mark.promise("FR-RW-03")
def test_再改照自己的想法寫的文章_沒有素材項目也開得出來_拿一份新的快照(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, material=NOTES)
    first = _written(data, cid, card_id)

    again = open_job_from(data, REWRITE, first, now=NOW, feedback="開頭改短", conversation=cid)

    assert (again.parent, again.feed, again.card, again.feedback) == (first, None, card_id, "開頭改短")
    # ADR-0025 §3.4：改稿也是新工單、新快照（卡沒變，內容跟上一版的一樣）
    card_dir = data.job_dir(again.id) / "input" / "card"
    assert (card_dir / "card.md").read_text(encoding="utf-8") == cards.writing_brief(cards.read_card(data, cid, card_id))
    digest = hashlib.sha256(NOTES.encode()).hexdigest()
    assert (card_dir / "materials" / digest).read_text(encoding="utf-8") == NOTES


@pytest.mark.promise("FR-RW-03")
def test_文章寫好之後編輯卡又改過_改稿不開_請創作者照新的卡再寫(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    first = _written(data, cid, card_id)
    cards.reask_station(data, cid, card_id, 2, now=NOW)
    before = sorted(p.name for p in data.jobs.iterdir())

    with pytest.raises(CardChanged, match="照這張編輯卡寫稿"):
        open_job_from(data, REWRITE, first, now=NOW, feedback="開頭改短", conversation=cid)
    assert sorted(p.name for p in data.jobs.iterdir()) == before


def test_照編輯卡寫的文章改稿不能換文體_要改編輯卡第_1_關(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, genre="research-review")
    first = _written(data, cid, card_id)

    with pytest.raises(CardChanged, match="改編輯卡第 1 關"):
        open_job_from(data, REWRITE, first, now=NOW, genre="explainer", feedback="改成教學", conversation=cid)
    # 文體一樣不算換
    same = open_job_from(data, REWRITE, first, now=NOW, genre="research-review", feedback="改短", conversation=cid)
    assert same.target_genre == "research-review"


def test_作者素材的檔不見了_不開單_說是哪一份(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, material=NOTES)
    cards.material_path(data, cid, card_id, "M1").unlink()

    with pytest.raises(ValueError, match="M1"):
        _open(data, cid, card_id)
    assert list(data.jobs.iterdir()) == []


def test_作者素材的檔壞了_不開單(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid, material=NOTES)
    stored = cards.material_path(data, cid, card_id, "M1")
    stored.write_text("被改過的內容", encoding="utf-8")

    with pytest.raises(ValueError, match="M1"):
        _open(data, cid, card_id)
    assert list(data.jobs.iterdir()) == []
