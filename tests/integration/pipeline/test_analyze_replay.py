"""重播分析配方 `analyze`：真的 Pi＋ef-tools＋閘道重播模式，`ef new` → brief → 查證與找題材 → 分析報告（#78）。

錄音在 `tests/fixtures/recordings/analyze-library/`。ADR-0014：錄音與原文會進公開 repo，
所以**種子與「搜到的」文章全部是為測試寫的虛構短文**，跟 `restructure` 的重播用同一組：
種子是 `tests/fixtures/articles/library-night.md`，相關文章在 `tests/fixtures/web/pages/`。
錄音時模型是真的（brief 段本機 qwen3.5:9b、查證與找題材段 Claude Haiku 5.5，也就是 `fast`），網路是假的
（`tests/fixtures/web/fake_web.py`）。重播用錄音當時的配方等級，見 `conftest.py`。
作家設定用 `tests/fixtures/authors/`（分析用不到，但開單照樣放）。

重錄（改了 system-prompt、skill、任務說明、文類定義、自寫文章或升了 Pi，請求就會變；
會花 Anthropic 的錢，錄之前先照 `PIPELINE.md` 量 Windows 的記憶體額度再載本機模型）：

    D=<空資料夾>; S=<空資料夾>; mkdir -p $D $S && cp -r tests/fixtures/authors $D/
    ln -s ~/.config/evidence-forge/secrets/anthropic $S/anthropic   # 真的金鑰只連結、不複製
    echo tvly-fake-xxxx-recording > $S/tavily                        # 假 Tavily 不看金鑰
    uv run python tests/fixtures/web/fake_web.py seed $D
    uv run python tests/fixtures/web/fake_web.py tavily --port 8765 &
    # H=<harness 的複本>：repo 的 harness/ 每一項連結過去，只有 recipes/ 換成 conftest.py 的
    # RECORDED_STAGES 那幾行（釘在本機與 fast，不讓它升到 strong）
    EF_DATA=$D uv run ef new --harness $H --budget-usd 0.1 \\
        --text "$(cat tests/fixtures/articles/library-night.md)"
    EF_DATA=$D EF_SECRETS=$S EF_TAVILY_URL=http://127.0.0.1:8765 EF_GATEWAY_MODE=live \\
        uv run ef serve --harness $H --once --port <沒人用的埠>
    # 把那張工單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/analyze-library/
    # 複製前先看模型請求（kind: llm 的 request blob）裡有沒有工單 id：本機模型偶爾會 read_file
    # 讀 job.md（工單 id、預算每次開單都不同），那份錄音重播對不上，要重錄
    # 錄完卸載本機模型：curl -s localhost:11434/api/generate -d '{"model":"qwen3.5:9b","keep_alive":0}'

2026-10-15 重錄的這一份（#100：報告改成一句話、下一步、新的五節、可以寫的題材）：brief 在本機一次就過
（沒有升級），查證段在 Haiku 一次就過，搜 3 次、列了 4 篇來源，四條關鍵主張都是「查不到」
（種子是虛構的，模型照實寫查不到，沒有用記憶補），三個可以寫的題材各附抓過的文章。花 US$0.0054。

重播時模型回應、搜尋、抓取都從錄音回放；讀寫工單、放來源正文、程式檢查照樣真的跑。
不需要 Ollama，不打任何付費 API，也不連網。
"""

import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from evidence_forge.checks import ChecksReport
from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.harness import Harness, default_harness
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import FetchRecord

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "library-night.md"
AUTHORS = FIXTURES / "authors"
WEB_PAGES = FIXTURES / "web" / "pages"
RECORDING = FIXTURES / "recordings" / "analyze-library"

PI_BIN = default_harness().pi.pi_bin

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(
        not PI_BIN.exists() or shutil.which("node") is None,
        reason="沒有鎖版的 Pi：先用 nvm 的 Node 在 harness/pi/ 跑 npm ci",
    ),
]

# 分析報告的五節，照 PRD §5、ADR-0024 的順序（獨立寫一份，不從程式拿）
FIVE_SECTIONS = ["作者在說什麼", "查證", "作者的看法", "可以寫的題材", "來源清單"]


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
def job_dir(
    ef_data: Path,
    capsys: pytest.CaptureFixture[str],
    port: int,
    recorded_harness: Harness,
) -> Path:
    # 錄音時用 shell 的 `$(cat …)` 開單，結尾換行被吃掉；多一個換行請求就對不上。
    # 不帶 --recipe：分析是 `ef new` 的預設配方。配方用錄音當時的等級（conftest.py）
    text = ARTICLE.read_text(encoding="utf-8").rstrip("\n")
    harness = ["--harness", str(recorded_harness.root)]
    assert main(["new", *harness, "--budget-usd", "3", "--text", text]) == 0
    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["serve", *harness, "--once", "--port", str(port)]) == 0
    return ef_data / "jobs" / job_id


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


