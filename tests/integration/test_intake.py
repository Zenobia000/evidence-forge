"""SEAM-09：手動投入 → 先抓取與抽文字 → `inbox/manual/<項目>/` → 開單（FR-IN-01）。

網址一律走假的 DNS 與本機假網站（`fake_web`），不連外網。
"""

import json
import multiprocessing
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from evidence_forge.documents import read_job
from evidence_forge.extract import ExtractError
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.ledger_models import IntakeEntry
from evidence_forge.models import Item, Recipe, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.write_once import write_once
from evidence_forge.runner.intake import (
    Intake,
    PastedText,
    Source,
    SourceUnreadable,
    UploadedFile,
    WebPage,
    open_job,
)

if TYPE_CHECKING:
    from integration.conftest import FakeWeb

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PDF = Path(__file__).parents[1] / "fixtures" / "pdf"
DEFAULT_GOAL = "用兩三句話說明這篇文章在講什麼"
RECIPE = Recipe(
    name="echo",
    description="test",
    stages=(RecipeStage(id="echo", skill="echo", tier="local", output=("a.json",)),),
    on_feedback="echo",
    default_goal=DEFAULT_GOAL,
)
PAGE = (
    "<html><head><title>Agents in production</title>"
    '<meta name="author" content="Jane Doe"></head>'
    "<body><nav>選單</nav><article><h1>Agents</h1><p>最難的是可觀測性。</p></article></body></html>"
)


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    return root


@pytest.fixture
def fetcher(data: DataDir, fake_web: "FakeWeb") -> Fetcher:
    return Fetcher(data, resolver=fake_web.resolver, dial=fake_web.dial)


def _submit(
    data: DataDir,
    source: Source,
    goal: str | None = "給 PM 看的觀點文",
    fetcher: Fetcher | None = None,
    recipe: Recipe = RECIPE,
) -> Intake:
    return open_job(data, recipe, source, goal=goal, now=NOW, fetcher=fetcher)


def _items(data: DataDir) -> list[Path]:
    manual = data.inbox / "manual"
    return sorted(manual.iterdir()) if manual.is_dir() else []


def _jobs(data: DataDir) -> list[Path]:
    return sorted(data.jobs.iterdir())


def _item(data: DataDir, intake: Intake) -> Item:
    return Item.model_validate_json(
        (data.root / intake.job.feed.item / "item.json").read_bytes()
    )


def _intake_lines(data: DataDir) -> list[dict[str, object]]:
    path = data.root / "ledger" / "intake.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- 三種投入各開一張單 ---------------------------------------------------------


@pytest.mark.promise("FR-IN-01")
def test_貼文字_寫素材項目並開一張排隊中的工單(data: DataDir) -> None:
    intake = _submit(data, PastedText("# 標題\n\n內文"))

    assert intake.created
    job = read_job(data.job_dir(intake.job.id) / "job.md").job
    assert (job.status, job.goal, job.recipe) == ("queued", "給 PM 看的觀點文", "echo")
    item_dir = data.root / job.feed.item
    assert item_dir.parent == data.inbox / "manual"
    assert (item_dir / "content.md").read_text(encoding="utf-8") == "# 標題\n\n內文"
    assert _item(data, intake).title == "標題"


@pytest.mark.promise("FR-IN-01")
@pytest.mark.parametrize(
    ("name", "payload", "content"),
    [
        ("a.md", "# A\n\n內文 A".encode(), "# A\n\n內文 A"),
        ("b.txt", "﻿純文字 B".encode(), "純文字 B"),
        (
            "c.pdf",
            (PDF / "article.pdf").read_bytes(),
            "Agents in production\nThe hardest part after launch is observability.",
        ),
    ],
)
def test_上傳_md_txt_pdf_各開一張_原檔留在_raw(
    data: DataDir, name: str, payload: bytes, content: str
) -> None:
    intake = _submit(data, UploadedFile(name, payload))

    item_dir = data.root / intake.job.feed.item
    assert (item_dir / "content.md").read_text(encoding="utf-8") == content
    assert (item_dir / "raw" / name).read_bytes() == payload
    assert (data.job_dir(intake.job.id) / "input" / "content.md").read_text(
        encoding="utf-8"
    ) == content


