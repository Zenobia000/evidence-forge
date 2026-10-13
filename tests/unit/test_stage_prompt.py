"""任務說明要告訴 agent 前面各段的產出在哪（二創段要讀整理結果，S0.8）。

agent 不知道整理段第幾次才合格；執行器知道，寫進任務說明。
"""

from pathlib import Path

from evidence_forge.models import Recipe, RecipeStage
from evidence_forge.runner.runner import stage_prompt, upstream_outputs
from evidence_forge.runner.sources import Source

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


def test_補料段放了來源之後_任務說明列出每個引用編號_正文的位置與註腳(
    tmp_path: Path,
) -> None:
    # #70：二創要綜合多個來源，執行器告訴它有哪些來源、各自叫什麼編號；
    # 註腳整行由執行器給（標題＋網址，種子也附出處，PRD §6 第 7 點），C3 才認得出照抄的是出處
    prompt = stage_prompt(
        RECIPE,
        RECIPE.stages[1],
        1,
        "寫一篇",
        cited={
            "S1": Source(
                Path("input/content.md"), "夜間圖書館", "https://seed.example/p"
            ),
            "S2": Source(
                Path("input/sources/S2.md"), "加班的成本", "https://b.example/2"
            ),
            "S3": Source(
                Path("input/sources/S3.md"), "第三空間", "https://c.example/3"
            ),
        },
    )
    assert (
        "來源（引用編號：正文）：S1 input/content.md、S2 input/sources/S2.md、"
        "S3 input/sources/S3.md\n"
        "註腳（稿子結尾照抄）：\n"
        "[^S1]: 夜間圖書館，https://seed.example/p\n"
        "[^S2]: 加班的成本，https://b.example/2\n"
        "[^S3]: 第三空間，https://c.example/3\n"
        "照系統提示裡的 skill"
    ) in prompt


def test_種子是貼上的文字_沒有網址_註腳標原文() -> None:
    prompt = stage_prompt(
        RECIPE,
        RECIPE.stages[1],
        1,
        "寫一篇",
        cited={
            "S1": Source(Path("input/content.md"), "夜間圖書館"),
            "S2": Source(
                Path("input/sources/S2.md"), "加班的成本", "https://b.example/2"
            ),
        },
    )
    assert "[^S1]: 夜間圖書館（原文）\n" in prompt


def test_只有種子一個來源_而且是貼上的文字時_任務說明不多一個字() -> None:
    # 錄音的種子都是貼上的文字（沒有網址）：錄好的請求不能因為多一行而對不上
    only_seed = {"S1": Source(Path("input/content.md"), "夜間圖書館")}
    assert stage_prompt(
        RECIPE, RECIPE.stages[0], 1, "寫一篇", cited=only_seed
    ) == stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇")


def test_種子有網址時_還沒放來源的段也拿得到原文出處() -> None:
    # 分析報告的來源清單要列原文的網址（PRD §5），agent 只看得到 input/content.md 的正文
    only_seed = {
        "S1": Source(Path("input/content.md"), "夜間圖書館", "https://s.example/a")
    }
    prompt = stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇", cited=only_seed)
    assert "素材在 input/content.md。\n原文出處：夜間圖書館，https://s.example/a\n" in prompt


def test_原文出處的標題壓成一行_太長就截短() -> None:
    # 標題可能是網頁的 og:title：換行會把任務說明拆亂，超長的會塞爆提示
    title = "第一行\n  第二行\t" + "長" * 300
    only_seed = {"S1": Source(Path("input/content.md"), title, "https://s.example/a")}
    prompt = stage_prompt(RECIPE, RECIPE.stages[0], 1, "寫一篇", cited=only_seed)
    [line] = [x for x in prompt.splitlines() if x.startswith("原文出處：")]
    assert line.startswith("原文出處：第一行 第二行 長長")
    assert line.endswith("…，https://s.example/a")
    assert len(line) < 200 + len("原文出處：…，https://s.example/a") + 1
