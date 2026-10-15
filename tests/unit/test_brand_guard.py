"""私人內容守門（#126）：commit 與 push 之前掃指紋。

這個檔裡的人名、色票、用語全部是**虛構**的，每個測試自己寫進暫存的 `EF_DATA`：
測試資料不准用真人的名字或真實的品牌內容（帳本 L-028）。
"""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from evidence_forge import brand_guard
from evidence_forge.brand_guard import (
    FINGERPRINTS,
    MASK_PATH,
    ROOT,
    Fingerprints,
    GuardError,
    format_hits,
    digest_words,
    hits_in_text,
    load_fingerprints,
    parse_diff,
    resolve_data_dir,
    run,
    scan_diff,
    scan_file_text,
)

# 虛構的角色：假名字、假筆名、假色票、假封面預設名稱、假句子
FAKE_NAME = "Quillon Tavernash"
FAKE_BRAND = "Marbled Fox Atelier"
FAKE_CJK = "岑嵐瑞"
FAKE_COLOR = "#13579B"
FAKE_PRESET = "velvet-comet"
FAKE_PHRASE = "Paint every subject as though lit by a single borrowed lantern"

VOICE = f"""---
id: quillon
name: {FAKE_NAME}
default_genre: explainer
---

## 語氣
冷靜。
"""

BRAND = f"""name: {FAKE_BRAND}
palette:
  paper: "{FAKE_COLOR}"
  ink: "#222222"
cover:
  shared_rules: "{FAKE_PHRASE}, and keep the margins quiet."
  presets:
    {FAKE_PRESET}:
      form: painting
"""


def _persona(data: Path, folder: str = "quillon") -> Path:
    persona = data / "authors" / folder
    persona.mkdir(parents=True)
    (persona / "voice.md").write_text(VOICE, encoding="utf-8")
    (persona / "brand.yaml").write_text(BRAND, encoding="utf-8")
    (persona / "persona.json").write_text(f'{{"display_name": "{FAKE_CJK}"}}', encoding="utf-8")
    return persona


@pytest.fixture
def data(tmp_path: Path) -> Path:
    path = tmp_path / "ef_data"
    _persona(path)
    return path


@pytest.fixture
def fp(data: Path) -> Fingerprints:
    return load_fingerprints({"EF_DATA": str(data)})


def _lines(text: str, fingerprints: Fingerprints) -> set[int]:
    return scan_file_text(text, fingerprints)


# ── 指紋從 EF_DATA 現算，抓得到假名字與假色票 ───────────────────────────


def test_抓得到假名字_整個名字與只用一半_大小寫標點都不影響(fp: Fingerprints) -> None:
    assert _lines("x = 'Quillon Tavernash'", fp) == {1}
    assert _lines("persona = 'QUILLON'", fp) == {1}  # 只用了名字的一半，真名事件就是這樣
    assert _lines("who: tavernash.", fp) == {1}
    assert _lines("quillon-tavernash", fp) == {1}


def test_抓得到假色票與假品牌名與假封面預設名稱(fp: Fingerprints) -> None:
    assert _lines('accent = "#13579b"', fp) == {1}
    assert _lines("brand = 'marbled fox atelier'", fp) == {1}
    assert _lines("preset = 'Velvet Comet'", fp) == {1}


def test_抓得到假句子的前八個詞(fp: Fingerprints) -> None:
    assert _lines("Paint every subject as though lit by a single lantern", fp) == {1}
    assert _lines("Paint every subject as\nthough lit by a", fp) == {1}  # 跨行


def test_抓得到中文名字_顯示名稱_中間夾標點也算(fp: Fingerprints) -> None:
    assert _lines("作者：岑嵐瑞", fp) == {1}
    assert _lines("岑，嵐 瑞", fp) == {1}
    assert _lines("叫我嵐瑞就好", fp) == set()  # 顯示名稱不套「只用名」的規則


