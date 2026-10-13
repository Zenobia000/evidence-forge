"""聊天室畫面測試用的資料目錄：手寫的假對話、分析工單、重構工單（不是執行器產的）。

分析工單與重構工單的檔案長相照 `harness/recipes/analyze.md`、`rewrite.md` 與 PRD v3.0 §5：
`stages/<段>/<次>/` 底下的 `brief.md`、`brief.json`、`report.md`、`sources.json`、`article.md`、`article.json`，
`input/sources/S<n>.md`，`checks/checks.vN.json`（只有 C3 的重疊條目是畫面要讀的）。
重構工單的格式是 #92 才進來的，所以這裡就是畫面對它的期待：畫面只讀這些檔，不依賴那一包的程式。

所有文字都是為測試現編的（ADR-0014），網址一律是 `.example`。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evidence_forge.conversations import (
    Conversation,
    ConversationJob,
    add_job,
    create,
    save,
)
from evidence_forge.documents import JobDocument, write_job
from evidence_forge.models import Job, Recipe, RecipeStage
from evidence_forge.paths import DataDir

TAIPEI = timezone(timedelta(hours=8))
# 假的「現在」：測試用它蓋時間，不看時鐘
NOW = datetime(2026, 10, 13, 15, 0, 0, tzinfo=TAIPEI)

ANALYZE = Recipe(
    name="analyze",
    description="分析",
    stages=(
        RecipeStage(id="brief", skill="brief", tier="local", output=("brief.md", "brief.json")),
        RecipeStage(
            id="investigate",
            skill="investigate",
            tier="strong",
            output=("report.md", "sources.json"),
            tavily_credits=12,
        ),
    ),
    on_feedback="investigate",
)
REWRITE = Recipe(
    name="rewrite",
    description="重構",
    stages=(
        RecipeStage(id="rewrite", skill="rewrite", tier="strong", output=("article.md", "article.json")),
    ),
    on_feedback="rewrite",
)
RECIPES = {"analyze": ANALYZE, "rewrite": REWRITE}

REPORT = """# 長上下文會不會取代檢索

## 作者在說什麼
作者認為超長上下文還撐不起大多數檢索場景。他的結論是兩年內檢索仍是主流。

## 事實與觀點
- 事實：長上下文比檢索省 70% 成本。
- 觀點：兩年內檢索仍是主流。

## 查證
- K1 有爭議：兩份來源的算法對不上，見 [S2] 與 [S3]。
- K2 查不到：搜了三次找不到這個數字的出處。
- K3 有佐證：官方文件寫得很清楚 [S4]。

## 研究題材
- 長上下文的實際成本：讀 [S2]，原文沒算到錢。

## 來源清單
- S1 長上下文會不會取代檢索 https://seed.example/post
- S2 價目表 https://pricing.example/list
"""

BRIEF_JSON = {
    "genre": {"primary": "opinion", "confidence": 0.8},
    "topics": ["RAG", "成本"],
    "key_claims": [
        {"id": "K1", "claim": "長上下文比檢索省 70% 成本"},
        {"id": "K2", "claim": "八成團隊還在用檢索"},
        {"id": "K3", "claim": "上下文越長，第一個字出來得越慢"},
    ],
}

SOURCES_JSON = {
    "queries": ["long context cost", "rag adoption"],
    "sources": [
        {"id": "S2", "url": "https://pricing.example/list", "title": "價目表", "why": "算帳"},
        {"id": "S3", "url": "https://vendor.example/blog", "title": "廠商部落格", "why": "支持的一方"},
        {"id": "S4", "url": "https://docs.example/latency", "title": "延遲說明", "why": "佐證"},
    ],
    "fact_checks": [
        {"claim": "K1", "verdict": "disputed", "sources": ["S2", "S3"]},
        {"claim": "K2", "verdict": "not_found", "sources": []},
        {"claim": "K3", "verdict": "supported", "sources": ["S4"]},
    ],
    "topics": [{"title": "長上下文的實際成本", "sources": ["S2"]}],
}

ARTICLE_V1 = """# 長上下文不會取代檢索

最近常聽到一種說法：上下文長到一百萬 token，檢索就沒用了。這篇想慢慢拆開它。

## 先算錢
照公開價目算，把整份資料塞進上下文通常貴好幾倍。

## 參考來源
1. 長上下文會不會取代檢索，https://seed.example/post
"""

ARTICLE_V2 = """# 長上下文不會取代檢索

結論先講：未來兩年多數產品還是會用檢索。

## 先算錢
照公開價目算，把整份資料塞進上下文通常貴好幾倍。

