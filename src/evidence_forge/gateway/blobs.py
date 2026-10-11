"""`blobs/<hash>`：模型請求與回應的原文，依 SHA-256 存，只增不改（v0.3 §4.3、NFR-KEEP-01）。

檔名就是內容的 hash，所以同一個檔名底下只會有一種正確的內容：

- 存：先寫進同一個資料夾裡的暫存檔，寫完才 `os.replace` 換成 hash 檔名。換名是作業系統的
  一個原子動作，程序在寫到一半被砍，hash 檔名底下不會出現半截內容（只會留下一個暫存檔）。
  已經有內容正確的同名檔就不重寫；內容不對（舊版寫到一半留下的）就換掉。
  覆蓋成同樣的內容沒有壞處，所以不用 `write_once` 的獨佔建立——那樣半截檔會永遠擋住修復。
- 讀：內容的 hash 跟檔名對不上，就當作沒有這份。
"""

import hashlib
import os
import uuid
from pathlib import Path


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, data: bytes) -> str:
        """存一份原文，回傳它的 hash。同樣的內容只存一次。"""
        digest = hashlib.sha256(data).hexdigest()
        if self.get(digest) is not None:
            return digest
        self.root.mkdir(parents=True, exist_ok=True)
        # 點開頭、帶亂數：不會跟 hash 檔名撞，兩個請求同時存同一份也各寫各的暫存檔
        temporary = self.root / f".{digest}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
            os.replace(temporary, self.root / digest)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return digest

    def get(self, digest: str) -> bytes | None:
        path = self.root / digest
        if not path.is_file():
            return None
        data = path.read_bytes()
        return data if hashlib.sha256(data).hexdigest() == digest else None
