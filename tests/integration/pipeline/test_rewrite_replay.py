"""重播 `analyze` → `rewrite`：真的 Pi＋ef-tools＋閘道重播模式，先分析、再從那張分析工單開重構（#79）。

錄音在 `tests/fixtures/recordings/rewrite-library/`，只有重構那張單；分析那張用 `analyze-library` 的錄音。
重播是看（段, 第幾次, 請求 hash）找回應，跟工單 id 無關，所以測試把兩份錄音併成一份交給閘道。
ADR-0014：錄音會進公開 repo，**種子、來源、作家設定、標題與金句規則全部是為測試寫的虛構短文**
（`tests/fixtures/articles/library-night.md`、`tests/fixtures/web/pages/`、`tests/fixtures/authors/`、
`tests/fixtures/style/`）。創作者自己的標題與金句規則只放 `EF_DATA`，不進錄音。

重錄（改了 rewrite 的 SKILL.md、任務說明、system-prompt、文類定義、自寫設定，或升了 Pi，請求就會變；
會花 Anthropic 的錢）：

    D=<空資料夾>; S=<空資料夾>; mkdir -p $D/style $S && cp -r tests/fixtures/authors $D/
    cp tests/fixtures/style/headline-punchline-system.md $D/style/
    ln -s ~/.config/evidence-forge/secrets/anthropic $S/anthropic   # 真的金鑰只連結、不複製
    # 1. 重播分析（不花錢）
    EF_DATA=$D EF_GATEWAY_MODE=replay EF_GATEWAY_REPLAY_DIR=$PWD/tests/fixtures/recordings/analyze-library \\
        uv run ef new --budget-usd 3 --text "$(cat tests/fixtures/articles/library-night.md)"
    EF_DATA=$D EF_GATEWAY_MODE=replay EF_GATEWAY_REPLAY_DIR=$PWD/tests/fixtures/recordings/analyze-library \\
        uv run ef serve --once
    # 2. 正式跑重構（花錢）；$H 是配方釘在 conftest.py RECORDED_STAGES 那幾行的 harness 複本
    EF_DATA=$D EF_SECRETS=$S EF_GATEWAY_MODE=live uv run ef new --harness $H --from <分析工單> \\
        --persona me --genre research-review --goal "$(cat <GOAL 那一行>)" --budget-usd 0.05
    EF_DATA=$D EF_SECRETS=$S EF_GATEWAY_MODE=live uv run ef serve --harness $H --once --parallel 1 \\
        --port <沒人用的埠>
    # 把重構那張單的 ledger.jsonl 與它指到的 blobs/ 複製進 recordings/rewrite-library/。
    # 複製前先看模型請求裡有沒有工單 id（agent 讀了 job.md 之類的檔），有就重錄

2026-10-15 重錄的這一份（#106：目標文體換成 7 種之一的研究評論，`input/target-genre.md` 是新的通用定義，
沒有轉換說明；T3 第一輪後通用定義改用自己的話寫）：重構段直接用 `fast`（Claude Haiku 5.5），一次就過
（沒有回去修），花 US$0.0065。目標是對話的
agent 在創作者說「寫第 1 個」時會帶的樣子：那個題材的暫定標題與角度；文體照報告的建議文體。

錄音只准用本機與 Haiku（月底額度）；重播用錄音當時的配方等級（`conftest.py`）。
重播時模型回應從錄音回放；開單、放參考資料、程式檢查照樣真的跑。不需要 Ollama，不打任何付費 API。
"""

import os
import re
import shutil
from pathlib import Path

import pytest

from evidence_forge.checks import ChecksReport
from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.harness import Harness, default_harness
from evidence_forge.ledger_models import LedgerEntry

FIXTURES = Path(__file__).parents[2] / "fixtures"
ARTICLE = FIXTURES / "articles" / "library-night.md"
RECORDINGS = FIXTURES / "recordings"
# 創作者說「寫第 1 個」時，對話的 agent 帶的目標：那個題材的暫定標題與角度（chat-system-prompt.md、ADR-0024）
GOAL = (
    "題材 1：夜間開館的人流為什麼先高後降：頭半年的數字該怎麼看。"
    "角度：用創作者自己的觀察說明，為什麼新開的夜間時段要等到第九個月才看得出真正的人流，以及評估時該看哪一條線。"
    "寫給台灣上班族看的短文。"
)
# 那個題材的建議文體（analyze-library 錄音的報告）：研究評論（research-review）
GENRE = "research-review"

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
    rewrite = _new(
        capsys,
        *harness,
        "--from",
        analysis,
        "--persona",
        "me",
        "--genre",
        GENRE,
        "--goal",
        GOAL,
        "--budget-usd",
        "0.5",
    )
    assert main(["serve", *harness, "--once", "--port", str(port)]) == 0
    return ef_data / "jobs" / analysis, ef_data / "jobs" / rewrite