@pytest.mark.promise("FR-IN-01")
def test_不支援的檔案格式_報錯_不建素材也不開單(data: DataDir) -> None:
    with pytest.raises(ValueError, match="docx"):
        _submit(data, UploadedFile("a.docx", b"PK\x03\x04"))
    assert (_items(data), _jobs(data)) == ([], [])


@pytest.mark.promise("FR-IN-01")
def test_抽不到文字的_PDF_明確報錯_不開單(data: DataDir) -> None:
    with pytest.raises(ExtractError, match="抽不到文字"):
        _submit(data, UploadedFile("scan.pdf", (PDF / "scanned.pdf").read_bytes()))
    assert (_items(data), _jobs(data)) == ([], [])


@pytest.mark.promise("FR-IN-01", "FR-LG-01", "NFR-OBS-01")
def test_貼網址_先抓再開單_帳記在_intake_jsonl_不記進工單帳本(
    data: DataDir, fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", body=PAGE)
    intake = _submit(data, WebPage("http://news.example/p/1"), fetcher=fetcher)

    item = _item(data, intake)
    assert (item.url, item.title, item.author) == (
        "http://news.example/p/1",
        "Agents in production",
        "Jane Doe",
    )
    item_dir = data.root / intake.job.feed.item
    assert (item_dir / "content.md").read_text(encoding="utf-8") == (
        "# Agents\n\n最難的是可觀測性。"
    )
    assert (item_dir / "raw" / "page.html").read_text(encoding="utf-8") == PAGE

    [line] = _intake_lines(data)
    entry = IntakeEntry.model_validate(line)
    assert (entry.kind, entry.status, entry.url) == (
        "fetch",
        "ok",
        "http://news.example/p/1",
    )
    assert entry.response_hash is not None
    assert not (data.job_dir(intake.job.id) / "ledger.jsonl").exists()


# --- 讀不到：不開單、標讀不到、寫原因 -------------------------------------------


@pytest.mark.promise("FR-IN-01", "NFR-RUN-08", "NFR-SEC-03")
@pytest.mark.parametrize(
    ("path", "reason"),
    [("/forbidden", "403"), ("/paid", "付費牆"), ("/private/x", "robots")],
)
def test_網址讀不到_不開單_素材項目標讀不到並寫原因(
    data: DataDir, fetcher: Fetcher, fake_web: "FakeWeb", path: str, reason: str
) -> None:
    fake_web.route(
        "news.example",
        "/robots.txt",
        body="User-agent: *\nDisallow: /private/\n",
        headers={"Content-Type": "text/plain"},
    )
    fake_web.route("news.example", "/forbidden", 403)
    fake_web.route(
        "news.example",
        "/paid",
        body='<script type="application/ld+json">{"isAccessibleForFree":"False"}</script>',
    )
    url = f"http://news.example{path}"
    with pytest.raises(SourceUnreadable, match=reason) as raised:
        _submit(data, WebPage(url), fetcher=fetcher)

    assert _jobs(data) == []
    [item_dir] = _items(data)
    item = Item.model_validate_json((item_dir / "item.json").read_bytes())
    assert (item.url, item.unavailable, item.content_hash) == (url, True, None)
    assert reason in item.unavailable_reason
    assert raised.value.reason == item.unavailable_reason
    assert [line["status"] for line in _intake_lines(data)] == ["error"]


@pytest.mark.promise("NFR-SEC-03")
def test_內網網址_不開單_連線都沒發生(
    data: DataDir, fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    with pytest.raises(SourceUnreadable, match="內網"):
        _submit(
            data, WebPage(f"http://127.0.0.1:{fake_web.port}/bills"), fetcher=fetcher
        )
    assert fake_web.requests == []
    assert _jobs(data) == []


# --- 同一份素材、同樣的目標 -------------------------------------------------------


@pytest.mark.promise("FR-IN-01")
def test_同一個內容只建一份素材項目_目標不同就在同一份素材上開新工單(
    data: DataDir,
) -> None:
    first = _submit(data, PastedText("同一篇文章"), goal="目標一")
    second = _submit(data, PastedText("同一篇文章"), goal="給工程師看的 800 字筆記摘要")

    assert second.created
    assert first.job.id != second.job.id
    assert first.job.feed.item == second.job.feed.item
    assert len(_items(data)) == 1


@pytest.mark.promise("FR-IN-01")
def test_寫素材項目寫到一半被中斷_再投入同一份照樣成功(
    data: DataDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(path: Path, payload: str | bytes) -> None:
        if path.name == "item.json":
            raise KeyboardInterrupt  # Ctrl-C 或 WSL 當掉：content.md 寫了、item.json 還沒
        write_once(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr("evidence_forge.runner.intake.write_once", interrupted)
        with pytest.raises(KeyboardInterrupt):
            _submit(data, PastedText("同一篇文章"))

    intake = _submit(data, PastedText("同一篇文章"))

    assert intake.created
    assert _item(data, intake).content_hash is not None
    assert len(_items(data)) == 1


@pytest.mark.promise("FR-IN-01")
def test_同素材同目標_已有未丟棄的工單_回傳那張_不另開(data: DataDir) -> None:
    first = _submit(data, PastedText("同一篇文章"), goal="給 PM 看的觀點文")
    again = _submit(data, PastedText("同一篇文章"), goal="  給 PM 看的觀點文  ")

    assert (again.created, again.job.id) == (False, first.job.id)
    assert len(_jobs(data)) == 1


def _submit_in_child(root: str, start: "multiprocessing.synchronize.Barrier") -> None:
    start.wait()
    _submit(DataDir(Path(root)), PastedText("同一篇文章"))


@pytest.mark.promise("FR-IN-01")
def test_同素材同目標_兩個程序同時投入_只開一張(
    data: DataDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    # UI 的送出被連按兩下、或 UI 與 `ef new` 同時投入：兩個程序都掃描完才開單的話，
    # 會開出兩張、各花一次錢。這裡把「掃描完到開單」的空檔拉長，讓兩邊一定重疊
    from evidence_forge.runner import intake as intake_module

    scan = intake_module._open_job_for

    def slow_scan(*args: object, **kwargs: object) -> object:
        found = scan(*args, **kwargs)  # type: ignore[arg-type]
        time.sleep(0.5)
        return found

    monkeypatch.setattr("evidence_forge.runner.intake._open_job_for", slow_scan)
    # fork：子程序繼承上面換掉的掃描；兩個程序真的各自開檔、各自上鎖
    context = multiprocessing.get_context("fork")
    start = context.Barrier(2)
    children = [
        context.Process(target=_submit_in_child, args=(str(data.root), start))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=30)

    assert [child.exitcode for child in children] == [0, 0]
    assert len(_jobs(data)) == 1


@pytest.mark.promise("FR-IN-01")
def test_目標中間差一個字就算不同(data: DataDir) -> None:
    _submit(data, PastedText("同一篇文章"), goal="給 PM 看的觀點文")
    other = _submit(data, PastedText("同一篇文章"), goal="給 PM 看的評論文")
    assert other.created
    assert len(_jobs(data)) == 2


@pytest.mark.promise("FR-IN-01")
def test_沒填目標等於手打配方預設目標(data: DataDir) -> None:
    first = _submit(data, PastedText("同一篇文章"), goal=None)
    typed = _submit(data, PastedText("同一篇文章"), goal=f"  {DEFAULT_GOAL} ")
    blank = _submit(data, PastedText("同一篇文章"), goal="   ")

    assert first.job.goal == DEFAULT_GOAL
    assert {typed.job.id, blank.job.id} == {first.job.id}
    assert not typed.created and not blank.created


@pytest.mark.promise("FR-IN-01", "NFR-COST-03")
def test_同一個網址同樣目標再投入_不重抓_回傳已有工單(
    data: DataDir, fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", body=PAGE)
    first = _submit(data, WebPage("http://news.example/p/1"), fetcher=fetcher)
    again = _submit(data, WebPage("http://news.example/p/1"), fetcher=fetcher)

    assert (again.created, again.job.id) == (False, first.job.id)
    assert fake_web.hits("news.example", "/p/1") == 1
    assert [line["status"] for line in _intake_lines(data)] == ["ok", "cache_hit"]


# --- 目標、文類、預算 -------------------------------------------------------------


@pytest.mark.promise("FR-IN-01")
def test_沒填目標用配方的預設目標(data: DataDir) -> None:
    intake = _submit(data, PastedText("文章"), goal=None)
    assert read_job(data.job_dir(intake.job.id) / "job.md").job.goal == DEFAULT_GOAL


def test_沒填目標而配方也沒有預設目標_拒絕開單(data: DataDir) -> None:
    bare = RECIPE.model_copy(update={"default_goal": None})
    with pytest.raises(ValueError, match="目標"):
        _submit(data, PastedText("文章"), goal="", recipe=bare)
    assert _jobs(data) == []


@pytest.mark.promise("FR-IN-01", "FR-CP-03")
def test_文類與預算寫進_job_md(data: DataDir) -> None:
    intake = open_job(
        data,
        RECIPE,
        PastedText("文章"),
        goal="目標",
        now=NOW,
        budget_usd=1.25,
        genre="explainer",
    )
    job = read_job(data.job_dir(intake.job.id) / "job.md").job
    assert (job.target_genre, job.budget.usd) == ("explainer", 1.25)


def _author(data: DataDir, default_genre: str) -> None:
    author = data.authors / "me"
    author.mkdir(parents=True)
    (author / "voice.md").write_text(
        f"---\nid: me\ndefault_genre: {default_genre}\n---\n聲音\n", encoding="utf-8"
    )


@pytest.mark.promise("FR-CP-03")
def test_沒指定目標文類_用作家設定的預設文類_記在_job_md(data: DataDir) -> None:
    _author(data, "opinion")
    intake = _submit(data, PastedText("文章"))
    job = read_job(data.job_dir(intake.job.id) / "job.md").job
    assert job.target_genre == "opinion"


@pytest.mark.promise("FR-CP-03")
def test_有指定目標文類_不用作家的預設(data: DataDir) -> None:
    _author(data, "opinion")
    intake = open_job(
        data, RECIPE, PastedText("文章"), goal="目標", now=NOW, genre="explainer"
    )
    assert intake.job.target_genre == "explainer"


def test_沒有作家設定也沒指定_目標文類留空(data: DataDir) -> None:
    intake = _submit(data, PastedText("文章"))
    assert intake.job.target_genre is None


# --- Tavily credits 的預設（#69） ---------------------------------------------


@pytest.mark.promise("NFR-COST-01", "FR-RS-01")
def test_開單沒指定_credits_用配方各段_tavily_credits_的總和(data: DataDir) -> None:
    searching = RECIPE.model_copy(
        update={
            "stages": (
                RecipeStage(id="echo", skill="echo", tier="local", output=("a.json",)),
                RecipeStage(
                    id="research",
                    skill="echo",
                    tier="local",
                    output=("b.json",),
                    tavily_credits=10,
                ),
            )
        }
    )
    assert _submit(data, PastedText("配方沒有搜尋段")).job.budget.tavily_credits == 0
    job = _submit(data, PastedText("配方有搜尋段"), recipe=searching).job
    assert job.budget.tavily_credits == 10
    given = open_job(
        data, searching, PastedText("開單指定"), goal="x", now=NOW, budget_credits=3
    ).job
    assert given.budget.tavily_credits == 3
