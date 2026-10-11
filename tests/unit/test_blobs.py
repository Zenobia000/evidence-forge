"""`blobs/<hash>`（`gateway/blobs.py`）：程序在寫檔途中被砍，留下的半截檔不能卡住之後的存與讀。"""

import hashlib
from pathlib import Path

import pytest

from evidence_forge.gateway.blobs import BlobStore

DATA = "完整的模型回應。".encode() * 200
# 檔名就是內容的 SHA-256（NFR-KEEP-01）
DIGEST = hashlib.sha256(DATA).hexdigest()


def _half_written(root: Path) -> Path:
    """模擬寫到一半被砍：hash 檔名底下只有前面一截。"""
    root.mkdir(parents=True)
    path = root / DIGEST
    path.write_bytes(DATA[:37])
    return path


@pytest.mark.promise("NFR-KEEP-01")
def test_hash_檔名底下是寫到一半的半截檔_再存一次同樣的內容就修好(
    tmp_path: Path,
) -> None:
    # 請求每次都會存一份，相同的請求很常見：半截檔要是永遠留著，重播與回頭查都讀到壞的
    path = _half_written(tmp_path / "blobs")
    assert BlobStore(tmp_path / "blobs").put(DATA) == DIGEST
    assert path.read_bytes() == DATA


@pytest.mark.promise("NFR-KEEP-01", "NFR-COST-02")
def test_內容跟檔名的_hash_對不上就當作沒有(tmp_path: Path) -> None:
    # 讀到半截的回應就回放的話，Pi 拿到的是壞掉的串流
    _half_written(tmp_path / "blobs")
    assert BlobStore(tmp_path / "blobs").get(DIGEST) is None


def test_存過的內容讀得回來(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    assert store.get(store.put(DATA)) == DATA
