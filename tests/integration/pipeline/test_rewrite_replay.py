"""重播 `analyze` → 編輯卡 → 寫稿：真的 Pi＋ef-tools＋閘道重播模式（#79、#110）。

先重播分析，再把一張六關都決定好的編輯卡（`card_writing.py`，從報告的第 1 個題材開始、虛構角色 `nightowl`、
研究評論）照畫面按「照這張編輯卡寫稿」的路開寫稿工單（工作台的 `write_from_card`），最後重播寫稿段。
錄音在 `tests/fixtures/recordings/rewrite-library/`，只有寫稿那張單；分析那張用 `analyze-library` 的錄音。
重播是看（段, 第幾次, 請求 hash）找回應，跟工單 id 無關，所以測試把兩份錄音併成一份交給閘道。
ADR-0014：錄音會進公開 repo，**種子、來源、角色資料、標題與金句規則、編輯卡與作者素材全部是為測試寫的虛構短文**
（`tests/fixtures/articles/library-night.md`、`tests/fixtures/web/pages/`、`tests/fixtures/authors/`、
`tests/fixtures/style/`、`card_writing.py`）。創作者自己的資料只放 `EF_DATA`，不進錄音。

重錄（改了 rewrite 的 SKILL.md、任務說明、編輯卡摘要的寫法、角色 fixture、文體定義，或升了 Pi，請求就會變；
會花 Anthropic 的錢；配方釘到本機時也會載入本機模型，先看 Windows 的 commit 餘量至少 23 GB）：

    D=<空資料夾>; S=<空資料夾>; mkdir -p $D/style $S && cp -r tests/fixtures/authors $D/
    cp tests/fixtures/style/headline-punchline-system.md $D/style/
    ln -s ~/.config/evidence-forge/secrets/anthropic $S/anthropic   # 真的金鑰只連結、不複製
    # $H 是配方釘在 conftest.py RECORDED_STAGES 那幾行的 harness 複本；埠避開 8787、8790
    # 1. 重播分析（不花錢）
    EF_DATA=$D EF_GATEWAY_MODE=replay EF_GATEWAY_REPLAY_DIR=$PWD/tests/fixtures/recordings/analyze-library \\
        uv run ef new --harness $H --budget-usd 3 --text "$(cat tests/fixtures/articles/library-night.md)"
    EF_DATA=$D EF_GATEWAY_MODE=replay EF_GATEWAY_REPLAY_DIR=$PWD/tests/fixtures/recordings/analyze-library \\
        uv run ef serve --harness $H --once --port <沒人用的埠>
    # 2. 照編輯卡開寫稿工單（不花錢）
    EF_DATA=$D uv run python tests/card_writing.py <分析工單> $H
    # 3. 正式跑寫稿段（花錢；只准本機與 fast）
    EF_DATA=$D EF_SECRETS=$S EF_GATEWAY_MODE=live uv run ef serve --harness $H --once --parallel 1 \\
        --port <沒人用的埠>
    # 把寫稿那張單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/rewrite-library/。
    # 複製前先看模型請求裡有沒有工單、卡、對話的 id 或本機路徑，有就重錄

2026-10-16 重錄的這一份（#110 T3：照編輯卡寫，只帶那個題材的來源、不帶 brief）：只用 `fast`（Claude Haiku 5.5），
一次就過，共花 US$0.0084。錄的時候 Windows 的 commit 餘量只剩 4.7 GB，所以沒有碰本機模型。
（第一次錄的時候本機 Qwen3.5 9B 兩次都沒過檢查：用了骨架字當標題、把表格也留了圖位。）

錄音只准用本機與 Haiku；重播用錄音當時的配方等級（`conftest.py`）。
重播時模型回應從錄音回放；開單、放參考資料、程式檢查照樣真的跑。不需要 Ollama，不打任何付費 API。
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest
import yaml
from card_writing import GENRE, PERSONA, THEME, open_writing

from evidence_forge.checks import ChecksReport
from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.harness import Harness, default_harness
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "library-night.md"
RECORDINGS = FIXTURES / "recordings"
# 錄音時寫稿工單的預算（`card_writing.py` 當腳本跑時用的）
RECORDED_BUDGET_USD = 0.03

PI_BIN = default_harness().pi.pi_bin

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.skipif(
        not PI_BIN.exists() or shutil.which("node") is None,
        reason="沒有鎖版的 Pi：先用 nvm 的 Node 在 harness/pi/ 跑 npm ci",
    ),
]


def _merged_recording(target: Path) -> Path:
    """`analyze-library` 加 `rewrite-library` 併成一份錄音。"""
    (target / "blobs").mkdir(parents=True)
    lines: list[str] = []
    for name in ("analyze-library", "rewrite-library"):
        source = RECORDINGS / name
        for blob in (source / "blobs").iterdir():
            shutil.copy(blob, target / "blobs" / blob.name)
        lines.extend(
            (source / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        )
    (target / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


@pytest.fixture
def ef_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    (root / "style").mkdir(parents=True)
    shutil.copytree(FIXTURES / "authors", root / "authors")
    shutil.copy(FIXTURES / "style" / "headline-punchline-system.md", root / "style")
    monkeypatch.setenv("EF_DATA", str(root))
    monkeypatch.setenv(
        "EF_GATEWAY_REPLAY_DIR", str(_merged_recording(tmp_path / "recording"))
    )
    assert os.environ["EF_GATEWAY_MODE"] == "replay"
    return root


def _new(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["new", *args]) == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


@pytest.fixture
def jobs(
    ef_data: Path,
    capsys: pytest.CaptureFixture[str],
    port: int,
    recorded_harness: Harness,
) -> tuple[Path, Path]:
    # 錄音時用 shell 的 `$(cat …)` 開單，結尾換行被吃掉；多一個換行請求就對不上。
    # 配方用錄音當時的等級（conftest.py）
    text = ARTICLE.read_text(encoding="utf-8").rstrip("\n")
    harness = ["--harness", str(recorded_harness.root)]
    analysis = _new(capsys, *harness, "--budget-usd", "3", "--text", text)
    assert main(["serve", *harness, "--once", "--port", str(port)]) == 0
    writing = open_writing(DataDir(ef_data), recorded_harness, analysis, RECORDED_BUDGET_USD)
    assert main(["serve", *harness, "--once", "--port", str(port)]) == 0
    return ef_data / "jobs" / analysis, ef_data / "jobs" / writing


def _ledger(path: Path) -> list[LedgerEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LedgerEntry.model_validate_json(line) for line in lines]


def _article(writing: Path) -> str:
    job = read_job(writing / "job.md").job
    assert job.stage is not None
    return (writing / "stages" / "rewrite" / str(job.stage.attempt) / "article.md").read_text(encoding="utf-8")


@pytest.mark.promise("FR-RW-01", "FR-RW-02", "FR-RC-01", "NFR-COST-06")
def test_照編輯卡寫稿_重播跑完_文章照卡的文體主題角色_圖位與參考來源都對(
    jobs: tuple[Path, Path],
) -> None:
    analysis, writing = jobs
    job = read_job(writing / "job.md").job
    assert (job.recipe, job.parent, job.persona, job.target_genre) == ("rewrite", analysis.name, PERSONA, GENRE)
    assert job.card is not None
    assert job.result is not None and job.result.outcome == "complete", job.result

    article = _article(writing)
    front = yaml.safe_load(article.split("---\n", 2)[1])
    assert (front["genre"], front["theme"]) == (GENRE, THEME)
    assert front["title"] and front["description"]
    # 分鏡：一張數據圖有圖位，表格寫成 Markdown 表格
    assert article.count("](./images/fig-1.webp)") == 1 and "fig-2" not in article
    assert re.search(r"^\|.*\|\s*$", article, flags=re.MULTILINE)
    # 參考來源第一條是原文；內文不提原文（研究評論不是 essay，ADR-0024）
    body, references = article.split("## 參考來源", 1)
    original = ARTICLE.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
    first = next(line for line in references.splitlines() if line.strip())
    assert original in first
    assert original not in body and "原作者" not in body
    assert not re.search(r"\[\^?S\d+\]", article)

    reports = [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted((writing / "checks").glob("checks.v*.json"))
    ]
    assert reports[-1].passed
    reasons = [e.reason for e in reports[-1].entries if e.status == "pass"]
    for expected in ("圖文分鏡的每一張圖都有圖位", "角色列的句型都在次數以內"):
        assert expected in reasons
    assert any(r.startswith("front matter 齊全") for r in reasons)


@pytest.mark.promise("FR-RW-01", "FR-RW-02")
def test_寫稿工單拿到編輯卡快照_報告與這個題材的來源_只有這篇用得到的角色資料(jobs: tuple[Path, Path]) -> None:
    analysis, writing = jobs
    inputs = writing / "input"
    assert not (inputs / "content.md").exists()
    assert not (inputs / "brief.md").exists()
    assert (inputs / "card" / "card.md").is_file()
    assert [p.name for p in (inputs / "card" / "materials").iterdir()]
    # 只帶報告第 1 個題材列的來源（分析的 sources.json），本機的上下文才有機會塞得下
    listed = max((analysis / "stages" / "investigate").glob("*/sources.json"), key=lambda p: int(p.parent.name))
    topic = json.loads(listed.read_text(encoding="utf-8"))["topics"][0]["sources"]
    assert sorted(p.stem for p in (inputs / "sources").iterdir()) == sorted(topic)
    assert len(topic) < len(list((analysis / "input" / "sources").iterdir()))
    author = inputs / "author"
    assert sorted(p.name for p in (author / "craft").iterdir()) == ["openings.md", "titles.md"]
    assert (author / "theme.md").is_file() and (author / "lessons.md").is_file()
    # 角色有自己的標題手藝：不再放共用的標題與金句規則
    assert not (inputs / "headline-punchline.md").exists()


@pytest.mark.promise("NFR-COST-06")
def test_寫稿段全部從錄音回放_錄音只用本機與_fast_花費在上限內(
    jobs: tuple[Path, Path],
) -> None:
    _, writing = jobs
    ledger = _ledger(writing / "ledger.jsonl")
    assert ledger and {(e.kind, e.status) for e in ledger} == {("llm", "cache_hit")}
    recorded = _ledger(RECORDINGS / "rewrite-library" / "ledger.jsonl")
    assert len(ledger) == len(recorded)
    assert {e.kind for e in recorded} == {"llm"}
    assert {e.tier for e in recorded} <= {"local", "fast"}
    assert sum(e.cost_usd or 0 for e in recorded) <= 0.05
