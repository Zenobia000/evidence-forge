"""UI 樣式檔的守門：只用 token、名單齊全、不載外部資源（ADR-0007、ADR-0017）。"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CSS_DIR = REPO_ROOT / "src" / "evidence_forge" / "ui" / "static" / "css"
TOKENS_CSS = CSS_DIR / "tokens.css"
S0_CSS = CSS_DIR / "s0.css"
ADR_0017 = (
    REPO_ROOT / "docs" / "10-truth" / "adr" / "0017-status-tokens-for-new-design.md"
)
ADR_0023 = REPO_ROOT / "docs" / "10-truth" / "adr" / "0023-chat-room-tokens.md"
CHAT_CSS = CSS_DIR / "chat.css"

# S0 要用的四組前綴；--source-*、--license-*、--asset-* 是 S3 才加的，不在這裡檢查。
S0_PREFIXES = ("job", "check", "claim", "genre")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _defined(css: str) -> set[str]:
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", _strip_comments(css)))


def _used(css: str) -> set[str]:
    return set(re.findall(r"var\(\s*(--[a-z0-9-]+)", _strip_comments(css)))


def _adr_names() -> set[str]:
    """ADR-0017 的名單，扣掉 ADR-0023 §3.2 退役的。"""
    prefixes = "|".join(S0_PREFIXES)
    names = set(
        re.findall(
            rf"`(--(?:{prefixes})-[a-z-]+)`", ADR_0017.read_text(encoding="utf-8")
        )
    )
    return names - _retired_names()


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find("\n### ")
    return rest if end == -1 else rest[:end]


def _first_cells(section: str) -> set[str]:
    """表格每一列第一欄裡的 token 名（`--x-y`、`--x-z`）。"""
    names: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| `--"):
            names |= set(re.findall(r"`(--[a-z]+-[a-z-]+)`", line.split("|")[1]))
    return names


def _retired_names() -> set[str]:
    text = ADR_0023.read_text(encoding="utf-8")
    return _first_cells(_section(text, "### 3.2 退役"))


def _new_names() -> set[str]:
    text = ADR_0023.read_text(encoding="utf-8")
    return _first_cells(_section(text, "### 3.1 新增"))


@pytest.fixture(scope="module")
def tokens_css() -> str:
    return TOKENS_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def s0_css() -> str:
    return S0_CSS.read_text(encoding="utf-8")


def test_every_variable_s0_uses_is_defined(tokens_css: str, s0_css: str) -> None:
    missing = _used(s0_css) - _defined(tokens_css) - _defined(s0_css)
    assert not missing, f"s0.css 用到但沒定義的變數：{sorted(missing)}"


def test_s0_has_no_hard_coded_colors(s0_css: str) -> None:
    # 顏色只能來自 tokens.css 的變數；色碼與 rgb()/hsl() 都不准直接寫。
    body = _strip_comments(s0_css)
    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    functions = re.findall(r"\b(?:rgba?|hsla?)\(", body)
    assert not hex_colors and not functions, f"寫死的顏色：{hex_colors + functions}"


def test_adr_0017_lists_names_this_test_can_find() -> None:
    # 擋住「ADR 改了寫法、正規式抓不到，下面那條測試就變成空檢查」的情況。
    names = _adr_names()
    for prefix in S0_PREFIXES:
        assert any(n.startswith(f"--{prefix}-") for n in names), prefix
    assert {"--claim-fact", "--claim-data", "--claim-opinion"} <= names


def test_every_s0_status_token_in_adr_0017_is_defined(s0_css: str) -> None:
    missing = _adr_names() - _defined(s0_css)
    assert not missing, f"ADR-0017 有、s0.css 沒定義：{sorted(missing)}"


def test_adr_0023_new_tokens_are_defined_and_retired_ones_are_gone(s0_css: str) -> None:
    # 擋住「ADR 改了寫法、正規式抓不到」變成空檢查
    assert {"--chat-new", "--verify-disputed", "--verify-unfound", "--diff-insert"} <= _new_names()
    assert {"--job-review", "--job-approved", "--job-discarded"} == _retired_names()
    assert not (_new_names() - _defined(s0_css)), "ADR-0023 有、s0.css 沒定義"
    assert not (_retired_names() & _defined(s0_css)), "ADR-0023 已退役、s0.css 還留著"


def test_chat_css_uses_only_defined_variables_and_no_hard_coded_colors(
    tokens_css: str, s0_css: str
) -> None:
    css = CHAT_CSS.read_text(encoding="utf-8")
    missing = _used(css) - _defined(tokens_css) - _defined(s0_css) - _defined(css)
    assert not missing, f"chat.css 用到但沒定義的變數：{sorted(missing)}"
    body = _strip_comments(css)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not re.findall(r"\b(?:rgba?|hsla?)\(", body)
    assert "@import" not in body


@pytest.mark.parametrize("path", [TOKENS_CSS, S0_CSS, CHAT_CSS], ids=lambda p: p.name)
def test_css_loads_nothing_from_outside(path: Path) -> None:
    # 不載網路字型、不連 CDN：中文用系統字型，英文字型自己放（ADR-0017 §3.3）。
    body = _strip_comments(path.read_text(encoding="utf-8"))
    assert "@import" not in body
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", body)
