"""`job.md` 與配方這兩種「frontmatter＋Markdown」的檔（架構草案 v0.3 §2.3、§3.1）。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_forge.documents import (
    FrontmatterError,
    parse_job,
    parse_recipe,
    read_job,
    read_recipe,
    render_job,
    write_job,
)

TAIPEI = timezone(timedelta(hours=8))

# 照 v0.3 §2.3 的範例手寫（加上 S0.2 票要的 target_genre、waiting），保留註解與空行。
HAND_WRITTEN_JOB = """---
id: 2026-10-10-substack-abc123
recipe: restructure
feed: { connector: rss, name: some-newsletter, item: inbox/some-newsletter/abc123 }
goal: 把這篇的核心主張整理成給台灣 PM 看的 1,500 字觀點文，補一個反例
target_genre: opinion
budget: { usd: 0.50, tavily_credits: 20 }
status: running            # queued | running | review | approved | discarded
stage: { id: compose, index: 3, total: 4, attempt: 1, started_at: 2026-10-10T14:03:11+08:00 }
waiting: { kind: gpu, since: 2026-10-10T14:05:00+08:00 }
result: null
versions: { draft: 1 }
---

（可選）給 agent 的補充說明。
UI 的「新增工單」表單會把你打的字放在這裡。
"""


def test_hand_written_job_md_is_read_into_typed_fields() -> None:
    doc = parse_job(HAND_WRITTEN_JOB)
    job = doc.job
    assert job.id == "2026-10-10-substack-abc123"
    assert job.feed.connector == "rss"
    assert job.feed.item == "inbox/some-newsletter/abc123"
    assert job.target_genre == "opinion"
    assert job.budget.usd == 0.50
    assert job.budget.tavily_credits == 20
    assert job.status == "running"
    assert job.stage is not None
    assert (job.stage.id, job.stage.index, job.stage.total) == ("compose", 3, 4)
    assert job.stage.started_at == datetime(2026, 10, 10, 14, 3, 11, tzinfo=TAIPEI)
    assert job.waiting is not None and job.waiting.kind == "gpu"
    assert job.result is None
    assert job.versions.draft == 1


def test_notes_are_everything_after_the_closing_fence_untouched() -> None:
    doc = parse_job(HAND_WRITTEN_JOB)
    assert doc.notes == (
        "\n（可選）給 agent 的補充說明。\nUI 的「新增工單」表單會把你打的字放在這裡。\n"
    )


@pytest.mark.promise("NFR-RUN-02")
def test_job_md_round_trip_keeps_frontmatter_and_notes() -> None:
    original = parse_job(HAND_WRITTEN_JOB)
    again = parse_job(render_job(original))
    assert again == original


@pytest.mark.parametrize(
    "notes",
    [
        "",
        "沒有結尾換行",
        "\n\n前後都有空行\n\n",
        "內文裡也有一行\n---\n分隔線不能被當成 frontmatter 的結尾\n",
        "行尾空白   \n\t縮排\n",
    ],
)
def test_notes_survive_a_round_trip_byte_for_byte(notes: str) -> None:
    doc = parse_job(HAND_WRITTEN_JOB).model_copy(update={"notes": notes})
    assert parse_job(render_job(doc)).notes == notes


def test_rendering_is_stable_once_written() -> None:
    rendered = render_job(parse_job(HAND_WRITTEN_JOB))
    assert render_job(parse_job(rendered)) == rendered


def test_read_and_write_go_through_the_file(tmp_path: Path) -> None:
    doc = parse_job(HAND_WRITTEN_JOB)
    path = tmp_path / "jobs" / doc.job.id / "job.md"
    write_job(path, doc)
    assert read_job(path) == doc


def test_job_md_saved_by_a_windows_editor_is_still_read(tmp_path: Path) -> None:
    # 創作者可能用 Windows 的編輯器透過 \\wsl.localhost 存檔：開頭帶 BOM、換行是 CRLF。
    path = tmp_path / "job.md"
    windows_text = "﻿" + HAND_WRITTEN_JOB.replace("\n", "\r\n")
    path.write_bytes(windows_text.encode("utf-8"))
    assert read_job(path) == parse_job(HAND_WRITTEN_JOB)


def test_write_job_replaces_the_previous_state(tmp_path: Path) -> None:
    # job.md 是資料夾裡唯一會被改寫的檔：狀態欄由執行器更新（v0.3 §2.2）。
    doc = parse_job(HAND_WRITTEN_JOB)
    path = tmp_path / "job.md"
    write_job(path, doc)
    queued = doc.model_copy(
        update={"job": doc.job.model_copy(update={"status": "queued", "waiting": None})}
    )
    write_job(path, queued)
    assert read_job(path).job.status == "queued"
    assert [p.name for p in tmp_path.iterdir()] == ["job.md"]


@pytest.mark.parametrize(
    "text",
    [
        "沒有 frontmatter 的檔\n",
        "---\nid: x\n沒有結尾的分隔線\n",
        "---\n- 是清單不是對應表\n---\n",
    ],
)
def test_malformed_frontmatter_is_refused(text: str) -> None:
    with pytest.raises(FrontmatterError):
        parse_job(text)


# --- 配方 ------------------------------------------------------------------

# v0.3 §3.1 的範例原文。
RESTRUCTURE_RECIPE = """---
name: restructure
description: 把一篇別人的文章整理成主張與研究方向，再用我的風格二創
stages:
  - { id: digest,   skill: digest,   tier: local,  output: [digest.md, claims.json] }
  - { id: research, skill: research, tier: local,  output: [research.md, sources.json], tavily_credits: 10 }
  - { id: compose,  skill: compose,  tier: strong, output: [draft.md], uses: [my-style] }
  - { id: verify,   skill: verify,   tier: local,  output: [checks.json], escalate_to: strong }
