"""對話的工作台：替對話開分析與重構工單、讀工單的狀態、報告、文章與角色清單（ADR-0021 §3.4）。

閘道的 `chat_tools` 只管鑰匙、範圍與記帳；開單與讀結果在這裡，因為這裡知道配方與資料目錄。
開單照舊走 `runner.intake.open_job`：同一個對話裡同一份素材、同樣的目標已有工單就拿回那一張（ADR-0021 §3.6）。
重構照舊走 `runner.intake.open_job_from`（等同 `ef new --from`，#102）：每一版都是新工單，不走去重。
文體（ADR-0026 的 7 種）可以給 slug（`explainer`）、中文名（`理論教學`），或報告「建議文體」的寫法
「中文名（slug）」。創作者明說的文體（`genre`）對不上就不開單、列出有哪些；報告的建議文體（`suggested_genre`）
是模型寫的自由文字（「評論」，或舊報告的「觀點文」），對不上就照沒給文體開單（角色的預設文體），結果附一句
`note` 讓對話的 agent 跟創作者說。舊的 12 種文類不做對照（ADR-0026 §3.4）。不認得的文體都不寫進工單，
免得執行器放參考資料時才失敗。
開不了單的原因會交給對話的模型：裡面資料目錄的絕對路徑換成相對路徑（`authors/<角色>/`）。
開出來的單是「排隊中」，由同一個 `ef serve` 裡的執行器照配方跑（ADR-0019 §3.3）。

「照這張編輯卡寫稿」（`write_from_card`，ADR-0025 §3.3～§3.4、#110）是創作者在畫面上按的，只由對話主機叫
（閘道的 `/tools/` 一律拒絕）：再查一次卡能不能寫、事件筆數跟創作者確認時一樣，拿快照照 `rewrite` 配方開單
（`runner.intake.open_job_from_card`），記進對話的 `jobs.jsonl`（版本鏈的第 1 版）。它不經閘道的工具，
所以「數在跑的單 → 開單 → 記進去」在這裡做，跟閘道共用同一把對話的開單鎖（`chat_tools.opening_lock`）。

報告與文章都是工單最後交出來的檔，只是看哪個檔：
- 報告：產出 `report.md` 的那一段（分析配方的查證段，連同 `sources.json`）。
- 文章：產出 `article.md` 的那一段（重構配方，#79）；不是重構的單就說沒有文章。
每個檔最多回 `MAX_FILE_CHARS` 字，太長就截掉並註明：整份是給對話的模型讀的，不能塞爆它的上下文。
"""

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from evidence_forge import cards, confined, conversations, cost_settings
from evidence_forge.documents import FrontmatterError, read_job, split_frontmatter
from evidence_forge.gateway.chat_tools import opening_lock, running_jobs, too_many_running
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.models import Recipe, RecipeStage
from evidence_forge.persona_spec import genre_file
from evidence_forge.paths import DataDir
from evidence_forge.runner.intake import (
    CardChanged,
    PastedText,
    Source,
    SourceUnreadable,
    WebPage,
    open_job,
    open_job_from,
    open_job_from_card,
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
        max_running_jobs: int = 1,
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

        `max_running_jobs`：「照這張編輯卡寫稿」開單時，這個對話同一時間最多幾張單在跑
        （跟閘道的 `chat_max_running_jobs` 同一個數字，`ef serve` 傳進來）。
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
        self._max_running_jobs = max_running_jobs

    @property
    def recipe_name(self) -> str:
        return self._recipe_name

    @property
    def rewrite_recipe(self) -> str:
        return self._rewrite_recipe

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

        角色、文體、目標沒給的照 `open_job_from`：再改沿用上一版，從分析開是 `me`、角色的預設文體、
        配方的預設目標。文體有兩個來源，對不上時處理不同：
        - `genre`：創作者明說的文體。對不上就不開單、列出有哪些，不能默默改寫成別的文體。
        - `suggested_genre`：報告「可以寫的題材」的建議文體，是模型寫的自由文字。對不上就當成沒給，
          結果附 `note`，「寫第 N 個」不會因此開不了。兩個都給時以 `genre` 為準。
        開不了（角色不存在、上一張還沒跑完或交不出報告）回 `rejected` 與原因。
        照編輯卡寫的文章：卡在寫好之後又改過、或改稿要換文體，丟 `ToolRejected(409)`（`CardChanged`，#110）。
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
                return {"status": "rejected", "reason": f"沒有文體 {genre}（有：{listed}）"}
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
                # 沿用來的文體（上一張單的、角色的預設）也要是 7 種之一，不然開了只會在執行器失敗
                allowed_genres=GENRE_NAME,
            )
        except CardChanged as exc:
            # 照編輯卡寫的文章：卡改過、或要換文體（#110）。不是重試修得好的，要創作者改卡、再按寫稿
            raise ToolRejected(409, self._tidy(str(exc))) from None
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
                else "角色的寫法（沒有預設文體）"
            )
            opened["note"] = f"文體「{unknown_genre}」對不上，改用預設的{used}"
        return opened

    def write_from_card(
        self, conversation_id: str, card_id: str, confirmed_events: object
    ) -> dict[str, Any]:
        """「照這張編輯卡寫稿」：開一張寫稿工單，記進對話，通知對話主機。錯誤丟 `ToolRejected`（帶狀態碼）。

        `confirmed_events` 是創作者按確認時畫面上那張卡的事件筆數（`card.events`）。開單前拿著卡的鎖讀一份快照
        （`cards.snapshot`）：還不能寫（409，說是哪幾關）、筆數對不上（確認之後又有人改過，409，請他重新確認）
        都不開。卡不是這個對話的 404。寫稿讀的就是這份快照，不再讀活的 `card.json`。
        """
        if (
            isinstance(confirmed_events, bool)
            or not isinstance(confirmed_events, int)
            or confirmed_events < 0
        ):
            raise ToolRejected(400, "confirmed_events 要是創作者確認時那張卡的事件筆數（0 以上的整數）")
        recipe = self._recipes.get(self._rewrite_recipe)
        if recipe is None:
            raise ToolRejected(409, f"沒有重構配方 {self._rewrite_recipe}（有：{'、'.join(sorted(self._recipes))}）")
        # 最後一道關（ADR-0025 §3.3）：六關都有決定、沒有要重確認
        cards.require_writable(self._data, conversation_id, card_id)
        taken = cards.snapshot(self._data, conversation_id, card_id)
        card = taken.card
        if not card.writable:
            # require_writable 與拿快照之間有人改了卡
            raise ToolRejected(409, "還不能照這張編輯卡寫稿：編輯卡剛剛改過，請重新確認")
        if card.events != confirmed_events:
            raise ToolRejected(
                409,
                f"編輯卡在確認之後又改過（確認時 {confirmed_events} 筆，現在 {card.events} 筆）："
                "請重新確認（看過最後的確認卡，再按一次「照這張編輯卡寫稿」）",
            )
        origin = card.origin
        parent = origin.job_id if origin is not None and origin.kind == "report_topic" else None
        if parent is not None and not conversations.owns_job(self._data, conversation_id, parent):
            # 範圍照 ADR-0021：只從這個對話自己的分析接著寫
            raise ToolRejected(403, "這張卡的分析工單不是這個對話開的")
        with opening_lock(conversation_id):
            running = running_jobs(self._data, conversation_id)
            if len(running) >= self._max_running_jobs:
                raise too_many_running(running, self._max_running_jobs)
            try:
                job = open_job_from_card(
                    self._data,
                    recipe,
                    taken,
                    now=self._clock(),
                    budget_usd=self._budget("rewrite"),
                    conversation=conversation_id,
                    allowed_genres=GENRE_NAME,
                )
            except ValueError as exc:
                raise ToolRejected(409, self._tidy(str(exc))) from None
            # 新文章是一條新的版本鏈（CONTEXT.md〈版本鏈〉）；從報告開始時接在那張分析後面
            conversations.add_job(
                self._data,
                conversation_id,
                conversations.ConversationJob(job_id=job.id, ts=self._clock(), version=1, parent=parent),
            )
        self.recorded(conversation_id, job.id)
        return {
            "job_id": job.id,
            "created": True,
            "status": job.status,
            "recipe": job.recipe,
            "stages": len(recipe.stages),
            "card_id": card.card_id,
            "events": card.events,
            "persona": job.persona,
            "genre": job.target_genre,
            "theme": card.theme,
        }

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
        """現有的角色（閘道的 `list_personas`）；清單怎麼來的見模組層的 `list_personas`。"""
        return list_personas(self._data)

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


