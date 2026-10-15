"""「角色」頁（#116）：列出角色、看每個檔（唯讀）、新增（範本或複製）、改顯示名稱。

| 路徑 | 做什麼 |
| :--- | :--- |
| `GET /personas` | 角色清單：顯示名稱、寫過幾篇、文體校準了幾種、缺了哪些部分；加一個「新增角色」的表單 |
| `GET /personas/<角色>` | 一個角色：資料夾的每一部分（`persona.PARTS`）唯讀顯示，缺的標出來並說明用什麼預設；改顯示名稱的表單 |
| `GET /api/personas` | 角色清單的 JSON：就是對話的 `list_personas` 那一份（`chat.desk.list_personas`），每個角色多一欄 `display_name`（沒設是 null） |
| `POST /api/personas` | 新增：`{"name", "display_name"?, "from"?}`；`from` 是既有角色就複製，沒給就用範本 → 201 `{"persona", "files"}`；名字已經有 409，不合規則 400 |
| `POST /api/personas/<角色>/rename` | 改顯示名稱：`{"display_name"}` → `{"persona", "display_name"}`（不改資料夾名字） |

跟聊天室其他頁一樣：只綁 127.0.0.1，POST 只收同源的 `application/json`（`chat/api.py` 的中介層擋）。
這一頁**不編輯**角色的任何檔；檔案只能讀。角色的內容是創作者私人的寫作系統（ADR-0014），
所以：每個檔都經 `confined`（`O_NOFOLLOW` 加資料夾 fd，沿路不跟隨任何符號連結，檢查與讀取同一個 fd；
連結一律標「不讀」，內容不進畫面），Markdown 先過 `ui/markdown.py`（原始 HTML 不放行）。
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError

from evidence_forge import confined, persona
from evidence_forge.chat.desk import list_personas
from evidence_forge.documents import FrontmatterError, split_frontmatter
from evidence_forge.figures.brand import Brand
from evidence_forge.harness import Harness
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir
from evidence_forge.persona import GENRE_SLUGS, PARTS, PersonaError, PartSpec
from evidence_forge.ui import artifacts, markdown
from evidence_forge.ui.format import GENRE_NAME

# 最近的 lessons 顯示幾筆（跟訪談與寫稿開工前讀的一樣，ADR-0026 §3.3）
RECENT_LESSONS = 10
_MAX_CELL = 200

State = Literal["ok", "unsafe", "unreadable", "large"]


@dataclass(frozen=True)
class FileView:
    """角色資料夾裡的一個檔。`unsafe`：符號連結（不跟隨）或不是一般檔案，不讀；`unreadable`：不是 UTF-8；
    `large`：超過 `confined.MAX_TEXT_BYTES`，不讀、畫面說太大。"""

    path: str
    state: State
    html: Markup = Markup("")
    # front matter 的欄位（`calibrated: false` 這類），小字顯示在檔名旁
    meta: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PartView:
    spec: PartSpec
    files: tuple[FileView, ...]

    @property
    def present(self) -> bool:
        return any(f.state == "ok" for f in self.files)

    @property
    def blocked(self) -> bool:
        return any(f.state != "ok" for f in self.files)


@dataclass(frozen=True)
class GenreRow:
    slug: str
    name: str
    written: bool
    calibrated: bool


@dataclass(frozen=True)
class Detail:
    name: str
    shown: str
    has_display_name: bool
    parts: tuple[PartView, ...]
    genres: tuple[GenreRow, ...]
    lessons: tuple[str, ...]
    lessons_total: int
    brand_rows: tuple[tuple[str, str], ...]
    brand_problem: str | None
    articles: int
    versions: int
    missing: tuple[PartSpec, ...] = field(default=())


# --- 讀角色資料夾 -------------------------------------------------------------


def _paths(folder: Path, spec: PartSpec) -> list[str]:
    """這一部分在硬碟上有的東西（相對於角色資料夾的路徑）。連結也算「有」，讓畫面能說它被擋下了。"""
    if spec.key == "genres":
        names = [persona.genre_file(slug) for slug in GENRE_SLUGS]
    elif spec.key == "craft":
        kind, entries = confined.listdir(folder, persona.CRAFT_DIR)
        if kind in ("link", "other"):
            return [persona.CRAFT_DIR]  # 資料夾本身是連結：整個不讀
        names = [f"{persona.CRAFT_DIR}/{n}" for n, _ in entries if n.endswith(".md")]
    else:
        names = [spec.path]
    return [n for n in names if confined.kind_of(folder, n) != "missing"]


def _view_file(folder: Path, relative: str) -> FileView:
    kind, text = confined.read_text(folder, relative)
    if kind == "large":
        return FileView(relative, "large")
    if kind != "file":
        return FileView(relative, "unsafe")
    if text is None:
        return FileView(relative, "unreadable")
    meta: tuple[tuple[str, str], ...] = ()
    body = text
    if text.startswith("---"):
        try:
            front, body = split_frontmatter(text)
            meta = tuple((str(k), _cell(v)) for k, v in front.items())
        except FrontmatterError:
            body = text
    return FileView(relative, "ok", markdown.render(body), meta)


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_cell(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(v) for v in value)
    text = "" if value is None else str(value)
    return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 1] + "…"


def part_views(folder: Path, *, render: bool = True) -> tuple[PartView, ...]:
    """`persona.PARTS` 的每一部分各一個 `PartView`。`render=False` 只看有沒有、不讀內容（清單頁用）。

    每個檔都不跟隨符號連結（`confined`）：連結（不管指到哪）標成不讀，內容不會出現在畫面上。
    """
    views: list[PartView] = []
    for spec in PARTS:
        files: list[FileView] = []
        for relative in _paths(folder, spec):
            if render:
                files.append(_view_file(folder, relative))
            else:
                files.append(FileView(relative, "ok" if confined.kind_of(folder, relative) == "file" else "unsafe"))
        views.append(PartView(spec, tuple(files)))
    return tuple(views)


def genre_rows(folder: Path) -> tuple[GenreRow, ...]:
    """7 種文體各一列：這個角色寫了沒、校準了沒（front matter 寫 `calibrated: true`，跟執行器同一條規則）。"""
    rows: list[GenreRow] = []
    for slug in GENRE_SLUGS:
        kind, text = confined.read_text(folder, persona.genre_file(slug))
        calibrated = False
        if text is not None:
            try:
                calibrated = split_frontmatter(text)[0].get("calibrated") is True
            except FrontmatterError:
                calibrated = False
        rows.append(GenreRow(slug, GENRE_NAME[slug], kind in ("file", "large"), calibrated))
    return tuple(rows)


def recent_lessons(folder: Path) -> tuple[tuple[str, ...], int]:
    """`lessons.md` 最近 10 筆（新的在前）與總數。一筆一行、以 `- ` 開頭；`<!-- -->` 註解不算。"""
    text = confined.text(folder, persona.LESSONS_FILE)
    if text is None:
        return (), 0
    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    items = [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]
    items = [item for item in items if item]
    return tuple(reversed(items[-RECENT_LESSONS:])), len(items)


def brand_table(folder: Path) -> tuple[tuple[tuple[str, str], ...], str | None]:
    """`brand.yaml` 攤成「欄位 → 值」的表（巢狀的欄位用 `.` 串起來）；檔案讀不懂或欄位不合格就說原因。

    表列的是檔裡實際寫了什麼；沒寫的欄位用中性預設值（`figures.brand`），所以不列。
    欄位合不合格用讀進來的同一份文字驗（`Brand.model_validate`），不再用路徑開第二次。
    """
    text = confined.text(folder, persona.BRAND_FILE)
    if text is None:
        return (), None
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return (), "不是合法的 YAML"
    problem: str | None = None
    if loaded is not None and not isinstance(loaded, dict):
        problem = "最上層必須是欄位對照（mapping）"
    else:
        try:
            Brand.model_validate(loaded or {})
        except ValidationError as exc:
            found = [f"{'.'.join(str(p) for p in e['loc'])}：{e['msg']}" for e in exc.errors()[:3]]
            problem = "欄位不合格（" + "；".join(found) + "）"
    rows: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict) and value:
            for key, inner in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), inner)
        else:
            rows.append((prefix, _cell(value)))

    if isinstance(loaded, dict):
        walk("", loaded)
    return tuple(rows), problem


# --- 寫過幾篇 -----------------------------------------------------------------


def article_counts(data: DataDir, recipes: Mapping[str, Recipe]) -> dict[str, tuple[int, int]]:
    """每個角色寫過的（文章篇數，版本數）：真的交出 `article.md` 的重構工單，同一條版本鏈算一篇。

    看對話與工單資料夾，不看帳本：帳本只記有花錢的那幾個月，這裡要的是全部。
    帳單的「依角色」（`bills.month_view`）照舊，那是這個月花了多少。
    """
    chains: dict[str, set[tuple[str, int]]] = {}
    versions: dict[str, int] = {}
    groups: list[tuple[str, list[artifacts.JobInfo]]] = []
    root = data.conversations
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            try:
                data.conversation_dir(folder.name)
                groups.append((folder.name, artifacts.conversation_jobs(data, dict(recipes), folder.name)))
            except (OSError, ValueError):
                continue  # 名字不合規則、或對話資料夾讀不懂：不算
    groups.append((artifacts.LOOSE, artifacts.loose_jobs(data, dict(recipes))))
    for group, infos in groups:
        for info in infos:
            if info.kind != "rewrite" or not artifacts.has_output(data, info):
                continue
            who = info.job.persona or artifacts.DEFAULT_PERSONA
            chains.setdefault(who, set()).add((group, info.chain))
            versions[who] = versions.get(who, 0) + 1
    return {who: (len(found), versions[who]) for who, found in chains.items()}


# --- 組畫面要的東西 -----------------------------------------------------------


def build_detail(data: DataDir, name: str, counts: Mapping[str, tuple[int, int]]) -> Detail:
    folder = persona.folder_of(data, name)
    parts = part_views(folder)
    lessons, total = recent_lessons(folder)
    rows, problem = brand_table(folder)
    shown = persona.display_name(data, name)
    articles, versions = counts.get(name, (0, 0))
    return Detail(
        name=name,
        shown=shown or name,
        has_display_name=shown is not None,
        parts=parts,
        genres=genre_rows(folder),
        lessons=lessons,
        lessons_total=total,
        brand_rows=rows,
        brand_problem=problem,
        articles=articles,
        versions=versions,
        missing=tuple(p.spec for p in parts if not p.present),
    )


def build_rows(data: DataDir, counts: Mapping[str, tuple[int, int]]) -> list[dict[str, Any]]:
    """清單頁每個角色一列。角色清單就是對話的 `list_personas`；這裡只再加顯示名稱、篇數、缺什麼。"""
    rows: list[dict[str, Any]] = []
    for entry in list_personas(data)["personas"]:
        name = entry["name"]
        openable = persona.name_is_valid(name)
        missing: list[PartSpec] = []
        problem: str | None = None
        shown = None
        if openable:
            try:
                folder = persona.folder_of(data, name)
            except PersonaError as exc:
                openable, problem = False, str(exc)
            else:
                missing = [p.spec for p in part_views(folder, render=False) if not p.present]
                shown = persona.display_name(data, name)
        else:
            problem = "資料夾名字不合規則（只能英數字開頭，之後英數字、點、底線、連字號），這一頁打不開它"
        articles, versions = counts.get(name, (0, 0))
        rows.append(
            {
                **entry,
                "display_name": shown,
                "shown": shown or name,
                "openable": openable,
                "problem": problem,
                "articles": articles,
                "versions": versions,
                "calibrated": len(entry["calibrated_genres"]),
                "written": len(entry["written_genres"]),
                "missing": missing,
            }
        )
    return rows


# --- 路由 ---------------------------------------------------------------------


def install(
    app: FastAPI,
    *,
    templates: Jinja2Templates,
    data: DataDir,
    recipes: Mapping[str, Recipe],
    harness: Harness,
    clock: Callable[[], datetime],
    nav_count: Callable[[datetime], Awaitable[int]],
) -> None:
    def page(request: Request, name: str, context: dict[str, Any], status: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context, status_code=status)

    async def counts() -> dict[str, tuple[int, int]]:
        return await asyncio.to_thread(article_counts, data, recipes)

    @app.get("/personas", response_class=HTMLResponse)
    async def personas_page(request: Request) -> HTMLResponse:
        count = await nav_count(clock())
        rows = await asyncio.to_thread(build_rows, data, await counts())
        return page(
            request,
            "personas.html",
            {
                "nav": "personas",
                "new_count": count,
                "rows": rows,
                "page_title": (f"({count}) " if count else "") + "Evidence Forge・角色",
            },
        )

    @app.get("/personas/{name}", response_class=HTMLResponse)
    async def persona_page(request: Request, name: str) -> HTMLResponse:
        count = await nav_count(clock())
        try:
            detail = await asyncio.to_thread(build_detail, data, name, await counts())
        except PersonaError as exc:
            return page(
                request,
                "error.html",
                {
                    "nav": "personas",
                    "new_count": count,
                    "title": "找不到這個角色",
                    "body": f"{exc}。回角色頁看有哪些角色。",
                    "page_title": "找不到這個角色・Evidence Forge",
                },
                404,
            )
        return page(
            request,
            "persona.html",
            {
                "nav": "personas",
                "new_count": count,
                "d": detail,
                "max_mb": confined.MAX_TEXT_BYTES // 1_000_000,
                "page_title": (f"({count}) " if count else "") + f"Evidence Forge・角色 {detail.shown}",
            },
        )

    def listed() -> list[dict[str, Any]]:
        found = list_personas(data)["personas"]
        for entry in found:
            entry["display_name"] = persona.display_name(data, entry["name"])
        return found  # type: ignore[no-any-return]

    @app.get("/api/personas")
    async def personas_api() -> Response:
        return JSONResponse({"personas": await asyncio.to_thread(listed)})

    async def body_of(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "請求內容要是 JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "請求內容要是 JSON 物件"}, status_code=400)
        return body

    @app.post("/api/personas")
    async def create_persona(request: Request) -> Response:
        # 跨站與表單偷送由 `chat/api.py` 的中介層擋（只收本機、同源、application/json）
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        name = body.get("name")
        origin = body.get("from")
        if not isinstance(name, str):
            return JSONResponse({"error": "要有字串欄位 name"}, status_code=400)
        if origin is not None and not isinstance(origin, str):
            return JSONResponse({"error": "from 要是既有角色的名字，或不給（用範本）"}, status_code=400)
        shown = body.get("display_name")

        def work() -> dict[str, Any]:
            if origin is None:
                return {"files": persona.create_from_template(data, harness, name, shown)}
            copied = persona.copy_persona(data, origin, name, shown)
            return {"files": copied.files, "skipped_links": list(copied.skipped_links)}

        try:
            result = await asyncio.to_thread(work)
        except PersonaError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except OSError as exc:
            return JSONResponse({"error": f"沒建成：{exc.strerror or exc}"}, status_code=500)
        return JSONResponse({"persona": name, **result}, status_code=201)

    @app.post("/api/personas/{name}/rename")
    async def rename_persona(request: Request, name: str) -> Response:
        body = await body_of(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            shown = await asyncio.to_thread(persona.rename, data, name, body.get("display_name"))
        except PersonaError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except OSError as exc:
            return JSONResponse({"error": f"沒存成：{exc.strerror or exc}"}, status_code=500)
        return JSONResponse({"persona": name, "display_name": shown})