on_feedback: compose        # 退件時預設從哪一段重跑
max_attempts: 2             # 每段最多試幾次（含升級前）
auto_fix: 1                 # 檢查沒過時，自動回寫作段修幾次
---
（給人看的說明：這個配方適合什麼素材、產出什麼）
"""


def test_the_architecture_example_recipe_is_read() -> None:
    recipe = parse_recipe(RESTRUCTURE_RECIPE)
    assert recipe.name == "restructure"
    assert [stage.id for stage in recipe.stages] == [
        "digest",
        "research",
        "compose",
        "verify",
    ]
    assert recipe.stages[0].output == ("digest.md", "claims.json")
    assert recipe.stages[1].tavily_credits == 10
    assert recipe.stages[2].uses == ("my-style",)
    assert recipe.stages[3].escalate_to == "strong"
    assert (recipe.on_feedback, recipe.max_attempts, recipe.auto_fix) == (
        "compose",
        2,
        1,
    )


def test_stage_options_default_to_nothing_extra() -> None:
    digest = parse_recipe(RESTRUCTURE_RECIPE).stages[0]
    assert digest.uses == ()
    assert digest.tavily_credits == 0
    assert digest.escalate_to is None


def test_read_recipe_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "restructure.md"
    path.write_text(RESTRUCTURE_RECIPE, encoding="utf-8")
    assert read_recipe(path) == parse_recipe(RESTRUCTURE_RECIPE)


@pytest.mark.parametrize(
    ("broken", "fixed"),
    [
        # 退件要重跑的段不存在
        ("on_feedback: compose ", "on_feedback: rewrite "),
        # 兩段同名，產出目錄 stages/<段>/ 會撞在一起
        ("{ id: research,", "{ id: digest,"),
        # 升級只能往上，不能升到同一級或更低
        ("escalate_to: strong", "escalate_to: local"),
        # 不存在的等級
        ("tier: strong", "tier: huge"),
        # 至少要試一次
        ("max_attempts: 2 ", "max_attempts: 0 "),
    ],
)
def test_inconsistent_recipe_is_refused(broken: str, fixed: str) -> None:
    assert broken in RESTRUCTURE_RECIPE
    with pytest.raises(ValidationError):
        parse_recipe(RESTRUCTURE_RECIPE.replace(broken, fixed))


def test_recipe_needs_at_least_one_stage() -> None:
    text = "---\nname: empty\ndescription: x\nstages: []\non_feedback: x\n---\n"
    with pytest.raises(ValidationError):
        parse_recipe(text)
