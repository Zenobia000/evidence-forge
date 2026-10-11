"""只增不改的寫檔工具（NFR-KEEP-01、架構草案 v0.3 §2.2）。"""

import multiprocessing
import multiprocessing.synchronize
from multiprocessing.queues import Queue
from pathlib import Path

import pytest

from evidence_forge.write_once import latest_version, write_next_version, write_once


@pytest.mark.promise("NFR-KEEP-01")
def test_write_once_creates_the_file_with_the_given_content(tmp_path: Path) -> None:
    target = tmp_path / "stages" / "digest" / "1" / "digest.md"
    write_once(target, "# 整理\n")
    assert target.read_text(encoding="utf-8") == "# 整理\n"


def test_write_once_accepts_bytes(tmp_path: Path) -> None:
    target = tmp_path / "raw.html"
    write_once(target, b"<p>\xe5\x8e\x9f\xe6\x96\x87</p>")
    assert target.read_bytes() == b"<p>\xe5\x8e\x9f\xe6\x96\x87</p>"


@pytest.mark.promise("NFR-KEEP-01")
@pytest.mark.promise("NFR-KEEP-02")
def test_write_once_refuses_an_existing_file_and_keeps_its_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "draft.v1.md"
    write_once(target, "第一版")
    with pytest.raises(FileExistsError):
        write_once(target, "想蓋掉第一版")
    assert target.read_text(encoding="utf-8") == "第一版"


def test_latest_version_is_zero_when_nothing_was_written(tmp_path: Path) -> None:
    assert latest_version(tmp_path, "decision", ".json") == 0


def test_latest_version_is_zero_when_the_directory_does_not_exist(
    tmp_path: Path,
) -> None:
    assert latest_version(tmp_path / "review", "decision", ".json") == 0


def test_latest_version_reads_the_highest_number_not_the_last_name(
    tmp_path: Path,
) -> None:
    # 檔名排序會把 v10 排在 v9 前面；版本號要照數字比。
    for name in ["draft.v1.md", "draft.v9.md", "draft.v10.md"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert latest_version(tmp_path, "draft", ".md") == 10


def test_latest_version_ignores_other_kinds_of_file(tmp_path: Path) -> None:
    for name in ["draft.v3.md", "checks.v7.json", "draft.v5.md.tmp", "draft.md"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert latest_version(tmp_path, "draft", ".md") == 3


@pytest.mark.promise("NFR-KEEP-01")
def test_first_version_is_v1(tmp_path: Path) -> None:
    written = write_next_version(tmp_path / "review", "decision", ".json", "{}")
    assert written.version == 1
    assert written.path == tmp_path / "review" / "decision.v1.json"
    assert written.path.read_text(encoding="utf-8") == "{}"


@pytest.mark.promise("NFR-KEEP-01")
def test_next_version_follows_the_highest_existing_one(tmp_path: Path) -> None:
    (tmp_path / "draft.v1.md").write_text("一", encoding="utf-8")
    (tmp_path / "draft.v2.md").write_text("二", encoding="utf-8")
    written = write_next_version(tmp_path, "draft", ".md", "三")
    assert written.version == 3
    assert (tmp_path / "draft.v1.md").read_text(encoding="utf-8") == "一"
    assert (tmp_path / "draft.v2.md").read_text(encoding="utf-8") == "二"


WRITERS = 6
WRITES_EACH = 5


def _race(
    directory: Path,
    writer: int,
    start: multiprocessing.synchronize.Barrier,
    results: "Queue[tuple[int, str]]",
) -> None:
    start.wait()
    for n in range(WRITES_EACH):
        content = f"writer-{writer}-{n}"
        written = write_next_version(directory, "decision", ".json", content)
        results.put((written.version, content))


@pytest.mark.promise("NFR-KEEP-01")
@pytest.mark.promise("NFR-KEEP-02")
def test_concurrent_writers_never_get_the_same_version(tmp_path: Path) -> None:
    # 兩個分頁同時審同一張單的情況（S0 UI IA §8）：多個程序同一刻搶號。
    ctx = multiprocessing.get_context("fork")
    start = ctx.Barrier(WRITERS)
    results: Queue[tuple[int, str]] = ctx.Queue()
    workers = [
        ctx.Process(target=_race, args=(tmp_path, i, start, results))
        for i in range(WRITERS)
    ]
    for worker in workers:
        worker.start()
    claimed = [results.get(timeout=30) for _ in range(WRITERS * WRITES_EACH)]
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0

    total = WRITERS * WRITES_EACH
    versions = [version for version, _ in claimed]
    # 每一號只有一個程序拿到，而且號碼連續不跳。
    assert sorted(versions) == list(range(1, total + 1))
    # 每個檔的內容就是拿到那一號的程序寫的，沒有被別人蓋掉。
    for version, content in claimed:
        path = tmp_path / f"decision.v{version}.json"
        assert path.read_text(encoding="utf-8") == content
