"""整合測試共用：把閘道（或假的 Ollama）真的開在 127.0.0.1 的一個空埠上。"""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@contextmanager
def serve_app(app: Any) -> Iterator[str]:
    """在背景執行緒跑 ASGI app，回傳 base URL；離開時關掉。"""
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # 這裡等的是伺服器啟動完成的旗標，不是拿時間當斷言
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("測試伺服器沒有啟動")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def serve() -> Any:
    """整合測試用：`with serve(app) as url:`。"""
    return serve_app


@pytest.fixture
def port() -> int:
    """127.0.0.1 上一個空的埠，給要自己開伺服器的測試（例如 `ef serve`）。"""
    return free_port()
