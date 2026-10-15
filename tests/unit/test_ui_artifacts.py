"""右側面板讀的東西（`ui/artifacts.py`）：從對話的工單資料夾讀報告與文章，格式見 `tests/chat_folder.py`。

用手寫的假資料夾當輸入，從公開函式看輸出；舊版畫面找的 `drafts/draft.vN.md` 這裡一個都不用。
"""

import json
from pathlib import Path

import pytest
from chat_folder import (
    ARTICLE_V1,
    ARTICLE_V2,
    OLD_REPORT,
    RECIPES,
    REPORT,
    ChatFolder,
)

from evidence_forge.ui import artifacts


@pytest.fixture
def folder(tmp_path: Path) -> ChatFolder:
    return ChatFolder(tmp_path / "data")


def _jobs(folder: ChatFolder, conversation: str) -> list[artifacts.JobInfo]:
    return artifacts.conversation_jobs(folder.data, RECIPES, conversation)


# --- 工單與版本 -------------------------------------------------------------------


def test_每張工單依配方分成分析或重構_版本接著上一版數(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.analysis(cid, "a2", parent="a1", version=2, minutes=5)
    folder.article(cid, "w1", parent="a2", minutes=10)
    folder.article(cid, "w2", parent="w1", version=2, minutes=20)

    jobs = _jobs(folder, cid)

    assert [(j.job_id, j.kind, j.label) for j in jobs] == [
        ("a1", "analysis", "v1"),
        ("a2", "analysis", "v2"),
        ("w1", "rewrite", "v1"),
        ("w2", "rewrite", "v2"),
    ]
    # 同一條版本鏈算一篇（Bills 的規則）：w1、w2 同一個 chain
    assert jobs[2].chain == jobs[3].chain
    assert jobs[0].chain == jobs[1].chain


def test_兩條不同的鏈_標籤帶上第幾篇(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", minutes=10)
    folder.article(cid, "w2", parent="a1", persona="b2b", minutes=20)

    labels = [j.label for j in _jobs(folder, cid) if j.kind == "rewrite"]

    assert labels == ["文章 1 v1", "文章 2 v1"]


def test_舊的待審工單讀成結束_讀不懂的工單跳過(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "old")
    # 舊版 job.md：狀態是 approved
    path = folder.data.job_dir("old") / "job.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: done", "status: approved"), encoding="utf-8")
    (folder.data.job_dir("broken")).mkdir()
    (folder.data.job_dir("broken") / "job.md").write_text("不是 frontmatter", encoding="utf-8")
    folder.update_conversation(cid)

    assert [j.job_id for j in _jobs(folder, cid)] == ["old"]
    assert _jobs(folder, cid)[0].job.status == "done"


# --- 報告 -------------------------------------------------------------------------


