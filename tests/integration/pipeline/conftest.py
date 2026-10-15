"""重播共用：錄音當時的配方等級。

錄音的請求裡有等級別名（`"model": "strong"`），重播是照請求 hash 找回應。ADR-0022 把配方改成
本機優先（`local` → `fast` → `strong`）之後，第 1 次的請求變成 `"model": "local"`，就對不上錄音了。
錄音本身沒有變，所以重播用一份「錄音當時的配方」：其他東西（skill、Pi、文類）全部連回 repo 的
`harness/`，只有 `recipes/` 換成把那幾段改回錄音時的等級。重錄之後這裡對應的那一行就可以拿掉。
"""

from pathlib import Path

import pytest

from evidence_forge.harness import Harness, default_harness

# 配方檔 → {段 id: 錄音當時那一段的寫法}
RECORDED_STAGES: dict[str, dict[str, str]] = {
    # 2026-10-15 錄的 analyze-library（#106 重錄）：brief 在本機（不行才升 fast）、查證段直接用 fast。
    # 錄音只准用本機與 Haiku（月底額度），所以錄的時候把配方釘在這兩級，不讓它升到 strong
    "analyze.md": {
        "brief": "  - { id: brief, skill: brief, tier: local, escalate_to: [fast], "
        "output: [brief.md, brief.json] }",
        "investigate": "  - { id: investigate, skill: investigate, tier: fast, "
        "output: [report.md, sources.json], tavily_credits: 12 }",
    },
    # 2026-10-15 錄的 rewrite-library（#110 重錄：照編輯卡寫稿）：本機先試、不行升 fast，不升 strong
    # （錄音只准本機與 Haiku）。本機兩次都沒過檢查，第 3 次 fast 過了
    "rewrite.md": {
        "rewrite": "  - { id: rewrite, skill: rewrite, tier: local, escalate_to: [fast], "
        "output: [article.md, article.json] }",
    },
}


def _pin(text: str, stages: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    for stage_id, recorded in stages.items():
        prefix = f"  - {{ id: {stage_id},"
        [index] = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        lines[index] = recorded + "\n"
    return "".join(lines)


@pytest.fixture
def recorded_harness(tmp_path: Path) -> Harness:
    """repo 的 harness，只有配方換成錄音當時的等級。"""
    repo = default_harness().root
    root = tmp_path / "harness"
    root.mkdir()
    for entry in repo.iterdir():
        if entry.name != "recipes":
            (root / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    recipes = root / "recipes"
    recipes.mkdir()
    for recipe in (repo / "recipes").glob("*.md"):
        text = recipe.read_text(encoding="utf-8")
        stages = RECORDED_STAGES.get(recipe.name)
        (recipes / recipe.name).write_text(
            _pin(text, stages) if stages else text, encoding="utf-8"
        )
    return Harness.at(root)
