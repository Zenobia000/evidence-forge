"""對話的工作台：替對話開分析與重構工單、讀工單的狀態、報告、文章與角色清單（ADR-0021 §3.4）。

閘道的 `chat_tools` 只管鑰匙、範圍與記帳；開單與讀結果在這裡，因為這裡知道配方與資料目錄。
開單照舊走 `runner.intake.open_job`：同一個對話裡同一份素材、同樣的目標已有工單就拿回那一張（ADR-0021 §3.6）。
重構照舊走 `runner.intake.open_job_from`（等同 `ef new --from`，#102）：每一版都是新工單，不走去重。
文類可以給 id（`opinion`）或中文名（`觀點文`，報告的「建議文類」寫的是中文名）。
創作者明說的文類（`genre`）對不上就不開單、列出有哪些；報告的建議文類（`suggested_genre`）是模型寫的
自由文字（「評論」「深度報導」），對不上就照沒給文類開單（角色的預設文類），結果附一句 `note` 讓對話的 agent
跟創作者說。不認得的文類都不寫進工單，免得執行器放參考資料時才失敗。
開不了單的原因會交給對話的模型：裡面資料目錄的絕對路徑換成相對路徑（`authors/<角色>/`）。
開出來的單是「排隊中」，由同一個 `ef serve` 裡的執行器照配方跑（ADR-0019 §3.3）。

報告與文章都是工單最後交出來的檔，只是看哪個檔：
- 報告：產出 `report.md` 的那一段（分析配方的查證段，連同 `sources.json`）。
- 文章：產出 `article.md` 的那一段（重構配方，#79）；不是重構的單就說沒有文章。
每個檔最多回 `MAX_FILE_CHARS` 字，太長就截掉並註明：整份是給對話的模型讀的，不能塞爆它的上下文。
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from evidence_forge import cost_settings
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
    open_job_from,
)
from evidence_forge.runner.stage_inputs import default_genre
from evidence_forge.ui.format import GENRE_NAME

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
        budget_usd: float | None = None,
        on_opened: Callable[[str, str], None] | None = None,
        rewrite_recipe: str = "rewrite",
        on_card: Callable[[str, str, list[dict[str, Any]]], None] | None = None,
    ) -> None:
        """`recipe_name` 是 `analyze` 開的配方、`rewrite_recipe` 是 `rewrite` 開的；
        找不到不擋啟動，開單時才說（`ef serve` 照樣能跑工單）。

        `budget_usd` 沒給時，每張單的預算照費用設定（`cost_settings`，分析與重構各一個數字），
        每次開單都重讀：設定頁改了不用重開 `ef serve`。

        `on_opened(對話 id, 工單 id)` 在閘道把單記進對話的 `jobs.jsonl` 之後呼叫（`recorded`）：
        對話主機據此馬上推一次進度，單已經跑完就送結束通知。不在開單當下叫：那時主機在 `jobs.jsonl`
        還找不到這張單，跑得快的單結束通知會丟掉。

        `on_card(對話 id, 卡 id, 新事件)` 在閘道替對話的 agent 寫完編輯卡的事件之後呼叫（ADR-0025 §3.3）：
        對話主機據此推 `ef_card` 給畫面（例如 agent 剛提出的問題卡）。
        """
        self._data = data
        self._recipes = recipes
        self._recipe_name = recipe_name
        self._clock = clock
        self._fetcher = fetcher
        self._budget_usd = budget_usd
        self._on_opened = on_opened
        self._rewrite_recipe = rewrite_recipe
        self._on_card = on_card

    @property
    def recipe_name(self) -> str:
        return self._recipe_name

    def _budget(self, kind: Literal["analysis", "rewrite"]) -> float:
        if self._budget_usd is not None:
            return self._budget_usd
        settings = cost_settings.load(self._data).settings
        return settings.analysis_budget_usd if kind == "analysis" else settings.rewrite_budget_usd

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
                budget_usd=self._budget("analysis"),
                conversation=conversation_id,
                again=again,
                before_create=before_create,
            )
        except SourceUnreadable as exc:
            # 讀不到就不開工，原因交回對話講清楚（FR-IN-01）
            return {"status": "unavailable", "reason": exc.reason}
        except ValueError as exc:
            return {"status": "rejected", "reason": self._tidy(str(exc))}
        return {
            "job_id": intake.job.id,
            "created": intake.created,
            "status": intake.job.status,
            "recipe": intake.job.recipe,
            "stages": len(recipe.stages),
        }

    def open_rewrite(
        self,
        *,
        conversation_id: str,
        from_job: str,
        persona: str | None,
        genre: str | None,
        suggested_genre: str | None,
        goal: str | None,
        feedback: str | None,
    ) -> dict[str, Any]:
        """從 `from_job` 接著開一張重構工單。`from_job` 是不是這個對話的，閘道已經查過。

        角色、文類、目標沒給的照 `open_job_from`：再改沿用上一版，從分析開是 `me`、角色的預設文類、
        配方的預設目標。文類有兩個來源，對不上時處理不同：
        - `genre`：創作者明說的文類。對不上就不開單、列出有哪些，不能默默改寫成別的文類。
        - `suggested_genre`：報告「可以寫的題材」的建議文類，是模型寫的自由文字。對不上就當成沒給，
          結果附 `note`，「寫第 N 個」不會因此開不了。兩個都給時以 `genre` 為準。
        開不了（角色不存在、上一張還沒跑完或交不出報告）回 `rejected` 與原因。
        """
        recipe = self._recipes.get(self._rewrite_recipe)
        if recipe is None:
            return {
                "status": "rejected",
                "reason": f"沒有重構配方 {self._rewrite_recipe}（有：{'、'.join(sorted(self._recipes))}）",
            }
        unknown_genre: str | None = None
        if genre is not None:
            known = _genre_id(genre)
            if known is None:
                listed = "、".join(f"{key}（{name}）" for key, name in GENRE_NAME.items())
                return {"status": "rejected", "reason": f"沒有文類 {genre}（有：{listed}）"}
            genre = known
        elif suggested_genre is not None:
            genre = _genre_id(suggested_genre)
            if genre is None:
                unknown_genre = suggested_genre
        try:
            job = open_job_from(
                self._data,
                recipe,
                from_job,
                goal=goal,
                now=self._clock(),
                persona=persona,
                genre=genre,
                feedback=feedback,
                budget_usd=self._budget("rewrite"),
                conversation=conversation_id,
            )
        except ValueError as exc:
            return {"status": "rejected", "reason": self._tidy(str(exc))}
        opened: dict[str, Any] = {
            "job_id": job.id,
            "created": True,
            "status": job.status,
            "recipe": job.recipe,
            "stages": len(recipe.stages),
            "persona": job.persona,
            "genre": job.target_genre,
        }
        if unknown_genre is not None:
            used = (
                f"{GENRE_NAME.get(job.target_genre, job.target_genre)}（{job.target_genre}）"
                if job.target_genre
                else "角色的寫法（沒有預設文類）"
            )
            opened["note"] = f"文類「{unknown_genre}」對不上，改用預設的{used}"
        return opened

    def recorded(self, conversation_id: str, job_id: str) -> None:
        """閘道已經把這張單記進對話的 `jobs.jsonl`：通知對話主機（見 `on_opened`）。"""
        if self._on_opened is not None:
            self._on_opened(conversation_id, job_id)

    def card_changed(
        self, conversation_id: str, card_id: str, events: list[dict[str, Any]]
    ) -> None:
        """閘道替對話的 agent 寫了編輯卡的事件：通知對話主機（見 `on_card`）。"""
        if self._on_card is not None:
            self._on_card(conversation_id, card_id, events)

    def _tidy(self, reason: str) -> str:
        """原因要交給對話的模型：資料目錄的絕對路徑換成相對路徑。"""
        return reason.replace(f"{self._data.root}/", "")

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


def _genre_id(genre: str) -> str | None:
    """文類 id 或中文名 → 文類 id；不認得是 None。"""
    wanted = genre.strip()
    if wanted in GENRE_NAME:
        return wanted
    return next((key for key, name in GENRE_NAME.items() if name == wanted), None)


def _latest_attempt(stage_root: Path) -> Path | None:
    if not stage_root.is_dir():
        return None
    attempts = [p for p in stage_root.iterdir() if p.is_dir() and p.name.isdigit()]
    return max(attempts, key=lambda p: int(p.name)) if attempts else None
