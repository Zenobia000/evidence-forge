"""skill 與配方的契約（S0.8）：

- C1、C2 只認叫 `quote`、`fact` 的欄位（`checks/stages.py`）。skill 的 schema 改了欄位名，
  C5 照樣過、C1／C2 卻變成每次都過；所以這裡讀 schema，斷言兩個欄位真的有宣告、而且是必填。
- agent 只看得到系統提示裡的 `SKILL.md` 與工單資料夾（ADR-0018）：`SKILL.md` 提到的路徑
  都要是執行器真的放進工單的（`stage_inputs.PROVIDED`）或這一段的產出目錄。
- skill 的腳本不准寫檔，結果只印到 stdout（PR #55 審查）。
- 預設配方 `restructure`：整理用本機（NFR-COST-07）；二創 MVP 暫用本機，改回 `strong` 見 #63。
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.checks.stages import FACT_FIELD, QUOTE_FIELD
from evidence_forge.harness import default_harness
from evidence_forge.runner.recipes import load_recipes
from evidence_forge.runner.stage_inputs import PROVIDED

HARNESS = default_harness()
SKILLS = sorted(
    path.parent.name for path in (HARNESS.root / "skills").glob("*/SKILL.md")
)


def _schema(skill: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        HARNESS.schema_file(skill).read_text(encoding="utf-8")
    )
    return loaded


def _item_schema(schema: dict[str, Any], array: str) -> dict[str, Any]:
    assert array in schema["required"]
    prop: dict[str, Any] = schema["properties"][array]
    assert prop["type"] == "array"
    assert prop.get("minItems", 0) >= 1, f"{array} 要至少一筆"
    items: dict[str, Any] = prop["items"]
    return items


@pytest.mark.promise("FR-DG-01", "FR-VF-01")
def test_digest_的_schema_每條主張都要有_C1_認得的_quote_欄位() -> None:
    claim = _item_schema(_schema("digest"), "claims")
    assert QUOTE_FIELD in claim["required"]
    assert claim["properties"][QUOTE_FIELD]["type"] == "string"
    assert claim["properties"][QUOTE_FIELD]["minLength"] >= 1


@pytest.mark.promise("FR-CP-01", "FR-VF-01")
def test_compose_的_schema_每句事實句都要有_C2_認得的_fact_欄位() -> None:
    fact = _item_schema(_schema("compose"), "facts")
    assert FACT_FIELD in fact["required"]
    assert fact["properties"][FACT_FIELD]["type"] == "string"
    assert fact["properties"][FACT_FIELD]["minLength"] >= 1


@pytest.mark.promise("FR-DG-02")
def test_digest_的_schema_文類只收_harness_genres_裡的_12_種() -> None:
    allowed = set(_schema("digest")["$defs"]["genre_id"]["enum"])
    assert allowed == {path.stem for path in HARNESS.genres.glob("*.md")}
    assert len(allowed) == 12


@pytest.mark.promise("NFR-RUN-03")
@pytest.mark.parametrize("skill", SKILLS)
def test_每個_skill_都有_schema_json(skill: str) -> None:
    # 配方用到的 skill 少了 schema，`ef serve` 會擋；這裡讓寫 skill 的人更早知道
    assert HARNESS.schema_file(skill).is_file()


# `SKILL.md` 裡用反引號包住、看起來像路徑的字（含 / 的）
_PATHISH = re.compile(r"`([A-Za-z0-9_.<>{}-]+/[A-Za-z0-9_./<>{}-]*)`")


def mentioned_paths(text: str) -> list[str]:
    return _PATHISH.findall(text)


def unreachable(paths: list[str]) -> list[str]:
    """agent 讀不到的路徑：不是執行器放進工單的，也不是這一段的產出目錄。"""
    return [
        path
        for path in paths
        if not path.startswith("stages/")
        and not any(
            path == p or (p.endswith("/") and path.startswith(p)) for p in PROVIDED
        )
    ]


def test_路徑掃描_抓得到_skill_資料夾裡的附帶檔() -> None:
    text = "Read `templates/intro.md` and `input/content.md`, then `input/author/voice.md`."
    assert unreachable(mentioned_paths(text)) == ["templates/intro.md"]


@pytest.mark.parametrize("skill", SKILLS)
def test_SKILL_md_只提到_agent_讀得到的路徑(skill: str) -> None:
    text = (HARNESS.skill_dir(skill) / "SKILL.md").read_text(encoding="utf-8")
    assert unreachable(mentioned_paths(text)) == []


# 常見的寫檔寫法；抓不到全部，剩下的靠審查
_WRITES = re.compile(
    r"open\([^)]*['\"][wax]b?\+?['\"]"
    r"|mode\s*=\s*['\"][wax]"
    r"|\.write_text\(|\.write_bytes\("
    r"|shutil\.(?:copy\w*|move)\("
    r"|os\.(?:rename|replace|remove|unlink)\("
    r"|\.(?:rename|unlink|touch|mkdir)\("
    # `.replace(` 太常見（`str.replace`）：只抓 `Path(...)` 直接接的那種
    r"|Path\([^)]*\)\.replace\("
)


def writes(source: str) -> list[str]:
    return [match.group(0) for match in _WRITES.finditer(source)]


@pytest.mark.parametrize(
    "source",
    [
        "open(path, 'w').write(x)",
        'open(p, "a")',
        "open(p, mode='xb')",
        "Path(p).write_text(x)",
        "Path(p).write_bytes(x)",
        "shutil.copyfile(a, b)",
        "shutil.move(a, b)",
        "os.replace(a, b)",
        "Path(a).rename(b)",
        "Path(a).replace(b)",
    ],
)
def test_腳本掃描_抓得到常見的寫檔寫法(source: str) -> None:
    assert writes(source)


def test_腳本掃描_讀檔與印到_stdout_不算寫檔() -> None:
    assert writes("print(json.dumps(open(p).read()))\nsys.stdin.read()") == []
    assert writes('text.replace("\\r\\n", "\\n")\nwhen.replace(tzinfo=None)') == []


SCRIPTS = sorted((HARNESS.root / "skills").glob("*/scripts/*.py"))


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "script", SCRIPTS, ids=lambda p: str(p.relative_to(HARNESS.root))
)
def test_skill_的腳本不寫檔_結果只印到_stdout(script: Path) -> None:
    assert writes(script.read_text(encoding="utf-8")) == []


@pytest.mark.promise("NFR-COST-07", "FR-RC-01")
def test_預設配方_restructure_整理與二創都在本機_退件從二創重跑() -> None:
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["restructure"]
    assert [(s.id, s.skill, s.tier, s.escalate_to) for s in recipe.stages] == [
        ("digest", "digest", "local", None),
        # MVP 暫用 local；改回 strong 見 #63
        ("compose", "compose", "local", None),
    ]
    assert recipe.on_feedback == "compose"
    assert (recipe.max_attempts, recipe.auto_fix) == (2, 1)
    assert recipe.default_goal
