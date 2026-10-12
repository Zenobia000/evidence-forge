"""重播預設配方 `restructure`：真的 Pi＋ef-tools＋閘道重播模式，`ef new` → 整理 → 補料 → 二創 → 待審（#70）。

錄音在 `tests/fixtures/recordings/restructure-library/`。ADR-0014：錄音與原文會進公開 repo，
所以**種子與「搜到的」文章全部是為測試寫的虛構短文**：種子是 `tests/fixtures/articles/library-night.md`，
相關文章在 `tests/fixtures/web/pages/`。錄音時模型是真的（整理段本機 qwen3.5:9b、補料與二創段
Anthropic strong），網路是假的：`tests/fixtures/web/fake_web.py` 把自寫文章放進抓取快取、
開一個假的 Tavily，不管查什麼都回那幾篇（加一篇抓不到的候選）。作家設定用 `tests/fixtures/authors/`。

重錄（改了 system-prompt、skill、任務說明、文類定義、作家設定範例、自寫文章或升了 Pi，請求就會變；
會花 Anthropic 的錢，錄之前先照 `PIPELINE.md` 量 Windows 的記憶體額度再載本機模型）：

    D=<空資料夾>; S=<空資料夾>; mkdir -p $D $S && cp -r tests/fixtures/authors $D/
    ln -s ~/.config/evidence-forge/secrets/anthropic $S/anthropic   # 真的金鑰只連結、不複製
    echo tvly-fake-xxxx-recording > $S/tavily                        # 假 Tavily 不看金鑰
    uv run python tests/fixtures/web/fake_web.py seed $D
    uv run python tests/fixtures/web/fake_web.py tavily --port 8765 &
    EF_DATA=$D uv run ef new --recipe restructure --budget-usd 3 \
        --text "$(cat tests/fixtures/articles/library-night.md)"
    EF_DATA=$D EF_SECRETS=$S EF_TAVILY_URL=http://127.0.0.1:8765 EF_GATEWAY_MODE=live \
        uv run ef serve --once
    # 把那張工單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/restructure-library/

重播時模型回應、搜尋、抓取都從錄音回放；讀寫工單、放來源正文、C1～C5 照樣真的跑。
不需要 Ollama，不打任何付費 API，也不連網。
"""

import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from evidence_forge.checks import ChecksReport
from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.harness import default_harness
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import FetchRecord

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "library-night.md"
AUTHORS = FIXTURES / "authors"
WEB_PAGES = FIXTURES / "web" / "pages"
RECORDING = FIXTURES / "recordings" / "restructure-library"

PI_BIN = default_harness().pi.pi_bin

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(
        not PI_BIN.exists() or shutil.which("node") is None,
        reason="沒有鎖版的 Pi：先用 nvm 的 Node 在 harness/pi/ 跑 npm ci",
    ),
]


@pytest.fixture
def ef_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    shutil.copytree(AUTHORS, root / "authors")
    monkeypatch.setenv("EF_DATA", str(root))
    monkeypatch.setenv("EF_GATEWAY_REPLAY_DIR", str(RECORDING))
    assert os.environ["EF_GATEWAY_MODE"] == "replay"
    return root


@pytest.fixture
def job_dir(ef_data: Path, capsys: pytest.CaptureFixture[str], port: int) -> Path:
    # 錄音時用 shell 的 `$(cat …)` 開單，結尾換行被吃掉；多一個換行請求就對不上
    text = ARTICLE.read_text(encoding="utf-8").rstrip("\n")
    assert main(["new", "--recipe", "restructure", "--text", text]) == 0
    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["serve", "--once", "--port", str(port)]) == 0
    return ef_data / "jobs" / job_id


def _reports(job_dir: Path) -> list[ChecksReport]:
    return [
        ChecksReport.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((job_dir / "checks").glob("checks.v*.json"))
    ]


def _last_attempt(job_dir: Path, stage: str) -> Path:
    attempts = [p for p in (job_dir / "stages" / stage).iterdir() if p.name.isdigit()]
    return max(attempts, key=lambda p: int(p.name))


def _ledger(path: Path) -> list[LedgerEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LedgerEntry.model_validate_json(line) for line in lines]


def _page_bodies() -> list[str]:
    return [
        path.read_text(encoding="utf-8").split("---\n", 2)[2].strip()
        for path in sorted(WEB_PAGES.glob("*.md"))
    ]


