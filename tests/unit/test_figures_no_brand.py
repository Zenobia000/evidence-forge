"""守門：創作者的品牌內容不准進 repo（`src/` 與 `tests/`）。

封面 prompt 的長句子屬於 `$EF_DATA` 的 brand.yaml。色票、品牌名這類低熵的指紋不放在 repo 裡（見 `evidence_forge.brand_guard`）。
這個測試比對的是「指紋」而不是原文：把原文寫在測試裡，等於自己把它放進 repo。

指紋清單與比對方法的唯一一份在 `evidence_forge.brand_guard`（#126：commit 與 push 之前的關卡也用它），
怎麼做指紋、限制是什麼、怎麼新增都寫在那裡的說明。這個測試是整套測試時的最後一道：
關卡被繞過去（`--no-verify`、沒設 `core.hooksPath`）時，至少在測試這裡還抓得到。
"""

import re
from pathlib import Path

import pytest

from evidence_forge.brand_guard import FINGERPRINTS, Fingerprints, digest_words, hits_in_text

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests")
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".ts", ".js", ".html", ".css"}
SKIP_PARTS = {"node_modules", "__pycache__", ".venv"}


def hits(
    text: str, fingerprints: tuple[tuple[int, str], ...] = FINGERPRINTS
) -> list[tuple[int, str]]:
    """文字裡出現的指紋（詞數, 雜湊）。"""
    return sorted(hits_in_text(text, Fingerprints(words=set(fingerprints))))


def _files() -> list[Path]:
    out: list[Path] = []
    for name in SCAN_DIRS:
        for path in (ROOT / name).rglob("*"):
            if path.suffix in SUFFIXES and path.is_file() and not SKIP_PARTS & set(path.parts):
                out.append(path)
    return out


def test_偵測器自己有效_大小寫標點換行都不影響() -> None:
    # 用無害的假指紋驗證比對邏輯，不放任何真實品牌內容
    fake = (
        (1, digest_words(["harmless"])),
        (2, digest_words(["harmless", "placeholder"])),
    )

    assert hits("Nothing here", fake) == []
    assert hits("A HARMLESS\n  Placeholder.", fake) == [fake[0], fake[1]]
    assert hits("harmless-placeholder", fake) == [fake[0], fake[1]]


def test_指紋本身不是明文() -> None:
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in FINGERPRINTS)
    # repo 裡只放高熵的長句子；色碼與名字（低熵，雜湊等於明文）只在執行期從 EF_DATA 現算（#126）
    assert {n for n, _ in FINGERPRINTS} == {8}


@pytest.mark.parametrize("name", SCAN_DIRS)
def test_程式與測試裡沒有創作者的品牌內容(name: str) -> None:
    offenders = {
        str(path.relative_to(ROOT)): found
        for path in _files()
        if path.relative_to(ROOT).parts[0] == name
        and (found := hits(path.read_text(encoding="utf-8", errors="ignore")))
    }

    assert offenders == {}, "這些檔案含有屬於 brand.yaml 的品牌內容（只列檔名與指紋，不列原文）"
