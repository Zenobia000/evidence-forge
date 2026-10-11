"""資料目錄的位置與骨架（ADR-0014、架構草案 v0.3 §2.1）。"""

from pathlib import Path

import pytest

from evidence_forge.paths import DataDir, resolve_data_dir

# 這個 repo 的根目錄：tests/ 的上一層。獨立算出來，不借用被測程式的算法。
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.promise("NFR-KEEP-04")
def test_without_ef_data_the_data_dir_is_the_projects_data_folder() -> None:
    assert resolve_data_dir({}).root == REPO_ROOT / "data"


@pytest.mark.promise("NFR-KEEP-04")
def test_empty_ef_data_counts_as_unset() -> None:
    # .env.example 裡 EF_DATA= 留空，載入後是空字串，不能被當成「目前目錄」。
    assert resolve_data_dir({"EF_DATA": ""}).root == REPO_ROOT / "data"


@pytest.mark.promise("NFR-KEEP-04")
def test_ef_data_points_the_data_dir_elsewhere(tmp_path: Path) -> None:
    assert resolve_data_dir({"EF_DATA": str(tmp_path)}).root == tmp_path


def test_relative_ef_data_is_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_data_dir({"EF_DATA": "somewhere"}).root == tmp_path / "somewhere"


def test_ensure_creates_inbox_jobs_cache_and_blobs(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    data.ensure()
    created = sorted(p.name for p in (tmp_path / "data").iterdir() if p.is_dir())
    assert created == ["blobs", "cache", "inbox", "jobs"]


def test_ensure_is_safe_to_run_twice(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    data.ensure()
    data.ensure()
    assert (tmp_path / "jobs").is_dir()


def test_named_locations_follow_the_folder_layout(tmp_path: Path) -> None:
    data = DataDir(tmp_path)
    assert data.inbox == tmp_path / "inbox"
    assert data.jobs == tmp_path / "jobs"
    assert data.cache == tmp_path / "cache"
    assert data.blobs == tmp_path / "blobs"
    assert data.job_dir("2026-10-10-substack-abc123") == (
        tmp_path / "jobs" / "2026-10-10-substack-abc123"
    )
