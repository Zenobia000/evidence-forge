"""補料段接進執行器（#70）：假 Pi、真的閘道與執行器。

- 補料段交的 `sources.json` 只是清單；正文由執行器從這一次真的抓過的網頁放進 `input/sources/`。
- 清單裡有沒抓過的網址：這一段這一次不合格，照重試規則走，用完以失敗進待審。
- 二創段的任務說明列出 S1（種子）加 S2…Sn；C2 認這些編號、C3 拿稿子比每一個來源。

抓取不連網：假網頁事先放進閘道的抓取快取（跨工單共用的那一份），`fetch_url` 直接命中。
"""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from runner_world import FAKE_PI_LIB, NOW, World, fake_harness, runner_world

from evidence_forge.checks import ChecksReport
from evidence_forge.gateway.fetch import cache_key
from evidence_forge.harness import default_harness
from evidence_forge.models import FetchRecord, Recipe
from evidence_forge.paths import DataDir

pytestmark = pytest.mark.promise("FR-RS-01", "FR-VF-01")

# 原文（tests/fixtures/articles/tracer.md）裡真的有的一句
REAL_QUOTE = "以前每天大約有兩成的麵包賣不掉"
PAGES = {
    "https://bakery.example/preorder": (
        "預約制麵包店一年後",
        "東區一家麵包店改成預約制滿一年，每天的報廢量從三十個降到八個。",
    ),
    "https://food.example/waste": (
        "烘焙業的剩食",
        "烘焙業的剩食多半發生在打烊前兩小時，折扣能救回的不到一半。",
    ),
    # 在共用快取裡，但這張單的 agent 沒抓過
    "https://elsewhere.example/other": ("別張單抓的", "這一頁是別張單抓的。"),
}

PI_BODY = """
if STAGE == "digest":
    ask()
    write("claims.json", {"claims": [{"claim": "麵包少賣剩", "quote": QUOTE}]})
elif STAGE == "research":
    ask()
    for url in FETCH:
        status, _ = call("/tools/fetch_url", {"url": url})
        assert status == 200, status
    write("sources.json", {"queries": ["預約制 麵包 報廢"], "sources": [
        {"id": "S" + str(i + 2), "url": url, "title": "t", "why": "補數字"}
        for i, url in enumerate(LISTED)]})
else:
    ask()
    write("draft.md", DRAFT)
    write("compose.json", {"facts": [{"fact": f} for f in FACTS]})
event(type="agent_end")
"""

FETCHED = ["https://bakery.example/preorder", "https://food.example/waste"]
DRAFT = (
    "# 麵包店的預約制\n\n"
    "改成預約之後，賣剩的麵包少了一半以上[^S1]。"
    "另一家店做滿一年，報廢從三十個降到八個[^S2]。我認為這值得更多店試試。\n\n"
    "[^S1]: 原文\n[^S2]: 預約制麵包店一年後（https://bakery.example/preorder）\n"
)
FACTS = [
    "改成預約之後，賣剩的麵包少了一半以上[^S1]。",
    "另一家店做滿一年，報廢從三十個降到八個[^S2]。",
]


def _script(
    *,
    fetch: list[str] = FETCHED,
    listed: list[str] = FETCHED,
    draft: str = DRAFT,
    facts: list[str] = FACTS,
) -> str:
    return (
        FAKE_PI_LIB
        + f"QUOTE = {REAL_QUOTE!r}\n"
        + f"FETCH = {fetch!r}\nLISTED = {listed!r}\n"
        + f"DRAFT = {draft!r}\nFACTS = {facts!r}\n"
        + PI_BODY
    )


def _recipe() -> Recipe:
    return Recipe.model_validate(
        {
            "name": "researched",
            "description": "test",
            "stages": [
                {
                    "id": "digest",
                    "skill": "digest",
                    "tier": "local",
                    "output": ["claims.json"],
                },
                {
                    "id": "research",
                    "skill": "research",
                    "tier": "local",
                    "output": ["sources.json"],
                    "tavily_credits": 10,
                },
                {
                    "id": "compose",
                    "skill": "compose",
                    "tier": "local",
                    "output": ["draft.md", "compose.json"],
                },
            ],
            "on_feedback": "compose",
            "max_attempts": 2,
            "auto_fix": 0,
        }
    )


def _seed_cache(data: DataDir) -> None:
    for url, (title, text) in PAGES.items():
        record = FetchRecord(
            url=url,
            final_url=url,
            status="ok",
            fetched_at=NOW,
            expires_at=NOW + timedelta(days=7),
            title=title,
            text=text,
        )
        directory = data.fetch_cache / cache_key(url)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "20261011T040000000000Z.json").write_text(
            record.model_dump_json(), encoding="utf-8"
        )


