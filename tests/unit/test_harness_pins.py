"""repo 裡鎖住的東西：Pi 的版本、錄好的模型回應。

這兩樣壞掉都不會當場紅：Pi 被升版，送出的請求就跟錄音對不上，重播整條流程才會莫名失敗；
錄音被改了一個位元組（例如被換行設定改成 CRLF），hash 對不上，也是到重播才炸。
"""

import hashlib
import json
from pathlib import Path

import pytest

from evidence_forge.harness import default_harness

RECORDINGS = Path(__file__).parents[1] / "fixtures" / "recordings"


def test_Pi_版本寫死成一個確切的版本_而且有_lock_檔() -> None:
    pi_dir = default_harness().root / "pi"
    package = json.loads((pi_dir / "package.json").read_text(encoding="utf-8"))
    version = package["dependencies"]["@earendil-works/pi-coding-agent"]
    assert version == "1.1.0"
    lock = json.loads((pi_dir / "package-lock.json").read_text(encoding="utf-8"))
    locked = lock["packages"]["node_modules/@earendil-works/pi-coding-agent"]["version"]
    assert locked == version


@pytest.mark.parametrize(
    "recording", sorted(path.name for path in RECORDINGS.iterdir() if path.is_dir())
)
def test_錄音的每個_blob_內容跟檔名的_hash_相同_帳本指到的都在(recording: str) -> None:
    root = RECORDINGS / recording
    for blob in (root / "blobs").iterdir():
        assert hashlib.sha256(blob.read_bytes()).hexdigest() == blob.name
    for line in (root / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        for field in ("request_hash", "response_hash"):
            assert (root / "blobs" / entry[field]).is_file(), (recording, field)
