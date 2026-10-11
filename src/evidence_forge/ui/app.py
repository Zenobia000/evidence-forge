"""簡易 UI 的 FastAPI 應用（架構草案 v0.3 §8、ADR-0017）。

伺服器產生頁面，htmx 只做「送到伺服器 → 換掉一塊 HTML」：沒有前端狀態、沒有 Node 建置。
UI 只讀資料夾；寫決定檔與新增工單是 S0.12 的事。
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from evidence_forge.models import JobStatus
from evidence_forge.paths import DataDir, resolve_data_dir
from evidence_forge.ui import detail, present, reading

_UI_DIR = Path(__file__).resolve().parent
# 配方在 repo 的 harness/recipes/（src/evidence_forge/ui/ 往上三層是 repo 根）。
_DEFAULT_RECIPES = _UI_DIR.parents[2] / "harness" / "recipes"

COLUMN_ORDER: list[JobStatus] = ["queued", "running", "review", "approved", "discarded"]


def _now() -> datetime:
    return datetime.now().astimezone()


def create_app(
    data_dir: DataDir | None = None,
    *,
    recipes_dir: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    """組出 UI。`now` 讓測試固定「現在」，「跑了多久」才不會看時鐘。"""
    data = data_dir if data_dir is not None else resolve_data_dir()
    recipes = recipes_dir if recipes_dir is not None else _DEFAULT_RECIPES
    clock = now if now is not None else _now
    templates = Jinja2Templates(directory=_UI_DIR / "templates")
    app = FastAPI(
        title="Evidence Forge", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.mount("/static", StaticFiles(directory=_UI_DIR / "static"), name="static")

    def board_context(fragment: bool) -> dict[str, object]:
        moment = clock()
        context: dict[str, object] = {
            "fragment": fragment,
            "nav": "board",
            "updated": moment.strftime("%H:%M:%S"),
            "error": None,
            "unreadable": [],
            "columns": [],
            "total": 0,
            "review_count": 0,
            "page_title": "Evidence Forge",
        }
        try:
            board = reading.load_board(data)
        except reading.DataDirError as exc:
            context["error"] = exc
            return context
        columns = []
        for status in COLUMN_ORDER:
            briefs = board.by_status[status]
            columns.append(
                {
                    "status": status,
                    "label": present.STATUS_LABEL[status],
                    "count": board.counts[status],
                    "cards": [present.build_card(data, b, moment) for b in briefs],
                    "truncated": board.counts[status] > len(briefs),
                    "limit": reading.RECENT_LIMIT,
                }
            )
        review = board.counts["review"]
        context.update(
            columns=columns,
            unreadable=board.unreadable,
            total=board.total,
            review_count=review,
            page_title=f"({review}) Evidence Forge" if review else "Evidence Forge",
        )
        return context

    @app.get("/", response_class=HTMLResponse)
    def board_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "board.html", board_context(False))

    @app.get("/board", response_class=HTMLResponse)
    def board_fragment(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_board.html", board_context(True))

    def error_page(
        request: Request, status: int, title: str, body: str
    ) -> HTMLResponse:
        context = {
            "nav": "",
            "review_count": 0,
            "page_title": f"{title}・Evidence Forge",
            "title": title,
            "body": body,
        }
        return templates.TemplateResponse(
            request, "error.html", context, status_code=status
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(
        request: Request,
        job_id: str,
        tab: str | None = None,
        v: int | None = None,
    ) -> Response:
        try:
            view = detail.build_detail(data, recipes, job_id, tab, v, clock())
        except reading.DataDirError as exc:
            return error_page(
                request, 503, "讀不到資料目錄", f"路徑：{exc.path}　原因：{exc.reason}"
            )
        except reading.JobNotFound:
            return error_page(
                request,
                404,
                "找不到這張單",
                f"編號 {job_id} 不在資料目錄裡。可能是網址打錯，或資料夾被移走了。",
            )
        except reading.JobUnreadable as exc:
            return error_page(request, 500, "這張單的 job.md 讀不懂", exc.reason)
        # htmx 換內容時只回那一塊；瀏覽器直接開網址、或按上一頁還原歷史時回整頁。
        is_fragment = (
            request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-History-Restore-Request") != "true"
        )
        context = {
            "view": view,
            "nav": "",
            "review_count": reading.count_status(data, "review"),
            "page_title": f"{view.title}・Evidence Forge",
        }
        response = templates.TemplateResponse(
            request, "_job_view.html" if is_fragment else "job.html", context
        )
        response.headers["Vary"] = "HX-Request"
        return response

    return app