def test_只用名不用姓_只套在voice的名字_品牌名不套(tmp_path: Path) -> None:
    folder = tmp_path / "ef" / "authors" / "plain"
    folder.mkdir(parents=True)
    (folder / "voice.md").write_text("---\nname: 宋硯川\n---\n", encoding="utf-8")
    (folder / "brand.yaml").write_text("name: 澄光書坊\n", encoding="utf-8")
    fingerprints = load_fingerprints({"EF_DATA": str(tmp_path / "ef")})

    assert _lines("宋硯川", fingerprints) == {1}
    assert _lines("叫我硯川就好", fingerprints) == {1}  # voice 的名字：後兩個字（名）也算
    assert _lines("澄光書坊", fingerprints) == {1}
    assert _lines("一間書坊", fingerprints) == set()  # 品牌名的後兩個字是一般的詞，不算


def test_全形英文也抓得到(fp: Fingerprints) -> None:
    assert _lines("Ｑｕｉｌｌｏｎ", fp) == {1}


def test_不誤擋_太短太常見的詞與無關的字(data: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    persona = other / "authors" / "me"
    persona.mkdir(parents=True)
    (persona / "voice.md").write_text("---\nid: me\nname: Me\n---\n", encoding="utf-8")
    plain = load_fingerprints({"EF_DATA": str(other)})

    assert len(plain) == len(Fingerprints.fixed())  # id: me 一組都沒加
    assert _lines("give me the default test sample", plain) == set()
    assert _lines("quillon", load_fingerprints({"EF_DATA": str(data)})) == {1}


def test_範本預設值與公開預設色票不算私人內容(tmp_path: Path) -> None:
    # 角色資料夾直接複製範本：名字還是範本的佔位字、色票是預設值
    folder = tmp_path / "ef" / "authors" / "copied"
    folder.mkdir(parents=True)
    template = (ROOT / "harness/templates/author-voice.md").read_text(encoding="utf-8")
    (folder / "voice.md").write_text(template, encoding="utf-8")
    default_brand = (ROOT / "src/evidence_forge/figures/brand.py").read_text(encoding="utf-8")
    (folder / "brand.yaml").write_text("palette:\n  paper: '#F5F5F5'\n  accent: '#2F6DB5'\n", encoding="utf-8")

    fingerprints = load_fingerprints({"EF_DATA": str(tmp_path / "ef")})

    assert hits_in_text(template, fingerprints) == hits_in_text(template, Fingerprints.fixed())
    assert hits_in_text(default_brand, fingerprints) == hits_in_text(default_brand, Fingerprints.fixed())


def test_隱藏資料夾不當角色(data: Path) -> None:
    backup = data / "authors" / ".backup" / "old"
    backup.mkdir(parents=True)
    (backup / "voice.md").write_text("---\nname: Zephyrine Okonkwo\n---\n", encoding="utf-8")

    assert _lines("Zephyrine", load_fingerprints({"EF_DATA": str(data)})) == set()


# ── 沒有 EF_DATA：固定指紋照跑 ──────────────────────────────────────────


def test_沒有EF_DATA時只用固定指紋(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    fingerprints = load_fingerprints({"EF_DATA": str(empty)})
    assert fingerprints.words == set(FINGERPRINTS)
    assert fingerprints.runtime_terms == 0
    # 沒設 EF_DATA、repo 底下也沒有 data/：同樣
    assert load_fingerprints({}, root=tmp_path).words == set(FINGERPRINTS)


def test_EF_DATA設了卻讀不到_是掃不了_不是退回固定指紋(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(GuardError):
        load_fingerprints({"EF_DATA": str(tmp_path / "missing")})
    monkeypatch.setenv("EF_DATA", str(tmp_path / "missing"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert run(["diff"]) == 2
    assert "先擋下" in capsys.readouterr().err


def test_repo裡只有高熵的長句子指紋_色碼與名字只在執行期現算() -> None:
    # 色碼只有 2^24 種、名字是一個詞：它們的 sha256 等於明文，不能進 repo
    assert {n for n, _ in FINGERPRINTS} == {8}


def test_固定指紋真的在掃(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 不能把真的品牌內容寫進測試，所以換一組假的固定指紋，驗「沒有 EF_DATA 時這條路是通的」
    monkeypatch.setattr(brand_guard, "FINGERPRINTS", ((1, digest_words(["glorbnax"])),))
    monkeypatch.setenv("EF_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO("diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n@@ -0,0 +1 @@\n+x = 'Glorbnax'\n"))

    assert run(["diff"]) == 1
    err = capsys.readouterr().err
    assert "src/a.py:1" in err
    assert "glorbnax" not in err.lower()
    assert "只有 repo 裡的" in err  # 說清楚這次沒有現算的那一部分


def test_真的固定指紋一組都不少() -> None:
    assert len(Fingerprints.fixed()) == len(FINGERPRINTS) > 0


def test_worktree裡找不到data時去主工作目錄找(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "commit", "-q", "--allow-empty", "-m", "init")
    _persona(main / "data")
    tree = tmp_path / "tree"
    _git(main, "worktree", "add", "-q", str(tree), "-b", "side")

    assert resolve_data_dir({}, root=tree) == (main / "data").resolve()
    with pytest.raises(GuardError):  # 明說了就照明說的，讀不到就是掃不了
        resolve_data_dir({"EF_DATA": str(tmp_path / "nope")}, root=tree)


# ── 輸出不含命中的字 ────────────────────────────────────────────────────


def test_輸出只有檔名與行號_不含命中的字(
    data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = (
        "diff --git a/tests/unit/test_x.py b/tests/unit/test_x.py\n"
        "--- a/tests/unit/test_x.py\n"
        "+++ b/tests/unit/test_x.py\n"
        "@@ -10,2 +10,4 @@ def f():\n"
        "     keep = 1\n"
        f"+    name = '{FAKE_NAME}'\n"
        f"+    color = '{FAKE_COLOR}'\n"
        f"+    who = '{FAKE_CJK}'\n"
        "     done = 2\n"
    )
    monkeypatch.setenv("EF_DATA", str(data))
    monkeypatch.setattr(sys, "stdin", io.StringIO(diff))

    assert run(["diff"]) == 1

    err = capsys.readouterr().err
    assert "tests/unit/test_x.py:11, 12, 13" in err
    for secret in (*FAKE_NAME.split(), *FAKE_BRAND.split(), FAKE_CJK, FAKE_COLOR[1:], FAKE_PRESET):
        assert secret.lower() not in err.lower()
    assert capsys.readouterr().out == ""


def test_乾淨的diff_exit0_而且不出聲(
    data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EF_DATA", str(data))
    monkeypatch.setattr(sys, "stdin", io.StringIO("diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"))

    assert run(["diff"]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


# ── diff 解析 ───────────────────────────────────────────────────────────


def test_只算新增行_舊的內容與上下文裡的不算(fp: Fingerprints) -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -5,3 +5,3 @@\n"
        f" # {FAKE_NAME} 已經在上下文裡\n"
        f"-old = '{FAKE_NAME}'\n"
        "+new = 'clean'\n"
        " tail\n"
    )

    assert scan_diff(diff, fp) == {}


def _added(path: str, text: str) -> str:
    return f"diff --git a/{path} b/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+{text}\n"


@pytest.mark.parametrize("tree", ["docs", "Ai-coding-principle", ".claude/skills/x"])
def test_文件底下也掃_只有公開的品牌名放行(fp: Fingerprints, tree: str) -> None:
    path = f"{tree}/x.md"

    assert scan_diff(_added(path, FAKE_BRAND), fp) == {}  # 品牌名：文件本來就會提到
    assert scan_diff(_added(path, FAKE_NAME), fp) == {("", path): {1}}  # 真名、色票、句子：照擋
    assert scan_diff(_added(path, FAKE_COLOR), fp) == {("", path): {1}}
    assert scan_diff(_added(path, FAKE_PHRASE), fp) == {("", path): {1}}
    # 同一個品牌名在程式與測試裡照擋
    assert scan_diff(_added("src/x.py", FAKE_BRAND), fp) == {("", "src/x.py"): {1}}


def test_路徑本身命中_不印路徑_只印commit與遮蔽標籤(fp: Fingerprints) -> None:
    sha = "c" * 40
    leaky = f"data/{FAKE_NAME.split()[0].lower()}/notes.txt"
    diff = (
        f"commit {sha}\n\n"
        f"diff --git a/{leaky} b/{leaky}\nnew file mode 100644\n"  # 沒有內容的新檔：只有標頭看得到路徑
        "diff --git a/old.txt b/data/ok.txt\nsimilarity index 100%\n"
        f"rename from old.txt\nrename to data/{FAKE_NAME.split()[1].lower()}.txt\n"
        f"diff --git a/{leaky}2 b/{leaky}2\n+++ b/{leaky}2\n@@ -0,0 +1 @@\n+{FAKE_NAME}\n"
    )

    hits = scan_diff(diff, fp)

    assert set(hits) == {("cccccccc", MASK_PATH)}
    assert hits[("cccccccc", MASK_PATH)] == {1}  # 內容那一行號碼也在，但路徑已遮蔽
    text = format_hits(hits, fp)
    assert "notes.txt" not in text and "data/" not in text
    assert "（路徑已遮蔽）" in text


def test_詞窗跨過新增行與上下文行也算(fp: Fingerprints) -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1,2 @@\n"
        " # Quillon\n"
        "+# Tavernash\n"
    )

    assert scan_diff(diff, fp) == {("", "src/a.py"): {2}}


def test_log_的commit標頭變成標籤_說明文字不被當成內容(fp: Fingerprints) -> None:
    sha = "a" * 40
    diff = (
        f"commit {sha}\n"
        "\n"
        f"    msg mentions {FAKE_NAME}\n"
        "\n"
        "diff --git a/src/a.py b/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        f"+b = '{FAKE_NAME}'\n"
        f"commit {'b' * 40}\n"
        "\n"
        "diff --git a/src/b.py b/src/b.py\n"
        "+++ b/src/b.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )

    assert scan_diff(diff, fp) == {("aaaaaaaa", "src/a.py"): {1}}
    assert [(h.commit, h.path) for h in parse_diff(diff) if not h.header] == [("aaaaaaaa", "src/a.py"), ("bbbbbbbb", "src/b.py")]


def test_沒有行數的單行hunk與刪除檔不會壞掉(fp: Fingerprints) -> None:
    diff = (
        "diff --git a/src/gone.py b/src/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-a\n"
        f"-{FAKE_NAME}\n"
        "diff --git a/src/one.py b/src/one.py\n"
        "+++ b/src/one.py\n"
        "@@ -3 +3 @@\n"
        "-a\n"
        f"+{FAKE_NAME}\n"
        "\\ No newline at end of file\n"
    )

    assert scan_diff(diff, fp) == {("", "src/one.py"): {3}}


# ── 真的 git：staged、worktree、push ────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "README.txt").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.txt")
    _git(path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(path)
    monkeypatch.setenv("EF_DATA", str(data))
    return path


def _write(repo: Path, name: str, text: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_staged_擋下暫存區裡的假名字(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "tests/test_a.py", f"name = '{FAKE_NAME}'\n")
    _git(repo, "add", "tests/test_a.py")

    assert run(["staged"]) == 1
    err = capsys.readouterr().err
    assert "tests/test_a.py:1" in err
    assert "quillon" not in err.lower()


def test_staged_乾淨放行_沒暫存的髒東西不管(repo: Path) -> None:
    _write(repo, "tests/ok.py", "x = 1\n")
    _git(repo, "add", "tests/ok.py")
    _write(repo, "tests/unstaged.py", f"name = '{FAKE_NAME}'\n")  # 沒 add，也不在這次 commit 裡

    assert run(["staged"]) == 0


def test_staged_檔名有假名字_路徑不印出來(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "data/quillon-notes.txt", "nothing in it\n")
    _git(repo, "add", "data/quillon-notes.txt")

    assert run(["staged"]) == 1
    err = capsys.readouterr().err
    assert "（路徑已遮蔽）" in err
    assert "quillon" not in err.lower()


def test_message_檔_註解行不掃_訊息有假名字就擋(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = tmp_path / "ok.txt"
    ok.write_text(f"subject\n\n# git 加的說明 {FAKE_NAME}\nbody\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text(f"subject\n\nbody mentions {FAKE_BRAND}\n", encoding="utf-8")

    assert run(["message", str(ok)]) == 0
    assert run(["message", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "（commit message）:3" in err
    assert "marbled" not in err.lower()
    assert run(["message", str(tmp_path / "missing.txt")]) == 2


def test_push_commit_message裡的假名字也擋_只印commit與行號(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _remote(repo, tmp_path)
    _write(repo, "src/ok.py", "x = 1\n")
    _git(repo, "add", "src/ok.py")
    _git(repo, "commit", "-q", "-m", f"fix the thing\n\nfor {FAKE_NAME}")
    sha = _git(repo, "rev-parse", "--short=8", "HEAD").strip()

    assert run(["push", "--", "origin HEAD"]) == 1
    err = capsys.readouterr().err
    assert f"{sha} （commit message）:3" in err
    assert "tavernash" not in err.lower()


def test_push_merge_commit裡解衝突寫進去的假名字也擋(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _remote(repo, tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    _write(repo, "m.txt", "side\n")
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-q", "-m", "side")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "m.txt", "main\n")
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-q", "-m", "main")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "side"], capture_output=True, check=False)
    _write(repo, "m.txt", f"resolved by {FAKE_NAME}\n")  # 衝突解掉的內容只存在 merge commit 裡
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-q", "-m", "merge side")
    merge = _git(repo, "rev-parse", "--short=8", "HEAD").strip()

    assert run(["push", "--", "origin HEAD"]) == 1
    assert f"{merge} m.txt:1" in capsys.readouterr().err


def _remote(repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "HEAD")


def test_push_擋下還沒推出去的commit裡的假名字_後面補一個刪掉的commit也沒用(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _remote(repo, tmp_path)
    _write(repo, "tests/test_a.py", f"name = '{FAKE_NAME}'\n")
    _git(repo, "add", "tests/test_a.py")
    _git(repo, "commit", "-q", "-m", "add")
    leak = _git(repo, "rev-parse", "--short=8", "HEAD").strip()
    _write(repo, "tests/test_a.py", "name = 'clean'\n")
    _git(repo, "commit", "-q", "-am", "fix")  # 歷史上還是有

    assert run(["push", "--", "origin HEAD"]) == 1
    err = capsys.readouterr().err
    assert f"{leak} tests/test_a.py:1" in err
    assert "quillon" not in err.lower()


def test_push_乾淨的或已經在遠端的不擋(repo: Path, tmp_path: Path) -> None:
    _remote(repo, tmp_path)
    assert run(["push", "--", "origin HEAD"]) == 0  # 沒有新 commit

    _write(repo, "tests/ok.py", "x = 1\n")
    _git(repo, "add", "tests/ok.py")
    _git(repo, "commit", "-q", "-m", "ok")
    assert run(["push", "--", "-u origin HEAD"]) == 0
    assert run(["push", "--", ""]) == 0  # 沒寫遠端與 refspec：目前的分支


def test_push_指定其他分支與_all(repo: Path, tmp_path: Path) -> None:
    _remote(repo, tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    _write(repo, "src/leak.py", f"x = '{FAKE_NAME}'\n")
    _git(repo, "add", "src/leak.py")
    _git(repo, "commit", "-q", "-m", "leak")
    _git(repo, "checkout", "-q", "main")

    assert run(["push", "--", "origin HEAD"]) == 0  # main 本身是乾淨的
    assert run(["push", "--", "origin side"]) == 1
    assert run(["push", "--", "origin side:refs/heads/other"]) == 1
    assert run(["push", "--", "--all origin"]) == 1
    assert run(["push", "--", "origin :side"]) == 0  # 刪遠端分支：沒有新內容
    assert run(["push", "--", "--delete origin side"]) == 0


def test_push_解不開的來源是掃不了_不是略過(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _remote(repo, tmp_path)

    assert run(["push", "--", "origin no-such-branch"]) == 2
    assert "先擋下" in capsys.readouterr().err
    assert run(["push", "--", "origin HEAD no-such-branch"]) == 2


def test_push_tags(repo: Path, tmp_path: Path) -> None:
    _remote(repo, tmp_path)
    _write(repo, "src/leak.py", f"x = '{FAKE_NAME}'\n")
    _git(repo, "add", "src/leak.py")
    _git(repo, "commit", "-q", "-m", "leak")
    _git(repo, "tag", "v1")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")  # 分支乾淨了，但 tag 還指著洩漏的 commit

    assert run(["push", "--", "origin HEAD"]) == 0
    assert run(["push", "--", "--tags origin"]) == 1


def test_pre_push_原生hook的標準輸入(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _remote(repo, tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "src/leak.py", f"x = '{FAKE_NAME}'\n")
    _git(repo, "add", "src/leak.py")
    _git(repo, "commit", "-q", "-m", "leak")
    head = _git(repo, "rev-parse", "HEAD").strip()
    zeros = "0" * 40

    for remote_sha in (base, zeros):  # 遠端已有舊的，與遠端還沒有這個分支
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {remote_sha}\n"))
        assert run(["pre-push", "origin", "unused"]) == 1
        assert "src/leak.py:1" in capsys.readouterr().err

    monkeypatch.setattr(sys, "stdin", io.StringIO(f"(delete) {zeros} refs/heads/old {base}\n"))
    assert run(["pre-push", "origin"]) == 0


def test_掃不了的時候exit2_不是靜靜放行(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # 不是 git repo
    monkeypatch.setenv("EF_DATA", str(tmp_path))

    assert run(["staged"]) == 2
    assert "先擋下" in capsys.readouterr().err


def test_參數壞掉也是掃不了_不是traceback(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["push", "--", "origin 'unclosed"]) == 2
    assert "先擋下" in capsys.readouterr().err


def test_到處都有的色碼不收(tmp_path: Path) -> None:
    persona = tmp_path / "ef" / "authors" / "plain"
    persona.mkdir(parents=True)
    (persona / "brand.yaml").write_text('paper: "#FFFFFF"\nink: "#000000"\naccent: "#13579B"\n', encoding="utf-8")

    fingerprints = load_fingerprints({"EF_DATA": str(tmp_path / "ef")})

    assert _lines("color: #ffffff; background: #000000", fingerprints) == set()
    assert _lines("color: #13579b", fingerprints) == {1}


def test_指令列入口跑得起來_不需要uv環境(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "brand_guard.py"), "stats"],
        env={"EF_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert proc.returncode == 0
    assert "指紋" in proc.stderr


def _zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)


def test_imports的zip也算來源_色碼與每行的開頭(tmp_path: Path) -> None:
    data = tmp_path / "ef"
    (data / "imports").mkdir(parents=True)
    _zip(
        data / "imports" / "system.zip",
        {
            ".claude/skills/x/scripts/chart_style.py": f'PAPER = "{FAKE_COLOR}"\n',
            ".claude/skills/x/references/craft/open.md": "Open every piece with one borrowed lantern and a quiet question\n",
            ".claude/skills/x/references/craft/zh.md": "每一篇都從一個只有作者看得見的小場景開始寫起然後慢慢帶出主張\n",
            ".claude/skills/x/voice.md": "---\nname: Zephyr Quillborne\n---\n",
            "notes.bin": "ignored",
        },
    )
    fingerprints = load_fingerprints({"EF_DATA": str(data)})

    assert fingerprints.source_files == 4
    assert _lines('color = "#13579b"', fingerprints) == {1}
    assert _lines("Open every piece with one borrowed lantern and", fingerprints) == {1}
    assert _lines("每一篇都從一個只有作者看得見的小場景開始", fingerprints) == {1}
    assert _lines("Quillborne", fingerprints) == {1}
    assert _lines("a perfectly ordinary sentence about nothing at all here", fingerprints) == set()


def test_imports裡讀不開的zip是掃不了(tmp_path: Path) -> None:
    data = tmp_path / "ef"
    (data / "imports").mkdir(parents=True)
    (data / "imports" / "broken.zip").write_bytes(b"not a zip")

    with pytest.raises(GuardError):
        load_fingerprints({"EF_DATA": str(data)})
