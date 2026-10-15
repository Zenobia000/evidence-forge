"""工單資料夾裡的符號連結不能把外面的內容送出去（`ui/artifacts.py` 的 `safe_path`）。

畫面讀 `stages/…/article.md`、`report.md`、`sources.json`、`job.md` 這些檔時，檔案本身或中間任何一層資料夾
是符號連結、指到工單資料夾外面，就當沒有這個檔，不讀、不顯示。
"""

from pathlib import Path

import pytest
from chat_folder import ARTICLE_V1, RECIPES, ChatFolder

from evidence_forge.ui import artifacts


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    return ChatFolder(tmp_path / "data")


def _secret_dir(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "article.md").write_text("# 外面的祕密\n\n不該被讀到。", encoding="utf-8")
    (outside / "report.md").write_text("## 作者在說什麼\n外面的祕密報告", encoding="utf-8")
    return outside


def _rewrite_info(folder: ChatFolder) -> artifacts.JobInfo:
    return [j for j in artifacts.loose_jobs(folder.data, RECIPES) if j.kind == "rewrite"][0]


def test_article_md_是符號連結指到外面_不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = _secret_dir(tmp_path)
    folder.analysis(None, "a1")
    folder.article(None, "w1", parent="a1", text=ARTICLE_V1)
    article = folder.data.job_dir("w1") / "stages" / "rewrite" / "1" / "article.md"
    article.unlink()
    article.symlink_to(outside / "article.md")
    info = _rewrite_info(folder)

    view = artifacts.article_view(folder.data, info, None)

    assert view.error is not None
    assert "祕密" not in str(view.html) and "祕密" not in view.text
    assert not artifacts.has_output(folder.data, info)


def test_stages_rewrite_整個資料夾是符號連結指到外面_不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    (outside / "1").mkdir(parents=True)
    (outside / "1" / "article.md").write_text("# 外面的祕密", encoding="utf-8")
    (outside / "1" / "article.json").write_text('{"title": "祕密標題"}', encoding="utf-8")
    folder.analysis(None, "a1")
    folder.article(None, "w1", parent="a1")
    stages = folder.data.job_dir("w1") / "stages" / "rewrite"
    for attempt in stages.iterdir():
        for leaf in attempt.iterdir():
            leaf.unlink()
        attempt.rmdir()
    stages.rmdir()
    stages.symlink_to(outside, target_is_directory=True)
    info = _rewrite_info(folder)

    view = artifacts.article_view(folder.data, info, None)

    assert view.error is not None
    assert "祕密" not in view.text and view.title != "祕密標題"
    assert artifacts.finished_at(folder.data, info) is None


def test_report_md_是符號連結也不讀(folder: ChatFolder, tmp_path: Path) -> None:
    outside = _secret_dir(tmp_path)
    folder.analysis(None, "a1")
    report = folder.data.job_dir("a1") / "stages" / "investigate" / "1" / "report.md"
    report.unlink()
    report.symlink_to(outside / "report.md")
    info = artifacts.loose_jobs(folder.data, RECIPES)[0]

    view = artifacts.report_view(folder.data, info)

    assert view.error is not None and "祕密" not in str(view.intro_html)


def test_對話裡的工單一樣_job_md_是符號連結指到外面就不收(folder: ChatFolder, tmp_path: Path) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    job_md = folder.data.job_dir("a1") / "job.md"
    moved = tmp_path / "moved-job.md"
    moved.write_text(job_md.read_text(encoding="utf-8"), encoding="utf-8")
    job_md.unlink()
    job_md.symlink_to(moved)

    assert artifacts.conversation_jobs(folder.data, RECIPES, cid) == []


def test_指到工單資料夾自己底下的符號連結沒問題(folder: ChatFolder) -> None:
    folder.analysis(None, "a1")
    folder.article(None, "w1", parent="a1", text=ARTICLE_V1)
    stages = folder.data.job_dir("w1") / "stages" / "rewrite"
    (stages / "latest").symlink_to(stages / "1", target_is_directory=True)
    info = _rewrite_info(folder)

    assert artifacts.article_view(folder.data, info, None).error is None


def test_safe_path_迴圈的符號連結也安全(tmp_path: Path) -> None:
    root = tmp_path / "job"
    root.mkdir()
    (root / "loop").symlink_to(root / "loop")

    assert artifacts.safe_path(root, root / "loop") is None
