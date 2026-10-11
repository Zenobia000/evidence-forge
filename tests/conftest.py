"""測試共用的 fixture。"""

from pathlib import Path

import pytest

from ui_folder import Folder


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    return Folder(tmp_path / "ef-data")
