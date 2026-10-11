"""讀 `harness/recipes/*.md`，並確認每份都能跑（FR-RC-01、SEAM-05）。

新增一份配方就能用，執行器不改程式；格式不合的配方在 `ef serve` 啟動與開單時就被擋下，
訊息寫明是哪一份、哪一欄，不等到跑到一半才出錯。
"""

from collections.abc import Collection
from pathlib import Path

from pydantic import ValidationError

from evidence_forge.documents import FrontmatterError, read_recipe
from evidence_forge.harness import Harness
from evidence_forge.jobkey import InvalidJobKey, check_name
from evidence_forge.models import Recipe


class RecipeError(ValueError):
    """配方不能用；訊息點名檔案與欄位。"""


def _field(location: tuple[str | int, ...]) -> str:
    text = ""
    for part in location:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text or "(frontmatter)"


def load_recipe(harness: Harness, path: Path, tiers: Collection[str]) -> Recipe:
    try:
        recipe = read_recipe(path)
    except FrontmatterError as exc:
        raise RecipeError(f"{path.name}：{exc}") from None
    except ValidationError as exc:
        first = exc.errors()[0]
        raise RecipeError(
            f"{path.name} 的 {_field(first['loc'])}：{first['msg']}"
        ) from None
    if recipe.name != path.stem:
        raise RecipeError(f"{path.name} 的 name：要跟檔名一樣是 {path.stem}")
    for index, stage in enumerate(recipe.stages):
        try:
            # 段 id 會拼進工單鑰匙與 stages/<段>/ 路徑，字元規則跟鑰匙一樣
            check_name("段 id", stage.id)
        except InvalidJobKey as exc:
            raise RecipeError(f"{path.name} 的 stages[{index}].id：{exc}") from None
        if not (harness.skill_dir(stage.skill) / "SKILL.md").is_file():
            raise RecipeError(
                f"{path.name} 的 stages[{index}].skill：找不到 skill {stage.skill}"
            )
        for tier_field, tier in (
            ("tier", stage.tier),
            ("escalate_to", stage.escalate_to),
        ):
            if tier is not None and tier not in tiers:
                raise RecipeError(
                    f"{path.name} 的 stages[{index}].{tier_field}："
                    f"等級 {tier} 不在閘道的對照表（models.yaml）"
                )
    return recipe


def load_recipes(harness: Harness, tiers: Collection[str]) -> dict[str, Recipe]:
    """讀全部配方；任何一份不能用就丟 `RecipeError`。"""
    return {
        recipe.name: recipe
        for recipe in (
            load_recipe(harness, path, tiers)
            for path in sorted(harness.recipes.glob("*.md"))
        )
    }