def _ledger(path: Path) -> list[LedgerEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LedgerEntry.model_validate_json(line) for line in lines]


@pytest.mark.promise("FR-RW-01", "FR-RW-02", "FR-RC-01", "NFR-COST-06")
def test_自己寫的短文_重播跑完分析再重構_文章合格_結尾列原文出處(
    jobs: tuple[Path, Path],
) -> None:
    analysis, rewrite = jobs
    job = read_job(rewrite / "job.md").job
    assert (job.recipe, job.parent) == ("rewrite", analysis.name)
    assert job.result is not None and job.result.outcome == "complete", job.result
    assert job.target_genre == GENRE
    # 測試用的作家設定沒有研究評論的文體風格：標未校準
    assert "未校準" in job.result.reason

    article = (rewrite / "stages" / "rewrite" / "1" / "article.md").read_text(
        encoding="utf-8"
    )
    headings = re.findall(r"^## (.+)$", article, flags=re.MULTILINE)
    assert headings[-1] == "參考來源"
    original = ARTICLE.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
    body, references = article.split("## 參考來源", 1)
    # 參考來源第一條是原文；內文不提原文（ADR-0024）
    first = next(line for line in references.splitlines() if line.strip())
    assert original in first
    assert original not in body and "原作者" not in body and "這篇談的是" not in body
    # 文類骨架只是思考順序，不當標題
    skeleton = re.compile(r"^(?:先講|我的)?(?:結論|論據|反方|建議)")
    assert not [h for h in re.findall(r"^#{1,6} (.+)$", body, flags=re.MULTILINE) if skeleton.match(h)]
    # 不掛逐句引用編號
    assert not re.search(r"\[\^?S\d+\]", article)

    [report] = [
        ChecksReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted((rewrite / "checks").glob("checks.v*.json"))
    ]
    assert report.passed
    summaries = [e for e in report.entries if e.check == "C3" and e.location is None]
    # 重疊率對原文與分析段抓過的每一份來源各算一次
    placed = sorted(p.stem for p in (analysis / "input" / "sources").glob("S*.md"))
    assert [e.reason.split("：", 1)[0] for e in summaries] == ["原文", *placed]
    assert len(placed) >= 2
    # 新的兩項文章檢查都跑了，而且過了
    reasons = [e.reason for e in report.entries if e.check == "C5" and e.status == "pass"]
    assert "標題沒有用文類骨架的字" in reasons and "內文沒有提原文的標題與原作者" in reasons


@pytest.mark.promise("FR-RW-01")
def test_重構工單沒有原文全文_拿到的是報告_來源與規則(jobs: tuple[Path, Path]) -> None:
    analysis, rewrite = jobs
    inputs = rewrite / "input"
    assert not (inputs / "content.md").exists()
    assert (inputs / "report.md").read_bytes() == max(
        (analysis / "stages" / "investigate").glob("*/report.md"),
        key=lambda p: int(p.parent.name),
    ).read_bytes()
    assert sorted(p.name for p in (inputs / "sources").iterdir()) == sorted(
        p.name for p in (analysis / "input" / "sources").iterdir()
    )
    assert (inputs / "headline-punchline.md").is_file()


@pytest.mark.promise("NFR-COST-06")
def test_重構段全部從錄音回放_錄音沒有讀原文也沒有上網(
    jobs: tuple[Path, Path],
) -> None:
    _, rewrite = jobs
    ledger = _ledger(rewrite / "ledger.jsonl")
    assert ledger and {(e.kind, e.status) for e in ledger} == {("llm", "cache_hit")}
    recorded = _ledger(RECORDINGS / "rewrite-library" / "ledger.jsonl")
    assert len(ledger) == len(recorded)
    assert {e.kind for e in recorded} == {"llm"}
    assert all(e.tier == "fast" for e in recorded)
