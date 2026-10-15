"""私人內容守門：commit 與 push 之前，掃「這次要進歷史的內容」有沒有創作者的私人字眼（#126）。

為什麼有這支：這個 repo 是公開的。舊的守門是 `tests/unit/test_figures_no_brand.py`，它只在整套測試跑完才紅，
那時 commit 早就做了、push 也已經推出去。2026-10-15 有人把創作者的真名當成測試角色名，一個 commit 就推上去了；
公開歷史上的東西只有 GitHub 客服收得回來。所以這裡把同一套比對搬到 commit 與 push 之前當關卡。

誰來呼叫（分工見 README「私人內容關卡」）：
- **主關卡是 git 原生 hook**：`.githooks/pre-commit`（暫存區）、`commit-msg`（訊息）、`pre-push`（要推的 commit）。
  `git config core.hooksPath .githooks` 之後，手動或任何工具下的 git 指令都會經過。
- Claude Code 的守衛（`.claude/hooks/guard-git.sh`）呼叫 `guard`：目標 repo 沒有啟用這些 hook 就擋下
  commit／push／`gh pr create`／`gh pr merge`，並掃指令裡的訊息文字（見 `git_gate.py`）。

比對的是「指紋」不是原文（`$EF_DATA` 的私人內容絕不明文進 repo，也不明文進輸出）：
- 文字轉 Unicode NFKC、小寫，英數字詞用 `[a-z0-9]+` 取出；連續 n 個詞用空白接起來算 sha256。
  所以大小寫、標點、連字號、換行、全形英數都不影響。
- 中日韓文字沒有「詞」，另開一條流：只留漢字，連續 n 個字直接接起來算 sha256。
- **repo 裡只放高熵的指紋**（`FINGERPRINTS`：封面 prompt 的長句子，八個詞）。色碼只有 2^24 種、名字是一個詞，
  它們的 sha256 等於明文（一台電腦幾秒窮舉完），所以**不進 repo**——這類指紋只在執行期、從 `$EF_DATA` 現算：
  `authors/*/` 的 `voice.md`（`name`、`id`）、`brand.yaml`（`name`、色碼、封面預設名稱、長句子）、
  `persona.json` 的 `display_name`，以及 `imports/*.zip`（創作者的寫作系統：色碼與每行的開頭）。只活在記憶體裡，不寫檔。
  沒有 `$EF_DATA`（例如 CI）時只剩 repo 裡那一組。
  （舊版把色碼、品牌名的雜湊放在 repo 裡，它們已經在 dev 的歷史上，這次只是不再往前帶。）

輸出只講「哪個 commit、哪個檔、第幾行」，**絕不印命中的字**：印出來就等於又洩一次。
路徑本身命中時連路徑也不印，只印 commit 與「（路徑已遮蔽）」。

範圍：新增的行、檔案路徑、commit message 都掃；`docs/`、`Ai-coding-principle/`、`.claude/skills/` 也掃，
只有「創作者已經公開的品牌名」（`brand.yaml` 的 `name`）在這三棵樹底下放行（文件本來就會提到它）。

限制（老實講）：
- 它只認得「現在 `$EF_DATA` 裡有的」：創作者改名之後舊名字就不在指紋裡了。
- 太短或太常見的詞（少於三個字母、`me`、`default` 這類）不收，不然每個 commit 都被擋，人就學會繞過。
- 分支名、PR 本文（不經過 `gh pr create` 指令的話）不在範圍內。
- 在專案的 worktree 裡找不到 `data/` 時，會去主工作目錄找（見 `resolve_data_dir`）。
- `$EF_DATA` 設了卻讀不到、`imports/` 裡有讀不開的 zip：掃不了，**擋下**（exit 2），不是放行。

要新增固定指紋：只收長句子，用同一套轉換算出雜湊（見 `digest_words`）貼進下面的清單。
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# 本檔在 src/evidence_forge/ 底下，往上兩層就是 repo（或 worktree）根目錄。
ROOT = Path(__file__).resolve().parents[2]

Fingerprint = tuple[int, str]  # (詞數或字數, sha256)

# (詞數, sha256)：只放高熵的長句子。低熵的（色碼、名字）只在執行期現算，見檔頭。
FINGERPRINTS: tuple[Fingerprint, ...] = (
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
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")

# 太常見、收進去只會讓每個 commit 都被擋的詞；人被誤擋幾次就會繞過關卡，所以寧可不收。
_STOP_WORDS = frozenset(
    {
        "the", "and", "for", "with", "you", "your", "not", "but", "from", "that", "this",
        "mr", "ms", "mrs", "dr", "von", "van", "der", "del", "los", "las",
        "me", "my", "default", "example", "demo", "test", "sample", "author", "persona",
        "none", "null", "name", "brand", "voice", "user", "admin", "anonymous",
    }
)

# 取名字用的欄位：frontmatter 與 brand.yaml 裡值就是「這個人／這個品牌叫什麼」的鍵
_NAME_KEYS = frozenset({"id", "name", "display_name", "pen_name", "nickname", "alias", "handle"})

# 沒設 `EF_DATA` 的機器、或角色資料夾直接複製範本時，會有跟範本一樣的預設值；
# 這幾份是進了 repo 的公開預設，裡面本來就有的詞不算私人內容。
_PUBLIC_DEFAULTS = (
    "harness/templates/author-voice.md",
    "harness/templates/author-brand.yaml",
    "src/evidence_forge/figures/brand.py",
)

_MAX_FILE_BYTES = 1_000_000
_ZIP_TEXT_SUFFIXES = (".md", ".py", ".yaml", ".yml", ".json", ".txt")
_MAX_ZIP_MEMBERS = 4000

# 這三棵樹裡本來就會提到創作者公開的品牌名（ADR、路線圖、帳本都這樣寫）：只有那一組放行，其餘照掃。
DOCS_PREFIXES = ("docs/", "Ai-coding-principle/", ".claude/skills/")

# 輸出裡代替「會洩漏的東西」的標籤
MASK_PATH = "（路徑已遮蔽）"
LABEL_MESSAGE = "（commit message）"


class GuardError(RuntimeError):
    """掃描本身出了問題（不是命中）：讀不到 git、讀不到資料目錄、參數不對。呼叫端要當成「擋下」。"""


# ── 指紋 ────────────────────────────────────────────────────────────────


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def digest_words(words: Sequence[str]) -> str:
    """英數字詞的指紋：用空白接起來算 sha256（固定指紋與執行期指紋同一套）。"""
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def digest_chars(chars: Sequence[str]) -> str:
    """漢字的指紋：直接接起來算 sha256。"""
    return hashlib.sha256("".join(chars).encode()).hexdigest()


@dataclass
class Fingerprints:
    """一次掃描用的全部指紋：英數字詞流一份、漢字流一份。"""

    words: set[Fingerprint] = field(default_factory=set)
    cjk: set[Fingerprint] = field(default_factory=set)
    # 上面兩份裡「創作者已經公開的品牌名」那一組：在 `DOCS_PREFIXES` 底下不擋
    public_words: set[Fingerprint] = field(default_factory=set)
    public_cjk: set[Fingerprint] = field(default_factory=set)
    runtime_terms: int = 0  # 從 `$EF_DATA` 現算出幾組（只用在說明，不含內容）
    source_files: int = 0

    @classmethod
    def fixed(cls) -> "Fingerprints":
        return cls(words=set(FINGERPRINTS))

    def without_public(self) -> "Fingerprints":
        return Fingerprints(words=self.words - self.public_words, cjk=self.cjk - self.public_cjk)

    def __len__(self) -> int:
        return len(self.words) + len(self.cjk)


def _word_tokens(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def _cjk_tokens(text: str) -> list[str]:
    return _CJK.findall(unicodedata.normalize("NFKC", text))


def _usable_word(word: str) -> bool:
    return len(word) >= 3 and word not in _STOP_WORDS


def term_fingerprints(
    term: str, *, split_words: bool = False, given_name: bool = False
) -> Fingerprints:
    """一個名字或詞組的指紋。

    - 完整詞組一組（最多取前八個詞）；單一詞太短或太常見就不收。
    - `split_words`（人名、品牌名才開）：多詞名字的每個詞也各收一組，因為出事的就是「只用了名字的一半」。
    - 漢字名字：完整一組。
    - `given_name`（**只有角色 `voice.md` 的名字**才開）：三～四個字時另收後兩個字（慣例上的「名」），
      因為只用名不用姓最常見。品牌名不開：品牌名的後兩個字常常是一般的詞，會天天誤擋。
    """
    out = Fingerprints()
    words = _word_tokens(term)
    if len(words) == 1:
        if _usable_word(words[0]):
            out.words.add((1, digest_words(words)))
    elif words:
        head = words[:8]
        out.words.add((len(head), digest_words(head)))
        if split_words:
            for word in words:
                if _usable_word(word):
                    out.words.add((1, digest_words([word])))
    chars = _cjk_tokens(term)
    if len(chars) >= 2:
        head_c = chars[:12]
        out.cjk.add((len(head_c), digest_chars(head_c)))
        if given_name and 3 <= len(chars) <= 4:
            out.cjk.add((2, digest_chars(chars[-2:])))
    return out


def _merge(into: Fingerprints, other: Fingerprints) -> None:
    into.words |= other.words
    into.cjk |= other.cjk
    into.public_words |= other.public_words
    into.public_cjk |= other.public_cjk


def _clean_scalar(raw: str) -> str:
    """YAML 純量的值：去掉行尾註解與前後引號。只處理單行，夠用即可。"""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else value[1:]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _frontmatter_pairs(text: str) -> Iterator[tuple[str, str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return
    for line in lines[1:]:
        if line.strip() == "---":
            return
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if match:
            yield match.group(1), _clean_scalar(match.group(2))


_KV = re.compile(r"^(\s*)(?:-\s+)?([A-Za-z_][\w-]*)\s*:(?:\s+(.*))?$")


def _hex_fingerprints(text: str) -> Fingerprints:
    out = Fingerprints()
    for color in _HEX_COLOR.findall(text):
        if len(set(color[1:].lower())) > 1:  # #000000、#ffffff 這種到處都有的不收
            _merge(out, term_fingerprints(color))
    return out


def brand_fingerprints(text: str) -> Fingerprints:
    """`brand.yaml` 的指紋：`name`、封面預設名稱、色碼、長句子的前八個詞。

    `name`（品牌名）算「已經公開的品牌名」那一組（`public_*`）。
    不靠 PyYAML：這支要能在沒有裝相依的 `python3` 底下跑（git 的 hook 不一定在 uv 環境裡）。
    寫法是逐行看縮排，形狀不對的行直接略過——漏掉一個欄位比整支壞掉好，而且色碼是整份文字用樣式找的。
    """
    out = _hex_fingerprints(text)
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        match = _KV.match(line.rstrip())
        if match is None:
            _merge(out, _phrase_fingerprints(body.removeprefix("- ")))
            continue
        indent, key, raw = len(match.group(1)), match.group(2), match.group(3) or ""
        while stack and stack[-1][0] >= indent:
            stack.pop()
        value = _clean_scalar(raw) if raw.strip() else ""
        if not value:
            if stack and stack[-1][1] == "presets":
                _merge(out, term_fingerprints(key))  # 封面預設的名稱
            stack.append((indent, key))
        elif key in _NAME_KEYS:
            found = term_fingerprints(value, split_words=True)
            if key == "name":
                found.public_words |= found.words
                found.public_cjk |= found.cjk
            _merge(out, found)
        else:
            _merge(out, _phrase_fingerprints(value))
    return out


def _phrase_fingerprints(text: str) -> Fingerprints:
    """長句子（八個詞以上）的前八個詞，跟固定指紋裡的封面 prompt 句子同一種。"""
    out = Fingerprints()
    words = _word_tokens(text)
    if len(words) >= 8:
        out.words.add((8, digest_words(words[:8])))
    return out


def _voice_fingerprints(text: str) -> Fingerprints:
    out = Fingerprints()
    for key, value in _frontmatter_pairs(text):
        if key in _NAME_KEYS and value:
            _merge(out, term_fingerprints(value, split_words=True, given_name=True))
    return out


def persona_fingerprints(persona_dir: Path) -> tuple[Fingerprints, int]:
    """一個角色資料夾的指紋，與讀到幾個檔。內容只在記憶體裡變成雜湊。"""
    out = Fingerprints()
    files = 0
    _merge(out, term_fingerprints(persona_dir.name, split_words=True))
    voice = _read_small(persona_dir / "voice.md")
    if voice is not None:
        files += 1
        _merge(out, _voice_fingerprints(voice))
    brand = _read_small(persona_dir / "brand.yaml")
    if brand is not None:
        files += 1
        _merge(out, brand_fingerprints(brand))
    meta = _read_small(persona_dir / "persona.json")
    if meta is not None:
        files += 1
        try:
            loaded = json.loads(meta)
        except ValueError:
            loaded = None
        shown = loaded.get("display_name") if isinstance(loaded, dict) else None
        if isinstance(shown, str) and shown.strip():
            _merge(out, term_fingerprints(shown, split_words=True))
    return out, files


def _line_heads(text: str) -> Fingerprints:
    """每一行的開頭：英文行取前八個詞、中文行取前十二個字。拿來擋「措辭跟私人素材幾乎一樣」。"""
    out = Fingerprints()
    for line in text.splitlines():
        words = _word_tokens(line)
        if len(words) >= 8:
            out.words.add((8, digest_words(words[:8])))
        chars = _cjk_tokens(line)
        if len(chars) >= 16:
            out.cjk.add((12, digest_chars(chars[:12])))
    return out


def import_fingerprints(imports: Path) -> tuple[Fingerprints, int]:
    """`$EF_DATA/imports/*.zip`（創作者的寫作系統）的指紋：色碼、每行的開頭、與裡面的 voice／brand。

    直接讀成員、不解開到硬碟。讀不開的 zip 算「掃不了」：靜靜略過的話，保護就在不知情的時候少了一塊。
    """
    out = Fingerprints()
    files = 0
    for archive in sorted(imports.glob("*.zip")):
        try:
            with zipfile.ZipFile(archive) as zf:
                members = [m for m in zf.infolist() if not m.is_dir()][:_MAX_ZIP_MEMBERS]
                for member in members:
                    name = member.filename
                    if not name.lower().endswith(_ZIP_TEXT_SUFFIXES) or member.file_size > _MAX_FILE_BYTES:
                        continue
                    text = zf.read(member).decode("utf-8", errors="ignore")
                    files += 1
                    # 色碼只從程式與設定檔取（說明文件裡的色碼是舉例，到處都有）；
                    # 每行的開頭只從文章取（程式碼的行是機制，不是私人素材，拿來比對只會誤擋）
                    if name.lower().endswith((".py", ".yaml", ".yml", ".json")):
                        _merge(out, _hex_fingerprints(text))
                    if name.lower().endswith((".md", ".txt")):
                        _merge(out, _line_heads(text))
                    base = name.rsplit("/", 1)[-1].lower()
                    if base == "voice.md":
                        _merge(out, _voice_fingerprints(text))
                    elif base == "brand.yaml":
                        _merge(out, brand_fingerprints(text))
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise GuardError("imports 裡有讀不開的 zip") from exc
    return out, files


def _read_small(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def resolve_data_dir(env: Mapping[str, str], root: Path = ROOT) -> Path | None:
    """資料目錄在哪；沒設又找不到回 None（那就只用 repo 裡的指紋）。

    跟 `paths.resolve_data_dir` 同一條規則：有設 `EF_DATA` 就用它，沒設用 repo 的 `data/`。
    **設了卻不是讀得到的資料夾 -> `GuardError`（擋下）**：明說了要用它卻讀不到，靜靜退回只有固定指紋，
    保護就在沒人發現的時候少了一大半。
    多一步：在子代理的 worktree（`.claude/worktrees/…`）裡 `data/` 不存在，私人資料放在主工作目錄，
    所以再去 git 的共用目錄旁邊找——不然最常出事的那個地方反而沒有執行期指紋。
    """
    configured = env.get("EF_DATA", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise GuardError("EF_DATA 設了，但指到的不是讀得到的資料夾")
        return path
    candidates = [root / "data"]
    try:
        common = _git(["rev-parse", "--git-common-dir"], cwd=root).strip()
        candidates.append((root / common).resolve().parent / "data")
    except GuardError:
        pass
    return next((path for path in candidates if path.is_dir()), None)


def load_fingerprints(env: Mapping[str, str] | None = None, root: Path = ROOT) -> Fingerprints:
    """repo 裡的長句子指紋，加上 `$EF_DATA` 現算的執行期指紋。"""
    environ = os.environ if env is None else env
    result = Fingerprints.fixed()
    data = resolve_data_dir(environ, root)
    if data is None:
        return result
    runtime = Fingerprints()
    authors = data / "authors"
    if authors.is_dir():
        for persona in sorted(authors.iterdir()):
            if persona.name.startswith(".") or not persona.is_dir():
                continue
            found, files = persona_fingerprints(persona)
            _merge(runtime, found)
            runtime.source_files += files
    if (data / "imports").is_dir():
        imported, files = import_fingerprints(data / "imports")
        _merge(runtime, imported)
        runtime.source_files += files
    # 公開預設裡本來就有的詞（範本的預設名字、預設色票）不算私人內容
    for rel in _PUBLIC_DEFAULTS:
        text = _read_small(root / rel)
        if text is None:
            continue
        public = hits_in_text(text, Fingerprints(words=set(runtime.words), cjk=set(runtime.cjk)))
        runtime.words -= public
        runtime.cjk -= public
    _merge(result, runtime)
    result.runtime_terms = len(runtime)
    result.source_files = runtime.source_files
    return result


# ── 比對 ────────────────────────────────────────────────────────────────

Token = tuple[str, int, bool]  # (詞或字, 行號, 是不是新增的)


def _scan_tokens(tokens: Sequence[Token], wanted: set[Fingerprint], joiner: str) -> set[int]:
    by_n: dict[int, set[str]] = {}
    for n, digest in wanted:
        by_n.setdefault(n, set()).add(digest)
    lines: set[int] = set()
    for n, digests in by_n.items():
        for i in range(len(tokens) - n + 1):
            window = tokens[i : i + n]
            added = [t[1] for t in window if t[2]]
            if not added:
                continue  # 整個窗都是上下文，不是這次新加的
            if hashlib.sha256(joiner.join(t[0] for t in window).encode()).hexdigest() in digests:
                lines.add(added[0])
    return lines


def scan_lines(lines: Iterable[tuple[int, str, bool]], fingerprints: Fingerprints) -> set[int]:
    """依序給 (行號, 文字, 是不是新增行)，回命中的行號。窗可以跨行，且至少要含一個新增的詞。"""
    wtokens: list[Token] = []
    ctokens: list[Token] = []
    for lineno, text, added in lines:
        wtokens.extend((w, lineno, added) for w in _word_tokens(text))
        ctokens.extend((c, lineno, added) for c in _cjk_tokens(text))
    return _scan_tokens(wtokens, fingerprints.words, " ") | _scan_tokens(
        ctokens, fingerprints.cjk, ""
    )


def hits_in_text(text: str, fingerprints: Fingerprints) -> set[Fingerprint]:
    """整段文字裡出現了哪些指紋。給測試與「剔除公開預設」用。"""
    found: set[Fingerprint] = set()
    for tokens, wanted, joiner in (
        (_word_tokens(text), fingerprints.words, " "),
        (_cjk_tokens(text), fingerprints.cjk, ""),
    ):
        for n in {n for n, _ in wanted}:
            digests = {d for k, d in wanted if k == n}
            for i in range(len(tokens) - n + 1):
                digest = hashlib.sha256(joiner.join(tokens[i : i + n]).encode()).hexdigest()
                if digest in digests:
                    found.add((n, digest))
    return found


def scan_text(text: str, fingerprints: Fingerprints) -> set[int]:
    """一段文字（整檔、commit message、指令文字）：每一行都算新增，回命中的行號。"""
    return scan_lines(((i, line, True) for i, line in enumerate(text.splitlines(), 1)), fingerprints)


scan_file_text = scan_text


# ── diff ────────────────────────────────────────────────────────────────

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_COMMIT = re.compile(r"^commit ([0-9a-f]{40})\b")


@dataclass
class Hunk:
    commit: str  # 空字串＝不是從 git log 來的（暫存區）
    path: str
    lines: list[tuple[int, str, bool]]
    # 非空＝這不是內容，是一個檔的標頭（`diff --git a/x b/y` 後面那段，含改名前後的路徑），拿來掃路徑
    header: str = ""


def parse_diff(diff: str) -> Iterator[Hunk]:
    """把 unified diff（`git diff`、`git log -p --format='commit %H'`）拆成一段一段的 hunk。

    靠 hunk 標頭的行數決定哪裡結束，不靠看起來像不像：commit 說明、空行、下一個檔的標頭都不會被當成內容。
    每個檔另外先吐一個只帶 `header` 的 Hunk，路徑（含改名、沒有內容的新檔）才掃得到。
    """
    commit = ""
    path = ""
    old_left = new_left = 0
    lineno = 0
    current: list[tuple[int, str, bool]] | None = None
    for raw in diff.splitlines():
        if old_left > 0 or new_left > 0:
            assert current is not None
            tag, body = raw[:1], raw[1:]
            if tag == "+":
                new_left -= 1
                current.append((lineno, body, True))
                lineno += 1
            elif tag == "-":
                old_left -= 1
            elif tag == " " or raw == "":
                old_left -= 1
                new_left -= 1
                current.append((lineno, body, False))
                lineno += 1
            elif tag != "\\":  # 形狀不對：這段到此為止
                old_left = new_left = 0
            if old_left <= 0 and new_left <= 0:
                yield Hunk(commit, path, current)
                current = None
                old_left = new_left = 0
            continue
        if current is not None:  # 形狀不對收尾的那一段
            yield Hunk(commit, path, current)
            current = None
        if (found := _COMMIT.match(raw)) is not None:
            commit = found.group(1)[:8]
        elif raw.startswith("diff --git "):
            path = ""
            yield Hunk(commit, path, [], header=raw[len("diff --git ") :])
        elif raw.startswith("+++ "):
            name = raw[4:].split("\t", 1)[0].strip()
            if name == "/dev/null":
                path = ""
            else:
                name = name.strip('"')
                path = name[2:] if name[:2] in ("a/", "b/") else name
        elif (hunk := _HUNK.match(raw)) is not None:
            old_left = int(hunk.group(1)) if hunk.group(1) is not None else 1
            lineno = int(hunk.group(2))
            new_left = int(hunk.group(3)) if hunk.group(3) is not None else 1
            current = []
            if old_left == 0 and new_left == 0:  # `-0,0 +1,0`：空 hunk
                yield Hunk(commit, path, current)
                current = None
    if current is not None:  # 輸入在 hunk 中間斷掉（行數對不上）：已讀到的照樣掃
        yield Hunk(commit, path, current)


Hits = dict[tuple[str, str], set[int]]  # (commit, 路徑或標籤) -> 行號


def scan_diff(diff: str, fingerprints: Fingerprints) -> Hits:
    hits: Hits = {}
    docs_fp = fingerprints.without_public()
    masked: dict[str, bool] = {}
    for hunk in parse_diff(diff):
        if hunk.header:
            in_docs = hunk.header.startswith(tuple("a/" + p for p in DOCS_PREFIXES))
            if scan_lines([(0, hunk.header, True)], docs_fp if in_docs else fingerprints):
                hits.setdefault((hunk.commit, MASK_PATH), set())
            continue
        if not hunk.lines:
            continue
        fp = docs_fp if hunk.path.startswith(DOCS_PREFIXES) else fingerprints
        found = scan_lines(hunk.lines, fp)
        if not found:
            continue
        if hunk.path not in masked:  # 路徑本身會洩漏的話，連路徑也不印
            masked[hunk.path] = bool(scan_lines([(0, hunk.path, True)], fp))
        shown = MASK_PATH if masked[hunk.path] else hunk.path
        hits.setdefault((hunk.commit, shown), set()).update(found)
    return hits


# ── git ─────────────────────────────────────────────────────────────────


def _git(args: Sequence[str], cwd: Path | None = None, ok: Iterable[int] = (0,)) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise GuardError(f"跑不了 git：{exc.strerror}") from exc
    if proc.returncode not in tuple(ok):
        raise GuardError(f"git {args[0]} 失敗（exit {proc.returncode}）：{proc.stderr.strip()[:200]}")
    return proc.stdout


_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/", "-U3")


def staged_diff() -> str:
    return _git(["diff", "--cached", *_DIFF_FLAGS])


def _add_hits(into: Hits, other: Hits) -> None:
    for key, lines in other.items():
        into.setdefault(key, set()).update(lines)


def _log_diff(revs: Sequence[str]) -> str:
    # `--diff-merges=first-parent`：merge commit 也要有 diff（衝突解掉的內容只存在 merge commit 裡）；
    # 預設的 `git log -p` 不給 merge 的 diff。需要 git 2.31 以上。
    return _git(["log", "-p", "--diff-merges=first-parent", "--format=commit %H", *_DIFF_FLAGS, *revs])


def _log_messages(revs: Sequence[str], fingerprints: Fingerprints) -> Hits:
    """要推的每個 commit 的 message。"""
    out = _git(["log", "--format=%H%x00%B%x00", *revs])
    parts = out.split("\0")
    hits: Hits = {}
    for i in range(0, len(parts) - 1, 2):
        sha, body = parts[i].strip(), parts[i + 1]
        if found := scan_text(body, fingerprints):
            hits.setdefault((sha[:8], LABEL_MESSAGE), set()).update(found)
    return hits


def _scan_range(revs: Sequence[str], fingerprints: Fingerprints) -> Hits:
    hits = scan_diff(_log_diff(revs), fingerprints)
    _add_hits(hits, _log_messages(revs, fingerprints))
    return hits


def _remote_scope(remote: str | None) -> list[str]:
    """「還沒有任何遠端有」的條件：指到的是遠端名稱就看那個遠端，否則看全部遠端。"""
    if remote and remote in _git(["remote"]).split():
        return ["--not", f"--remotes={remote}"]
    return ["--not", "--remotes"]


def push_command_revs(args: Sequence[str]) -> tuple[list[str], list[str]]:
    """`git push` 後面的參數 -> (要推的 revs, 範圍條件)。只認得常見的寫法。

    第一個不帶 `-` 的詞是遠端，之後的是 refspec（`+src:dst`、`HEAD`、分支名）；沒寫 refspec 就是目前分支。
    **解不開的來源 -> `GuardError`（擋下）**：打錯字的分支名、認不得的寫法，靜靜略過等於沒掃。
    只有刪除（`:dst`、`--delete`）沒有新內容，那才是合法的略過。
    """
    if "--delete" in args or "-d" in args:
        return [], []
    positional = [a for a in args if not a.startswith("-")]
    remote = positional[0] if positional else None
    specs = positional[1:]
    revs: list[str] = []
    if "--all" in args or "--mirror" in args:
        revs.append("--branches")
    if "--tags" in args:
        revs.append("--tags")
    if not revs or specs:
        for spec in specs or ["HEAD"]:
            src = spec.lstrip("+").split(":", 1)[0]
            if not src:  # `:dst` 是刪除遠端的分支
                continue
            if not _has(src):
                raise GuardError(f"解不開要推的來源「{src}」，不知道會推出什麼")
            revs.append(src)
    return revs, _remote_scope(remote)


def push_hits(args: Sequence[str], fingerprints: Fingerprints) -> Hits:
    revs, scope = push_command_revs(args)
    if not revs:
        return {}
    return _scan_range([*revs, *scope], fingerprints)


def prepush_hits(remote: str, stdin: Iterable[str], fingerprints: Fingerprints) -> Hits:
    """git 原生 pre-push 的標準輸入：每行 `<本地 ref> <本地 sha> <遠端 ref> <遠端 sha>`。"""
    zeros = "0" * 40
    hits: Hits = {}
    for line in stdin:
        parts = line.split()
        if len(parts) != 4 or parts[1] == zeros:  # 刪除遠端分支：沒有新內容
            continue
        local, remote_sha = parts[1], parts[3]
        known = remote_sha != zeros and _has(remote_sha)
        revs = [f"{remote_sha}..{local}"] if known else [local, *_remote_scope(remote)]
        _add_hits(hits, _scan_range(revs, fingerprints))
    return hits


def _has(rev: str) -> bool:
    """這個 rev 本機解不解得開成 commit（別人推過、我們還沒 fetch 的 sha 就解不開）。"""
    return bool(_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], ok=(0, 1)).strip())


def message_hits(path: Path, fingerprints: Fingerprints) -> Hits:
    """`commit-msg` hook：git 給的訊息檔。`#` 開頭的行是 git 加的說明，不是訊息，不掃。"""
    text = _read_small(path)
    if text is None:
        raise GuardError("讀不到 commit message 檔")
    kept = "\n".join("" if line.startswith("#") else line for line in text.splitlines())
    found = scan_text(kept, fingerprints)
    return {("", LABEL_MESSAGE): found} if found else {}


# ── 輸出與進入點 ────────────────────────────────────────────────────────


def format_hits(hits: Hits, fingerprints: Fingerprints) -> str:
    out = ["brand_guard：掃到創作者的私人內容（只列位置，不列內容）："]
    for (commit, label), lines in hits.items():
        where = ":" + ", ".join(str(n) for n in sorted(lines)) if lines else ""
        out.append(f"  {commit + ' ' if commit else ''}{label or '(刪除的檔)'}{where}")
    out.append(
        "怎麼辦：改成虛構的名字、虛構的色票與用語（例：假角色 Ada Fakename、#123456），不要用真人的；"
        "已經 commit 進本機歷史的，推之前先改掉那幾個 commit。不要用 --no-verify 繞過，那是使用者自己的決定。"
    )
    out.append(describe(fingerprints))
    return "\n".join(out)


def describe(fingerprints: Fingerprints) -> str:
    if fingerprints.runtime_terms:
        return (
            f"（指紋：repo 裡 {len(FINGERPRINTS)} 組長句子，另有 {fingerprints.runtime_terms} 組從 "
            f"$EF_DATA 的 {fingerprints.source_files} 個檔現算）"
        )
    return (
        f"（指紋：只有 repo 裡的 {len(FINGERPRINTS)} 組長句子；沒有從 $EF_DATA 現算出任何指紋"
        "（沒設、或裡面沒有 authors／imports），名字、色碼這類低熵的指紋這次沒有）"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brand_guard",
        description="commit／push 之前掃私人內容指紋。有命中 exit 1（只印位置），掃不了 exit 2，乾淨 exit 0。",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("staged", help="掃暫存區的 diff（路徑也掃；git 原生 pre-commit 用）")
    message = sub.add_parser("message", help="掃 commit message 檔（git 原生 commit-msg 用）")
    message.add_argument("path")
    push = sub.add_parser("push", help="掃 `git push` 指令要推出去的 commit；`--` 後面照 git push 的參數寫")
    push.add_argument("push_args", nargs="*")
    prepush = sub.add_parser("pre-push", help="給 git 原生 pre-push 用：參數是遠端名稱，標準輸入是 ref 清單")
    prepush.add_argument("remote", nargs="?", default="")
    prepush.add_argument("url", nargs="?", default="")
    sub.add_parser("diff", help="從標準輸入讀 unified diff")
    files = sub.add_parser("files", help="整檔掃描")
    files.add_argument("paths", nargs="+")
    guard = sub.add_parser(
        "guard", help="給 Claude Code 守衛用：標準輸入是指令；目標 repo 沒啟用 .githooks 就擋（見 git_gate.py）"
    )
    guard.add_argument("--cwd", default="")
    guard.add_argument("--raw-file", default="", help="沒去掉 heredoc 的原始指令，掃裡面的訊息文字用")
    sub.add_parser("stats", help="只印指紋的來源與數量，不掃")
    return parser


def run(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(list(argv))
    hits: Hits = {}
    fingerprints = Fingerprints.fixed()
    try:
        fingerprints = load_fingerprints(env)
        if args.mode == "staged":
            hits = scan_diff(staged_diff(), fingerprints)
        elif args.mode == "message":
            hits = message_hits(Path(args.path), fingerprints)
        elif args.mode == "push":
            hits = push_hits(shlex.split(" ".join(args.push_args)), fingerprints)
        elif args.mode == "pre-push":
            hits = prepush_hits(args.remote, sys.stdin, fingerprints)
        elif args.mode == "diff":
            hits = scan_diff(sys.stdin.read(), fingerprints)
        elif args.mode == "files":
            for name in args.paths:
                if not Path(name).is_file():
                    raise GuardError(f"讀不到檔案：{name}")
                text = _read_small(Path(name))
                if text is None:  # 太大：不是文字稿，略過
                    continue
                if found := scan_text(text, fingerprints):
                    hits[("", name)] = found
        elif args.mode == "guard":
            from evidence_forge.git_gate import guard_command

            raw = _read_small(Path(args.raw_file)) if args.raw_file else None
            problems = guard_command(sys.stdin.read(), args.cwd, raw, fingerprints)
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 1
            return 0
        else:
            print(describe(fingerprints), file=sys.stderr)
            return 0
    except (GuardError, ValueError, OSError) as exc:  # 引號沒收好的參數、讀檔失敗也算「掃不了」
        print(f"brand_guard：掃不了，所以先擋下：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if hits:
        print(format_hits(hits, fingerprints), file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
