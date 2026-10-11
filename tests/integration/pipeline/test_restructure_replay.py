"""重播預設配方 `restructure`：真的 Pi＋ef-tools＋閘道重播模式，`ef new` → 整理 → 二創 → 待審（S0.8）。

錄音在 `tests/fixtures/recordings/restructure-library/`：用 `ef serve` 的 live 模式跑
`tests/fixtures/articles/library-night.md`（為測試寫的虛構短文，ADR-0014：錄音與原文會進公開 repo）。
整理段與二創段都用本機 qwen3.5:9b（MVP 暫用 local，改回 strong 見 #63）。作家設定用
`tests/fixtures/authors/`（同樣是為測試寫的），不用創作者自己的 `voice.md`。

重錄（改了 system-prompt、skill、任務說明、文類定義、作家設定範例或升了 Pi，請求就會變；二創改回 strong 後會花錢）：

    D=<空資料夾>; mkdir -p $D && cp -r tests/fixtures/authors $D/
    EF_DATA=$D uv run ef new --recipe restructure --text "$(cat tests/fixtures/articles/library-night.md)"
    EF_DATA=$D EF_GATEWAY_MODE=live uv run ef serve --once
    # 把那張工單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/restructure-library/

重播只回放模型的回應；工具呼叫（讀 `input/`、寫產出）照樣真的經過閘道，C1～C5 由執行器照樣真的跑。
不需要 Ollama，也不打任何付費 API。
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from evidence_forge.checks import ChecksReport
from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.harness import default_harness
from evidence_forge.ledger_models import LedgerEntry

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "library-night.md"
AUTHORS = FIXTURES / "authors"
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


@pytest.mark.promise("FR-RC-01", "FR-VF-01", "FR-DG-01", "FR-CP-01", "NFR-COST-06")
def test_自己寫的短文_重播跑完整理與二創_執行器的硬檢查_C1_到_C5_全部通過(
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
    assert "[^S1]" in (_last_attempt(job_dir, "compose") / "draft.md").read_text(
        encoding="utf-8"
    )

    ledger = _ledger(job_dir / "ledger.jsonl")
    assert len(ledger) == len(_ledger(RECORDING / "ledger.jsonl"))
    assert {entry.status for entry in ledger} == {"cache_hit"}


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


@pytest.mark.promise("NFR-COST-07")
def test_錄音的帳本_兩段都用本機_不花錢_沒有升級() -> None:
    # MVP 的二創暫用 local（#63 改回 strong 時，這條改成二創段 strong 且金額大於 0）
    recorded = [e for e in _ledger(RECORDING / "ledger.jsonl") if e.kind == "llm"]
    stages = {entry.stage for entry in recorded}
    assert stages == {"digest", "compose"}
    for entry in recorded:
        assert (entry.tier, entry.cost_usd, entry.escalated_from) == ("local", 0, None)
