"""任務說明要告訴 agent 前面各段的產出在哪（二創段要讀整理結果，S0.8）。

agent 不知道整理段第幾次才合格；執行器知道，寫進任務說明。
"""

from pathlib import Path

from evidence_forge.models import Recipe, RecipeStage
from evidence_forge.runner.runner import stage_prompt, upstream_outputs

RECIPE = Recipe(
    name="restructure",
    description="test",
    stages=(
        RecipeStage(id="digest", skill="digest", tier="local", output=("claims.json",)),
        RecipeStage(
            id="compose",
            skill="compose",
            tier="strong",
            output=("draft.md", "compose.json"),
        ),
    ),
    on_feedback="compose",
)


def test_二創段的任務說明列出整理段最後一次的產出(tmp_path: Path) -> None:
    for attempt in ("1", "2"):
        (tmp_path / "stages" / "digest" / attempt).mkdir(parents=True)
    # 數字照數值排，不照字串排
    (tmp_path / "stages" / "digest" / "10").mkdir()

    upstream = upstream_outputs(tmp_path, RECIPE, RECIPE.stages[1])

    assert upstream == ["stages/digest/10/claims.json"]
    prompt = stage_prompt(RECIPE, RECIPE.stages[1], 1, "寫一篇", upstream=upstream)
    assert "前面各段的產出：stages/digest/10/claims.json" in prompt


def test_第一段沒有前面的產出_任務說明跟以前逐位元組相同(tmp_path: Path) -> None:
    # 改了任務說明，錄好的回應就對不上；沒有前段時一個字都不能多
    assert upstream_outputs(tmp_path, RECIPE, RECIPE.stages[0]) == []
    assert stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇") == (
        "配方「restructure」的「digest」段，第 1 次。\n"
        "目標：寫一篇\n"
        "素材在 input/content.md。\n"
        "照系統提示裡的 skill 完成這一段，用 write_file 寫出：stages/digest/1/claims.json"
    )


def test_補料段放了來源之後_任務說明列出每個引用編號與正文的位置(
    tmp_path: Path,
) -> None:
    # #70：二創要綜合多個來源，執行器告訴它有哪些來源、各自叫什麼編號
    prompt = stage_prompt(
        RECIPE,
        RECIPE.stages[1],
        1,
        "寫一篇",
        sources={
            "S1": Path("input/content.md"),
            "S2": Path("input/sources/S2.md"),
            "S3": Path("input/sources/S3.md"),
        },
    )
    assert (
        "來源（引用編號：正文）：S1 input/content.md、S2 input/sources/S2.md、"
        "S3 input/sources/S3.md\n"
    ) in prompt


def test_只有種子一個來源時_任務說明不多一個字() -> None:
    # 整理段、補料段、echo 都只有種子：錄好的請求不能因為這一行而對不上
    only_seed = {"S1": Path("input/content.md")}
    assert stage_prompt(
        RECIPE, RECIPE.stages[0], 1, "寫一篇", sources=only_seed
    ) == stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇")
