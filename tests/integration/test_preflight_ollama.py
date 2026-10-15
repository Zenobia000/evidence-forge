"""SEAM-10（整合）：開跑前檢查讀 Ollama 的 `/api/ps`，拿載入中的模型上下文（ADR-0022）。

一個本機 stub 扮演 Ollama：只回 `/api/ps`，沒有 `/api/generate`，所以查上下文絕對不會載入模型。
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import FastAPI

from evidence_forge.runner.preflight import http_probes

Serve = Callable[[Any], AbstractContextManager[str]]


def _ollama(models: list[dict[str, Any]]) -> FastAPI:
    stub = FastAPI()

    @stub.get("/api/ps")
    async def loaded() -> dict[str, Any]:
        return {"models": models}

    return stub


def test_模型載入中_讀到它的上下文長度(serve: Serve) -> None:
    models = [{"name": "qwen3.5:9b", "model": "qwen3.5:9b", "context_length": 32768}]
    with serve(_ollama(models)) as url:
        assert http_probes().ollama_context(url, "qwen3.5:9b") == 32768


def test_模型沒載入或沒回報上下文_回_None_不去載(serve: Serve) -> None:
    other = [{"name": "llama3:8b", "model": "llama3:8b", "context_length": 8192}]
    bare = [{"name": "qwen3.5:9b", "model": "qwen3.5:9b"}]
    with serve(_ollama(other)) as url:
        assert http_probes().ollama_context(url, "qwen3.5:9b") is None
    with serve(_ollama(bare)) as url:
        assert http_probes().ollama_context(url, "qwen3.5:9b") is None


def test_Ollama_連不上_回_None() -> None:
    assert http_probes().ollama_context("http://127.0.0.1:9", "qwen3.5:9b") is None
