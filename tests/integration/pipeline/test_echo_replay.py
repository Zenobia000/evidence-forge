"""重播整條流程：真的 Pi＋ef-tools＋閘道重播模式，`ef new` 到待審（NFR-RUN-07 的第一條）。

錄音在 `tests/fixtures/recordings/echo-tracer/`：用 `ef serve` 的 live 模式、本機 qwen3.5:9b
跑 `tests/fixtures/articles/tracer.md`（為測試寫的虛構短文）錄下的帳本與 blobs。
重錄（改了 system-prompt、skill、任務說明或升了 Pi，請求就會變）：

    EF_DATA=<空資料夾> uv run ef new --text "$(cat tests/fixtures/articles/tracer.md)" \
        --goal "用一段話說明這篇在講什麼"
    EF_DATA=<同一個> uv run ef serve --once
    # 把那張工單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/echo-tracer/
重播只回放模型的回應；工具呼叫（讀素材、寫產出）照樣真的經過閘道，所以這條測試
同時證明 Pi 真的只拿到 ef-tools 的五個工具、寫的檔真的落在這一段這一次的目錄。

要先用 nvm 的 Node 在 `harness/pi/` 跑 `npm ci`；沒裝就跳過並說原因（CI 的 pipeline job 在 S0.13）。
不需要 Ollama，也不打任何付費 API。
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.harness import default_harness
from evidence_forge.ledger_models import LedgerEntry

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "tracer.md"
RECORDING = FIXTURES / "recordings" / "echo-tracer"
# 錄音時用的目標；換一個字請求就不同，重播就對不上
GOAL = "用一段話說明這篇在講什麼"

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
    monkeypatch.setenv("EF_DATA", str(root))
    monkeypatch.setenv("EF_GATEWAY_REPLAY_DIR", str(RECORDING))
    # conftest 已把 EF_GATEWAY_MODE 設成 replay；這裡再確認一次，免得錄音以外的回應混進來
    assert os.environ["EF_GATEWAY_MODE"] == "replay"
    return root


def _run_tracer(ef_data: Path, capsys: pytest.CaptureFixture[str], port: int) -> Path:
    # 錄音時是用 shell 的 `ef new --text "$(cat tracer.md)"` 開單，`$(...)` 會吃掉結尾換行；
    # 多一個換行，agent 讀到的素材就不同，第二個請求就對不上錄音
    text = ARTICLE.read_text(encoding="utf-8").rstrip("\n")
    assert main(["new", "--recipe", "echo", "--text", text, "--goal", GOAL]) == 0
    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["serve", "--once", "--port", str(port)]) == 0
    return ef_data / "jobs" / job_id


def _ledger(job_dir: Path) -> list[LedgerEntry]:
    lines = (job_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    return [LedgerEntry.model_validate_json(line) for line in lines]


@pytest.mark.promise("FR-RC-01", "NFR-COST-06", "NFR-KEEP-02")
def test_貼文字開單_真的_Pi_經閘道重播跑完_echo_段_工單進待審且結果完成(
    ef_data: Path, capsys: pytest.CaptureFixture[str], port: int
) -> None:
    job_dir = _run_tracer(ef_data, capsys, port)

    job = read_job(job_dir / "job.md").job
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "complete", job.result
    summary = json.loads(
        (job_dir / "stages" / "echo" / "1" / "summary.json").read_text(encoding="utf-8")
    )
    assert "麵包店" in summary["summary"]
    # 每一次模型呼叫都是從錄音回放的，沒有任何一次轉送到後端
    ledger = _ledger(job_dir)
    recorded = (RECORDING / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger) == len(recorded)
    assert {entry.status for entry in ledger} == {"cache_hit"}


@pytest.mark.promise("NFR-SEC-02")
def test_真的_Pi_送給模型的工具只有_ef_tools_的五個(
    ef_data: Path, capsys: pytest.CaptureFixture[str], port: int
) -> None:
    job_dir = _run_tracer(ef_data, capsys, port)

    ledger = _ledger(job_dir)
    assert ledger, "Pi 一個模型請求都沒送"
    for entry in ledger:
        request = json.loads((ef_data / "blobs" / entry.request_hash).read_bytes())
        # Pi 內建的 bash、edit 等都不在；只有 ef-tools 經閘道的那幾個
        assert sorted(tool["name"] for tool in request["tools"]) == [
            "fetch_url",
            "list_files",
            "read_file",
            "run_script",
            "write_file",
        ]


@pytest.mark.promise("NFR-SEC-02")
def test_Pi_的設定資料夾在資料目錄裡_repo_的範本資料夾沒有多出檔案(
    ef_data: Path, capsys: pytest.CaptureFixture[str], port: int
) -> None:
    template = default_harness().pi.agent_template
    before = sorted(path.name for path in template.iterdir())
    _run_tracer(ef_data, capsys, port)

    assert sorted(path.name for path in template.iterdir()) == before
    assert (ef_data / "run" / "pi-agent" / "models.json").is_file()
