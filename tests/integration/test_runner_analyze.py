"""分析配方接進執行器（#78、SEAM-05、SEAM-06）：假 Pi、真的閘道、執行器、`analyze` 配方與兩個 skill 的 schema。

- brief 段：`brief.json` 合 schema、`brief.md` 六節都在才合格；本機試滿兩次還不行升 `strong`。
- 查證與找題材段：`report.md` 五節依序都在、`sources.json` 合 schema、查證與題材引用的編號都在清單上、
  清單上的網址都是這一段這一次真的抓過的；全部過了才放來源正文。
- 執行器不靠 skill 名稱決定跑哪些檢查：看這一段宣告的產出（`brief.md`、`report.md`、`sources.json`）。

抓取不連網：假網頁事先放進閘道的抓取快取，`fetch_url` 直接命中。
"""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from runner_world import FAKE_PI_LIB, NOW, World, runner_world

from evidence_forge.checks import ChecksReport
from evidence_forge.gateway.fetch import cache_key
from evidence_forge.models import FetchRecord
from evidence_forge.paths import DataDir

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
FETCHED = ["https://bakery.example/preorder", "https://food.example/waste"]

BRIEF_MD = """## 論點與立場
作者認為改成預約制值得。

## 事實與數據
- 賣剩的麵包少了一半以上（關鍵 K1）

## 觀點
- 其他店也該試試

## 文類
opinion，0.8，有明確立場。

## 主題標籤
預約制、剩食

## 可以延伸的方向
- 別家店改預約制之後的數字
"""
BRIEF_JSON = {
    "genre": {"primary": "opinion", "confidence": 0.8},
    "topics": ["預約制", "剩食"],
    "key_claims": [{"id": "K1", "claim": "改成預約制之後賣剩的麵包少了一半以上"}],
}
REPORT_MD = """# 巷口麵包店改成預約制之後

## 作者在說什麼
作者主張預約制值得。

## 事實與觀點
報廢量少了一半是事實；值得推廣是觀點。

## 查證
- K1 有佐證：另一家店也降了很多 [S2]。

## 研究題材
- 剩食發生的時段 [S3]：補了原文沒有的時段分布。

## 來源清單
- S1 巷口麵包店改成預約制之後
- S2 預約制麵包店一年後，https://bakery.example/preorder
- S3 烘焙業的剩食，https://food.example/waste
"""

PI_BODY = """
if STAGE == "brief":
    ask()
    write("brief.md", BRIEF_MD_BY_TIER.get(TIER, BRIEF_MD))
    write("brief.json", BRIEF_JSON)
else:
    ask()
    for url in FETCH:
        status, _ = call("/tools/fetch_url", {"url": url})
        assert status == 200, status
    write("sources.json", {
        "queries": ["預約制 麵包 報廢"],
        "sources": [
            {"id": "S" + str(i + 2), "url": url, "title": "t", "why": "補數字"}
            for i, url in enumerate(LISTED_BY_ATTEMPT.get(ATTEMPT, LISTED))],
        "fact_checks": FACT_CHECKS or [
            {"claim": "K1", "verdict": "supported", "sources": [CITED]}],
        "topics": [{"title": "剩食的時段", "sources": ["S2"]}],
    })
    write("report.md", REPORT_BY_ATTEMPT.get(ATTEMPT, REPORT_MD))
event(type="agent_end")
"""


