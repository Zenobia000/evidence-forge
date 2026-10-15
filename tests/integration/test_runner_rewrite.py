"""重構配方接進執行器（#79、SEAM-05、SEAM-06）：假 Pi、真的閘道、執行器、`rewrite` 配方與 skill 的 schema。

- 從分析工單開重構：執行器把報告、brief、來源正文放進新工單的 `input/`，原文全文不放，
  閘道也不讓這一段用 `fetch_url` 把原文抓回來；
  任務說明給原文出處，不提 `input/content.md`。
- 文章結尾的參考來源沒列原文出處：帶著檢查結果回這一段修，修不好以未完成結束。
- 跟原文、跟來源的逐字重疊只顯示：照抄一大段照樣完成，檢查結果裡是 warn。
- 角色在目標文類沒有校準過的風格：結果上標未校準。
- 再改：新工單拿到上一版文章與一句意見，上一版不動。
- 檢查由執行器自己跑；agent 自己寫的 `checks.json` 不採用。
"""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from card_world import decided_card
from runner_world import FAKE_PI_LIB, NOW, World, runner_world

from evidence_forge import cards, conversations
from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.models import Item, JobResult
from evidence_forge.runner.intake import PastedText, open_job, open_job_from, open_job_from_card

SEED = "# 夜間圖書館\n\n市立圖書館延長開放到半夜之後，晚上來的多半是下班的上班族與準備考試的學生。\n"
SEED_URL = "https://seed.example/night-library"
SOURCE_S2 = "加班的人需要一個安靜又不用消費的地方，咖啡店不是每個人都待得起。"
REPORT = "# 夜間圖書館\n\n## 作者在說什麼\n延長開放值得。\n"

ARTICLE = (
    "# 半夜的圖書館，替誰開燈\n\n"
    "我認為延長開放的價值不在借書量，在於給下班的人一個不用花錢的去處。\n\n"
    "## 參考來源\n\n"
    f"- 王小明，〈夜間圖書館〉，{SEED_URL}\n"
    "- 〈加班的成本〉，https://b.example/2\n"
)
NO_ORIGINAL = ARTICLE.replace(f"- 王小明，〈夜間圖書館〉，{SEED_URL}\n", "")
COPIED = ARTICLE.replace(
    "我認為延長開放的價值不在借書量，在於給下班的人一個不用花錢的去處。",
    "市立圖書館延長開放到半夜之後，晚上來的多半是下班的上班族與準備考試的學生。",
)

PI_BODY = """
ask()
status, _ = call("/tools/read_file", {"path": "input/report.md"})
assert status == 200, status
# 重構工單裡沒有原文全文
status, _ = call("/tools/read_file", {"path": "input/content.md"})
assert status == 404, status
# 也不能上網把原文抓回來：重構段沒有搜尋額度，閘道拒絕 fetch_url
status, _ = call("/tools/fetch_url", {"url": SEED_URL})
assert status == 403, status
if STAGE == "rewrite":
    write("article.md", ARTICLE_BY_ATTEMPT.get(ATTEMPT, ARTICLE))
    write("article.json", {"title": "半夜的圖書館，替誰開燈", "title_candidates": ["半夜的圖書館，替誰開燈"]})
    if SELF_CHECKS:
        write("checks.json", {"entries": [{"check": "C5", "status": "pass", "reason": "我自己說過了"}]})
event(type="agent_end")
"""


def _script(
    article: str = ARTICLE,
    by_attempt: dict[str, str] | None = None,
    self_checks: bool = False,
) -> str:
    return (
        FAKE_PI_LIB
        + f"SEED_URL = {SEED_URL!r}\n"
        + f"ARTICLE = {article!r}\nARTICLE_BY_ATTEMPT = {by_attempt or {}!r}\n"
        + f"SELF_CHECKS = {self_checks!r}\n"
        + PI_BODY
    )


@pytest.fixture
def world_for(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
) -> Iterator[Callable[[str], World]]:
    with runner_world(tmp_path, serve) as build:
        yield build


def _finished(world: World, job_id: str, stage: str, files: dict[str, str]) -> None:
    """當成這張單跑完了：段產出、執行器的檢查結果、狀態改成結束。"""
    job_dir = world.data.job_dir(job_id)
    base = job_dir / "stages" / stage / "1"
    base.mkdir(parents=True)
    for name, text in files.items():
        (base / name).write_text(text, encoding="utf-8")
    write_checks_report(
        job_dir,
        ChecksReport(
            stage=stage,
            attempt=1,
            files=tuple(f"stages/{stage}/1/{name}" for name in files),
            created_at=NOW,
            entries=(CheckEntry(check="C5", status="pass", reason="測試"),),
        ),
    )
    doc = read_job(job_dir / "job.md")
    job = doc.job.model_copy(
        update={"status": "review", "result": JobResult(outcome="complete")}
    )
    write_job(job_dir / "job.md", JobDocument(job=job, notes=doc.notes))


