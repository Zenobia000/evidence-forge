"""聊天室畫面的頁面與片段（FR-UI-11、FR-UI-12；IA §2）。裝進對話 API 的 app，跟 API 同源。

| 路徑 | 做什麼 |
| :--- | :--- |
| `GET /`、`GET /c/<對話 id>` | 聊天室外框：三欄。內容由 `static/js/app.js` 依下面的片段與 API 填 |
| `GET /bills?month=2026-10` | 帳單：伺服器整頁產生，沒有前端狀態 |
| `GET /ui/list?current=<id>` | 對話清單（HTML 片段） |
| `GET /ui/c/<id>/thread` | 對話串流（HTML 片段）：Pi 的完整紀錄加工單；Pi 接不回來回 503 的錯誤片段 |
| `GET /ui/c/<id>/panel?tab=report｜article｜cost&v=<工單 id>&diff=1` | 右側面板（HTML 片段） |
| `GET /ui/c/<id>/article.md?v=<工單 id>` | 文章的 Markdown 原文（複製用） |
| `POST /api/extract` | 上傳的檔案（Markdown、純文字、PDF）抽成文字：`{"name", "data"（base64）}` → `{"name", "text", "chars"}` |
| `/static/…` | 樣式、字型、script（沒有建置，沒有外部資源） |

所有片段的內容都經 Jinja 跳脫；Markdown 先過 `ui/markdown.py`（原始 HTML 不放行）。
工單 id、對話 id 一律先對照這個對話的 `jobs.jsonl`，不拿網址上的字去拼路徑。
"""

import asyncio
import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from evidence_forge.chat.desk import MAX_FILE_CHARS
from evidence_forge.chat.host import ConversationHost, ConversationNotFound
from evidence_forge.chat.pi_rpc import PiExited
from evidence_forge.extract import ExtractError, pdf_to_text
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir
from evidence_forge.ui import artifacts, bills, thread
from evidence_forge.ui.format import clock as format_clock
from evidence_forge.ui.format import format_usd, month_day_clock, relative_day

_UI_DIR = Path(__file__).resolve().parent
# 上傳檔案的上限：原始位元組。文章不會比這大；PDF 也一樣
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_TEXT_SUFFIXES = (".md", ".markdown", ".txt")
NO_CONVERSATION_TITLE = "不在對話裡的工單"
TABS = ("report", "article", "cost")


@dataclass(frozen=True)
class ListRow:
    conversation_id: str
    title: str
    # 狀態小標：analyzing／writing／new／failed／None
    state: str | None
    stamp: str
    spent: str
    current: bool
    # 「不在對話裡的工單」那一列：沒有 Pi、不能送訊息
    loose: bool = False


def _badge(summary: dict[str, Any], current: bool) -> str | None:
    """對話清單的狀態小標（IA §3、決定 1）：跑著是分析中或寫作中；結束了而且創作者還沒看過是
    有新結果（未完成也算，因為它確實交出了東西）；失敗標失敗。正在看的這個對話不標有新結果。"""
    latest = summary.get("latest_job")
    if not isinstance(latest, dict):
        return None
    if not latest.get("finished"):
        return "writing" if latest.get("kind") == "rewrite" else "analyzing"
    if latest.get("outcome") == "failed":
        return "failed"
    if latest.get("job_id") != summary.get("seen_job") and not current:
        return "new"
    return None


def list_rows(summaries: list[dict[str, Any]], current: str | None, now: datetime) -> list[ListRow]:
    """有新結果、還沒看過的排最前面；其餘依最後活動時間，新的在上。"""
    rows: list[tuple[bool, str, ListRow]] = []
    for summary in summaries:
        is_current = summary["id"] == current
        state = _badge(summary, is_current)
        rows.append(
            (
                state == "new",
                str(summary["last_activity"]),
                ListRow(
                    conversation_id=summary["id"],
                    title=summary["title"],
                    state=state,
                    stamp=relative_day(datetime.fromisoformat(summary["last_activity"]), now),
                    spent=format_usd(float(summary["spent_usd"])),
                    current=is_current,
                ),
            )
        )
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [row for _, _, row in rows]


