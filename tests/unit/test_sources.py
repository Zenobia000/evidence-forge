"""補料段的來源（#70）：來源正文由執行器放進 `input/sources/`，不由 agent 寫。

agent 在補料段用 `fetch_url` 抓過的網頁，閘道都記在這張單的帳本（`kind: fetch`）、
結果存在 `blobs/`。補料段交出 `sources.json` 之後，執行器只從這裡取正文：
`sources.json` 列的網址這一次沒抓到過，就算這一次不合格，agent 捏不出一份「來源」。
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.gateway import ledger
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.fetch import request_bytes
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import FetchRecord, Item, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner import sources

pytestmark = pytest.mark.promise("FR-RS-01")

NOW = datetime(2026, 10, 12, 4, 0, tzinfo=UTC)
JOB = "20261012-120000-abcd1234"
STAGE = RecipeStage(
    id="research", skill="research", tier="strong", output=("sources.json",)
)


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.job_dir(JOB) / "input").mkdir(parents=True)
    (data.job_dir(JOB) / "input" / "content.md").write_text(SEED_TEXT, encoding="utf-8")
    return data


SEED_TEXT = "# 夜間圖書館\n\n種子文章的正文，講圖書館晚上開到幾點。\n"
SEED_URL = "https://seed.example/p"


def _seed(url: str | None = SEED_URL) -> Item:
    """種子的素材項目（`item.json`）：網址與內容 hash 跟工單的 content.md 同一份。"""
    return Item(
        id="seed",
        feed="manual",
        url=url,
        title="夜間圖書館",
        fetched_at=NOW,
        content_hash=hashlib.sha256(SEED_TEXT.encode("utf-8")).hexdigest(),
    )


def _fetched(
    data: DataDir,
    url: str,
    *,
    text: str | None = None,
    title: str | None = "標題",
    author: str | None = None,
    final_url: str | None = None,
    stage: str = "research",
    attempt: int = 1,
    status: str = "ok",
) -> None:
    """照閘道的做法記一次抓取：帳本一行，請求與結果存進 blobs/。"""
    record = FetchRecord(
        url=url,
        final_url=final_url or (url if status == "ok" else None),
        status="ok" if status == "ok" else "unavailable",
        reason="" if status == "ok" else "付費牆",
        fetched_at=NOW,
        title=title,
        author=author,
        # 每一頁的正文預設各不相同：同一份正文會被當成同一個來源
        text=(text if text is not None else f"{url} 的正文") if status == "ok" else "",
    )
    blobs = BlobStore(data.blobs)
    ledger.append(
        data.job_dir(JOB),
        LedgerEntry(
            ts=NOW,
            job_id=JOB,
            stage=stage,
            attempt=attempt,
            kind="fetch",
            latency_ms=1,
            status="ok" if status == "ok" else "error",
            request_hash=blobs.put(request_bytes(url)),
            response_hash=blobs.put(record.model_dump_json().encode("utf-8")),
        ),
    )


def _sources_json(data: DataDir, *entries: dict[str, str], attempt: int = 1) -> None:
    directory = data.job_dir(JOB) / "stages" / "research" / str(attempt)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sources.json").write_text(
        json.dumps({"queries": ["q"], "sources": list(entries)}, ensure_ascii=False),
        encoding="utf-8",
    )


def _entry(number: int, url: str) -> dict[str, str]:
    return {"id": f"S{number}", "url": url, "title": f"第 {number} 篇", "why": "補數字"}


def _place(data: DataDir, attempt: int = 1, seed: Item | None = None) -> list[str]:
    return sources.place_sources(data, data.job_dir(JOB), STAGE, attempt, seed=seed)


def test_抓過的網頁照_sources_json_放進_input_sources_附網址與抓取時間(
    data: DataDir,
) -> None:
    _fetched(data, "https://a.example/one", text="第一篇正文", author="王小明")
    _fetched(data, "https://b.example/two", text="第二篇正文", title=None)
    _sources_json(
        data, _entry(2, "https://a.example/one"), _entry(3, "https://b.example/two")
    )

    assert _place(data) == []

    job_dir = data.job_dir(JOB)
    first = (job_dir / "input" / "sources" / "S2.md").read_text(encoding="utf-8")
    assert first.endswith("第一篇正文\n")
    assert "網址：https://a.example/one" in first
    assert "標題：標題" in first
    assert "作者：王小明" in first
    assert f"抓取時間：{NOW.isoformat()}" in first
    # 網頁沒有標題時用 agent 寫的
    second = (job_dir / "input" / "sources" / "S3.md").read_text(encoding="utf-8")
    assert "標題：第 3 篇" in second
    assert "作者" not in second
    assert sources.job_sources(job_dir) == {
        "S1": Path("input/content.md"),
        "S2": Path("input/sources/S2.md"),
        "S3": Path("input/sources/S3.md"),
    }


def test_沒有抓過的網址_這一次不合格_一個檔都不放(data: DataDir) -> None:
    _fetched(data, "https://a.example/one")
    _sources_json(
        data,
        _entry(2, "https://a.example/one"),
        _entry(3, "https://made-up.example/x"),
    )

    problems = _place(data)

    assert len(problems) == 1
    assert "S3" in problems[0] and "made-up.example" in problems[0]
    assert not (data.job_dir(JOB) / "input" / "sources").exists()
    assert sources.job_sources(data.job_dir(JOB)) == {"S1": Path("input/content.md")}


@pytest.mark.parametrize(
    "fetched",
    [
        # 抓了但讀不到（付費牆）：沒有正文可放
        {"status": "unavailable", "attempt": 2},
        # 前一次抓的不算：這一次的 agent 沒讀過
        {"attempt": 1},
        # 別段抓的不算
        {"stage": "digest", "attempt": 2},
    ],
    ids=["讀不到", "前一次", "別段"],
)
def test_只認這一段這一次讀得到的抓取(data: DataDir, fetched: dict[str, Any]) -> None:
    _fetched(data, "https://a.example/one", **fetched)
    _sources_json(data, _entry(2, "https://a.example/one"), attempt=2)

    assert _place(data, attempt=2) != []


def test_網址只差片段_結尾斜線或大小寫的主機_照樣對得上_也認轉址後的網址(
    data: DataDir,
) -> None:
    _fetched(data, "https://A.example/one/")
    _fetched(data, "https://b.example/short", final_url="https://b.example/long")
    _sources_json(
        data,
        _entry(2, "https://a.example/one#part"),
        _entry(3, "https://b.example/long"),
    )

    assert _place(data) == []


def test_編號要從_S2_依序_網址不能重複_不能是種子本身(data: DataDir) -> None:
    for url in ("https://a.example/1", "https://b.example/2", "https://seed.example/p"):
        _fetched(data, url)

    _sources_json(data, _entry(3, "https://a.example/1"))
    assert any("S2" in p for p in _place(data))

    _sources_json(
        data,
        _entry(2, "https://a.example/1"),
        _entry(3, "https://a.example/1"),
        attempt=2,
    )
    _fetched(data, "https://a.example/1", attempt=2)
    assert any("重複" in p for p in _place(data, attempt=2))

    _sources_json(data, _entry(2, "https://seed.example/p"), attempt=3)
    _fetched(data, "https://seed.example/p", attempt=3)
    assert any("種子" in p for p in _place(data, attempt=3, seed=_seed()))
    assert not (data.job_dir(JOB) / "input" / "sources").exists()


def test_重開時同樣的來源再放一次沿用_內容不同就不合格(data: DataDir) -> None:
    _fetched(data, "https://a.example/one", text="第一版")
    _sources_json(data, _entry(2, "https://a.example/one"))
    assert _place(data) == []
    # 當機重開、同一次重跑：一樣的正文，沿用
    assert _place(data) == []

    _fetched(data, "https://b.example/other", text="另一篇", attempt=2)
    _sources_json(data, _entry(2, "https://b.example/other"), attempt=2)
    problems = _place(data, attempt=2)

    assert any("S2" in p for p in problems)
    placed = data.job_dir(JOB) / "input" / "sources" / "S2.md"
    assert placed.read_text(encoding="utf-8").endswith("第一版\n")


def test_同一份抓取_要求的網址與轉址後的網址不能列成兩個來源(data: DataDir) -> None:
    _fetched(data, "https://b.example/short", final_url="https://b.example/long")
    _sources_json(
        data, _entry(2, "https://b.example/short"), _entry(3, "https://b.example/long")
    )

    problems = _place(data)

    assert len(problems) == 1 and "S3" in problems[0] and "S2" in problems[0]
    assert not (data.job_dir(JOB) / "input" / "sources").exists()


def test_短網址轉到種子_算種子本身(data: DataDir) -> None:
    # 正文跟種子不同（網頁改版過），只靠轉址後的網址認出來
    _fetched(data, "https://sho.rt/abc", final_url=SEED_URL)
    _sources_json(data, _entry(2, "https://sho.rt/abc"))

    problems = _place(data, seed=_seed())

    assert len(problems) == 1 and "種子" in problems[0]


def test_清單上的網址抓回來正文跟種子相同_算種子本身(data: DataDir) -> None:
    # 網址跟種子不一樣（帶追蹤參數、鏡像站），但抓回來的正文就是種子那一篇
    _fetched(data, "https://mirror.example/library?ref=feed", text=SEED_TEXT)
    _sources_json(data, _entry(2, "https://mirror.example/library?ref=feed"))

    problems = _place(data, seed=_seed())

    assert len(problems) == 1 and "種子" in problems[0]
    assert not (data.job_dir(JOB) / "input" / "sources").exists()


def test_種子是貼上的文字_沒有網址_照樣用正文認出來(data: DataDir) -> None:
    _fetched(data, "https://a.example/same", text=SEED_TEXT)
    _sources_json(data, _entry(2, "https://a.example/same"))

    assert any("種子" in p for p in _place(data, seed=_seed(url=None)))


def test_種子的素材項目從_item_json_讀_讀不到是_None(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    item_dir = data.root / "inbox" / "manual" / "abc"
    item_dir.mkdir(parents=True)
    item = Item(
        id="abc",
        feed="manual",
        url="https://seed.example/p",
        title="種子",
        fetched_at=NOW,
        content_hash="0" * 64,
    )
    (item_dir / "item.json").write_text(item.model_dump_json(), encoding="utf-8")

    assert sources.seed_item(data, "inbox/manual/abc") == item
    assert sources.seed_item(data, "inbox/manual/missing") is None
