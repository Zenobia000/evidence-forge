"""Claude Code 守衛的 git 關卡：看懂一條 shell 指令，判斷它要 commit／push 的那個 repo 有沒有啟用私人內容 hook（#126）。

設計是創作者定的：**主關卡是 git 原生 hook**（`.githooks/pre-commit`、`commit-msg`、`pre-push`，
`git config core.hooksPath .githooks` 啟用）。Claude 守衛的工作變成兩件事：
1. 目標 repo 沒有啟用這些 hook（`core.hooksPath` 不是 `.githooks`、或 hook 檔缺了）就擋下
   `git commit`／`git push`／`gh pr create`／`gh pr merge`——沒有 hook 的 repo，守衛一個一個指令掃也掃不乾淨。
2. 不准用指令把 hook 關掉：`-c core.hooksPath=…`、`GIT_CONFIG_*` 環境變數、改 `core.hooksPath` 的 `git config`、
   `git push --no-verify`、`git commit --no-verify`／`-n`（任何位置）。
另外掃指令裡的文字（commit message、PR 標題與內文）與 `-F`／`--body-file` 指到的檔。

「目標 repo」不等於守衛的工作目錄：`git -C <dir> commit`、`cd <dir> && git commit`、
`git --git-dir=… --work-tree=… commit`、`bash -c '…'`、`eval '…'`、`$( )` 都會換地方。
這裡把指令切成片段、一路追 `cd`／`-C`，目標資料夾（起點是 hook 輸入的 `.cwd`）算不出來就**擋下**
（`$VAR`、`cd -` 這類算不出來的寫法也一樣）：看不清楚的 commit，不放行。

不處理的（寫出來，免得以為它保證了全部）：`xargs git …`、函式與 alias 在 shell 裡才定義的別名（git 設定裡的
alias 會查）、腳本檔裡面的 git 指令。這些情況由 git 原生 hook 在 commit／push 當下接住——那也是它是主關卡的原因。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from evidence_forge.brand_guard import Fingerprints, GuardError, _git, _read_small, scan_text

_SEP_CHARS = frozenset("&|;\n()`")
_WRAPPERS = frozenset({"command", "builtin", "exec", "time", "nice", "nohup", "env", "sudo", "doas"})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_HOOKS = ("pre-commit", "commit-msg", "pre-push")
_MAX_DEPTH = 4
# 查 alias 要多跑一次 git；這些是一定不是 alias 的內建指令，不查
_KNOWN_SUBS = frozenset(
    {
        "status", "log", "diff", "add", "commit", "push", "pull", "fetch", "checkout", "switch", "branch",
        "merge", "rebase", "reset", "restore", "stash", "show", "tag", "remote", "config", "clone", "init",
        "rm", "mv", "cherry-pick", "revert", "worktree", "rev-parse", "rev-list", "ls-files", "cat-file",
        "blame", "grep", "describe", "apply", "am", "format-patch", "bisect", "clean", "gc", "submodule",
    }
)  # fmt: skip
_VALUE_OPTS = ("-m", "-F", "-C", "-c", "-t", "--message", "--file", "--reuse-message", "--reedit-message", "--template", "--author", "--date")
MASK_FILE = "檔名已遮蔽"
_SENSITIVE = frozenset({"git commit", "git push", "gh pr create", "gh pr merge"})

FIX_HINT = (
    "啟用方式：在那個 repo 由使用者執行 `git config core.hooksPath .githooks`（代理不要自己改 git 設定）。"
    "沒有 hook 的 repo，commit／push 前沒有任何東西在掃私人內容，而推出去就收不回來。"
)


@dataclass
class Invocation:
    tool: str  # "git" | "gh"
    sub: str  # "commit"、"push"、"pr create"…
    args: list[str]
    dir: Path | None  # None＝算不出來
    git_opts: list[str] = field(default_factory=list)  # 原樣帶給後面查設定用的 --git-dir／--work-tree
    c_values: list[str] = field(default_factory=list)  # `-c k=v` 的 k=v
    env: list[str] = field(default_factory=list)  # 指令前面的 VAR=value

    @property
    def name(self) -> str:
        return f"{self.tool} {self.sub}"


# ── 把指令切開 ──────────────────────────────────────────────────────────


def tokenize(cmd: str) -> tuple[list[tuple[str, str]], list[str]]:
    """-> (詞與分隔符號, 雙引號裡藏的 `$( )`／反引號子指令)。詞是 ("w", 文字)、分隔是 ("s", 字元)。

    引號與反斜線照 shell 的規則拿掉；沒收好的引號丟 ValueError（呼叫端當成「掃不了」擋下）。
    """
    items: list[tuple[str, str]] = []
    nested: list[str] = []
    buf: list[str] = []
    have = False
    i, n = 0, len(cmd)

    def flush() -> None:
        nonlocal have
        if have:
            items.append(("w", "".join(buf)))
        buf.clear()
        have = False

    while i < n:
        c = cmd[i]
        if c == "\\":
            if i + 1 < n and cmd[i + 1] != "\n":
                buf.append(cmd[i + 1])
                have = True
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise ValueError("單引號沒收好")
            buf.append(cmd[i + 1 : j])
            have = True
            i = j + 1
        elif c == '"':
            j = i + 1
            inner: list[str] = []
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    inner.append(cmd[j + 1] if cmd[j + 1] in '"\\$`' else cmd[j : j + 2])
                    j += 2
                else:
                    inner.append(cmd[j])
                    j += 1
            if j >= n:
                raise ValueError("雙引號沒收好")
            text = "".join(inner)
            nested.extend(m.group(1) or m.group(2) for m in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", text))
            buf.append(text)
            have = True
            i = j + 1
        elif c in " \t\r":
            flush()
            i += 1
        elif c in _SEP_CHARS:
            flush()
            items.append(("s", c))
            i += 1
        elif c == "#" and not have:  # 行首的 # 到行尾是註解
            while i < n and cmd[i] != "\n":
                i += 1
        else:
            buf.append(c)
            have = True
            i += 1
    flush()
    return items, nested


def _join(base: Path | None, value: str) -> Path | None:
    """`cd`／`-C` 的路徑接在目前資料夾後面；含變數或命令替換就算不出來（None）。"""
    if base is None or "$" in value or "`" in value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _split_value(args: list[str], i: int) -> tuple[str, int]:
    """`--opt=value` 或 `--opt value`：回 (value, 下一個位置)。"""
    if "=" in args[i]:
        return args[i].split("=", 1)[1], i + 1
    if i + 1 >= len(args):
        raise ValueError(f"{args[i]} 少了值")
    return args[i + 1], i + 2


def _parse_git(args: list[str], base: Path | None, env: list[str]) -> Invocation:
    d = base
    git_opts: list[str] = []
    c_values: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C":
            if i + 1 >= len(args):
                raise ValueError("-C 少了值")
            d = _join(d, args[i + 1])
            i += 2
        elif a == "-c":
            if i + 1 >= len(args):
                raise ValueError("-c 少了值")
            c_values.append(args[i + 1])
            i += 2
        elif a.startswith(("--git-dir", "--work-tree")):
            value, i = _split_value(args, i)
            resolved = _join(d, value)
            if resolved is None:
                d = None
            else:
                git_opts.append(f"{a.split('=', 1)[0]}={resolved}")
        elif a.startswith("--config-env"):
            value, i = _split_value(args, i)
            c_values.append(value)  # `名稱=環境變數`：名稱是 core.hooksPath 一樣要擋
        elif a in ("--namespace", "--super-prefix"):
            i += 2
        else:
            i += 1  # 其他沒有值的全域旗標：-p、--no-pager、--bare…
    sub = args[i] if i < len(args) else ""
    rest = args[i + 1 :]
    for value in c_values:  # `-c alias.ci=commit ci`：命令列上臨時定義的 alias 也照它的意思算
        key, _, body = value.partition("=")
        if key.lower() == f"alias.{sub}":
            sub, rest = _alias_target(body, sub, rest)
            break
    else:
        if sub and sub not in _KNOWN_SUBS and d is not None:
            sub, rest = _resolve_alias(d, git_opts, sub, rest)
    return Invocation("git", sub, rest, d, git_opts, c_values, env)


def _resolve_alias(d: Path, git_opts: list[str], sub: str, rest: list[str]) -> tuple[str, list[str]]:
    """git 設定裡的 alias（`git ci` → `git commit`）。`!` 開頭的是 shell 別名，看得出 commit／push 就照那個算。"""
    try:
        value = _git(["-C", str(d), *git_opts, "config", "--get", f"alias.{sub}"], ok=(0, 1)).strip()
    except GuardError:
        return sub, rest
    return _alias_target(value, sub, rest) if value else (sub, rest)


def _alias_target(value: str, sub: str, rest: list[str]) -> tuple[str, list[str]]:
    if value.startswith("!"):
        found = re.search(r"\b(commit|push)\b", value)
        return (found.group(1), rest) if found else (sub, rest)
    words = value.split()
    return (words[0], words[1:] + rest) if words else (sub, rest)


def _parse_gh(args: list[str], base: Path | None, env: list[str]) -> Invocation | None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-R", "--repo") else 1
    if i + 1 >= len(args) or args[i] != "pr" or args[i + 1] not in ("create", "merge"):
        return None
    return Invocation("gh", f"pr {args[i + 1]}", args[i + 2 :], base, [], [], env)


def parse_command(cmd: str, base: Path | None, depth: int = 0) -> list[Invocation]:
    """一條指令裡所有的 git／gh 呼叫，各自附上「它會在哪個資料夾跑」。"""
    if depth > _MAX_DEPTH:
        raise ValueError("指令巢狀太深，看不清楚")
    items, nested = tokenize(cmd)
    invocations: list[Invocation] = []
    cur = base
    stack: list[Path | None] = []
    words: list[str] = []

    def segment() -> None:
        nonlocal cur, words
        seg, words = words, []
        env: list[str] = []
        while seg and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
            env.append(seg.pop(0))
        while seg and os.path.basename(seg[0]) in _WRAPPERS:
            seg.pop(0)
            while seg and (seg[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0])):
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
                    env.append(seg[0])
                seg.pop(0)
        if not seg:
            return
        head = os.path.basename(seg[0])
        if head in ("cd", "pushd"):
            args = [a for a in seg[1:] if a not in ("--", "-L", "-P", "-e", "-@")]
            if not args:
                cur = Path.home()
            elif args[0] == "-" or len(args) > 1:
                cur = None
            else:
                cur = _join(cur, args[0])
        elif head == "popd":
            cur = None
        elif head in _SHELLS:
            for k, a in enumerate(seg[1:], 1):
                if a.startswith("-") and not a.startswith("--") and "c" in a and k + 1 < len(seg):
                    invocations.extend(parse_command(seg[k + 1], cur, depth + 1))
                    break
        elif head == "eval":
            invocations.extend(parse_command(" ".join(seg[1:]), cur, depth + 1))
        elif head == "git":
            invocations.append(_parse_git(seg[1:], cur, env))
        elif head == "gh":
            found = _parse_gh(seg[1:], cur, env)
            if found is not None:
                invocations.append(found)

    for kind, value in items:
        if kind == "w":
            words.append(value)
            continue
        segment()
        if value == "(":
            stack.append(cur)
        elif value == ")" and stack:
            cur = stack.pop()
    segment()
    for text in nested:
        invocations.extend(parse_command(text, cur, depth + 1))
    return invocations


# ── 判斷 ────────────────────────────────────────────────────────────────


def _hooks_problem(inv: Invocation) -> str | None:
    """目標 repo 有沒有啟用 `.githooks`；有問題回說明，沒問題回 None。"""
    d = inv.dir
    if d is None:
        return "算不出它會在哪個資料夾跑（路徑含變數、`cd -` 之類），看不清楚的 commit／push 不放行。"
    if not d.is_dir():
        return "它要跑的資料夾不存在，看不清楚的 commit／push 不放行。"
    base = ["-C", str(d), *inv.git_opts]
    try:
        top = Path(_git([*base, "rev-parse", "--show-toplevel"]).strip())
        value = _git([*base, "config", "--get", "core.hooksPath"], ok=(0, 1)).strip()
    except GuardError:
        return "那裡讀不到 git repo，不知道 hook 有沒有啟用。"
    if not value:
        return "那個 repo 沒設 core.hooksPath，私人內容 hook 沒有啟用。"
    configured = Path(value).expanduser()
    resolved = configured if configured.is_absolute() else top / configured
    expected = top / ".githooks"
    if resolved.resolve() != expected.resolve():
        return "那個 repo 的 core.hooksPath 不是 .githooks，私人內容 hook 沒有啟用。"
    missing = [name for name in _HOOKS if not os.access(expected / name, os.X_OK)]
    if missing:
        return f"那個 repo 的 .githooks 缺了可執行的 {'、'.join(missing)}，私人內容 hook 沒有完整啟用。"
    return None


def _bypass_problems(inv: Invocation) -> list[str]:
    problems: list[str] = []
    if any(e.split("=", 1)[0].upper().startswith("GIT_CONFIG") for e in inv.env):
        problems.append("用 GIT_CONFIG_* 環境變數改 git 設定，等於能把 hook 關掉。")
    for value in inv.c_values:
        key = value.split("=", 1)[0].lower()
        if key.startswith("core.hookspath"):
            problems.append("`-c core.hooksPath=…` 會把私人內容 hook 換掉或關掉。")
        elif key.startswith("alias."):
            problems.append("用 `-c alias.…` 換指令的意思，看不清楚它真正要做什麼。")
    if inv.name == "git push" and any(a == "--no-verify" or a.startswith("--no-verify=") for a in inv.args):
        problems.append("`git push --no-verify` 會跳過 pre-push 的私人內容掃描。")
    if inv.name == "git commit":
        skip = False
        for a in inv.args:
            if skip:
                skip = False
            elif a in _VALUE_OPTS:
                skip = True
            elif a == "--no-verify" or re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a):
                problems.append("commit 跳過 hook（--no-verify／-n）。壞掉的 hook 是要修的訊號，繞過是使用者自己的決定。")
    return problems


def _config_problem(inv: Invocation) -> str | None:
    """`git config … core.hooksPath …`（寫入或刪除）：代理不改這個。只讀的 --get／--list 放行。"""
    if inv.name != "git config":
        return None
    lowered = [a.lower() for a in inv.args]
    if not any(a.startswith("core.hookspath") for a in lowered):
        return None
    if any(a in ("--get", "--get-all", "--list", "-l", "--get-regexp") for a in lowered):
        return None
    return "改 core.hooksPath 是使用者的事（它決定私人內容 hook 有沒有在守門），代理不改。"


def _message_files(inv: Invocation) -> list[Path]:
    """`-F`／`--file`／`--body-file` 指到的檔（commit message、PR 內文）。"""
    found: list[Path] = []
    args = inv.args
    for i, a in enumerate(args):
        value = None
        if a in ("-F", "--file", "--body-file") and i + 1 < len(args):
            value = args[i + 1]
        elif a.startswith(("--file=", "--body-file=")):
            value = a.split("=", 1)[1]
        if value and value != "-" and inv.dir is not None:
            path = Path(value).expanduser()
            found.append(path if path.is_absolute() else inv.dir / path)
    return found


def guard_command(
    cmd: str, cwd: str, raw: str | None, fingerprints: Fingerprints
) -> list[str]:
    """一條 Bash 指令 -> 該擋的理由（空＝放行）。`cmd` 是去掉 heredoc 內容的指令，`raw` 是原始的（掃訊息文字用）。"""
    base = Path(cwd) if cwd else Path.cwd()
    problems: list[str] = []
    text_checked = False
    for inv in parse_command(cmd, base):
        if (issue := _config_problem(inv)) is not None:
            problems.append(f"{inv.name}：{issue}")
        if inv.name not in _SENSITIVE:
            continue
        problems.extend(f"{inv.name}：{p}" for p in _bypass_problems(inv))
        if (issue := _hooks_problem(inv)) is not None:
            problems.append(f"{inv.name}：{issue}")
        if inv.name in ("git commit", "gh pr create"):
            if not text_checked:  # 整條指令只掃一次文字
                text_checked = True
                found = scan_text(raw if raw is not None else cmd, fingerprints)
                if found:
                    where = ", ".join(str(n) for n in sorted(found))
                    problems.append(f"{inv.name}：指令文字（commit message、PR 標題或內文）第 {where} 行有創作者的私人內容（不印內容）。")
            for path in _message_files(inv):
                text = _read_small(path)
                found = scan_text(text, fingerprints) if text is not None else set()
                if found:
                    where = ", ".join(str(n) for n in sorted(found))
                    problems.append(f"{inv.name}：訊息檔（{MASK_FILE}）第 {where} 行有創作者的私人內容（不印內容）。")
    if problems:
        problems.append(FIX_HINT)
    return list(dict.fromkeys(problems))
