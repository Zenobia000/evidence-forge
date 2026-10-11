"""SEAM-10（整合）：閘道給開跑前檢查用的兩個端點（NFR-RUN-01、NFR-SEC-04）。

- `GET /health`：閘道活著。
- `GET /health/anthropic`：閘道拿自己保管的金鑰問 Anthropic 的 `/v1/models`（不花錢）。
  金鑰只有閘道讀，執行器只拿到「有效／無效」，拿不到金鑰本身。

Anthropic 是本機 stub，不打真的服務。
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.paths import DataDir

FAKE_KEY = "sk-fake-xxxx-health"


def _gateway(tmp_path: Path, anthropic_url: str, key: str | None = FAKE_KEY) -> FastAPI:
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    if key is not None:
        (secrets / "anthropic").write_text(key, encoding="utf-8")
    config = GatewayConfig(
        data=DataDir(tmp_path / "data"),
        mode="live",
        anthropic_url=anthropic_url,
        secrets_dir=secrets,
    )
    return create_app(config, ActiveStages())


@pytest.fixture
def anthropic(serve: Callable[[Any], AbstractContextManager[str]]) -> Any:
    seen: list[str] = []
    stub = FastAPI()

    @stub.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        key = request.headers.get("x-api-key", "")
        seen.append(key)
        if key == FAKE_KEY:
            return JSONResponse({"data": []})
        return JSONResponse({"type": "error"}, status_code=401)

    with serve(stub) as url:
        yield url, seen


def test_閘道活著_health_回_200(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> None:
    with serve(_gateway(tmp_path, "http://127.0.0.1:9")) as url:
        assert httpx.get(f"{url}/health", timeout=10).status_code == 200


@pytest.mark.promise("NFR-RUN-01")
def test_金鑰有效_閘道問過_Anthropic_回_200(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    anthropic: tuple[str, list[str]],
) -> None:
    anthropic_url, seen = anthropic
    with serve(_gateway(tmp_path, anthropic_url)) as url:
        response = httpx.get(f"{url}/health/anthropic", timeout=10)
    assert response.status_code == 200
    assert seen == [FAKE_KEY]
    assert FAKE_KEY not in response.text


@pytest.mark.promise("NFR-RUN-01", "NFR-SEC-04")
def test_金鑰無效_回錯誤說明_不帶金鑰內容(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    anthropic: tuple[str, list[str]],
) -> None:
    anthropic_url, _ = anthropic
    with serve(_gateway(tmp_path, anthropic_url, key="sk-fake-xxxx-wrong")) as url:
        response = httpx.get(f"{url}/health/anthropic", timeout=10)
    assert response.status_code == 503
    assert "401" in response.json()["error"]
    assert "sk-fake" not in response.text


@pytest.mark.promise("NFR-RUN-01")
def test_沒有金鑰檔_回錯誤說明(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    anthropic: tuple[str, list[str]],
) -> None:
    anthropic_url, seen = anthropic
    with serve(_gateway(tmp_path, anthropic_url, key=None)) as url:
        response = httpx.get(f"{url}/health/anthropic", timeout=10)
    assert response.status_code == 503
    assert "讀不到 Anthropic 金鑰" in response.json()["error"]
    assert seen == []
