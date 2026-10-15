"""守門：創作者的品牌內容不准進 repo（`src/` 與 `tests/`）。

色票、品牌名、封面預設名稱與封面 prompt 的句子屬於 `$EF_DATA` 的 brand.yaml。
這個測試比對的是「指紋」而不是原文：把原文寫在測試裡，等於自己把它放進 repo。

指紋怎麼做：文字轉小寫、只留英數字詞（所以大小寫、標點、連字號、換行都不影響），
取連續 n 個詞接成一串算 sha256。掃描時對每個檔案的每個 n 詞視窗算一次再比對。
- n = 1：六位十六進位色碼、品牌名。
- n = 2：封面預設的名稱。
- n = 8：封面 prompt 裡幾句有辨識度的話的前八個詞。

限制（老實講）：sha256 擋得住「一眼看出內容」，擋不住有心人窮舉：六位色碼只有 2^24 種，
一台電腦幾秒就試完。這裡要的是防止不小心貼進來，不是保密；色票本來就公開在網站上。
要新增指紋：用同一套轉換（小寫、`[a-z0-9]+` 取詞、空白連接）算出雜湊，貼進下面的清單。
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests")
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".ts", ".js", ".html", ".css"}
SKIP_PARTS = {"node_modules", "__pycache__", ".venv"}

# (詞數, sha256)
FINGERPRINTS: tuple[tuple[int, str], ...] = (
    # 七個色碼
    (1, "46dfee0fad70a0c3bfc8ac762acf56d65b95476b33d4388641564e79f3d1f4d6"),
    (1, "6a2bc2d1357d7c6157ba5cc9cc1c257527b542582b3936d138c0f9f779a75c29"),
    (1, "534e8923582ab4d7169541d18cb7ccc50b1918811b5aefccce6abce161bdc56c"),
    (1, "43b27863bb4d85ea45adda2aae095eb072f9cc92124e7dd06f25617a3239995c"),
    (1, "f566415e57b78e8a2e7e86450f10296025f02e29ad6911604cd0839a9ec40b8d"),
    (1, "db4233e00da71b0157a5b1e35a5033a858f719a97abc1d9b87534d86d6df3a40"),
    (1, "db907ebcbfb04402ca5716e5dce4355893d32290a317c748714a668ba0bbeb92"),
    # 品牌名
    (1, "c2333a7e3a607935c67c1e6f6810395decc9f66f592b812aaada7db94ba215d6"),
    # 六個封面預設名稱
    (2, "28aff10a0f34e778f893161a010e8cd21c1fa105ff12203abf9a09f80f20af65"),
    (2, "0dab1336f25bcb42e248ba53aa6f6a3c923ad7d0ed63dc28ec3e58967a4a8f81"),
    (2, "4c50ab82b40ea813313e06dd1a0814427616b20a88de469c1f26bc6b7928282e"),
    (2, "891252b033c7a374d21450b8c1a1af285066fec1223a2ce1a991f0d135dfcff5"),
    (2, "a41f84b44c3f96e4fa5e73093635b9ee609d005e0f1867b6482de6829cc3626c"),
    (2, "84871b06a45f9b62428128e06e09fdebb84f700a7844edfd816e20e655a9129c"),
    # 封面 prompt 的句子（共同限制、形式、預設、市場觀察）
    (8, "6448165228f4ae849362cfc49d7bd52265c6b18671fa0fb650bb7b5330af3201"),
    (8, "3d7048faecf90b216fa8550752b47734659c9d13ba5949545db1f41335fa5923"),
    (8, "a20d045f08060e85ae85eb9f2689b191f5db7738b1f7820d5adeaa43d09b3d15"),
    (8, "faa6b67a5605b1ad4017601e906bb93841467b44b158138445ebc25b701cd38c"),
    (8, "8a0d258252f1789c370e283ae648e675f7fba02c95adfd433de24ab1ee31e0eb"),
    (8, "e69628d5a22f81a3c09a330b5a5c03e45699ed0db1abd4186065e5aad6476d94"),
    (8, "5f2d30809c7845ba21525e7630ca3f30d98b1b49445f2efc09e677529d2b7f30"),
    (8, "19072ab38e336454df75f9676f98d84761de77083f9d5795350c8f9eae62e84b"),
    (8, "1414c97865a75e15d752ed1834fa92f5e043d4f47c8e41a8c09f0c3a98101066"),
    (8, "1b30aef07e95c7aaefcc1b9b41bf776a3126da2feb7ef4e4735d9da1ed364611"),
    (8, "99404615cd3c8ea24cf4f6d051d0f48d8e391d37d67380817e5d7496077047c4"),
    (8, "345e8f4b8ac46e831530c49ec939a50498c1625cfd37b1a4032f36ad37b97dcb"),
    (8, "61eb5d4efe3b54f4b34bd211adc4c6588a438cc177d73a757bd75c28fbe99dcb"),
)

_WORD = re.compile(r"[a-z0-9]+")


def _digest(words: list[str]) -> str:
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def hits(
    text: str, fingerprints: tuple[tuple[int, str], ...] = FINGERPRINTS
) -> list[tuple[int, str]]:
    """文字裡出現的指紋（詞數, 雜湊）。"""
    words = _WORD.findall(text.lower())
    wanted: dict[int, set[str]] = {}
    for n, digest in fingerprints:
        wanted.setdefault(n, set()).add(digest)
    found: list[tuple[int, str]] = []
    for n, digests in wanted.items():
        for i in range(len(words) - n + 1):
            digest = _digest(words[i : i + n])
            if digest in digests:
                found.append((n, digest))
    return found


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
        (1, _digest(["harmless"])),
        (2, _digest(["harmless", "placeholder"])),
    )

    assert hits("Nothing here", fake) == []
    assert hits("A HARMLESS\n  Placeholder.", fake) == [fake[0], fake[1]]
    assert hits("harmless-placeholder", fake) == [fake[0], fake[1]]


def test_指紋本身不是明文() -> None:
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in FINGERPRINTS)
    assert {n for n, _ in FINGERPRINTS} == {1, 2, 8}


@pytest.mark.parametrize("name", SCAN_DIRS)
def test_程式與測試裡沒有創作者的品牌內容(name: str) -> None:
    offenders = {
        str(path.relative_to(ROOT)): found
        for path in _files()
        if path.relative_to(ROOT).parts[0] == name
        and (found := hits(path.read_text(encoding="utf-8", errors="ignore")))
    }

    assert offenders == {}, "這些檔案含有屬於 brand.yaml 的品牌內容（只列檔名與指紋，不列原文）"