def _analysis(world: World) -> str:
    """一張跑完的分析工單：原文有網址，查證段放了一份來源。角色 me 也準備好。"""
    _with_persona(world)
    job = open_job(
        world.data,
        world.runner.recipes["analyze"],
        PastedText(SEED),
        goal=None,
        now=NOW,
    ).job
    # 當成從網址投入的：素材項目帶網址與作者
    assert job.feed is not None
    item_json = world.data.root / job.feed.item / "item.json"
    item = Item.model_validate_json(item_json.read_bytes())
    item_json.write_text(
        item.model_copy(update={"url": SEED_URL, "author": "王小明"}).model_dump_json(),
        encoding="utf-8",
    )
    sources = world.data.job_dir(job.id) / "input" / "sources"
    sources.mkdir(parents=True)
    (sources / "S2.md").write_text(
        f"來源編號：S2\n標題：加班的成本\n網址：https://b.example/2\n\n---\n\n{SOURCE_S2}\n",
        encoding="utf-8",
    )
    _finished(world, job.id, "investigate", {"report.md": REPORT, "sources.json": "{}"})
    return job.id


def _rewrite(world: World, parent: str, **options: Any) -> str:
    return open_job_from(
        world.data, world.runner.recipes["rewrite"], parent, now=NOW, **options
    ).id


def _reports(world: World, job_id: str) -> list[ChecksReport]:
    directory = world.data.job_dir(job_id) / "checks"
    return [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("checks.v*.json"))
    ]


def _prompts(world: World) -> list[str]:
    return [
        str(call["messages"][0]["content"])
        for call in world.backend_calls
        if call.get("messages")
    ]


def _with_persona(world: World) -> None:
    author = world.data.authors / "me"
    author.mkdir(parents=True, exist_ok=True)
    (author / "voice.md").write_text(
        "---\nid: me\ndefault_genre: essay\n---\n同行交流的口吻。\n", encoding="utf-8"
    )


@pytest.mark.promise("FR-RW-01", "FR-RW-02", "FR-RC-01")
def test_從分析開重構_拿到報告與來源_沒有原文全文_文章合格就完成並標未校準(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script())
    job_id = _rewrite(world, _analysis(world), goal="寫給台灣 PM 看")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    # me 還沒有隨筆的文體風格：結果上標未校準（rewrite.feature）
    assert "未校準" in job.result.reason and "essay" in job.result.reason
    inputs = world.data.job_dir(job_id) / "input"
    assert (inputs / "report.md").read_text(encoding="utf-8") == REPORT
    assert (inputs / "sources" / "S2.md").is_file()
    assert (inputs / "author" / "voice.md").is_file()
    assert not (inputs / "content.md").exists()

    [prompt] = _prompts(world)
    assert "input/content.md" not in prompt
    assert f"原文出處：夜間圖書館，王小明，{SEED_URL}" in prompt

    [report] = _reports(world, job_id)
    assert report.passed
    assert report.files == (
        "stages/rewrite/1/article.md",
        "stages/rewrite/1/article.json",
    )
    overlap = [e for e in report.entries if e.check == "C3" and e.location is None]
    assert [e.reason.split("：", 1)[0] for e in overlap] == ["原文", "S2"]


@pytest.mark.promise("FR-RW-01", "NFR-RUN-03")
def test_參考來源沒列原文_帶著檢查結果回去修_第二次合格就完成(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(by_attempt={"1": NO_ORIGINAL}))
    job_id = _rewrite(world, _analysis(world))
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert [r.passed for r in _reports(world, job_id)] == [False, True]
    # 修的那一次，任務說明帶著上一次沒過的檢查
    fix = _prompts(world)[1]
    assert "自動檢查沒過：C5" in fix and SEED_URL in fix


def _llm_calls(world: World, job_id: str) -> list[tuple[int, str | None, str | None]]:
    return [
        (entry.attempt, entry.tier, entry.escalated_from)
        for entry in world.ledger(job_id)
        if entry.kind == "llm"
    ]


@pytest.mark.promise("FR-RW-01", "FR-MD-01")
def test_一直沒列原文出處_每一級都修到上限才升級_最後一級也沒過以未完成結束(
    world_for: Callable[[str], World],
) -> None:
    # 配方：本機起跑、升級鏈 fast、strong、auto_fix 1。每一級寫一次、修一次，修不好才往上（ADR-0022）
    world = world_for(_script(article=NO_ORIGINAL))
    job_id = _rewrite(world, _analysis(world))
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "incomplete"
    assert "C5" in job.result.reason
    # 沒做完也照樣標未校準
    assert "未校準" in job.result.reason
    assert _llm_calls(world, job_id) == [
        (1, "local", None),
        (2, "local", None),
        (3, "fast", "local"),
        (4, "fast", "local"),
        (5, "strong", "local"),
        (6, "strong", "local"),
    ]


@pytest.mark.promise("FR-RW-01", "FR-MD-01")
def test_本機寫的修不好_升到_fast_重寫就合格_不再往上(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(article=NO_ORIGINAL, by_attempt={"3": ARTICLE}))
    job_id = _rewrite(world, _analysis(world))
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert _llm_calls(world, job_id) == [
        (1, "local", None),
        (2, "local", None),
        (3, "fast", "local"),
    ]
    # 升上來的那一次是重寫，不是修：任務說明不帶上一級的檢查結果
    assert "自動檢查沒過" not in _prompts(world)[2]