@pytest.mark.promise("FR-AN-01", "FR-IV-01", "FR-RC-01", "NFR-COST-06")
def test_自己寫的短文_重播跑完分析_報告五節依序_來源都是抓過的網頁(
    job_dir: Path,
) -> None:
    job = read_job(job_dir / "job.md").job
    assert job.recipe == "analyze"
    assert job.result is not None and job.result.outcome == "complete", job.result

    report = (_last_attempt(job_dir, "investigate") / "report.md").read_text(
        encoding="utf-8"
    )
    headings = re.findall(r"^## (.+)$", report, flags=re.MULTILINE)
    assert headings == FIVE_SECTIONS
    # 第一個標題之前：一句話與下一步；報告裡沒有 K、S 編號；可以寫的題材有編號（ADR-0024）
    opening = report.split("\n## ", 1)[0]
    assert opening.strip() and "▶ 下一步" in opening
    assert not re.search(r"(?<![A-Za-z0-9])[KS]\d+(?![A-Za-z0-9])", report)
    topics = report.split("## 可以寫的題材", 1)[1].split("\n## ", 1)[0]
    numbers = re.findall(r"^### (\d+)\.", topics, flags=re.MULTILINE)
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)] and len(numbers) >= 2
    # 查證每一條直接標結果
    verify = report.split("## 查證", 1)[1].split("\n## ", 1)[0]
    items = [line for line in verify.splitlines() if line.startswith("- ")]
    assert items and all(re.match(r"- 【(有爭議|過時|查不到|有佐證)】", line) for line in items)

    # 兩段的檢查都是執行器自己算的，而且最後一次都過了
    reports = [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted((job_dir / "checks").glob("checks.v*.json"))
    ]
    assert [r.stage for r in reports] == ["brief", "investigate"]
    assert all(r.passed for r in reports)

    # 來源正文由執行器照抓過的網頁放：正文就是自寫文章的正文
    listed = json.loads(
        (_last_attempt(job_dir, "investigate") / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    placed = sorted((job_dir / "input" / "sources").glob("S*.md"))
    assert len(placed) == len(listed["sources"]) >= 2
    bodies = _page_bodies()
    for path in placed:
        body = path.read_text(encoding="utf-8").split("\n\n---\n\n", 1)[1].strip()
        assert body in bodies, path.name

    ledger = _ledger(job_dir / "ledger.jsonl")
    assert len(ledger) == len(_ledger(RECORDING / "ledger.jsonl"))
    assert {entry.status for entry in ledger if entry.kind != "fetch"} == {"cache_hit"}


@pytest.mark.promise("FR-AN-01", "FR-IV-01")
def test_brief_的關鍵主張每一條都有查證結果_可以寫的題材各附抓過的文章(
    job_dir: Path,
) -> None:
    brief = json.loads(
        (_last_attempt(job_dir, "brief") / "brief.json").read_text(encoding="utf-8")
    )
    found = json.loads(
        (_last_attempt(job_dir, "investigate") / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    claims = {claim["id"] for claim in brief["key_claims"]}
    assert 3 <= len(claims) <= 6
    assert {check["claim"] for check in found["fact_checks"]} == claims
    # 種子是虛構的：查不到就寫查不到，不用記憶補（analyze.feature「查不到不等於錯」）
    assert {check["verdict"] for check in found["fact_checks"]} == {"not_found"}
    listed = {source["id"] for source in found["sources"]}
    assert len(found["topics"]) >= 2
    assert all(set(topic["sources"]) <= listed for topic in found["topics"])
    assert brief["topics"]


def _recorded_llm_calls() -> list[LedgerEntry]:
    return [e for e in _ledger(RECORDING / "ledger.jsonl") if e.kind == "llm"]


@pytest.mark.promise("NFR-COST-07")
def test_錄音的帳本_brief_在本機不花錢_沒有升級() -> None:
    brief = [e for e in _recorded_llm_calls() if e.stage == "brief"]
    assert brief
    for entry in brief:
        assert (entry.tier, entry.cost_usd, entry.escalated_from) == ("local", 0, None)


@pytest.mark.promise("NFR-COST-01")
def test_錄音的帳本_查證段用_fast_只有查證段搜尋_沒超過額度() -> None:
    recorded = _recorded_llm_calls()
    assert {entry.stage for entry in recorded} == {"brief", "investigate"}
    for entry in recorded:
        if entry.stage == "investigate":
            assert entry.tier == "fast" and entry.cost_usd > 0
    searches = [e for e in _ledger(RECORDING / "ledger.jsonl") if e.kind == "search"]
    assert searches and all(e.stage == "investigate" for e in searches)
    assert len(searches) <= 6


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
