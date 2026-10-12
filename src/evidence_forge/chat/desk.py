"""對話的工作台：替對話開分析工單、讀工單的狀態、報告、文章與角色清單（ADR-0021 §3.4）。

閘道的 `chat_tools` 只管鑰匙、範圍與記帳；開單與讀結果在這裡，因為這裡知道配方與資料目錄。
開單照舊走 `runner.intake.open_job`：同一個對話裡同一份素材、同樣的目標已有工單就拿回那一張（ADR-0021 §3.6）。
開出來的單是「排隊中」，由同一個 `ef serve` 裡的執行器照配方跑（ADR-0019 §3.3）。

報告與文章都是工單最後交出來的檔，只是看哪個檔：
- 報告：產出 `report.md` 的那一段（分析配方的查證段，連同 `sources.json`）。
- 文章：產出 `article.md` 的那一段（重構配方，#79）；不是重構的單就說沒有文章。
每個檔最多回 `MAX_FILE_CHARS` 字，太長就截掉並註明：整份是給對話的模型讀的，不能塞爆它的上下文。
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from evidence_forge.documents import read_job
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.models import Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import (
    PastedText,
    Source,
    SourceUnreadable,
    WebPage,
    open_job,
)
from evidence_forge.runner.stage_inputs import default_genre

MAX_FILE_CHARS = 20_000
REPORT_FILE = "report.md"
ARTICLE_FILE = "article.md"
_UNFINISHED = ("queued", "running")


class Desk:
    def __init__(
        self,
        data: DataDir,
        recipes: Mapping[str, Recipe],
        recipe_name: str,
        *,
        clock: Callable[[], datetime],
        fetcher: Fetcher | None = None,
        budget_usd: float = 0.5,
        on_opened: Callable[[str, str], None] | None = None,
    ) -> None:
        """`recipe_name` 是 `analyze` 開的配方；找不到不擋啟動，開單時才說（`ef serve` 照樣能跑工單）。

        `on_opened(對話 id, 工單 id)` 在開了新單（或拿回舊單）之後呼叫：對話主機據此馬上推一次進度。
        """
        self._data = data
        self._recipes = recipes
        self._recipe_name = recipe_name
        self._clock = clock
        self._fetcher = fetcher
        self._budget_usd = budget_usd
        self._on_opened = on_opened

    @property
    def recipe_name(self) -> str:
        return self._recipe_name

    def open_analysis(
        self,
        *,
        conversation_id: str,
        url: str | None,
        text: str | None,
        goal: str | None,
        again: bool,
        before_create: Callable[[], None],
    ) -> dict[str, Any]:
        recipe = self._recipes.get(self._recipe_name)
        if recipe is None:
            return {
                "status": "rejected",
                "reason": f"沒有分析配方 {self._recipe_name}（有：{'、'.join(sorted(self._recipes))}）",
            }
        source: Source = WebPage(url) if url is not None else PastedText(text or "")
        try:
            intake = open_job(
                self._data,
                recipe,
                source,
                goal=goal,
                now=self._clock(),
                fetcher=self._fetcher,
                budget_usd=self._budget_usd,
                conversation=conversation_id,
                again=again,
                before_create=before_create,
            )
        except SourceUnreadable as exc:
            # 讀不到就不開工，原因交回對話講清楚（FR-IN-01）
            return {"status": "unavailable", "reason": exc.reason}
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc)}
        if self._on_opened is not None:
            self._on_opened(conversation_id, intake.job.id)
        return {
            "job_id": intake.job.id,
            "created": intake.created,
            "status": intake.job.status,
            "recipe": intake.job.recipe,
            "stages": len(recipe.stages),
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = read_job(self._data.job_dir(job_id) / "job.md").job
        found: dict[str, Any] = {
            "job_id": job_id,
            "recipe": job.recipe,
            "status": job.status,
        }
        if job.result is not None:
            found["outcome"] = job.result.outcome
            found["reason"] = job.result.reason
        if job.stage is not None:
            found["stage"] = {
                "id": job.stage.id,
                "index": job.stage.index,
                "total": job.stage.total,
                "attempt": job.stage.attempt,
            }
        return found

    def report(self, job_id: str) -> dict[str, Any]:
        """工單的報告：產出 `report.md` 的那一段，編號最大那一次的產出。還沒跑完就只回狀態。

        配方沒有一段產出 `report.md`（打通用的 echo 這類）就拿最後一段，但重構的文章不算報告。
        """

        def report_stage(recipe: Recipe) -> RecipeStage | None:
            named = next((s for s in recipe.stages if REPORT_FILE in s.output), None)
            if named is not None:
                return named
            last = recipe.stages[-1]
            return None if ARTICLE_FILE in last.output else last

        return self._result(job_id, report_stage, "報告")

    def article(self, job_id: str) -> dict[str, Any]:
        """工單的文章：產出 `article.md` 的那一段。不是重構的單就說沒有文章。"""

        def article_stage(recipe: Recipe) -> RecipeStage | None:
            return next((s for s in recipe.stages if ARTICLE_FILE in s.output), None)

        return self._result(job_id, article_stage, "文章")

    def personas(self) -> dict[str, Any]:
        """現有的角色：`authors/` 底下每個資料夾一個（`_`、`.` 開頭的是範本或暫存，不算）。

        每個角色附預設文類與寫過風格的文類（`genres/<文類>.md`）；沒寫過的文類寫出來是未校準。
        """
        root = self._data.authors
        found: list[dict[str, Any]] = []
        if root.is_dir():
            for folder in sorted(root.iterdir()):
                if not folder.is_dir() or folder.name.startswith(("_", ".")):
                    continue
                genres_dir = folder / "genres"
                calibrated = (
                    sorted(p.stem for p in genres_dir.glob("*.md"))
                    if genres_dir.is_dir()
                    else []
                )
                found.append(
                    {
                        "name": folder.name,
                        "default_genre": default_genre(self._data, folder.name),
                        "calibrated_genres": calibrated,
                    }
                )
        return {"personas": found}

    def _result(
        self,
        job_id: str,
        pick: Callable[[Recipe], RecipeStage | None],
        what: str,
    ) -> dict[str, Any]:
        found = self.status(job_id)
        if found["status"] in _UNFINISHED:
            found["note"] = "還沒跑完；跑完會自動通知這個對話"
            return found
        recipe = self._recipes.get(found["recipe"])
        stage = pick(recipe) if recipe is not None else None
        if stage is None:
            found["note"] = f"這張工單沒有{what}"
            found["files"] = {}
            return found
        job_dir = self._data.job_dir(job_id)
        files: dict[str, str] = {}
        attempt_dir = _latest_attempt(job_dir / "stages" / stage.id)
        if attempt_dir is not None:
            for name in stage.output:
                path = attempt_dir / name
                if path.is_file():
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if len(content) > MAX_FILE_CHARS:
                        content = content[:MAX_FILE_CHARS] + "\n…（後面截掉了）"
                    files[path.relative_to(job_dir).as_posix()] = content
        found["files"] = files
        return found


def _latest_attempt(stage_root: Path) -> Path | None:
    if not stage_root.is_dir():
        return None
    attempts = [p for p in stage_root.iterdir() if p.is_dir() and p.name.isdigit()]
    return max(attempts, key=lambda p: int(p.name)) if attempts else None