def first_persona(data: DataDir) -> str:
    """建議按鈕「用 me 寫一篇觀點文」裡的角色名：`authors/` 底下第一個角色，沒有就是 me。"""
    root = data.authors
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and not folder.name.startswith(("_", ".")):
                return folder.name
    return artifacts.DEFAULT_PERSONA


def install(
    app: FastAPI,
    host: ConversationHost,
    data: DataDir,
    recipes: Mapping[str, Recipe],
    *,
    now: Callable[[], datetime] | None = None,
) -> None:
    templates = Jinja2Templates(directory=_UI_DIR / "templates")
    templates.env.globals["usd"] = format_usd
    templates.env.filters["hm"] = format_clock
    templates.env.filters["mdhm"] = month_day_clock
    clock = now if now is not None else (lambda: datetime.now().astimezone())
    recipe_map = dict(recipes)
    app.mount("/static", StaticFiles(directory=_UI_DIR / "static"), name="static")

    def summaries() -> list[dict[str, Any]]:
        return host.list_conversations()

    def page(request: Request, name: str, context: dict[str, Any], status: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context, status_code=status)

    def new_count(rows: list[ListRow]) -> int:
        return sum(1 for r in rows if r.state == "new")

    def with_loose(rows: list[ListRow], current: str | None) -> list[ListRow]:
        """清單最後多一列「不在對話裡的工單」（命令列開的，P1 的分析、重構都在這裡）；沒有這種工單就不列。"""
        infos = artifacts.loose_jobs(data, recipe_map)
        cost = bills.conversation_cost(data, recipe_map, artifacts.LOOSE)
        if not infos and not cost.rows:
            return rows
        stamps = [i.row.ts for i in infos] + [r.when for r in cost.rows if r.when is not None]
        return [
            *rows,
            ListRow(
                conversation_id=artifacts.LOOSE,
                title=NO_CONVERSATION_TITLE,
                state=None,
                stamp=relative_day(max(stamps), clock()) if stamps else "",
                spent=format_usd(cost.total_usd),
                current=current == artifacts.LOOSE,
                loose=True,
            ),
        ]

    async def shell(request: Request, conversation_id: str | None) -> HTMLResponse:
        try:
            found = await asyncio.to_thread(summaries)
            error = None
        except OSError as exc:
            found, error = [], str(exc)
        title = None
        loose = conversation_id == artifacts.LOOSE
        if loose:
            title = NO_CONVERSATION_TITLE
        elif conversation_id is not None:
            match = next((s for s in found if s["id"] == conversation_id), None)
            if match is None:
                return page(
                    request,
                    "error.html",
                    {
                        "nav": "chat",
                        "new_count": 0,
                        "title": "找不到這個對話",
                        "body": f"編號 {conversation_id} 不在資料目錄裡。可能是網址打錯，或資料夾被移走了。",
                        "page_title": "找不到這個對話・Evidence Forge",
                    },
                    404,
                )
            title = match["title"]
        rows = list_rows(found, conversation_id, clock())
        count = new_count(rows)
        rows = await asyncio.to_thread(with_loose, rows, conversation_id)
        return page(
            request,
            "chat.html",
            {
                "loose": loose,
                "nav": "chat",
                "new_count": count,
                "rows": rows,
                "list_error": error,
                "data_root": str(data.root),
                "conversation_id": conversation_id,
                "conversation_title": title,
                "page_title": (f"({count}) " if count else "") + "Evidence Forge",
                "initial_tab": request.query_params.get("tab", ""),
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return await shell(request, None)

    @app.get("/c/{conversation_id}", response_class=HTMLResponse)
    async def conversation_page(request: Request, conversation_id: str) -> HTMLResponse:
        return await shell(request, conversation_id)

    @app.get("/ui/list", response_class=HTMLResponse)
    async def list_fragment(request: Request, current: str | None = None) -> HTMLResponse:
        try:
            found = await asyncio.to_thread(summaries)
        except OSError as exc:
            return page(request, "_list.html", {"rows": [], "list_error": str(exc), "data_root": str(data.root), "new_count": 0})
        rows = list_rows(found, current, clock())
        count = new_count(rows)
        rows = await asyncio.to_thread(with_loose, rows, current)
        return page(
            request,
            "_list.html",
            {"rows": rows, "list_error": None, "data_root": str(data.root), "new_count": count},
        )

    @app.get("/ui/c/{conversation_id}/thread", response_class=HTMLResponse)
    async def thread_fragment(request: Request, conversation_id: str) -> HTMLResponse:
        if conversation_id == artifacts.LOOSE:
            # 沒有對話、沒有 Pi：只列工單的結果卡，唯讀
            jobs = await asyncio.to_thread(artifacts.loose_jobs, data, recipe_map)
            blocks = await asyncio.to_thread(thread.build, data, jobs, [])
            return page(
                request,
                "_thread.html",
                {
                    "blocks": blocks,
                    "title": NO_CONVERSATION_TITLE,
                    "spent": format_usd(bills.conversation_cost(data, recipe_map, artifacts.LOOSE).total_usd),
                    "persona": first_persona(data),
                    "latest_job": "",
                    "latest_done": False,
                    "latest_failed": False,
                    "last_kind": "",
                    "has_report": any(j.kind == "analysis" for j in jobs),
                    "has_article": any(j.kind == "rewrite" for j in jobs),
                },
            )
        try:
            history = await asyncio.to_thread(host.history, conversation_id)
        except ConversationNotFound:
            return page(request, "_thread.html", {"missing": True, "blocks": []}, 404)
        except (PiExited, TimeoutError, OSError, RuntimeError) as exc:
            return page(request, "_thread.html", {"unavailable": str(exc), "blocks": []}, 503)
        jobs = await asyncio.to_thread(artifacts.conversation_jobs, data, recipe_map, conversation_id)
        blocks = await asyncio.to_thread(thread.build, data, jobs, history["messages"])
        summary = await asyncio.to_thread(host.summary, conversation_id)
        latest = jobs[-1] if jobs else None
        return page(
            request,
            "_thread.html",
            {
                "blocks": blocks,
                "title": summary["title"],
                "spent": format_usd(float(summary["spent_usd"])),
                "persona": first_persona(data),
                "latest_job": latest.job_id if latest else None,
                "latest_done": bool(latest and latest.job.status == "done"),
                "latest_failed": bool(
                    latest and latest.job.result is not None and latest.job.result.outcome == "failed"
                ),
                "last_kind": latest.kind if latest else None,
                "has_report": any(j.kind == "analysis" for j in jobs),
                "has_article": any(j.kind == "rewrite" for j in jobs),
            },
        )

    def pick(jobs: list[artifacts.JobInfo], wanted: str | None) -> artifacts.JobInfo | None:
        """網址上的 `v` 只拿來在這個對話的工單裡挑；對不上就給最新的，不拿它去拼路徑。"""
        found = next((j for j in jobs if j.job_id == wanted), None) if wanted else None
        return found or (jobs[-1] if jobs else None)

    @app.get("/ui/c/{conversation_id}/panel", response_class=HTMLResponse)
    async def panel_fragment(
        request: Request,
        conversation_id: str,
        tab: str = "report",
        v: str | None = None,
        diff: str | None = None,
    ) -> HTMLResponse:
        if conversation_id != artifacts.LOOSE:
            try:
                await asyncio.to_thread(host.summary, conversation_id)
            except ConversationNotFound:
                return page(request, "_panel.html", {"missing": True, "tab": "report"}, 404)
        if tab not in TABS:
            tab = "report"
        jobs = await asyncio.to_thread(artifacts.conversation_jobs, data, recipe_map, conversation_id)
        kinds = {"report": "analysis", "article": "rewrite"}
        context: dict[str, Any] = {"tab": tab, "conversation_id": conversation_id, "missing": False}
        if tab == "cost":
            context["cost"] = await asyncio.to_thread(bills.conversation_cost, data, recipe_map, conversation_id)
            budget = context["cost"].budget_usd
            context["budget"] = format_usd(budget) if budget is not None else None
        else:
            versions = [j for j in jobs if j.kind == kinds[tab]]
            chosen = pick(versions, v)
            context["versions"] = versions
            context["chosen"] = chosen
            if chosen is not None:
                if tab == "report":
                    context["report"] = await asyncio.to_thread(artifacts.report_view, data, chosen)
                else:
                    # 上一版跟著版本鏈走（`parent`、同一個角色），不是列表的上一個
                    before = next((j for j in versions if j.job_id == chosen.previous_id), None)
                    context["article"] = await asyncio.to_thread(artifacts.article_view, data, chosen, before)
                    context["diff"] = diff == "1"
        context["counts"] = {
            "report": sum(1 for j in jobs if j.kind == "analysis"),
            "article": sum(1 for j in jobs if j.kind == "rewrite"),
        }
        return page(request, "_panel.html", context)

    @app.get("/ui/c/{conversation_id}/article.md")
    async def article_markdown(conversation_id: str, v: str | None = None) -> Response:
        if conversation_id != artifacts.LOOSE:
            try:
                await asyncio.to_thread(host.summary, conversation_id)
            except ConversationNotFound:
                return PlainTextResponse("沒有這個對話", status_code=404)
        jobs = await asyncio.to_thread(artifacts.conversation_jobs, data, recipe_map, conversation_id)
        versions = [j for j in jobs if j.kind == "rewrite"]
        chosen = pick(versions, v)
        # 複製要的是指定的那一版：對不上就說沒有，不能悄悄給別版
        if chosen is None or (v and chosen.job_id != v):
            return PlainTextResponse("沒有這篇文章", status_code=404)
        view = await asyncio.to_thread(artifacts.article_view, data, chosen, None)
        if view.error is not None:
            return PlainTextResponse(view.error, status_code=404)
        return PlainTextResponse(view.text, media_type="text/markdown; charset=utf-8")

    @app.get("/bills", response_class=HTMLResponse)
    async def bills_page(request: Request, month: str | None = None) -> HTMLResponse:
        moment = clock()
        chosen = bills.parse_month(month, moment)
        view = await asyncio.to_thread(bills.month_view, data, recipe_map, chosen, now=moment)
        try:
            found = await asyncio.to_thread(summaries)
        except OSError:
            found = []
        count = new_count(list_rows(found, None, moment))
        return page(
            request,
            "bills.html",
            {
                "nav": "bills",
                "new_count": count,
                "view": view,
                "page_title": (f"({count}) " if count else "") + "Evidence Forge・帳單",
                "usd": format_usd,
            },
        )

    @app.post("/api/extract")
    async def extract(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "請求內容要是 JSON"}, status_code=400)
        name = body.get("name") if isinstance(body, dict) else None
        encoded = body.get("data") if isinstance(body, dict) else None
        if not isinstance(name, str) or not isinstance(encoded, str):
            return JSONResponse({"error": "要有字串欄位 name 與 data（base64）"}, status_code=400)
        if len(encoded) > MAX_UPLOAD_BYTES * 4 // 3 + 8:
            return JSONResponse({"error": "檔案太大（上限 5 MB）"}, status_code=413)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse({"error": "data 不是 base64"}, status_code=400)
        if len(raw) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "檔案太大（上限 5 MB）"}, status_code=413)
        short = PurePath(name.replace("\\", "/")).name or "upload"
        suffix = PurePath(short).suffix.lower()
        try:
            if suffix == ".pdf":
                text = await asyncio.to_thread(pdf_to_text, raw)
            elif suffix in _TEXT_SUFFIXES:
                text = raw.decode("utf-8-sig")
            else:
                return JSONResponse({"error": f"{short} 的格式不收；只收 Markdown、純文字、PDF"}, status_code=415)
        except UnicodeDecodeError:
            return JSONResponse({"error": f"{short} 不是 UTF-8 的文字檔"}, status_code=422)
        except ExtractError as exc:
            return JSONResponse({"error": f"{short} 讀不出文字：{exc}"}, status_code=422)
        text = text.strip()
        if not text:
            return JSONResponse({"error": f"{short} 沒有文字內容"}, status_code=422)
        if len(text) > MAX_FILE_CHARS:
            # 整份會放進對話、再交給 analyze：太長會塞爆對話的上下文，所以說清楚，不悄悄截掉
            return JSONResponse(
                {
                    "error": f"{short} 抽出來有 {len(text):,} 字，超過上限 {MAX_FILE_CHARS:,} 字。"
                    "請截短、或拆成幾份再丟",
                },
                status_code=413,
            )
        return JSONResponse({"name": short, "text": text, "chars": len(text)})