@pytest.fixture
def world_for(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
) -> Iterator[Callable[[str], World]]:
    repo = default_harness()
    root = fake_harness(
        tmp_path / "harness",
        {
            "digest": {"type": "object", "required": ["claims"]},
            "research": json.loads(
                repo.schema_file("research").read_text(encoding="utf-8")
            ),
            "compose": {"type": "object", "required": ["facts"]},
        },
    )
    with runner_world(tmp_path, serve) as build:

        def researched(script: str) -> World:
            world = build(script, recipes={"researched": _recipe()}, harness_root=root)
            _seed_cache(world.data)
            return world

        yield researched


def _reports(world: World, job_id: str) -> list[ChecksReport]:
    directory = world.data.job_dir(job_id) / "checks"
    return [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("checks.v*.json"))
    ]


def _user_text(call: dict[str, Any]) -> str:
    content = call["messages"][0]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def test_補料段抓過的網頁由執行器放進_input_sources_二創拿到多個來源_檢查認得這些編號(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script())
    job_id = world.job(recipe="researched")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    placed = world.data.job_dir(job_id) / "input" / "sources"
    assert sorted(p.name for p in placed.iterdir()) == ["S2.md", "S3.md"]
    s2 = (placed / "S2.md").read_text(encoding="utf-8")
    assert "網址：https://bakery.example/preorder" in s2
    assert s2.endswith(PAGES["https://bakery.example/preorder"][1] + "\n")

    # 二創段的任務說明列出每個來源；整理段與補料段的說明沒有這一行
    prompts = [_user_text(call) for call in world.backend_calls]
    compose = [p for p in prompts if "「compose」段" in p]
    assert (
        compose
        and (
            "來源（引用編號：正文）：S1 input/content.md、S2 input/sources/S2.md、"
            "S3 input/sources/S3.md"
        )
        in compose[0]
    )
    assert all("來源（引用編號" not in p for p in prompts if p not in compose)

    [report] = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert report.passed, report


def test_清單上有沒抓過的網址_補料段不合格_重試用完以失敗進待審_不放來源(
    world_for: Callable[[str], World],
) -> None:
    # 別張單抓過、還在共用快取裡的網頁也不算：這張單的 agent 沒讀過
    world = world_for(
        _script(
            listed=[
                "https://bakery.example/preorder",
                "https://elsewhere.example/other",
            ]
        )
    )
    job_id = world.job(recipe="researched")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "elsewhere.example" in job.result.reason
    assert "fetch_url" in job.result.reason
    assert not (world.data.job_dir(job_id) / "input" / "sources").exists()
    assert not (world.data.job_dir(job_id) / "stages" / "compose").exists()


def test_C3_拿稿子比所有來源_照抄補料找到的那篇也擋(
    world_for: Callable[[str], World],
) -> None:
    copied = PAGES["https://food.example/waste"][1]
    draft = f"# 剩食\n\n{copied}我認為這是重點。\n\n[^S1]: 原文\n"
    world = world_for(_script(draft=draft, facts=[]))
    job_id = world.job(recipe="researched")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "incomplete"
    [report] = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert "C3" in report.failed_checks


def test_引用編號對不到放進來的來源_C2_擋(world_for: Callable[[str], World]) -> None:
    # 正文照樣引用 S1、S2（綜合了兩個來源），只多一個對不到的 S4
    draft = DRAFT.replace("我認為", "聽說還有店家跟進[^S4]。我認為")
    world = world_for(_script(draft=draft))
    job_id = world.job(recipe="researched")
    world.runner.run_once()

    [report] = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert "C2" in report.failed_checks


def test_補料放了來源_稿子正文只引用種子_C2_擋(
    world_for: Callable[[str], World],
) -> None:
    # 「至少綜合兩個來源」不能只寫在提示裡（skill 規則 1）：只引 S1 的稿子要被 C2 擋下
    draft = DRAFT.replace("[^S2]。", "[^S1]。")
    facts = [fact.replace("[^S2]", "[^S1]") for fact in FACTS]
    world = world_for(_script(draft=draft, facts=facts))
    job_id = world.job(recipe="researched")
    world.runner.run_once()

    [report] = [r for r in _reports(world, job_id) if r.stage == "compose"]
    assert report.failed_checks == ("C2",)
    [failure] = [e for e in report.entries if e.status == "fail"]
    assert "（S1）" in failure.reason
