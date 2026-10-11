"""執行器開段時放參考資料進 `input/`，agent 只從那裡讀（ADR-0015 §3.7、ADR-0018）。

用假 Pi 確認：Pi 開起來之前，文類定義與作家聲音已經在工單裡，而且 agent 經閘道讀得到；
放不進去（目標文類不存在）時不開 Pi，工單以失敗進待審並寫明原因。
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from runner_world import ARTICLE, FAKE_PI_LIB, GOAL, NOW, World, runner_world

from evidence_forge.runner.intake import PastedText, open_job

# 假 Pi 經閘道讀 input/ 裡的參考資料，讀到了才寫產出
READS_INPUTS = (
    FAKE_PI_LIB
    + """
for path in ["input/genres.md", "input/target-genre.md", "input/author/voice.md"]:
    status, _ = call("/tools/read_file", {"path": path})
    assert status == 200, (path, status)
write("summary.json", {"summary": "讀到了"})
event(type="agent_end")
"""
)


@pytest.fixture
def world_for(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Callable[..., World]]:
    with runner_world(tmp_path, serve) as build:
        yield build


def _author(world: World) -> None:
    author = world.data.authors / "me"
    author.mkdir(parents=True)
    (author / "voice.md").write_text(
        "---\nid: me\ndefault_genre: opinion\n---\n聲音\n", encoding="utf-8"
    )


def _open(world: World, genre: str | None = None) -> str:
    return open_job(
        world.data,
        world.runner.recipes["echo"],
        PastedText(ARTICLE.read_text(encoding="utf-8")),
        goal=GOAL,
        now=NOW,
        genre=genre,
    ).job.id


@pytest.mark.promise("FR-CP-02", "FR-CP-03", "NFR-SEC-02")
def test_開段前文類與作家設定已經在_input_agent_經閘道讀得到(
    world_for: Callable[..., World],
) -> None:
    world = world_for(READS_INPUTS)
    _author(world)
    job_id = _open(world)

    world.runner.run_once()

    job = world.read(job_id)
    assert job.target_genre == "opinion"
    assert job.result is not None and job.result.outcome == "complete", job.result


def test_目標文類不存在_不開_Pi_以失敗進待審並寫明原因(
    world_for: Callable[..., World],
) -> None:
    world = world_for(READS_INPUTS)
    job_id = _open(world, genre="poem")

    world.runner.run_once()

    job = world.read(job_id)
    assert job.status == "review"
    assert job.result is not None and job.result.outcome == "failed"
    assert "poem" in job.result.reason
    assert world.backend_calls == []