@pytest.mark.promise("FR-RW-01")
def test_照抄原文一大段_重疊率只顯示_工單照樣完成(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(article=COPIED))
    job_id = _rewrite(world, _analysis(world))
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    [report] = _reports(world, job_id)
    warned = [e for e in report.entries if e.status == "warn"]
    assert warned and {e.check for e in warned} == {"C3"}
    assert warned[0].reason.startswith("原文：")


@pytest.mark.promise("FR-RW-03")
def test_再改_新工單拿到上一版文章與一句意見_上一版不動(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script())
    v1 = _rewrite(world, _analysis(world))
    world.runner.run_once()
    first = world.data.job_dir(v1) / "stages" / "rewrite" / "1" / "article.md"
    before = first.read_bytes()

    v2 = _rewrite(world, v1, feedback="開頭太長，直接講結論")
    world.runner.run_once()

    job = world.read(v2)
    assert job.result is not None and job.result.outcome == "complete", job.result
    inputs = world.data.job_dir(v2) / "input"
    assert (inputs / "article.md").read_bytes() == before
    assert (inputs / "report.md").read_text(encoding="utf-8") == REPORT
    assert "創作者對上一版的意見：開頭太長，直接講結論" in _prompts(world)[-1]
    assert first.read_bytes() == before


def test_agent_自己寫一份全部通過的_checks_json_執行器不採用(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(article=NO_ORIGINAL, self_checks=True))
    job_id = _rewrite(world, _analysis(world))
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "incomplete"
    stage_dir = world.data.job_dir(job_id) / "stages" / "rewrite" / "1"
    assert json.loads((stage_dir / "checks.json").read_text(encoding="utf-8"))
    assert _reports(world, job_id)[-1].failed_checks == ("C5",)


# --- 照編輯卡寫稿（#110）----------------------------------------------------------------------

CARD_ARTICLE = (
    "---\ntitle: 週三晚上的分館\ndescription: 夜間時段要等九個月才看得出真正的人流\n"
    "genre: explainer\ntheme: tools\n---\n\n"
    "# 週三晚上的分館\n\n我連續三個月在週三晚上去分館數人。\n\n"
    "![白天與晚上的使用者](./images/fig-1.webp)\n\n人流先高後降。\n\n"
    "![九個月的人流](./images/fig-2.webp)\n\n"
    "| 項目 | 做法 |\n| --- | --- |\n| 排班 | 先看九個月 |\n\n"
    "## 參考來源\n\n本文只根據作者自己的經驗與筆記。\n"
)
NO_FRONT = CARD_ARTICLE.split("---\n\n", 1)[1]

CARD_PI_BODY = """
ask()
status, _ = call("/tools/read_file", {"path": "input/card/card.md"})
assert status == 200, status
# 從自己的想法開始：沒有報告、沒有原文
status, _ = call("/tools/read_file", {"path": "input/report.md"})
assert status == 404, status
write("article.md", ARTICLE_BY_ATTEMPT.get(ATTEMPT, ARTICLE))
write("article.json", {"title": "週三晚上的分館", "title_candidates": ["週三晚上的分館"]})
event(type="agent_end")
"""


def _card_script(article: str = CARD_ARTICLE, by_attempt: dict[str, str] | None = None) -> str:
    return (
        FAKE_PI_LIB
        + f"ARTICLE = {article!r}\nARTICLE_BY_ATTEMPT = {by_attempt or {}!r}\n"
        + CARD_PI_BODY
    )


def _card_job(world: World) -> str:
    _with_persona(world)
    cid = conversations.create(world.data, NOW).id
    card_id = decided_card(world.data, cid, genre="explainer", theme="tools")
    return open_job_from_card(
        world.data,
        world.runner.recipes["rewrite"],
        cards.snapshot(world.data, cid, card_id),
        now=NOW,
        conversation=cid,
    ).id


@pytest.mark.promise("FR-RW-01")
def test_照自己的想法寫稿_沒有原文_任務說明沒有原文出處_文章照編輯卡就完成(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_card_script())
    job_id = _card_job(world)
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    [prompt] = _prompts(world)
    assert "原文出處" not in prompt and "input/card/card.md" in prompt
    [report] = _reports(world, job_id)
    reasons = [e.reason for e in report.entries if e.status == "pass"]
    assert "圖文分鏡的每一張圖都有圖位" in reasons
    assert any(r.startswith("front matter 齊全") for r in reasons)
    # 沒有原文：不算跟原文的重疊，也不查原文出處
    assert not [e for e in report.entries if e.check == "C3" and e.reason.startswith("原文")]


@pytest.mark.promise("FR-RW-01", "NFR-RUN-03")
def test_照編輯卡寫的文章少了_front_matter_帶著檢查結果回去修(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_card_script(by_attempt={"1": NO_FRONT}))
    job_id = _card_job(world)
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert [r.passed for r in _reports(world, job_id)] == [False, True]
    assert "front matter" in _prompts(world)[1]