def _script(
    *,
    brief_by_tier: dict[str, str] | None = None,
    fetch: list[str] = FETCHED,
    listed: list[str] = FETCHED,
    listed_by_attempt: dict[str, list[str]] | None = None,
    cited: str = "S2",
    report_by_attempt: dict[str, str] | None = None,
    brief_json: dict[str, Any] | None = None,
    fact_checks: list[dict[str, Any]] | None = None,
) -> str:
    return (
        FAKE_PI_LIB
        + f"BRIEF_MD = {BRIEF_MD!r}\nBRIEF_JSON = {brief_json or BRIEF_JSON!r}\n"
        + f"FACT_CHECKS = {fact_checks!r}\n"
        + f"BRIEF_MD_BY_TIER = {brief_by_tier or {}!r}\n"
        + f"FETCH = {fetch!r}\nLISTED = {listed!r}\nCITED = {cited!r}\n"
        + f"LISTED_BY_ATTEMPT = {listed_by_attempt or {}!r}\n"
        + f"REPORT_MD = {REPORT_MD!r}\n"
        + f"REPORT_BY_ATTEMPT = {report_by_attempt or {}!r}\n"
        + PI_BODY
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
    with runner_world(tmp_path, serve) as build:

        def analyzing(script: str) -> World:
            world = build(script)
            _seed_cache(world.data)
            return world

        yield analyzing


def _reports(world: World, job_id: str, stage: str) -> list[ChecksReport]:
    directory = world.data.job_dir(job_id) / "checks"
    found = [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("checks.v*.json"))
    ]
    return [report for report in found if report.stage == stage]


def _attempts(world: World, job_id: str, stage: str) -> list[str]:
    directory = world.data.job_dir(job_id) / "stages" / stage
    return sorted(p.name for p in directory.iterdir() if p.name.isdigit())


@pytest.mark.promise("FR-AN-01", "FR-IV-01", "FR-RC-01")
def test_analyze_跑完兩段_報告與來源清單合格_執行器放好抓過的來源(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script())
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    placed = world.data.job_dir(job_id) / "input" / "sources"
    assert sorted(p.name for p in placed.iterdir()) == ["S2.md", "S3.md"]
    assert (placed / "S3.md").read_text(encoding="utf-8").endswith(
        PAGES["https://food.example/waste"][1] + "\n"
    )
    # 兩段的檢查都是執行器自己跑的，而且都過了
    [brief] = _reports(world, job_id, "brief")
    [investigate] = _reports(world, job_id, "investigate")
    assert brief.passed and investigate.passed
    assert "stages/investigate/1/report.md" in investigate.files


@pytest.mark.promise("FR-AN-01", "NFR-RUN-03", "FR-MD-01")
def test_本機的_brief_兩次都少一節_升到_strong_再試_帳本記升級(
    world_for: Callable[[str], World],
) -> None:
    missing = BRIEF_MD.replace("## 觀點\n", "## 想法\n")
    world = world_for(_script(brief_by_tier={"local": missing}))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert _attempts(world, job_id, "brief") == ["1", "2", "3"]
    brief_calls = [
        (entry.attempt, entry.tier, entry.escalated_from)
        for entry in world.ledger(job_id)
        if entry.stage == "brief"
    ]
    assert brief_calls == [
        (1, "local", None),
        (2, "local", None),
        (3, "strong", "local"),
    ]
    failed = _reports(world, job_id, "brief")[0]
    assert not failed.passed
    assert any("觀點" in entry.reason for entry in failed.entries)


@pytest.mark.promise("FR-IV-01")
def test_報告少了研究題材一節_兩次都一樣就以失敗進待審_寫明少哪一節_不放來源(
    world_for: Callable[[str], World],
) -> None:
    broken = REPORT_MD.replace("## 研究題材\n", "## 延伸閱讀\n")
    world = world_for(_script(report_by_attempt={"1": broken, "2": broken}))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "研究題材" in job.result.reason
    assert not (world.data.job_dir(job_id) / "input" / "sources").exists()


@pytest.mark.promise("FR-IV-01")
def test_報告第一次不合格_第二次合格_來源只在合格那一次放(
    world_for: Callable[[str], World],
) -> None:
    # 來源正文只放一次（只增不改）：要是第一次檢查沒過就先放了，第二次換了來源的順序就放不進去
    broken = REPORT_MD.replace("## 查證\n", "")
    world = world_for(
        _script(
            report_by_attempt={"1": broken},
            listed_by_attempt={"2": list(reversed(FETCHED))},
        )
    )
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert _attempts(world, job_id, "investigate") == ["1", "2"]
    s2 = world.data.job_dir(job_id) / "input" / "sources" / "S2.md"
    assert "網址：https://food.example/waste" in s2.read_text(encoding="utf-8")


