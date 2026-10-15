"""「照這張編輯卡寫稿」（工作台的 `write_from_card`，ADR-0025 §3.3～§3.4、#110）。

創作者在畫面上按的，只由對話主機叫（閘道的 `/tools/` 一律拒絕）。開單前再查一次：
- 卡是這個對話的，六關都有決定、沒有要重確認（`cards.require_writable`）；
- 卡的事件筆數跟創作者確認時看到的一樣：確認之後又改過就不開，請他重新確認；
- 這個對話同一時間最多 `max_running_jobs` 張單在跑。
開了就記進對話的 `jobs.jsonl`（版本鏈的第 1 版；從報告開始時 `parent` 是那張分析），再通知對話主機。
"""

from pathlib import Path

import pytest
from card_world import NOW, decided_card

from evidence_forge import cards, conversations
from evidence_forge.chat.desk import Desk
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.harness import default_harness
from evidence_forge.models import TIER_ORDER, JobResult
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import PastedText, open_job
from evidence_forge.runner.recipes import load_recipes

RECIPES = load_recipes(default_harness(), TIER_ORDER)


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    (root.authors / "me").mkdir(parents=True)
    (root.authors / "me" / "voice.md").write_text("---\nid: me\n---\n\n聲音。\n", encoding="utf-8")
    return root


@pytest.fixture
def cid(data: DataDir) -> str:
    return conversations.create(data, NOW).id


def _desk(data: DataDir, opened: list[tuple[str, str]] | None = None, running: int = 1) -> Desk:
    return Desk(
        data,
        RECIPES,
        "analyze",
        clock=lambda: NOW,
        budget_usd=0.5,
        on_opened=(lambda c, j: opened.append((c, j))) if opened is not None else None,
        max_running_jobs=running,
    )


def _events(data: DataDir, cid: str, card_id: str) -> int:
    return cards.read_card(data, cid, card_id).events


def _analysis(data: DataDir, cid: str, *, done: bool = True) -> str:
    job_id = open_job(data, RECIPES["analyze"], PastedText("# 夜間圖書館\n\n原文。"), goal=None, now=NOW,
                      conversation=cid).job.id
    conversations.add_job(data, cid, conversations.ConversationJob(job_id=job_id, ts=NOW))
    if done:
        job_dir = data.job_dir(job_id)
        base = job_dir / "stages" / "investigate" / "1"
        base.mkdir(parents=True)
        (base / "report.md").write_text("報告", encoding="utf-8")
        write_checks_report(
            job_dir,
            ChecksReport(stage="investigate", attempt=1, files=("stages/investigate/1/report.md",),
                         created_at=NOW, entries=(CheckEntry(check="C5", status="pass", reason="測試"),)),
        )
        doc = read_job(job_dir / "job.md")
        finished = doc.job.model_copy(update={"status": "done", "result": JobResult(outcome="complete")})
        write_job(job_dir / "job.md", JobDocument(job=finished, notes=doc.notes))
    return job_id


@pytest.mark.promise("FR-RW-01", "FR-IW-03")
def test_照編輯卡寫稿_開一張寫稿單_記進對話之後才通知主機(data: DataDir, cid: str) -> None:
    opened: list[tuple[str, str]] = []
    card_id = decided_card(data, cid, genre="explainer", theme="tools")

    result = _desk(data, opened).write_from_card(cid, card_id, _events(data, cid, card_id))

    job_id = result["job_id"]
    job = read_job(data.job_dir(job_id) / "job.md").job
    assert (job.recipe, job.status, job.card, job.conversation) == ("rewrite", "queued", card_id, cid)
    assert (result["persona"], result["genre"], result["theme"]) == ("me", "explainer", "tools")
    [row] = conversations.jobs(data, cid)
    assert (row.job_id, row.version, row.parent) == (job_id, 1, None)
    assert opened == [(cid, job_id)]


