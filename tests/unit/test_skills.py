"""skill 與配方的契約（S0.8）：

- agent 只看得到系統提示裡的 `SKILL.md` 與工單資料夾（ADR-0018）：`SKILL.md` 提到的路徑
  都要是執行器真的放進工單的（`stage_inputs.PROVIDED`）或這一段的產出目錄。
- skill 的腳本不准寫檔，結果只印到 stdout（PR #55 審查）。
- 分析配方 `analyze`（#78）：brief 在本機、失敗升級 `strong`；查證與找題材用 `strong`、有搜尋額度。
  `brief.json` 不收逐字引句；`sources.json` 有來源清單、查證結果與研究題材。
- 重構配方 `rewrite`（#79）：一段 `strong`、要從分析工單接著做（`needs: [report.md]`），
  交 `article.md` 才會跑文章的程式檢查；SKILL.md 不叫 agent 讀原文全文。
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.checks.format import check_schema
from evidence_forge.harness import default_harness
from evidence_forge.runner import hard_checks, sources
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


# 來源清單的一筆：查證段交的 sources.json 裡的格式
_SOURCE = {
    "id": "S2",
    "url": "https://a.example/report",
    "title": "一份報告",
    "why": "補了種子沒有的數字",
}


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


# --- 分析配方（#78）-----------------------------------------------------------

_BRIEF: dict[str, Any] = {
    "genre": {"primary": "explainer", "confidence": 0.7},
    "topics": ["agent", "harness"],
    "key_claims": [{"id": "K1", "claim": "一條可以查證的事實"}],
}


def _failures(skill: str, payload: object, file: str) -> set[str | None]:
    """不符合那個 skill 的 schema 的位置（JSON pointer）；合格是空的。"""
    return {
        entry.location.pointer if entry.location is not None else None
        for entry in check_schema(payload, _schema(skill), file=file)
        if entry.status == "fail"
    }


@pytest.mark.promise("FR-AN-01")
def test_brief_的_schema_收文類_主題標籤_關鍵主張_不要逐字引句() -> None:
    assert _failures("brief", _BRIEF, "brief.json") == set()
    with_quote = {
        **_BRIEF,
        "key_claims": [{"id": "K1", "claim": "x", "quote": "原文的一句"}],
    }
    # 不附逐字引句（ADR-0020）：多寫 quote 欄位就不合格，免得模型又回去逐句抄
    assert _failures("brief", with_quote, "brief.json") == {"/key_claims/0"}


@pytest.mark.promise("FR-AN-01")
@pytest.mark.parametrize(
    ("change", "pointer"),
    [
        ({"key_claims": []}, "/key_claims"),
        (
            {"key_claims": [{"id": f"K{i}", "claim": "x"} for i in range(1, 8)]},
            "/key_claims",
        ),
        ({"genre": {"primary": "essay", "confidence": 0.5}}, "/genre/primary"),
        ({"genre": {"primary": "news", "confidence": 1.5}}, "/genre/confidence"),
        ({"topics": []}, "/topics"),
    ],
    ids=["零條關鍵主張", "超過六條", "文類不在清單", "信心超過1", "沒有主題標籤"],
)
def test_brief_的_schema_擋下不合格的產出(change: dict[str, Any], pointer: str) -> None:
    assert pointer in _failures("brief", {**_BRIEF, **change}, "brief.json")


def test_brief_的_schema_文類跟_harness_genres_一致() -> None:
    allowed = set(_schema("brief")["$defs"]["genre_id"]["enum"])
    assert allowed == {path.stem for path in HARNESS.genres.glob("*.md")}


_INVESTIGATE: dict[str, Any] = {
    "queries": ["specific query"],
    "sources": [dict(_SOURCE)],
    "fact_checks": [
        {"claim": "K1", "verdict": "supported", "sources": ["S2"]},
        {"claim": "K2", "verdict": "not_found", "sources": []},
    ],
    "topics": [{"title": "一個題材", "sources": ["S2"]}],
}


@pytest.mark.promise("FR-IV-01")
def test_investigate_的_schema_查不到可以沒有來源() -> None:
    assert _failures("investigate", _INVESTIGATE, "sources.json") == set()


@pytest.mark.promise("FR-IV-01")
@pytest.mark.parametrize(
    ("change", "pointer"),
    [
        (
            {"fact_checks": [{"claim": "K1", "verdict": "supported", "sources": []}]},
            "/fact_checks/0/sources",
        ),
        (
            {"fact_checks": [{"claim": "K1", "verdict": "wrong", "sources": ["S2"]}]},
            "/fact_checks/0/verdict",
        ),
        (
            {
                "fact_checks": [
                    {"claim": "K1", "verdict": "supported", "sources": ["S1"]}
                ]
            },
            "/fact_checks/0/sources/0",
        ),
        (
            {
                "fact_checks": [
                    {"claim": "K1", "verdict": "disputed", "sources": ["S2"]}
                ]
            },
            "/fact_checks/0/sources",
        ),
        (
            {
                "fact_checks": [
                    {"claim": "K1", "verdict": "disputed", "sources": ["S2", "S2"]}
                ]
            },
            "/fact_checks/0/sources",
        ),
        ({"topics": [{"title": "沒有相關文章", "sources": []}]}, "/topics/0/sources"),
        ({"topics": []}, "/topics"),
    ],
    ids=[
        "有佐證卻沒來源",
        "判定不在四種裡",
        "拿原文佐證原文",
        "有爭議只附一邊",
        "有爭議的兩邊是同一個來源",
        "題材沒附文章",
        "沒有題材",
    ],
)
def test_investigate_的_schema_擋下不合格的查證與題材(
    change: dict[str, Any], pointer: str
) -> None:
    payload = {**_INVESTIGATE, **change}
    assert pointer in _failures("investigate", payload, "sources.json")


@pytest.mark.promise("FR-RC-01", "FR-AN-01", "FR-IV-01", "NFR-COST-07")
def test_分析配方_brief_在本機失敗升級_查證用_strong_有搜尋額度() -> None:
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["analyze"]
    stages = {
        s.id: (s.skill, s.tier, s.escalate_to, s.tavily_credits, s.output)
        for s in recipe.stages
    }
    assert stages == {
        "brief": ("brief", "local", "strong", 0, ("brief.md", "brief.json")),
        "investigate": (
            "investigate",
            "strong",
            None,
            12,
            ("report.md", "sources.json"),
        ),
    }
    assert [s.id for s in recipe.stages] == ["brief", "investigate"]
    assert recipe.default_goal


@pytest.mark.promise("FR-AN-01", "FR-IV-01")
def test_分析配方兩段都有程式檢查_查證段交來源清單() -> None:
    # 執行器看產出檔名決定跑哪些檢查：配方改了產出名，這裡先壞，不會默默不檢查
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["analyze"]
    brief, investigate = recipe.stages
    assert hard_checks.checked_output(brief) == hard_checks.BRIEF_OUTPUT
    assert hard_checks.checked_output(investigate) == hard_checks.REPORT_OUTPUT
    assert sources.collects_sources(investigate)
    assert not sources.collects_sources(brief)


# --- 重構配方（#79）-----------------------------------------------------------


@pytest.mark.promise("FR-RC-01", "FR-RW-01", "NFR-COST-07")
def test_重構配方一段_strong_不搜尋_要從分析工單拿報告() -> None:
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["rewrite"]
    [stage] = recipe.stages
    assert (stage.id, stage.skill, stage.tier, stage.tavily_credits) == (
        "rewrite",
        "rewrite",
        "strong",
        0,
    )
    assert stage.output == ("article.md", "article.json")
    assert recipe.needs == ("report.md",)
    assert recipe.default_goal and recipe.auto_fix >= 1


@pytest.mark.promise("FR-RW-01")
def test_重構段交文章_跑文章的程式檢查_沒過回這一段修() -> None:
    [stage] = load_recipes(HARNESS, ("local", "strong", "max"))["rewrite"].stages
    assert hard_checks.checked_output(stage) == hard_checks.ARTICLE_OUTPUT
    assert hard_checks.fixable(stage)
    assert not sources.collects_sources(stage)


@pytest.mark.promise("FR-RW-01")
def test_重構的_SKILL_md_不叫_agent_讀原文全文() -> None:
    text = (HARNESS.skill_dir("rewrite") / "SKILL.md").read_text(encoding="utf-8")
    assert "input/content.md" not in text
    for path in ("input/report.md", "input/sources/", "input/headline-punchline.md"):
        assert path in text


@pytest.mark.promise("FR-RW-01")
def test_rewrite_的_schema_收標題與候選標題() -> None:
    good = {"title": "標題", "title_candidates": ["標題", "另一個"]}
    assert _failures("rewrite", good, "article.json") == set()
    assert _failures("rewrite", {"title": "標題"}, "article.json") == {""}
