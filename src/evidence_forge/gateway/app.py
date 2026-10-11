"""閘道的 HTTP 介面（架構草案 v0.3 §4）。

- `POST /tools/<名稱>`：ef-tools 的工具呼叫（`read_file`、`list_files`、`write_file`）。
- `POST /v1/messages`：Pi 的模型請求（Anthropic Messages 格式），見 `messages.py`。

兩種請求都在 `x-api-key` 帶工單鑰匙；閘道只接受登記中的那一把（`ActiveStages`）。
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evidence_forge.gateway import scope
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import GatewayConfig, require_active_key
from evidence_forge.gateway.messages import add_message_routes

__all__ = ["ActiveStages", "GatewayConfig", "JobBoard", "create_app"]


def _string(body: dict[str, Any], name: str, default: str | None = None) -> str:
    value = body.get(name, default)
    if not isinstance(value, str):
        raise scope.ToolRejected(400, f"缺少字串欄位 {name}")
    return value


def create_app(
    config: GatewayConfig, stages: ActiveStages, board: JobBoard | None = None
) -> FastAPI:
    """`board` 要跟執行器共用（`ef serve`）；不傳就自己開一個，執行器讀不到。"""
    app = FastAPI(title="evidence-forge gateway")

    @app.exception_handler(scope.ToolRejected)
    async def _rejected(_: Request, exc: scope.ToolRejected) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.post("/tools/{name}")
    async def tool(name: str, request: Request) -> dict[str, Any]:
        key = require_active_key(request.headers.get("x-api-key"), stages)
        try:
            body = await request.json()
        except ValueError:
            raise scope.ToolRejected(400, "請求內容要是 JSON") from None
        if not isinstance(body, dict):
            raise scope.ToolRejected(400, "請求內容要是 JSON 物件")
        job_dir = config.data.job_dir(key.job_id)
        if name == "read_file":
            return {"content": scope.read_file(job_dir, _string(body, "path"))}
        if name == "list_files":
            return {"entries": scope.list_files(job_dir, _string(body, "path", "."))}
        if name == "write_file":
            written = scope.write_file(
                job_dir, key, _string(body, "path"), _string(body, "content")
            )
            return {"written": written}
        raise scope.ToolRejected(404, f"沒有這個工具：{name}")

    add_message_routes(app, config, stages, board or JobBoard(config.clock))
    return app
