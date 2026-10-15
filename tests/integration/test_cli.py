"""`ef new`、`ef serve` 的指令列（SEAM-09 最薄的一層、FR-RC-01）。

投入的規則本身在 `test_intake.py`；這裡只測指令列接上了它、訊息與結束碼對。
"""

import json
import shutil
from pathlib import Path

import pytest

from evidence_forge.cli import main
from evidence_forge.documents import read_job
from evidence_forge.harness import default_harness

ARTICLE = Path(__file__).parents[1] / "fixtures" / "articles" / "tracer.md"


@pytest.fixture
def ef_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("EF_DATA", str(root))
    return root


@pytest.mark.promise("FR-IN-01", "FR-AN-01")
def test_ef_new_沒指定配方就開分析_寫素材項目並開一張排隊中的工單(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 預設配方是 analyze（#78、#68）：先分析，不是只把原文傳回來的 echo
    text = ARTICLE.read_text(encoding="utf-8")
    assert main(["new", "--text", text, "--goal", "一段話說明重點"]) == 0

    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert (job.status, job.recipe, job.goal) == ("queued", "analyze", "一段話說明重點")
    # 分析配方的查證段有搜尋額度：沒指定 credits 就用配方各段的總和
    assert job.budget.tavily_credits == 24
    item_dir = ef_data / job.feed.item
    assert item_dir.parent == ef_data / "inbox" / "manual"
    assert (item_dir / "content.md").read_text(encoding="utf-8") == text
    item = json.loads((item_dir / "item.json").read_text(encoding="utf-8"))
    assert item["feed"] == "manual"


def test_ef_new_同一段文字只建一份素材項目(ef_data: Path) -> None:
    for goal in ("目標一", "目標二"):
        assert main(["new", "--text", "同一段內容", "--goal", goal]) == 0
    assert len(list((ef_data / "inbox" / "manual").iterdir())) == 1


@pytest.mark.promise("FR-IN-01")
def test_ef_new_上傳檔案_沒填目標用配方的預設目標(
    ef_data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    article = tmp_path / "a.md"
    article.write_text("# 標題\n\n內文", encoding="utf-8")
    assert main(["new", "--recipe", "echo", "--file", str(article)]) == 0

    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert job.goal == "用兩三句話說明這篇文章在講什麼"
    assert (ef_data / job.feed.item / "raw" / "a.md").is_file()


@pytest.mark.promise("FR-IN-01", "FR-CP-03")
def test_ef_new_帶文類與預算(ef_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["new", "--recipe", "echo", "--text", "文章", "--genre", "explainer"]
    args += ["--budget-usd", "1.5"]
    assert main(args) == 0
    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert (job.target_genre, job.budget.usd) == ("explainer", 1.5)
    # echo 配方沒有一段要搜尋：沒指定 credits 就是 0
    assert job.budget.tavily_credits == 0


@pytest.mark.promise("FR-LG-03")
def test_ef_new_沒帶預算就照費用設定的分析預算(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from evidence_forge import cost_settings
    from evidence_forge.cost_settings import CostSettings
    from evidence_forge.paths import DataDir

    assert main(["new", "--text", "設定之前"]) == 0
    before = capsys.readouterr().out.strip().splitlines()[-1]
    cost_settings.save(
        DataDir(ef_data), CostSettings(analysis_budget_usd=2.0), now=datetime(2026, 10, 15, tzinfo=UTC)
    )
    assert main(["new", "--text", "設定之後"]) == 0
    after = capsys.readouterr().out.strip().splitlines()[-1]

    budgets = [read_job(ef_data / "jobs" / j / "job.md").job.budget.usd for j in (before, after)]
    assert budgets == [0.5, 2.0]


@pytest.mark.promise("NFR-COST-01")
def test_ef_new_帶_Tavily_credits(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--text", "要搜尋的文章", "--budget-credits", "7"]) == 0
    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert job.budget.tavily_credits == 7


@pytest.mark.promise("FR-IN-01")
def test_ef_new_同素材同目標_印出已有的那張_不另開(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--text", "同一篇", "--goal", "目標"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["new", "--text", "同一篇", "--goal", " 目標 "]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == first
    assert "已有工單" in captured.err
    assert "同一份配方" in captured.err
    assert len(list((ef_data / "jobs").iterdir())) == 1


@pytest.mark.promise("FR-IN-01", "NFR-SEC-03")
def test_ef_new_網址讀不到_不開單_提示改用_text_或_file(
    ef_data: Path, port: int, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--url", f"http://127.0.0.1:{port}/bills"]) == 1

    err = capsys.readouterr().err
    assert "內網" in err
    assert "--text" in err and "--file" in err
    assert not any((ef_data / "jobs").iterdir())
    lines = (ef_data / "ledger" / "intake.jsonl").read_text(encoding="utf-8")
    assert len(lines.splitlines()) == 1


def test_ef_new_檔案不存在_報錯(
    ef_data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--file", str(tmp_path / "nope.md")]) == 2
    assert "nope.md" in capsys.readouterr().err


@pytest.mark.promise("FR-RC-01")
def test_ef_new_配方不存在就拒絕開單(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--text", "x", "--goal", "y", "--recipe", "nope"]) != 0
    assert "nope" in capsys.readouterr().err
    assert not (ef_data / "jobs").exists() or not any((ef_data / "jobs").iterdir())


@pytest.mark.promise("FR-RC-01")
@pytest.mark.parametrize("command", ["serve", "new"])
def test_有一份配方格式不合_啟動與開單都拒絕並點名(
    ef_data: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    harness = tmp_path / "harness"
    shutil.copytree(default_harness().root / "recipes", harness / "recipes")
    shutil.copytree(default_harness().root / "skills", harness / "skills")
    (harness / "recipes" / "broken.md").write_text(
        "---\nname: broken\ndescription: x\nstages: []\non_feedback: a\n---\n",
        encoding="utf-8",
    )
    args = {
        "serve": ["serve", "--harness", str(harness), "--once"],
        "new": ["new", "--harness", str(harness), "--text", "x", "--goal", "y"],
    }[command]
    assert main(args) != 0
    assert "broken.md 的 stages" in capsys.readouterr().err


@pytest.mark.promise("NFR-RUN-01")
def test_正式執行設了故障注入_ef_serve_不啟動並點名這一項(
    ef_data: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EF_GATEWAY_MODE", "live")
    monkeypatch.setenv("EF_GATEWAY_FAULT", "rate_limit")

    assert main(["serve", "--once"]) == 2
    assert "EF_GATEWAY_FAULT" in capsys.readouterr().err


@pytest.mark.promise("NFR-RUN-01")
def test_資料目錄不可寫_ef_serve_不啟動並點名這一項(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    readonly = tmp_path / "ro"
    readonly.mkdir()
    readonly.chmod(0o555)
    monkeypatch.setenv("EF_DATA", str(readonly / "data"))
    monkeypatch.setenv("EF_GATEWAY_REPLAY_DIR", str(tmp_path / "recording"))
    try:
        assert main(["serve", "--once"]) == 2
    finally:
        readonly.chmod(0o755)
    assert "資料目錄不可寫" in capsys.readouterr().err


def _finished_analysis(ef_data: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """開一張分析工單，當成它跑完了：查證段交了一份過了檢查的報告。"""
    from datetime import UTC, datetime

    from evidence_forge.checks import CheckEntry, ChecksReport, write_checks_report
    from evidence_forge.documents import JobDocument, write_job
    from evidence_forge.models import JobResult

    assert main(["new", "--text", "# 夜間圖書館\n\n原文。"]) == 0
    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    job_dir = ef_data / "jobs" / job_id
    (job_dir / "stages" / "investigate" / "1").mkdir(parents=True)
    for name in ("report.md", "sources.json"):
        (job_dir / "stages" / "investigate" / "1" / name).write_text(
            "x", encoding="utf-8"
        )
    write_checks_report(
        job_dir,
        ChecksReport(
            stage="investigate",
            attempt=1,
            files=(
                "stages/investigate/1/report.md",
                "stages/investigate/1/sources.json",
            ),
            created_at=datetime(2026, 10, 13, tzinfo=UTC),
            entries=(CheckEntry(check="C5", status="pass", reason="測試"),),
        ),
    )
    doc = read_job(job_dir / "job.md")
    done = doc.job.model_copy(
        update={"status": "review", "result": JobResult(outcome="complete")}
    )
    write_job(job_dir / "job.md", JobDocument(job=done))
    (ef_data / "authors" / "me").mkdir(parents=True)
    return job_id


@pytest.mark.promise("FR-RW-01", "FR-RW-02")
def test_ef_new_from_開重構工單_預設配方是_rewrite_帶角色與文體(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = _finished_analysis(ef_data, capsys)
    args = ["new", "--from", parent, "--persona", "me", "--genre", "essay"]
    assert main([*args, "--goal", "寫給 PM"]) == 0

    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert (job.recipe, job.parent, job.persona, job.target_genre, job.goal) == (
        "rewrite",
        parent,
        "me",
        "essay",
        "寫給 PM",
    )


@pytest.mark.promise("FR-RW-02")
def test_ef_new_from_角色不存在_不開單_列出現有的角色(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = _finished_analysis(ef_data, capsys)
    assert main(["new", "--from", parent, "--persona", "ghost"]) == 2
    err = capsys.readouterr().err
    assert "ghost" in err and "me" in err
    assert len(list((ef_data / "jobs").iterdir())) == 1


def test_ef_new_重構配方沒帶_from_就拒絕(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "--recipe", "rewrite", "--text", "文章"]) == 2
    assert "--from" in capsys.readouterr().err