def test_報告的查證照有爭議_過時_查不到_有佐證排_每條附來源(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert [c.verdict for c in view.checks] == ["disputed", "unfound", "supported"]
    assert view.checks[0].claim == "長上下文比檢索省 70% 成本"
    assert [s.id for s in view.checks[0].sources] == ["S2", "S3"]
    assert view.checks[0].sources[0].url == "https://pricing.example/list"
    assert view.checks[0].sources[0].fetched_at == "10/13 15:01"
    assert (view.doubtful, view.supported, view.unfound) == (1, 1, 1)
    assert [t.title for t in view.topics] == ["長上下文的帳怎麼算", "第一個字為什麼越來越慢"]


def test_過時排在查不到前面(folder: ChatFolder) -> None:
    sources = {
        "queries": ["q"],
        "sources": [{"id": "S2", "url": "https://a.example/x", "title": "甲", "why": "w"}],
        "fact_checks": [
            {"claim": "K1", "verdict": "supported", "sources": ["S2"]},
            {"claim": "K2", "verdict": "not_found", "sources": []},
            {"claim": "K3", "verdict": "outdated", "sources": ["S2"]},
        ],
        "topics": [],
    }
    cid = folder.conversation()
    folder.analysis(cid, "a1", sources=sources)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert [c.verdict for c in view.checks] == ["outdated", "unfound", "supported"]
    assert view.doubtful == 1  # 過時也算有疑慮


def test_查證的事實與說明從報告那一條拆開_不重複標記(folder: ChatFolder) -> None:
    # ADR-0024：報告的查證一條是「【有爭議】事實：說明」，跟 sources.json 的 fact_checks 同一個順序
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert [c.claim for c in view.checks] == [
        "長上下文比檢索省 70% 成本",
        "八成團隊還在用檢索",
        "上下文越長，第一個字出來得越慢",
    ]
    note = str(view.checks[0].note_html or "")
    assert "價目表與廠商部落格的算法對不上" in note
    assert "有爭議" not in note and "【" not in note


def test_舊報告_查證的說明從報告原文對到那一條_引用編號變成連結(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1", report=OLD_REPORT)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    note = view.checks[0].note_html or ""
    assert "兩份來源的算法對不上" in note
    assert '<a href="https://pricing.example/list"' in note
    assert "K1" not in note  # 開頭的「K1 有爭議：」是重複的標記，不再顯示


def test_報告第一個標題前的一句話拿來當結果卡的摘要_下一步另外放(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.lead == "長上下文還撐不起大多數檢索場景，兩年內檢索仍是主流。"
    assert view.next_step == "▶ 下一步：下面有 2 個可以寫的題材，說「寫第 2 個」就開始。"


def test_舊報告沒有那一句話_摘要用作者在說什麼的第一句(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1", report=OLD_REPORT)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.lead == "作者認為超長上下文還撐不起大多數檢索場景。"
    assert view.next_step == ""


def test_可以寫的題材照編號_每個帶說明與用到的來源(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert [(t.number, t.title) for t in view.topics] == [
        (1, "長上下文的帳怎麼算"),
        (2, "第一個字為什麼越來越慢"),
    ]
    assert "拿公開價目表替讀者算一次" in str(view.topics[0].note_html)
    assert [[s.title for s in t.sources] for t in view.topics] == [["價目表"], ["延遲說明"]]
    assert (view.views_heading, view.topics_heading) == ("作者的看法", "可以寫的題材")
    assert "這是作者的判斷" in str(view.views_html)


def test_舊報告的節名照原樣顯示(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1", report=OLD_REPORT)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert (view.views_heading, view.topics_heading) == ("事實與觀點", "研究題材")
    assert "長上下文比檢索省 70% 成本" in str(view.views_html)
    assert view.topics[0].title == "長上下文的帳怎麼算"


def test_報告裡的網頁內容不會變成標記(folder: ChatFolder) -> None:
    hostile = REPORT.replace("作者認為", "<script>alert(1)</script>作者認為")
    cid = folder.conversation()
    folder.analysis(cid, "a1", report=hostile)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert "<script>" not in view.intro_html
    assert "&lt;script&gt;" in view.intro_html


def test_沒有來源清單時查證那一節照原文顯示_不猜結構(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    (folder.data.job_dir("a1") / "stages" / "investigate" / "1" / "sources.json").unlink()
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.checks == []
    assert "價目表與廠商部落格的算法對不上" in view.verify_html


def test_報告檔不見了_說明讀不到哪一個檔_不丟例外(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    (folder.data.job_dir("a1") / "stages" / "investigate" / "1" / "report.md").unlink()
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.error is not None and "report.md" in view.error


# --- 文章 -------------------------------------------------------------------------


def test_文章讀_article_md_角色文類字數都在(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1, overlaps={"原文": 6.0})
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    view = artifacts.article_view(folder.data, info, None)

    assert view.title == "長上下文不會取代檢索"
    assert (view.persona, view.genre) == ("me", "解說文")
    assert view.words > 40
    assert "<h2>先算錢</h2>" in view.html
    assert view.text == ARTICLE_V1


def test_重疊率顯示所有來源裡最高的那一個_標出是哪一份_含原文(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", overlaps={"原文": 6.0, "S2": 11.5, "S3": 2.0})
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    overlap = artifacts.article_view(folder.data, info, None).overlap

    assert overlap is not None
    assert (overlap.rate, overlap.source) == (11.5, "S2 價目表")
    assert overlap.warned is False  # 顯示而已，不擋，也不改變結果


def test_原文本身最高時標原文(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", overlaps={"原文": 6.0, "S2": 3.0})
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    overlap = artifacts.article_view(folder.data, info, None).overlap

    assert overlap is not None and (overlap.rate, overlap.source) == (6.0, "原文")


def test_沒有重疊條目就是沒有重疊率_不編一個數字(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1")
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    assert artifacts.article_view(folder.data, info, None).overlap is None


def test_結果上寫了未校準就標未校準(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(
        cid, "w1", parent="a1", reason="未校準：角色 me 還沒有校準過的 explainer 文類風格"
    )
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    view = artifacts.article_view(folder.data, info, None)

    assert view.uncalibrated is True
    assert view.uncalibrated_note.startswith("未校準：角色 me")


def test_跟上一版對照_新增的包_ins_刪掉的包_del_沒動的照常(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1, minutes=1)
    folder.article(cid, "w2", parent="w1", text=ARTICLE_V2, version=2, minutes=2)
    first, second = [j for j in _jobs(folder, cid) if j.kind == "rewrite"]

    view = artifacts.article_view(folder.data, second, first)

    assert view.diff_html is not None
    assert '<ins class="ch-ins">結論先講：未來兩年多數產品還是會用檢索。</ins>' in view.diff_html
    assert '<del class="ch-del">' in view.diff_html and "最近常聽到" in view.diff_html
    assert "<h2>先算錢</h2>" in view.diff_html  # 沒動的標題照常
    assert view.previous_words == artifacts.article_view(folder.data, first, None).words


def test_第一版沒有對照(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1")
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    assert artifacts.article_view(folder.data, info, None).diff_html is None


def test_來源網址只放行_http_和_https_agent_寫的_javascript_不會變成連結(folder: ChatFolder) -> None:
    sources = {
        "queries": ["q"],
        "sources": [
            {"id": "S2", "url": "javascript:alert(1)", "title": "壞", "why": "w"},
            {"id": "S3", "url": "https://ok.example/a", "title": "好", "why": "w"},
        ],
        "fact_checks": [{"claim": "K1", "verdict": "disputed", "sources": ["S2", "S3"]}],
        "topics": [],
    }
    cid = folder.conversation()
    folder.analysis(cid, "a1", sources=sources)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    urls = {s.id: s.url for c in view.checks for s in c.sources}
    assert urls == {"S2": None, "S3": "https://ok.example/a"}
    assert "javascript:" not in str(view.intro_html) + str(view.verify_html)


def test_重疊率讀得懂重構檢查真正寫的那一行(folder: ChatFolder) -> None:
    # 字串是 #92 的 `check_overlap` 實際算出來的第一條（label 是 S2），不是照畫面的寫法倒推的
    real = "S2：4-gram 重疊率 45.0%（提醒門檻 10.0%）；最長共同片段 12 個詞（提醒門檻 12）；門檻暫定（Q-28 未定）；逐字重疊偏多，只提醒、不擋"
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1")
    path = folder.data.job_dir("w1") / "checks" / "checks.v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["entries"] = [
        {"check": "C3", "status": "warn", "reason": real},
        {"check": "C3", "status": "warn", "reason": "S2：這段 12 個詞都落在跟S2一字不差的片段裡",
         "location": {"file": "a.md", "line": 2}, "excerpt": "x"},
        {"check": "C3", "status": "pass", "reason": "原文：4-gram 重疊率 6.0%（提醒門檻 10.0%）；最長共同片段 3 個詞（提醒門檻 12）"},
    ]
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    overlap = artifacts.article_view(folder.data, info, None).overlap

    assert overlap is not None
    assert (overlap.rate, overlap.source, overlap.warned) == (45.0, "S2 價目表", True)


def test_沒有份名的舊寫法當成原文(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1")
    path = folder.data.job_dir("w1") / "checks" / "checks.v1.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["entries"] = [{"check": "C3", "status": "pass", "reason": "4-gram 重疊率 3.5%（提醒門檻 10.0%）"}]
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    info = [j for j in _jobs(folder, cid) if j.kind == "rewrite"][0]

    overlap = artifacts.article_view(folder.data, info, None).overlap

    assert overlap is not None and (overlap.rate, overlap.source) == (3.5, "原文")


def test_網址有括號的來源照樣是連結(folder: ChatFolder) -> None:
    sources = {
        "queries": ["q"],
        "sources": [{"id": "S2", "url": "https://wiki.example/Foo_(bar)", "title": "條目", "why": "w"}],
        "fact_checks": [{"claim": "K1", "verdict": "supported", "sources": ["S2"]}],
        "topics": [],
    }
    report = OLD_REPORT.replace("見 [S2] 與 [S3]", "見 [S2]")
    cid = folder.conversation()
    folder.analysis(cid, "a1", sources=sources, report=report)
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.checks[0].sources[0].url == "https://wiki.example/Foo_(bar)"
    assert "https://wiki.example/Foo_%28bar%29" in str(view.verify_html)


# --- 不在任何對話裡的工單（命令列開的） -----------------------------------------------------


def test_不在對話裡的工單讀得到報告和文章_版本鏈照_parent(folder: ChatFolder) -> None:
    folder.analysis(None, "20261013-100000-aaaa")
    folder.article(None, "20261013-110000-bbbb", parent="20261013-100000-aaaa", text=ARTICLE_V1)
    folder.article(None, "20261013-120000-cccc", parent="20261013-110000-bbbb", text=ARTICLE_V2, version=2)

    jobs = artifacts.loose_jobs(folder.data, RECIPES)

    assert [(j.job_id[-4:], j.kind, j.label) for j in jobs] == [
        ("aaaa", "analysis", "v1"),
        ("bbbb", "rewrite", "v1"),
        ("cccc", "rewrite", "v2"),
    ]
    latest = artifacts.article_view(folder.data, jobs[2], jobs[1])
    assert latest.title == "長上下文不會取代檢索" and latest.diff_html is not None
    assert artifacts.report_view(folder.data, jobs[0]).checks


def test_已經在某個對話裡的工單不算不在對話裡(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "mine")
    folder.analysis(None, "loose1")

    assert [j.job_id for j in artifacts.loose_jobs(folder.data, RECIPES)] == ["loose1"]
    assert [j.job_id for j in artifacts.conversation_jobs(folder.data, RECIPES, artifacts.LOOSE)] == ["loose1"]


def test_舊配方的單_沒有報告也沒有文章_不放進不在對話裡的清單(folder: ChatFolder) -> None:
    folder.job(None, "old-echo", recipe="echo")

    assert artifacts.loose_jobs(folder.data, RECIPES) == []


def test_文章在第三次嘗試的資料夾_不是永遠第一次(folder: ChatFolder) -> None:
    folder.analysis(None, "a1")
    folder.article(None, "w1", parent="a1", text=ARTICLE_V1)
    job_dir = folder.data.job_dir("w1")
    (job_dir / "stages" / "rewrite" / "1" / "article.md").rename(job_dir / "stages" / "rewrite" / "3.tmp")
    folder.write("w1", "stages/rewrite/3/article.md", ARTICLE_V2)
    folder.write("w1", "stages/rewrite/3/article.json", {"title": "第三次", "title_candidates": ["第三次"]})
    # 第四次當掉、沒寫出文章：往前找有交出東西的那一次
    (job_dir / "stages" / "rewrite" / "4").mkdir()
    info = [j for j in artifacts.loose_jobs(folder.data, RECIPES) if j.kind == "rewrite"][0]

    assert artifacts.article_view(folder.data, info, None).title == "第三次"


# --- jobs.jsonl 裡的工單 id 不能拿來走出工單資料夾 ---------------------------------------------


def test_jobs_jsonl_的工單_id_有路徑字元或指到外面就跳過(folder: ChatFolder, tmp_path: Path) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "good")
    ledger = folder.data.conversation_dir(cid) / "jobs.jsonl"
    rows = [
        {"job_id": "../../etc", "ts": "2026-10-13T15:00:00+08:00"},
        {"job_id": "a/b", "ts": "2026-10-13T15:00:00+08:00"},
        {"job_id": "..", "ts": "2026-10-13T15:00:00+08:00"},
        {"job_id": "with space", "ts": "2026-10-13T15:00:00+08:00"},
        {"job_id": "evil", "ts": "2026-10-13T15:00:00+08:00"},
        {"job_id": "good", "ts": "2026-10-13T15:00:00+08:00", "parent": "../x"},
    ]
    with ledger.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    # jobs/evil 是符號連結，指到資料目錄外面一個有 job.md 的資料夾
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job.md").write_text((folder.data.job_dir("good") / "job.md").read_text(encoding="utf-8"), encoding="utf-8")
    (folder.data.jobs / "evil").symlink_to(outside, target_is_directory=True)

    assert [j.job_id for j in _jobs(folder, cid)] == ["good"]


# --- 換角色是另一條版本鏈 -------------------------------------------------------------------


def test_換角色就是新的一篇_即使_parent_有填(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", persona="me", text=ARTICLE_V1, minutes=1)
    folder.article(cid, "w2", parent="w1", persona="b2b", text=ARTICLE_V2, version=2, minutes=2)
    folder.article(cid, "w3", parent="w2", persona="b2b", text=ARTICLE_V1, version=3, minutes=3)
    first, second, third = [j for j in _jobs(folder, cid) if j.kind == "rewrite"]

    assert first.chain != second.chain and second.chain == third.chain
    assert (first.previous_id, second.previous_id, third.previous_id) == (None, None, "w2")
    assert [j.label for j in (first, second, third)] == ["文章 1 v1", "文章 2 v1", "文章 2 v2"]


def test_上一版跟著_parent_走_不是列表的上一個(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", persona="me", minutes=1)
    folder.article(cid, "w2", parent="a1", persona="b2b", minutes=2)
    folder.article(cid, "w3", parent="w1", persona="me", version=2, minutes=3)
    by_id = {j.job_id: j for j in _jobs(folder, cid)}

    # 列表順序是 w1、w2、w3，但 w3 的上一版是 w1，不是 w2
    assert by_id["w3"].previous_id == "w1"
