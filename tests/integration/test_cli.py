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


def test_ef_new_寫素材項目並開一張排隊中的工單(
    ef_data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = ARTICLE.read_text(encoding="utf-8")
    assert main(["new", "--text", text, "--goal", "一段話說明重點"]) == 0

    job_id = capsys.readouterr().out.strip().splitlines()[-1]
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert (job.status, job.recipe, job.goal) == ("queued", "echo", "一段話說明重點")
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
    assert main(["new", "--file", str(article)]) == 0

    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert job.goal == "用兩三句話說明這篇文章在講什麼"
    assert (ef_data / job.feed.item / "raw" / "a.md").is_file()


@pytest.mark.promise("FR-IN-01", "FR-CP-03")
def test_ef_new_帶文類與預算(ef_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["new", "--text", "文章", "--genre", "explainer", "--budget-usd", "1.5"]
    assert main(args) == 0
    job_id = capsys.readouterr().out.strip()
    job = read_job(ef_data / "jobs" / job_id / "job.md").job
    assert (job.target_genre, job.budget.usd) == ("explainer", 1.5)


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
