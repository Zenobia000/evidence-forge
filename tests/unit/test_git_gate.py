"""Claude 守衛的 git 關卡（#126）：目標 repo 要算對，沒啟用私人內容 hook 就擋。

用到的人名、色票全是虛構的（帳本 L-028）。bash 那一層（hook 輸入的 `.cwd`、退出碼）在
`.claude/hooks/test-guard-git.sh`；這裡驗解析與判斷本身，每一種繞法各一發。
"""

import subprocess
from pathlib import Path

import pytest

from evidence_forge.brand_guard import Fingerprints, term_fingerprints
from evidence_forge.git_gate import guard_command, parse_command, tokenize

FAKE_NAME = "Quillon Tavernash"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit_all(repo: Path) -> None:
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")


def _hook_dir(at: Path) -> Path:
    """在 `at/.githooks` 放三個可執行的 hook，回傳那個資料夾。"""
    hooks = at / ".githooks"
    hooks.mkdir(parents=True)
    for name in ("pre-commit", "commit-msg", "pre-push"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
    return hooks


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # 使用者的全域與系統 git 設定（可能也設了 core.hooksPath）不參與
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def hooked(tmp_path: Path) -> Path:
    """啟用了 .githooks（三個 hook 都在、可執行、跟 commit 進去的一樣）的 repo。"""
    repo = tmp_path / "hooked"
    _hook_dir(repo)
    _git(repo, "init", "-q")
    _commit_all(repo)
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    repo = tmp_path / "plain"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


FIXED = Fingerprints.fixed()


def _check(cmd: str, cwd: Path, fp: Fingerprints = FIXED) -> list[str]:
    return guard_command(cmd, str(cwd), None, fp)


# ── 切詞 ────────────────────────────────────────────────────────────────


def test_切詞_引號與反斜線照shell的規則() -> None:
    items, nested = tokenize("git -C 'a b' commit -m \"x \\\" y\" z\\ w && echo $(git status)")

    words = [v for k, v in items if k == "w"]
    assert words[:3] == ["git", "-C", "a b"]
    assert 'x " y' in words
    assert "z w" in words
    assert nested == []
    assert ("s", "&") in items


def test_切詞_雙引號裡的命令替換被挖出來() -> None:
    _, nested = tokenize('echo "$(git -C x commit) and `git push`"')

    assert nested == ["git -C x commit", "git push"]


@pytest.mark.parametrize("cmd", ["echo 'open", 'echo "open'])
def test_切詞_引號沒收好丟ValueError(cmd: str) -> None:
    with pytest.raises(ValueError):
        tokenize(cmd)


# ── 目標 repo 要算對 ────────────────────────────────────────────────────


def _targets(cmd: str, base: Path) -> list[tuple[str, Path | None]]:
    return [(i.name, i.dir) for i in parse_command(cmd, base)]


def test_目標_沒有任何改道就是起點(tmp_path: Path) -> None:
    assert _targets("git commit -m x", tmp_path) == [("git commit", tmp_path)]


def test_目標_C_相對路徑一路接下去(tmp_path: Path) -> None:
    assert _targets("git -C a -C b commit", tmp_path) == [("git commit", tmp_path / "a" / "b")]
    assert _targets("git -C /abs commit", tmp_path) == [("git commit", Path("/abs"))]


def test_目標_cd_之後的指令都在新的資料夾(tmp_path: Path) -> None:
    assert _targets("cd sub && git commit -m x; git push", tmp_path) == [
        ("git commit", tmp_path / "sub"),
        ("git push", tmp_path / "sub"),
    ]


def test_目標_子shell結束後回到原本的資料夾(tmp_path: Path) -> None:
    assert _targets("(cd sub && git commit); git push", tmp_path) == [
        ("git commit", tmp_path / "sub"),
        ("git push", tmp_path),
    ]


@pytest.mark.parametrize("cmd", ["cd $X && git commit", "git -C \"$(pwd)\" commit", "cd - && git commit", "cd a b && git commit"])
def test_目標_算不出來就是None(cmd: str, tmp_path: Path) -> None:
    assert _targets(cmd, tmp_path) == [("git commit", None)]


def test_目標_全域旗標與環境變數與包裝指令不擋住辨識(tmp_path: Path) -> None:
    (inv,) = parse_command("FOO=1 time git --no-pager -c user.name=x -p commit -m x", tmp_path)

    assert (inv.name, inv.dir) == ("git commit", tmp_path)
    assert "time" in inv.ambiguous
    assert inv.c_values == ["user.name=x"]
    assert inv.env == ["FOO=1"]
    assert [i.name for i in parse_command("/usr/bin/git push", tmp_path)] == ["git push"]


def test_目標_shell包的與eval與命令替換都挖出來(tmp_path: Path) -> None:
    for cmd in (
        "bash -c 'git -C a commit'",
        "sh -lc \"git -C a commit\"",
        "eval git -C a commit",
        "echo $(true; git -C a commit)",
        'echo "$(git -C a commit)"',
    ):
        assert ("git commit", tmp_path / "a") in _targets(cmd, tmp_path), cmd


def test_目標_git_dir與work_tree帶給後面的設定查詢(tmp_path: Path) -> None:
    (inv,) = parse_command("git --git-dir=g --work-tree w commit", tmp_path)

    assert inv.git_opts == [f"--git-dir={tmp_path / 'g'}", f"--work-tree={tmp_path / 'w'}"]


def test_目標_gh_pr_create_與_merge_認得_其他的gh不理(tmp_path: Path) -> None:
    assert [i.name for i in parse_command("gh -R o/r pr create --title x; gh pr merge 5; gh pr view 5; gh issue list", tmp_path)] == [
        "gh pr create",
        "gh pr merge",
    ]


def test_目標_git設定裡的alias也照意思算(plain: Path) -> None:
    _git(plain, "config", "alias.ci", "commit -v")
    _git(plain, "config", "alias.sh", "!git push -f")

    assert [i.name for i in parse_command("git ci -m x", plain)] == ["git commit"]
    assert [i.name for i in parse_command("git sh", plain)] == ["git push"]


def test_目標_巢狀太深看不清楚就丟ValueError(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_command("eval eval eval eval eval eval git commit", tmp_path)


# ── 判斷 ────────────────────────────────────────────────────────────────


def test_啟用了就放行(hooked: Path) -> None:
    for cmd in ("git commit -m x", "git push origin HEAD", "gh pr create --title t", "gh pr merge 5 --squash", "git status", "ls"):
        assert _check(cmd, hooked) == [], cmd


def test_沒啟用就擋_而且是用目標repo判_不是用起點(hooked: Path, plain: Path) -> None:
    for cmd in ("git commit -m x", "git push", "gh pr create", "gh pr merge 5 --squash"):
        assert any("core.hooksPath" in p for p in _check(cmd, plain)), cmd
    assert any("core.hooksPath" in p for p in _check(f"git -C {plain} commit -m x", hooked))
    assert any("core.hooksPath" in p for p in _check(f"cd {plain} && git push", hooked))
    assert _check(f"git -C {hooked} commit -m x", plain) == []
    assert _check(f"cd {hooked} && git commit -m x", plain) == []


def test_hooksPath指到別處或hook缺了也擋(tmp_path: Path, hooked: Path, plain: Path) -> None:
    _git(plain, "config", "core.hooksPath", "/dev/null")
    assert any(".githooks" in p for p in _check("git commit", plain))

    half = tmp_path / "half"
    (half / ".githooks").mkdir(parents=True)
    pre = half / ".githooks" / "pre-commit"
    pre.write_text("#!/bin/sh\n", encoding="utf-8")
    pre.chmod(0o755)
    _git(half, "init", "-q")
    _git(half, "config", "core.hooksPath", ".githooks")
    assert any("commit-msg" in p and "pre-push" in p for p in _check("git commit", half))

    (hooked / ".githooks" / "pre-push").chmod(0o644)  # 不可執行：git 不會跑它
    assert any("pre-push" in p for p in _check("git push", hooked))


def test_絕對路徑的hooksPath指到同一個_githooks也算啟用(hooked: Path) -> None:
    _git(hooked, "config", "core.hooksPath", str(hooked / ".githooks"))

    assert _check("git commit", hooked) == []


# ── 工作副本（#133）：開工作副本的工具會把共用設定改寫成主 checkout 的絕對路徑 ──────


@pytest.fixture
def main_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """主 checkout（.githooks 有進版控）與它開出來的一個工作副本；設定是兩邊共用的那一份。"""
    main = tmp_path / "main"
    _hook_dir(main)
    _git(main, "init", "-q", "-b", "main")
    _commit_all(main)
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    return main, wt


def test_工作副本_hooksPath是主checkout的絕對路徑_放行(main_and_worktree: tuple[Path, Path]) -> None:
    main, wt = main_and_worktree
    _git(main, "config", "core.hooksPath", str(main / ".githooks"))

    assert _check("git commit -m x", wt) == []
    assert _check("git push origin HEAD", wt) == []


def test_工作副本_hooksPath是別處的絕對路徑_擋_說明列出兩種可接受的值(
    main_and_worktree: tuple[Path, Path], tmp_path: Path
) -> None:
    main, wt = main_and_worktree
    elsewhere = _hook_dir(tmp_path / "elsewhere")  # hook 齊全也不行：位置不對
    _git(main, "config", "core.hooksPath", str(elsewhere))

    own, shared = str((wt / ".githooks").resolve()), str((main / ".githooks").resolve())
    problems = _check("git commit -m x", wt)
    assert any("core.hooksPath" in p and own in p and shared in p for p in problems), problems


def test_工作副本_相對路徑照舊放行(main_and_worktree: tuple[Path, Path]) -> None:
    main, wt = main_and_worktree
    _git(main, "config", "core.hooksPath", ".githooks")

    assert _check("git commit -m x", wt) == []


def test_工作副本_解析出來的資料夾缺hook就擋_看的是它不是自己的githooks(
    main_and_worktree: tuple[Path, Path],
) -> None:
    main, wt = main_and_worktree
    _git(main, "config", "core.hooksPath", str(main / ".githooks"))
    (main / ".githooks" / "pre-push").unlink()  # 工作副本自己那份還是齊的

    assert any("pre-push" in p for p in _check("git push origin HEAD", wt))


def test_工作副本_自己的githooks缺了_但解析到主checkout那份齊全_放行(
    main_and_worktree: tuple[Path, Path],
) -> None:
    main, wt = main_and_worktree
    _git(main, "config", "core.hooksPath", str(main / ".githooks"))
    (wt / ".githooks" / "commit-msg").chmod(0o644)  # 不會被跑到的那份，缺不缺不影響

    assert _check("git commit -m x", wt) == []


def test_工作副本_比對的是主checkout的HEAD_改主checkout那份擋_改自己那份不影響(
    main_and_worktree: tuple[Path, Path],
) -> None:
    main, wt = main_and_worktree
    _git(main, "config", "core.hooksPath", str(main / ".githooks"))
    (wt / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n# 工作副本自己的改動\n", encoding="utf-8")
    assert _check("git commit -m x", wt) == []

    (main / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n# 直接改主 checkout\n", encoding="utf-8")
    assert any("pre-commit" in p and "commit 進去的那一份不一樣" in p for p in _check("git commit -m x", wt))


def test_共用git資料夾不叫_git_上一層不算主checkout(tmp_path: Path) -> None:
    root = tmp_path / "sep"
    work = root / "work"
    _hook_dir(work)
    _hook_dir(root)  # 「共用 git 資料夾的上一層」那裡也放齊 hook：位置不對，齊了也不行
    _git(tmp_path, "init", "-q", f"--separate-git-dir={root / 'store.git'}", str(work))
    _commit_all(work)
    _git(work, "config", "core.hooksPath", str(root / ".githooks"))

    assert any("core.hooksPath" in p and "只接受" in p for p in _check("git commit -m x", work))
    _git(work, "config", "core.hooksPath", ".githooks")
    assert _check("git commit -m x", work) == []


# ── hook 本身要是 commit 進去的那一份 ───────────────────────────────────


def test_hook換成指到bin_true的符號連結就擋(hooked: Path) -> None:
    pre = hooked / ".githooks" / "pre-commit"
    pre.unlink()
    pre.symlink_to("/bin/true")

    assert any("pre-commit" in p and "符號連結" in p for p in _check("git commit -m x", hooked))


def test_hook內容被改過就擋(hooked: Path) -> None:
    (hooked / ".githooks" / "pre-push").write_text("#!/bin/sh\nexit 0  # 偷偷改掉\n", encoding="utf-8")

    problems = _check("git push origin HEAD", hooked)
    assert any("pre-push" in p and "commit 進去的那一份不一樣" in p for p in problems), problems


def test_hook沒commit進去也擋(tmp_path: Path) -> None:
    repo = tmp_path / "uncommitted"
    _hook_dir(repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.hooksPath", ".githooks")

    assert any("commit 進去的那一份不一樣" in p for p in _check("git commit -m x", repo))


def test_hook跟commit進去的一樣就放行(hooked: Path) -> None:
    assert _check("git commit -m x", hooked) == []
    assert _check("git push origin HEAD", hooked) == []


# ── 改道到別的 repo 的環境變數 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "GIT_DIR=/tmp/x/.git GIT_WORK_TREE=. git commit -m x",
        "GIT_INDEX_FILE=/tmp/other-index git commit -m x",
        "GIT_OBJECT_DIRECTORY=/tmp/objs git commit -m x",
        "GIT_COMMON_DIR=/tmp/x/.git git push origin HEAD",
        "env GIT_DIR=/tmp/x/.git git push origin HEAD",
        "export GIT_DIR=/tmp/x/.git; git commit -m x",
        "bash -c 'GIT_WORK_TREE=/tmp/x git commit -m x'",
    ],
)
def test_改道到別的repo的環境變數_commit或push就擋(hooked: Path, cmd: str) -> None:
    assert any("改道" in p for p in _check(cmd, hooked)), cmd


def test_改道環境變數_不是commit或push的指令不擋(hooked: Path) -> None:
    assert _check("GIT_DIR=/tmp/x/.git git status", hooked) == []
    assert _check("MY_GIT_DIR=/tmp/x git commit -m x", hooked) == []


def test_把hook關掉的寫法都擋(hooked: Path) -> None:
    blocked = [
        "git -c core.hooksPath=/dev/null commit -m x",
        "git -c core.hookspath=.githooks push",
        "git --config-env=core.hooksPath=HP commit",
        "git -c alias.ci=commit ci -m x",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=x git commit",
        "git config core.hooksPath /dev/null",
        "git config --unset core.hooksPath",
        "git push --no-verify",
        "git push -u origin HEAD --no-verify",
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git -C . commit -anm x",
        "git config --get core.hooksPath",  # 只讀也擋：關鍵字，不去分寫法
        "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null; git commit",
        "export GIT_CONFIG_COUNT=1; git commit -m x",
        "gh pr create --title 'mention --no-verify here'",
    ]
    for cmd in blocked:
        assert _check(cmd, hooked), cmd
    # 只讀、與跟 hook 無關的類似寫法放行
    for cmd in ("git config --list", "git -c user.name=x commit -m x", "git commit -m 'note -n'"):
        assert _check(cmd, hooked) == [], cmd


@pytest.mark.parametrize(
    "launcher",
    ["xargs", "sudo", "nohup", "command", "exec", "time", "nice -n 5", "timeout 5", "stdbuf -oL", "env -i", "env -u FOO"],
)
def test_沒完整解析的包裝指令後面接commit或push就擋_即使目標有啟用(hooked: Path, launcher: str) -> None:
    for tail in ("git commit -m x", "git push origin HEAD", "gh pr create --title t"):
        problems = _check(f"{launcher} {tail}", hooked)
        assert any("沒完整解析" in p or "沒解析" in p for p in problems), (launcher, tail, problems)
    # 不可能是 commit／push 的不擋
    assert _check(f"{launcher} git status", hooked) == []


def test_子shell括號裡的commit擋_但括號裡的唯讀指令不擋(hooked: Path) -> None:
    assert any("子 shell" in p for p in _check("(git commit -m x)", hooked))
    assert any("子 shell" in p for p in _check("true && (cd . && git push)", hooked))
    assert _check("(git status)", hooked) == []


def test_env_VAR等於值與_C_有解析_照目標repo判(hooked: Path, plain: Path) -> None:
    # 常見寫法：解析得清楚，目標有啟用就放行、沒啟用就擋
    assert _check(f"env FOO=1 git -C {hooked} commit -m x", plain) == []
    assert _check(f"env -C {hooked} git commit -m x", plain) == []
    assert _check(f"env --chdir={hooked} FOO=1 git push", plain) == []
    assert any("core.hooksPath" in p for p in _check(f"env -C {plain} git commit -m x", hooked))
    assert any("core.hooksPath" in p for p in _check(f"env --chdir {plain} git push", hooked))
    # env 的其他選項解析不了：擋
    assert any("env" in p for p in _check(f"env -i git -C {hooked} commit -m x", plain))


def test_算不出目標或資料夾不存在或不是repo都擋(hooked: Path, tmp_path: Path) -> None:
    assert any("算不出" in p for p in _check("cd $X && git commit", hooked))
    assert any("不存在" in p for p in _check(f"git -C {tmp_path / 'nope'} commit", hooked))
    assert any("git repo" in p for p in _check(f"git -C {tmp_path} commit", hooked))


def test_指令文字裡的私人內容_訊息_PR內文_訊息檔(hooked: Path, tmp_path: Path) -> None:
    fp = Fingerprints.fixed()
    fp.words |= term_fingerprints(FAKE_NAME, split_words=True).words
    msg = tmp_path / "msg.txt"
    msg.write_text(f"fine\n{FAKE_NAME}\n", encoding="utf-8")

    for cmd, where in (
        (f"git commit -m '{FAKE_NAME} did it'", "第 1 行"),
        (f"gh pr create --title t --body '{FAKE_NAME}'", "第 1 行"),
        (f"git commit -F {msg}", "第 2 行"),
        (f"gh pr create --body-file {msg}", "第 2 行"),
    ):
        problems = _check(cmd, hooked, fp)
        assert any(where in p for p in problems), cmd
        assert not any("quillon" in p.lower() or "tavernash" in p.lower() for p in problems), cmd
    # heredoc 裡的 message：用沒去掉 heredoc 的原始指令掃
    raw = f"git commit -m \"$(cat <<'EOF'\nsubject\n{FAKE_NAME}\nEOF\n)\""
    stripped = "git commit -m \"$(cat <<'EOF'\nEOF\n)\""
    assert any("第 3 行" in p for p in guard_command(stripped, str(hooked), raw, fp))
    # 沒有 commit／PR 就不掃文字
    assert _check(f"echo '{FAKE_NAME}'", hooked, fp) == []


def test_指令解析壞掉丟ValueError_由呼叫端當成掃不了(hooked: Path) -> None:
    with pytest.raises(ValueError):
        _check("git commit -m 'unclosed", hooked)
