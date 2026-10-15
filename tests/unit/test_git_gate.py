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


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # 使用者的全域與系統 git 設定（可能也設了 core.hooksPath）不參與
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def hooked(tmp_path: Path) -> Path:
    """啟用了 .githooks（三個 hook 都在、可執行）的 repo。"""
    repo = tmp_path / "hooked"
    (repo / ".githooks").mkdir(parents=True)
    for name in ("pre-commit", "commit-msg", "pre-push"):
        hook = repo / ".githooks" / name
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
    _git(repo, "init", "-q")
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
    ]
    for cmd in blocked:
        assert _check(cmd, hooked), cmd
    # 只讀、與跟 hook 無關的類似寫法放行
    for cmd in ("git config --get core.hooksPath", "git config --list", "git -c user.name=x commit -m x", "git commit -m 'note -n'"):
        assert _check(cmd, hooked) == [], cmd


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