@pytest.mark.promise("FR-RW-01")
def test_從報告的題材開始_版本鏈接在那張分析後面(data: DataDir, cid: str) -> None:
    analysis = _analysis(data, cid)
    card_id = decided_card(data, cid, origin={"kind": "report_topic", "job_id": analysis, "topic": 1})

    result = _desk(data).write_from_card(cid, card_id, _events(data, cid, card_id))

    row = next(r for r in conversations.jobs(data, cid) if r.job_id == result["job_id"])
    assert (row.version, row.parent) == (1, analysis)


def test_確認之後編輯卡又改過_不開單_請創作者重新確認(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    confirmed = _events(data, cid, card_id)
    cards.add_material(data, cid, card_id, "後來又貼的筆記", now=NOW)

    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(cid, card_id, confirmed)

    assert caught.value.status == 409 and "重新確認" in caught.value.message
    assert conversations.jobs(data, cid) == [] and list(data.jobs.iterdir()) == []


def test_卡還不能寫_不開單_說是哪幾關(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    cards.reask_station(data, cid, card_id, 5, now=NOW)

    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(cid, card_id, _events(data, cid, card_id))

    assert caught.value.status == 409 and "第 5 關" in caught.value.message


def test_別的對話的卡_跟沒有這張卡一樣(data: DataDir, cid: str) -> None:
    card_id = decided_card(data, cid)
    other = conversations.create(data, NOW).id

    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(other, card_id, _events(data, cid, card_id))

    assert caught.value.status == 404


def test_對話已經有單在跑_不開新的寫稿單(data: DataDir, cid: str) -> None:
    _analysis(data, cid, done=False)
    card_id = decided_card(data, cid)

    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(cid, card_id, _events(data, cid, card_id))

    assert caught.value.status == 409 and "在跑" in caught.value.message


@pytest.mark.parametrize("bad", [None, "12", True, -1, 1.5])
def test_確認時的筆數要是整數(data: DataDir, cid: str, bad: object) -> None:
    card_id = decided_card(data, cid)
    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(cid, card_id, bad)
    assert caught.value.status == 400


def test_分析交不出報告_不開單_原因不帶資料目錄的完整路徑(data: DataDir, cid: str) -> None:
    analysis = _analysis(data, cid, done=False)
    doc = read_job(data.job_dir(analysis) / "job.md")
    failed = doc.job.model_copy(update={"status": "done", "result": JobResult(outcome="failed", reason="x")})
    write_job(data.job_dir(analysis) / "job.md", JobDocument(job=failed, notes=doc.notes))
    card_id = decided_card(data, cid, origin={"kind": "report_topic", "job_id": analysis, "topic": 1})

    with pytest.raises(ToolRejected) as caught:
        _desk(data).write_from_card(cid, card_id, _events(data, cid, card_id))

    assert caught.value.status == 409 and "report.md" in caught.value.message
    assert str(data.root) not in caught.value.message


def _finish_article(data: DataDir, job_id: str) -> None:
    job_dir = data.job_dir(job_id)
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


def _revise(desk: Desk, cid: str, job_id: str, genre: str | None = None) -> dict[str, object]:
    return desk.open_rewrite(
        conversation_id=cid, from_job=job_id, persona=None, genre=genre, suggested_genre=None,
        goal=None, feedback="開頭改短",
    )


@pytest.mark.promise("FR-RW-03")
def test_照編輯卡寫的文章改稿_卡沒改就開_換文體或卡改過都_409(data: DataDir, cid: str) -> None:
    desk = _desk(data)
    card_id = decided_card(data, cid, genre="explainer", theme="tools")
    job_id = desk.write_from_card(cid, card_id, _events(data, cid, card_id))["job_id"]
    assert isinstance(job_id, str)
    _finish_article(data, job_id)

    with pytest.raises(ToolRejected) as genre_change:
        _revise(desk, cid, job_id, genre="trend")
    assert genre_change.value.status == 409 and "改編輯卡第 1 關" in genre_change.value.message

    revised = _revise(desk, cid, job_id)
    assert revised["status"] == "queued" and revised["genre"] == "explainer"

    cards.reask_station(data, cid, card_id, 2, now=NOW)
    with pytest.raises(ToolRejected) as changed:
        _revise(desk, cid, job_id)
    assert changed.value.status == 409 and "照這張編輯卡寫稿" in changed.value.message