## 參考來源
1. 長上下文會不會取代檢索，https://seed.example/post
"""


def at(minutes: float = 0.0) -> datetime:
    return NOW + timedelta(minutes=minutes)


class ChatFolder:
    """一個暫時的資料目錄，加幾個「蓋出對話與工單」的小工具。"""

    def __init__(self, root: Path) -> None:
        self.data = DataDir(root)
        self.data.ensure()

    def conversation(self, title: str = "長上下文會不會取代檢索", minutes: float = 0.0) -> str:
        created = create(self.data, at(minutes), title)
        return created.id

    def update_conversation(self, conversation_id: str, **changes: Any) -> Conversation:
        from evidence_forge.conversations import read

        conversation = read(self.data, conversation_id).model_copy(update=changes)
        save(self.data, conversation)
        return conversation

    def job(
        self,
        conversation_id: str,
        job_id: str,
        *,
        recipe: str = "analyze",
        status: str = "done",
        outcome: str | None = "complete",
        reason: str = "",
        persona: str | None = None,
        genre: str | None = None,
        parent: str | None = None,
        version: int = 1,
        stage: dict[str, Any] | None = None,
        waiting: dict[str, Any] | None = None,
        minutes: float = 0.0,
        item_title: str | None = "長上下文會不會取代檢索",
        item_url: str | None = "https://seed.example/post",
    ) -> Path:
        """寫 `job.md`、素材項目，並記進對話的 `jobs.jsonl`。回傳工單資料夾。"""
        item_dir = f"inbox/manual/{job_id}"
        data: dict[str, Any] = {
            "id": job_id,
            "recipe": recipe,
            "feed": {"connector": "manual", "name": "manual", "item": item_dir},
            "goal": "分析",
            "target_genre": genre,
            "conversation": conversation_id,
            "persona": persona,
            "parent": parent,
            "budget": {"usd": 1.0, "tavily_credits": 12},
            "status": status,
            "stage": stage,
            "waiting": waiting,
            "result": None if outcome is None else {"outcome": outcome, "reason": reason},
        }
        job_dir = self.data.job_dir(job_id)
        write_job(job_dir / "job.md", JobDocument(job=Job.model_validate(data)))
        if item_title is not None:
            folder = self.data.root / item_dir
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "item.json").write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "feed": "manual",
                        "url": item_url,
                        "title": item_title,
                        "fetched_at": at(minutes).isoformat(),
                        "content_hash": "a" * 64,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        add_job(
            self.data,
            conversation_id,
            ConversationJob(job_id=job_id, ts=at(minutes), version=version, parent=parent),
        )
        return job_dir

    def write(self, job_id: str, relative: str, content: str | object) -> None:
        path = self.data.job_dir(job_id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")

    def analysis(
        self,
        conversation_id: str,
        job_id: str,
        *,
        report: str = REPORT,
        sources: object = SOURCES_JSON,
        brief: object = BRIEF_JSON,
        minutes: float = 0.0,
        **kwargs: Any,
    ) -> Path:
        """一張跑完的分析工單：brief 與查證兩段的產出、放好的來源正文。"""
        job_dir = self.job(conversation_id, job_id, minutes=minutes, **kwargs)
        self.write(job_id, "stages/brief/1/brief.md", "# brief\n")
        self.write(job_id, "stages/brief/1/brief.json", brief)
        self.write(job_id, "stages/investigate/1/report.md", report)
        self.write(job_id, "stages/investigate/1/sources.json", sources)
        self.place_sources(job_id, minutes)
        return job_dir

    def place_sources(self, job_id: str, minutes: float = 0.0) -> None:
        """執行器放進工單的來源正文（`input/sources/S<n>.md`）；重構工單從分析工單原樣帶過去。"""
        for number, (title, url) in enumerate(
            [("價目表", "https://pricing.example/list"), ("廠商部落格", "https://vendor.example/blog")],
            start=2,
        ):
            self.write(
                job_id,
                f"input/sources/S{number}.md",
                f"來源編號：S{number}\n標題：{title}\n網址：{url}\n抓取時間：{at(minutes + 1).isoformat()}\n\n---\n\n正文\n",
            )

    def article(
        self,
        conversation_id: str,
        job_id: str,
        *,
        text: str = ARTICLE_V1,
        title: str = "長上下文不會取代檢索",
        overlaps: dict[str, float] | None = None,
        minutes: float = 0.0,
        persona: str = "me",
        genre: str = "explainer",
        **kwargs: Any,
    ) -> Path:
        """一張跑完的重構工單：文章、標題候選、跟各份文字的重疊檢查。"""
        job_dir = self.job(
            conversation_id,
            job_id,
            recipe="rewrite",
            persona=persona,
            genre=genre,
            minutes=minutes,
            **kwargs,
        )
        self.place_sources(job_id, minutes)
        self.write(job_id, "stages/rewrite/1/article.md", text)
        self.write(
            job_id,
            "stages/rewrite/1/article.json",
            {"title": title, "title_candidates": [title]},
        )
        entries = [
            {
                "check": "C3",
                "status": "pass",
                "reason": f"{label}：4-gram 重疊率 {rate:.1f}%（提醒門檻 10.0%）；最長共同片段 3 個詞（提醒門檻 12）",
            }
            for label, rate in (overlaps or {}).items()
        ]
        self.write(
            job_id,
            "checks/checks.v1.json",
            {
                "stage": "rewrite",
                "attempt": 1,
                "files": ["stages/rewrite/1/article.md"],
                "created_at": at(minutes + 2).isoformat(),
                "entries": entries or [{"check": "C5", "status": "pass", "reason": "格式正確"}],
            },
        )
        return job_dir

    def ledger(self, owner: str, rows: list[dict[str, Any]], *, conversation: bool = False) -> None:
        """寫帳本。`owner` 是工單 id，或（`conversation=True`）對話 id。"""
        folder = (
            self.data.conversation_dir(owner) if conversation else self.data.job_dir(owner)
        )
        folder.mkdir(parents=True, exist_ok=True)
        full = []
        for row in rows:
            entry: dict[str, Any] = {
                "ts": at().isoformat(),
                "stage": "brief",
                "attempt": 1,
                "kind": "llm",
                "tier": "local",
                "model": "qwen3.5:9b",
                "latency_ms": 1000,
                "status": "ok",
                "request_hash": "b" * 64,
                **row,
            }
            entry["conversation_id" if conversation else "job_id"] = owner
            full.append(entry)
        with (folder / "ledger.jsonl").open("a", encoding="utf-8") as handle:
            for entry in full:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
