"""`ef new`、`ef serve` 的指令列（SEAM-09 最薄的一層、FR-RC-01）。"""

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
