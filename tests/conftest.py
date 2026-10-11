"""所有測試共用的接頭（測試藍圖〈承諾標記〉）。

- 每個帶 `@pytest.mark.promise(...)` 的測試，把承諾編號寫進 junit 報告的 property。
- 閘道一律是重播模式（NFR-COST-06），而且只准連 127.0.0.1：
  測試裡任何往外的連線都直接失敗，不會不小心打到付費 API。
"""

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ui_folder import Folder

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _record_promises(request: pytest.FixtureRequest, record_property: Any) -> None:
    for marker in request.node.iter_markers("promise"):
        for promise_id in marker.args:
            record_property("promise", promise_id)


@pytest.fixture(autouse=True)
def _replay_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("EF_GATEWAY_MODE", "replay")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _check(address: Any) -> None:
        # AF_UNIX 的位址是字串路徑；網路位址是 (host, port, ...)
        if isinstance(address, tuple) and address[0] not in _LOOPBACK:
            raise ConnectionRefusedError(f"測試只准連 127.0.0.1，擋下了 {address[0]}")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        _check(address)
        original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _check(address)
        return original_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    """簡易 UI 測試用的資料目錄（`tests/ui_folder.py`）。"""
    return Folder(tmp_path / "ef-data")