# 報告「建議文體」的寫法：中文名（slug），括號全形半形都收
_NAME_AND_SLUG = re.compile(r"^(.+?)\s*[（(]\s*([^）)]+?)\s*[）)]$")


def list_personas(data: DataDir) -> dict[str, Any]:
    """現有的角色：`authors/` 底下每個資料夾一個（`_`、`.` 開頭的是範本或暫存，不算）。

    每個角色附預設文體、寫過風格的文體（`genres/<文體>.md`）與其中校準過的
    （front matter 寫 `calibrated: true`）；只列 7 種，舊的 12 種文類與範本檔不算。
    沒校準的文體寫出來標未校準。匯入角色前的備份放在 `authors/.backup/`，`.` 開頭，不算角色。

    對話的 `list_personas` 工具與「角色」頁（#116）都走這一份，兩邊看到的角色清單一定一樣。
    **先看資料夾本身、再讀裡面的檔，而且每個檔都不跟隨符號連結**（`confined`）：角色資料夾是連結、
    或 `voice.md`、風格檔是連結（不管指到哪），就當裡面什麼都沒有（只列名字），不讀、也不把外面檔案的內容
    （例如它的 `default_genre`）寫進回應；這份清單會交給對話的模型，也會顯示在畫面上。
    """
    root = data.authors
    found: list[dict[str, Any]] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            with confined.open_root(folder) as handle:
                real = handle is not None  # 最後一節是連結的資料夾開不起來
            written: list[str] = []
            calibrated: list[str] = []
            default: str | None = None
            if real:
                for slug in sorted(GENRE_NAME):
                    kind, text = confined.read_text(folder, genre_file(slug))
                    if kind in ("file", "large"):  # 太大不讀內容，但檔案是寫過的
                        written.append(slug)
                        if text is not None and _calibrated(text):
                            calibrated.append(slug)
                default = default_genre(data, folder.name)
            entry: dict[str, Any] = {
                "name": folder.name,
                # 舊角色的預設文類可能還是 12 種的 id：不認得的不列，免得看起來像第 8 種文體
                "default_genre": default if default in GENRE_NAME else None,
                "written_genres": written,
                "calibrated_genres": calibrated,
            }
            if default is not None and default not in GENRE_NAME:
                entry["note"] = f"預設文體 {default} 不是現在的 7 種之一，當成沒有預設文體"
            found.append(entry)
    return {"personas": found}


def _calibrated(text: str) -> bool:
    """風格檔的 front matter 寫了 `calibrated: true`；讀不懂當成沒校準（跟 `stage_inputs.uncalibrated` 同一條）。"""
    try:
        return split_frontmatter(text)[0].get("calibrated") is True
    except FrontmatterError:
        return False


def _genre_id(genre: str) -> str | None:
    """文體 slug、中文名或「中文名（slug）」→ slug；不認得、或名字跟 slug 指的不是同一種是 None。"""
    wanted = genre.strip()
    matched = _NAME_AND_SLUG.match(wanted)
    if matched is not None:
        name, slug = matched.groups()
        return slug if GENRE_NAME.get(slug) == name.strip() else None
    if wanted in GENRE_NAME:
        return wanted
    return next((key for key, name in GENRE_NAME.items() if name == wanted), None)


def _latest_attempt(stage_root: Path) -> Path | None:
    if not stage_root.is_dir():
        return None
    attempts = [p for p in stage_root.iterdir() if p.is_dir() and p.name.isdigit()]
    return max(attempts, key=lambda p: int(p.name)) if attempts else None
