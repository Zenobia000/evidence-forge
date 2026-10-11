"""FR-RC-01：新增一份配方就能用；格式不合的配方被擋下，訊息點名哪一份、哪一欄。"""

import shutil
from pathlib import Path

import pytest

from evidence_forge.harness import Harness, default_harness
from evidence_forge.runner.recipes import RecipeError, load_recipes

TIERS = ("local",)


def _harness(tmp_path: Path, recipe: str, name: str = "mine") -> Harness:
    root = tmp_path / "harness"
    (root / "recipes").mkdir(parents=True)
    shutil.copytree(default_harness().skill_dir("echo"), root / "skills" / "echo")
    (root / "recipes" / f"{name}.md").write_text(recipe, encoding="utf-8")
    return Harness.at(root)


GOOD = """---
name: mine
description: 新的配方
stages:
  - { id: first, skill: echo, tier: local, output: [summary.json] }
on_feedback: first
---
"""


@pytest.mark.promise("FR-RC-01")
def test_repo_裡的配方都讀得進來_含打通用的_echo() -> None:
    recipes = load_recipes(default_harness(), ("local", "strong", "max"))
    echo = recipes["echo"]
    assert [(s.id, s.skill, s.tier) for s in echo.stages] == [("echo", "echo", "local")]


@pytest.mark.promise("FR-RC-01")
def test_新增一份配方檔就讀得到_不改程式(tmp_path: Path) -> None:
    recipes = load_recipes(_harness(tmp_path, GOOD), TIERS)
    assert list(recipes) == ["mine"]
    assert recipes["mine"].stages[0].id == "first"


@pytest.mark.promise("FR-RC-01")
@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        (GOOD.replace("description: 新的配方\n", ""), "mine.md 的 description"),
        (GOOD.replace("skill: echo", "skill: nope"), "mine.md 的 stages[0].skill"),
        (GOOD.replace("tier: local", "tier: strong"), "mine.md 的 stages[0].tier"),
        (GOOD.replace("tier: local", "tier: huge"), "mine.md 的 stages[0].tier"),
        (GOOD.replace("name: mine", "name: other"), "mine.md 的 name"),
        # 段 id 會拼進工單鑰匙與 stages/<段>/ 路徑；不合格的要在啟動時擋，不是跑到一半才炸
        (GOOD.replace("first", "摘要"), "mine.md 的 stages[0].id"),
        (GOOD.replace("first", "a/b"), "mine.md 的 stages[0].id"),
        (GOOD.replace("first", "'a b'"), "mine.md 的 stages[0].id"),
        ("沒有 frontmatter", "mine.md"),
    ],
)
def test_配方格式不合_點名哪一份哪一欄(
    tmp_path: Path, recipe: str, expected: str
) -> None:
    with pytest.raises(RecipeError) as raised:
        load_recipes(_harness(tmp_path, recipe), TIERS)
    assert expected in str(raised.value)


@pytest.mark.promise("FR-RC-01", "NFR-RUN-03")
def test_skill_少了_schema_json_啟動就擋下_點名哪一份哪一段哪個_skill(
    tmp_path: Path,
) -> None:
    # 沒有 schema 就沒辦法驗產出；不擋的話，任何不是空的 JSON 都會被當成合格
    harness = _harness(tmp_path, GOOD)
    (harness.skill_dir("echo") / "schema.json").unlink()
    with pytest.raises(RecipeError) as raised:
        load_recipes(harness, TIERS)
    message = str(raised.value)
    assert "mine.md 的 stages[0].skill" in message
    assert "echo" in message and "schema.json" in message
