"""任務說明要告訴 agent 前面各段的產出在哪、原文出處是什麼（S0.8、#78、#79）。

agent 不知道 brief 段第幾次才合格；執行器知道，寫進任務說明。
"""

from pathlib import Path

import pytest

from evidence_forge.models import Recipe, RecipeStage
from evidence_forge.runner.runner import stage_prompt, upstream_outputs
from evidence_forge.runner.sources import Original

RECIPE = Recipe(
    name="analyze",
    description="test",
    stages=(
        RecipeStage(id="brief", skill="brief", tier="local", output=("brief.md",)),
        RecipeStage(
            id="investigate",
            skill="investigate",
            tier="strong",
            output=("report.md", "sources.json"),
        ),
    ),
    on_feedback="investigate",
)
REWRITE = Recipe(
    name="rewrite",
    description="test",
    stages=(
        RecipeStage(
            id="rewrite",
            skill="rewrite",
            tier="strong",
            output=("article.md", "article.json"),
        ),
    ),
    on_feedback="rewrite",
)


def test_後面的段的任務說明列出前一段最後一次的產出(tmp_path: Path) -> None:
    for attempt in ("1", "2"):
        (tmp_path / "stages" / "brief" / attempt).mkdir(parents=True)
    # 數字照數值排，不照字串排
    (tmp_path / "stages" / "brief" / "10").mkdir()

    upstream = upstream_outputs(tmp_path, RECIPE, RECIPE.stages[1])

    assert upstream == ["stages/brief/10/brief.md"]
    prompt = stage_prompt(RECIPE, RECIPE.stages[1], 1, "寫一篇", upstream=upstream)
    assert "前面各段的產出：stages/brief/10/brief.md" in prompt


def test_第一段沒有前面的產出_任務說明跟以前逐位元組相同(tmp_path: Path) -> None:
    # 改了任務說明，錄好的回應就對不上；沒有前段時一個字都不能多
    assert upstream_outputs(tmp_path, RECIPE, RECIPE.stages[0]) == []
    assert stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇") == (
        "配方「analyze」的「brief」段，第 1 次。\n"
        "目標：寫一篇\n"
        "素材在 input/content.md。\n"
        "照系統提示裡的 skill 完成這一段，用 write_file 寫出：stages/brief/1/brief.md"
    )


def test_原文是貼上的文字時_分析工單的任務說明不多一個字() -> None:
    # 錄音的種子都是貼上的文字（沒有網址）：錄好的請求不能因為多一行而對不上
    pasted = Original(title="夜間圖書館", text="正文")
    assert stage_prompt(
        RECIPE, RECIPE.stages[0], 1, "寫一篇", original=pasted
    ) == stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇")


def test_原文有網址時_分析的每一段都拿得到原文出處() -> None:
    # 分析報告的來源清單要列原文的網址（PRD §5），agent 只看得到 input/content.md 的正文
    linked = Original(title="夜間圖書館", text="正文", url="https://s.example/a")
    prompt = stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇", original=linked)
    assert "素材在 input/content.md。\n原文出處：夜間圖書館，https://s.example/a\n" in prompt


@pytest.mark.promise("FR-RW-01")
def test_重構工單沒有原文全文_任務說明不提_content_md_但給原文出處() -> None:
    pasted = Original(title="夜間圖書館", text="正文", author="王小明")
    prompt = stage_prompt(
        REWRITE, REWRITE.stages[0], 1, "寫給 PM", original=pasted, seed_in_job=False
    )
    assert "content.md" not in prompt
    assert "原文出處：夜間圖書館，王小明\n" in prompt


@pytest.mark.promise("FR-RW-03")
def test_再改的任務說明帶著創作者的一句意見() -> None:
    prompt = stage_prompt(
        REWRITE,
        REWRITE.stages[0],
        1,
        "寫給 PM",
        seed_in_job=False,
        feedback="開頭太長，直接講結論",
    )
    assert "創作者對上一版的意見：開頭太長，直接講結論\n" in prompt
