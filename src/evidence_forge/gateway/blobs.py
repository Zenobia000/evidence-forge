"""`blobs/<hash>`：模型請求與回應的原文，依 SHA-256 存，只增不改（v0.3 §4.3、NFR-KEEP-01）。"""

import hashlib
from pathlib import Path

from evidence_forge.write_once import write_once


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, data: bytes) -> str:
        """存一份原文，回傳它的 hash。同樣的內容只存一次（檔名就是內容的 hash）。"""
        digest = hashlib.sha256(data).hexdigest()
        try:
            write_once(self.root / digest, data)
        except FileExistsError:
            pass
        return digest

    def get(self, digest: str) -> bytes | None:
        path = self.root / digest
        return path.read_bytes() if path.is_file() else None