@pytest.mark.promise("FR-IV-01")
def test_來源清單有沒抓過的網址_查證段不合格_重試用完以失敗進待審(
    world_for: Callable[[str], World],
) -> None:
    listed = ["https://bakery.example/preorder", "https://elsewhere.example/other"]
    world = world_for(_script(listed=listed))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "elsewhere.example" in job.result.reason
    assert not (world.data.job_dir(job_id) / "input" / "sources").exists()


@pytest.mark.promise("FR-IV-01")
def test_查證引用了清單上沒有的編號_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(cited="S7"))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed"
    assert "S7" in job.result.reason
    report = json.loads(
        (
            world.data.job_dir(job_id) / "stages" / "investigate" / "1" / "sources.json"
        ).read_text(encoding="utf-8")
    )
    assert report["fact_checks"][0]["sources"] == ["S7"]


# --- 報告本身的出處、關鍵主張的查證、有疑慮的排前面（PR #88 第一輪審查）---------------


def _failed_reason(world: World, job_id: str) -> str:
    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "failed", job.result
    return job.result.reason


@pytest.mark.promise("FR-IV-01")
def test_報告內文引用了清單上沒有的編號_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    report = REPORT_MD.replace("另一家店也降了很多 [S2]", "另一家店也降了很多 [S9]")
    world = world_for(_script(report_by_attempt={"1": report, "2": report}))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    assert "S9" in _failed_reason(world, job_id)


@pytest.mark.promise("FR-IV-01")
def test_來源清單一節列了這一段沒抓過的網址_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    # 別張單抓過、在共用快取裡的也不算：這一段這一次沒抓過，就是憑記憶寫的
    report = REPORT_MD + "- 補充，https://elsewhere.example/other\n"
    world = world_for(_script(report_by_attempt={"1": report, "2": report}))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    assert "elsewhere.example" in _failed_reason(world, job_id)
    assert not (world.data.job_dir(job_id) / "input" / "sources").exists()


TWO_CLAIMS = {
    **BRIEF_JSON,
    "key_claims": [
        {"id": "K1", "claim": "賣剩的麵包少了一半以上"},
        {"id": "K2", "claim": "多數客人願意提前一天預約"},
    ],
}


@pytest.mark.promise("FR-IV-01")
def test_brief_有兩條關鍵主張_查證只交一條_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(brief_json=TWO_CLAIMS))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    assert "K2" in _failed_reason(world, job_id)


@pytest.mark.promise("FR-IV-01")
def test_同一條關鍵主張查證兩次_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    checks = [
        {"claim": "K1", "verdict": "not_found", "sources": []},
        {"claim": "K1", "verdict": "supported", "sources": ["S2"]},
    ]
    world = world_for(_script(fact_checks=checks))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    assert "K1" in _failed_reason(world, job_id)


DISPUTED_FIRST: list[dict[str, Any]] = [
    {"claim": "K2", "verdict": "disputed", "sources": ["S2", "S3"]},
    {"claim": "K1", "verdict": "supported", "sources": ["S2"]},
]


@pytest.mark.promise("FR-IV-01")
def test_有爭議與有佐證並存_有爭議的排前面而且附兩邊的來源_合格(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(_script(brief_json=TWO_CLAIMS, fact_checks=DISPUTED_FIRST))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    job = world.read(job_id)
    assert job.result is not None and job.result.outcome == "complete", job.result


@pytest.mark.promise("FR-IV-01")
def test_有佐證的排在有爭議前面_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    world = world_for(
        _script(brief_json=TWO_CLAIMS, fact_checks=list(reversed(DISPUTED_FIRST)))
    )
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    assert "K2" in _failed_reason(world, job_id)


@pytest.mark.promise("FR-IV-01")
def test_有爭議只附一邊的來源_查證段不合格(
    world_for: Callable[[str], World],
) -> None:
    one_side = [{**DISPUTED_FIRST[0], "sources": ["S2"]}, DISPUTED_FIRST[1]]
    world = world_for(_script(brief_json=TWO_CLAIMS, fact_checks=one_side))
    job_id = world.job(recipe="analyze")
    world.runner.run_once()

    # schema 擋下（validate_outputs 的訊息只有 jsonschema 的說明，沒有位置）
    assert "['S2'] is too short" in _failed_reason(world, job_id)
