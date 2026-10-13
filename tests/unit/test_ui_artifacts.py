"""右側面板讀的東西（`ui/artifacts.py`）：從對話的工單資料夾讀報告與文章，格式見 `tests/chat_folder.py`。

用手寫的假資料夾當輸入，從公開函式看輸出；舊版畫面找的 `drafts/draft.vN.md` 這裡一個都不用。
"""

from pathlib import Path

import pytest
from chat_folder import (
    ARTICLE_V1,
    ARTICLE_V2,
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
    assert view.topics[0].title == "長上下文的實際成本"


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


def test_查證的說明從報告原文對到那一條_引用編號變成連結(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    note = view.checks[0].note_html or ""
    assert "兩份來源的算法對不上" in note
    assert '<a href="https://pricing.example/list"' in note
    assert "K1" not in note  # 開頭的「K1 有爭議：」是重複的標記，不再顯示


def test_報告第一句拿來當結果卡的摘要(folder: ChatFolder) -> None:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    view = artifacts.report_view(folder.data, _jobs(folder, cid)[0])

    assert view.lead == "作者認為超長上下文還撐不起大多數檢索場景。"


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
    assert "兩份來源的算法對不上" in view.verify_html


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