@pytest.mark.promise(
    "FR-RC-01", "FR-RS-01", "FR-VF-01", "FR-DG-01", "FR-CP-01", "NFR-COST-06"
)
def test_自己寫的短文_重播跑完整理_補料與二創_稿子綜合多個來源_C1_到_C5_全部通過(
    job_dir: Path,
) -> None:
    job = read_job(job_dir / "job.md").job
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "complete", job.result

    reports = _reports(job_dir)
    digest = [r for r in reports if r.stage == "digest"]
    compose = [r for r in reports if r.stage == "compose"]
    # 每段最後一次的檢查結果是執行器自己算的，而且全部通過
    assert digest[-1].passed and {e.check for e in digest[-1].entries} == {"C1", "C5"}
    assert compose[-1].passed
    assert {e.check for e in compose[-1].entries} == {"C2", "C3", "C4", "C5"}
    # C1 真的比對了引句、C2 真的比對了事實句，不是因為「沒有東西可查」而通過
    claims = json.loads(
        (_last_attempt(job_dir, "digest") / "claims.json").read_text(encoding="utf-8")
    )
    assert claims["claims"] and all(c["quote"] for c in claims["claims"])
    facts = json.loads(
        (_last_attempt(job_dir, "compose") / "compose.json").read_text(encoding="utf-8")
    )["facts"]
    assert facts

    # 補料段挑的來源，由執行器照抓過的網頁放進 input/sources/：正文就是自寫文章的正文
    listed = json.loads(
        (_last_attempt(job_dir, "research") / "sources.json").read_text(
            encoding="utf-8"
        )
    )["sources"]
    placed = sorted((job_dir / "input" / "sources").glob("S*.md"))
    assert len(placed) == len(listed) >= 2
    bodies = _page_bodies()
    for path in placed:
        body = path.read_text(encoding="utf-8").split("\n\n---\n\n", 1)[1].strip()
        assert body in bodies, path.name

    # 稿子綜合了種子以外的來源（#70 之前只有 [^S1]）
    draft = (_last_attempt(job_dir, "compose") / "draft.md").read_text(encoding="utf-8")
    cited = {sid for sid in ("S1", *(p.stem for p in placed)) if f"[^{sid}]" in draft}
    assert len(cited - {"S1"}) >= 1 and len(cited) >= 2, cited

    ledger = _ledger(job_dir / "ledger.jsonl")
    assert len(ledger) == len(_ledger(RECORDING / "ledger.jsonl"))
    assert {entry.status for entry in ledger if entry.kind != "fetch"} == {"cache_hit"}


@pytest.mark.promise("FR-CP-02", "FR-CP-03", "FR-DG-02")
def test_沒指定目標文類_用作家的預設_沒有風格檔的文類標未校準(job_dir: Path) -> None:
    job = read_job(job_dir / "job.md").job
    assert job.target_genre == "opinion"

    inputs = job_dir / "input"
    assert (inputs / "author" / "voice.md").read_bytes() == (
        AUTHORS / "me" / "voice.md"
    ).read_bytes()
    assert (inputs / "target-genre.md").read_bytes() == (
        default_harness().genres / "opinion.md"
    ).read_bytes()
    assert not (inputs / "author" / "genre-style.md").exists()

    compose = json.loads(
        (_last_attempt(job_dir, "compose") / "compose.json").read_text(encoding="utf-8")
    )
    assert compose["target_genre"] == "opinion"
    assert compose["style_calibrated"] is False

    claims = json.loads(
        (_last_attempt(job_dir, "digest") / "claims.json").read_text(encoding="utf-8")
    )
    assert 0 <= claims["genre"]["confidence"] <= 1


def _recorded_llm_calls() -> list[LedgerEntry]:
    return [e for e in _ledger(RECORDING / "ledger.jsonl") if e.kind == "llm"]


@pytest.mark.promise("NFR-COST-07")
def test_錄音的帳本_整理在本機不花錢_沒有升級() -> None:
    digest = [e for e in _recorded_llm_calls() if e.stage == "digest"]
    assert digest
    for entry in digest:
        assert (entry.tier, entry.cost_usd, entry.escalated_from) == ("local", 0, None)


@pytest.mark.promise("NFR-COST-01")
def test_錄音的帳本_補料與二創暫用_strong_有花錢_沒有升級_只有補料段搜尋() -> None:
    # 補料段用 strong 是暫時偏離 NFR-COST-07（創作者 2026-10-12 決定，S1 評估再比較本機），不標那一條
    recorded = _recorded_llm_calls()
    assert {entry.stage for entry in recorded} == {"digest", "research", "compose"}
    for entry in recorded:
        if entry.stage != "digest":
            assert entry.escalated_from is None
            assert entry.tier == "strong" and entry.cost_usd > 0
    searches = [e for e in _ledger(RECORDING / "ledger.jsonl") if e.kind == "search"]
    assert searches and all(e.stage == "research" for e in searches)


def test_錄音裡沒有真的第三方網頁_抓取與搜尋結果都是自寫的() -> None:
    # ADR-0014：錄音進公開 repo；抓到的、搜到的都只能是 .example 網域的自寫內容
    blobs = BlobStore(RECORDING / "blobs")
    fetched = 0
    for entry in _ledger(RECORDING / "ledger.jsonl"):
        if entry.kind not in ("fetch", "search") or entry.response_hash is None:
            continue
        raw = blobs.get(entry.response_hash)
        assert raw is not None, entry
        if entry.kind == "fetch":
            record = FetchRecord.model_validate_json(raw)
            hosts = [urlsplit(record.url).hostname]
            fetched += record.status == "ok"
        else:
            hosts = [urlsplit(r["url"]).hostname for r in json.loads(raw)["results"]]
        for host in hosts:
            assert host is not None and host.endswith(".example"), host
    assert fetched >= 2
