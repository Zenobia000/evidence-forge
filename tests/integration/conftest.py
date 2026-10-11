"""整合測試共用：把閘道（或假的 Ollama、假的網站）真的開在 127.0.0.1 的一個空埠上。"""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# --- 假的外網：抓網頁的測試不連真的網路（NFR-SEC-03 的測試也靠它） ---------------

# 假的公開 IP：假 DNS 把主機名稱解析成這個，連線層再把它接到 127.0.0.1 的假伺服器
FAKE_PUBLIC_IP = "93.184.216.34"


@dataclass
class FakeRoute:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeWeb:
    """一台假的網站伺服器，加上假的 DNS 與連線對照。

    - `route(host, path, ...)`：設定某個主機、某個路徑的回應；沒設定的回 404。
    - `dns`：主機名稱 → IP 清單；`resolver` 就是查這張表。
    - `dial`：檢查過的 IP 是 `FAKE_PUBLIC_IP` 才接到假伺服器，其他位址原樣去連
      （會被 tests/conftest.py 的連線限制擋下，所以永遠連不出去）。
    - `requests`：假伺服器收到的每個請求（主機、路徑、User-Agent）。
    """

    port: int
    routes: dict[tuple[str, str], list[FakeRoute]] = field(default_factory=dict)
    dns: dict[str, list[str]] = field(default_factory=dict)
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    dialed: list[str] = field(default_factory=list)

    def route(
        self,
        host: str,
        path: str,
        status: int = 200,
        body: str | bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        """同一個路徑設定好幾次，就依序回應（最後一個一直重複）。"""
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.dns.setdefault(host, [FAKE_PUBLIC_IP])
        self.routes.setdefault((host, path), []).append(
            FakeRoute(status, payload, headers or {})
        )

    def resolver(self, host: str) -> list[str]:
        return list(self.dns.get(host, []))

    def dial(self, address: str, port: int) -> tuple[str, int]:
        self.dialed.append(address)
        if address == FAKE_PUBLIC_IP:
            return "127.0.0.1", self.port
        return address, port

    def hits(self, host: str, path: str) -> int:
        return sum(1 for h, p, _ in self.requests if (h, p) == (host, path))


@pytest.fixture
def fake_web() -> Iterator[FakeWeb]:
    web = FakeWeb(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802（http.server 規定的名字）
            host = (self.headers.get("Host") or "").split(":")[0]
            path = self.path
            web.requests.append((host, path, self.headers.get("User-Agent") or ""))
            queue = web.routes.get((host, path))
            if not queue:
                route = FakeRoute(404, b"not found")
            else:
                route = queue.pop(0) if len(queue) > 1 else queue[0]
            self.send_response(route.status)
            headers = {"Content-Type": "text/html; charset=utf-8", **route.headers}
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(route.body)))
            self.end_headers()
            self.wfile.write(route.body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    web.port = server.server_address[1]
    # poll_interval 短一點：shutdown() 要等一輪輪詢，預設 0.5 秒會拖慢每一條測試
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield web
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
