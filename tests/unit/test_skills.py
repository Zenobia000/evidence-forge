"""skill 與配方的契約（S0.8）：

- C1、C2 只認叫 `quote`、`fact` 的欄位（`checks/stages.py`）。skill 的 schema 改了欄位名，
  C5 照樣過、C1／C2 卻變成每次都過；所以這裡讀 schema，斷言兩個欄位真的有宣告、而且是必填。
- agent 只看得到系統提示裡的 `SKILL.md` 與工單資料夾（ADR-0018）：`SKILL.md` 提到的路徑
  都要是執行器真的放進工單的（`stage_inputs.PROVIDED`）或這一段的產出目錄。
- skill 的腳本不准寫檔，結果只印到 stdout（PR #55 審查）。
- 預設配方 `restructure`：整理用本機（NFR-COST-07）。補料與二創用 `strong`（#70、#63），只有補料段有搜尋額度；
  補料段用 `strong` 是 MVP 暫時偏離 NFR-COST-07（創作者 2026-10-12 決定），所以那條斷言不標它。
- 分析配方 `analyze`（#78）：brief 在本機、失敗升級 `strong`；查證與找題材用 `strong`、有搜尋額度。
  `brief.json` 不收逐字引句；`sources.json` 的來源一筆沿用 research 的格式，另外有查證結果與研究題材。
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from evidence_forge.checks.format import check_schema
from evidence_forge.checks.stages import FACT_FIELD, QUOTE_FIELD
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


# research 的產出：合格的最小樣本只有一個來源（額度用完時用手上的收尾，#70 審查第 7 條）
_RESEARCH_SOURCE = {
    "id": "S2",
    "url": "https://a.example/report",
    "title": "一份報告",
    "why": "補了種子沒有的數字",
}


def _research_output(**changes: str | None) -> dict[str, Any]:
    """`sources.json` 的樣本：只有一個來源；`changes` 改那一個來源的欄位，值是 None 就拿掉。"""
    source = dict(_RESEARCH_SOURCE)
    for name, value in changes.items():
        if value is None:
            source.pop(name)
        else:
            source[name] = value
    return {"queries": ["specific query"], "sources": [source]}


def _research_failures(payload: object) -> set[str | None]:
    """不符合 research schema 的位置（JSON pointer）；合格是空的。"""
    return {
        entry.location.pointer if entry.location is not None else None
        for entry in check_schema(payload, _schema("research"), file="sources.json")
        if entry.status == "fail"
    }


@pytest.mark.promise("FR-RS-01")
def test_research_的_schema_只找到一個來源也合格() -> None:
    # 單子是否未完成由 search_shortfalls 決定，不是讓這一段失敗
    assert _research_failures(_research_output()) == set()


@pytest.mark.promise("FR-RS-01")
@pytest.mark.parametrize(
    ("payload", "pointer"),
    [
        ({"queries": ["specific query"], "sources": []}, "/sources"),
        (_research_output(id="S1"), "/sources/0/id"),
        (_research_output(why=None), "/sources/0"),
        (_research_output(note="多寫的欄位"), "/sources/0"),
        (_research_output(url="ftp://a.example/report"), "/sources/0/url"),
    ],
    ids=["零個來源", "編號寫成S1", "缺why", "多一個欄位", "網址不是http"],
)
def test_research_的_schema_擋下不合格的來源清單(
    payload: dict[str, Any], pointer: str
) -> None:
    assert _research_failures(payload) == {pointer}


@pytest.mark.promise("FR-DG-02")
def test_digest_的_schema_文類只收_harness_genres_裡的_12_種() -> None:
    allowed = set(_schema("digest")["$defs"]["genre_id"]["enum"])
    assert allowed == {path.stem for path in HARNESS.genres.glob("*.md")}
    assert len(allowed) == 12


def test_compose_的_schema_目標文類跟_harness_genres_一致() -> None:
    # 新增文類只改了 genres/ 與 digest，compose 的清單沒跟上，C5 會每次都擋
    allowed = set(_schema("compose")["properties"]["target_genre"]["enum"])
    assert allowed == {path.stem for path in HARNESS.genres.glob("*.md")}


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


def _restructure_stages() -> dict[str, tuple[str, str, str | None, int]]:
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["restructure"]
    return {
        s.id: (s.skill, s.tier, s.escalate_to, s.tavily_credits) for s in recipe.stages
    }


@pytest.mark.promise("NFR-COST-07")
def test_預設配方_restructure_整理在本機() -> None:
    assert _restructure_stages()["digest"] == ("digest", "local", None, 0)


@pytest.mark.promise("FR-RC-01", "FR-RS-01")
def test_預設配方_restructure_三段_補料與二創暫用_strong_只有補料能搜尋() -> None:
    # 補料段用 strong 是暫時偏離 NFR-COST-07（PRD 要本機）：創作者 2026-10-12 決定，
    # 為了品質與工具呼叫可靠度，S1 評估時再跟本機比。二創改回 strong 是 #63
    recipe = load_recipes(HARNESS, ("local", "strong", "max"))["restructure"]
    assert _restructure_stages() == {
        "digest": ("digest", "local", None, 0),
        "research": ("research", "strong", None, 12),
        "compose": ("compose", "strong", None, 0),
    }
    assert [s.id for s in recipe.stages] == ["digest", "research", "compose"]
    assert recipe.on_feedback == "compose"
    assert (recipe.max_attempts, recipe.auto_fix) == (2, 1)
    assert recipe.default_goal


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
    "sources": [dict(_RESEARCH_SOURCE)],
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
            {"fact_checks": [{"claim": "K1", "verdict": "disputed", "sources": ["S2"]}]},
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
        "題材沒附文章",
        "沒有題材",
    ],
)
def test_investigate_的_schema_擋下不合格的查證與題材(
    change: dict[str, Any], pointer: str
) -> None:
    payload = {**_INVESTIGATE, **change}
    assert pointer in _failures("investigate", payload, "sources.json")


def test_investigate_的來源清單格式沿用_research() -> None:
    # 來源一筆的欄位跟 research 一樣：執行器用同一套規則放正文（runner/sources.py）
    research = _schema("research")["properties"]["sources"]["items"]
    investigate = _schema("investigate")["properties"]["sources"]["items"]
    assert investigate["required"] == research["required"]
    assert set(investigate["properties"]) == set(research["properties"])


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
