"""聊天室畫面（`ui/pages.py`）：跟對話 API 同一個程序、同源送出；頁面與片段從資料夾與假 Pi 的紀錄長出來。

真的開在 127.0.0.1 的空埠上；Pi 是假 Pi（`tests/chat_world.py`），紀錄用預先寫好的 session 檔放進去。
工單與報告、文章用手寫的假資料夾（`tests/chat_folder.py`）：#92 的重構配方還沒併進來，畫面只讀檔，不靠那一包的程式。
"""

import base64
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_folder import ARTICLE_V1, ARTICLE_V2, RECIPES, ChatFolder, at
from chat_world import fake_chat_harness

from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.chat.pi_rpc import session_dir
from evidence_forge.gateway.active import ActiveStages

PDF = Path(__file__).parents[1] / "fixtures" / "pdf"


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[tuple[str, ChatFolder, ConversationHost]]:
    folder = ChatFolder(tmp_path / "data")
    host = ConversationHost(
        data=folder.data,
        harness=fake_chat_harness(tmp_path / "harness"),
        stages=ActiveStages(),
        gateway_url="http://127.0.0.1:9",
        recipes=RECIPES,
        clock=lambda: at(),
    )
    host.start()
    try:
        with serve(create_app(host, data=folder.data, recipes=RECIPES)) as url:
            yield url, folder, host
    finally:
        host.close()


def _seed_messages(folder: ChatFolder, conversation_id: str, messages: list[dict[str, Any]]) -> None:
    """放進假 Pi 的紀錄檔：之後開 Pi 就接回這份。"""
    directory = session_dir(folder.data, conversation_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{conversation_id}.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages), encoding="utf-8"
    )


def _analysis_world(folder: ChatFolder) -> str:
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.ledger("a1", [{"stage": "investigate", "tier": "strong", "model": "claude", "cost_usd": 0.31}])
    _seed_messages(
        folder,
        cid,
        [
            {"role": "user", "content": [{"type": "text", "text": "分析這篇 https://seed.example/post"}]},
            {"role": "assistant", "content": [{"type": "toolCall", "id": "t", "name": "analyze", "arguments": {"url": "https://seed.example/post"}}]},
            {"role": "toolResult", "toolName": "analyze", "toolCallId": "t", "content": [{"type": "text", "text": "job_id: a1\nstatus: queued"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "重點有**兩個**。"}]},
        ],
    )
    return cid


# --- 外框與清單 ---------------------------------------------------------------------


def test_首頁是三欄外框_腳本和樣式都從同源來(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    folder.conversation("甲對話")

    page = httpx.get(f"{url}/")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    html = page.text
    for needle in ('id="ch-shell"', "對話清單", "甲對話", 'id="ch-input"', 'id="ch-panel"', "/static/js/app.js"):
        assert needle in html, needle
    # 沒有任何外部資源（連字型都自己放）：所有 src、href 不是相對路徑就是同源
    assert not re.findall(r'(?:src|href)="https?://(?!seed\.example)', html)
    assert re.findall(r'<script[^>]*src="([^"]+)"', html) == ["/static/js/app.js"]


def test_舊的看板和工單詳情路由沒有了(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    folder.conversation()
    folder.job(folder.conversation(), "j-old")

    for path in ("/board", "/jobs/j-old", "/jobs/new", "/jobs/j-old?tab=draft"):
        assert httpx.get(f"{url}{path}").status_code == 404, path


def test_清單有新結果的排最前面_標狀態_看過就不標(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    old = folder.conversation("舊的", minutes=-30)
    done = folder.conversation("跑完了", minutes=-20)
    folder.analysis(done, "a1")
    running = folder.conversation("跑著", minutes=-10)
    folder.job(running, "a2", status="running", outcome=None)
    failed = folder.conversation("失敗", minutes=-5)
    folder.job(failed, "a3", outcome="failed", reason="Ollama 沒回應")
    del old

    fragment = httpx.get(f"{url}/ui/list").text
    titles = re.findall(r'class="ch-row__title">([^<]+)<', fragment)

    assert titles[0] == "跑完了"
    assert "有新結果" in fragment and "分析中" in fragment and "失敗" in fragment
    assert fragment.count("data-new=") == 1
    assert 'data-new-count="1"' in fragment

    seen = httpx.post(f"{url}/api/conversations/{done}/seen", json={"job_id": "a1"})
    assert seen.status_code == 204
    after = httpx.get(f"{url}/ui/list").text
    assert "有新結果" not in after and 'data-new-count="0"' in after


def test_未完成也算有新結果_因為它確實交出了東西(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation("預算用完")
    folder.analysis(cid, "a1", outcome="incomplete", reason="預算用完")

    assert "有新結果" in httpx.get(f"{url}/ui/list").text


def test_正在看的對話不標有新結果(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation("正在看")
    folder.analysis(cid, "a1")

    assert "有新結果" not in httpx.get(f"{url}/ui/list", params={"current": cid}).text


def test_標題是未讀數_頁面標題跟著(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")

    assert "<title>(1) Evidence Forge</title>" in httpx.get(f"{url}/").text


def test_清單是空的有說明_資料目錄讀不到顯示路徑和原因(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world
    assert "還沒有對話" in httpx.get(f"{url}/ui/list").text


def test_回報看過_工單不是這個對話的就拒絕(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    mine, other = folder.conversation("甲"), folder.conversation("乙")
    folder.analysis(other, "b1")

    wrong = httpx.post(f"{url}/api/conversations/{mine}/seen", json={"job_id": "b1"})
    missing = httpx.post(f"{url}/api/conversations/c-nope/seen", json={"job_id": "b1"})
    bad = httpx.post(f"{url}/api/conversations/{mine}/seen", json={"job": 1})

    assert (wrong.status_code, missing.status_code, bad.status_code) == (400, 404, 400)


# --- 對話頁與串流片段 -----------------------------------------------------------------


def test_對話頁_不存在的對話是_404(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation("甲對話")

    ok = httpx.get(f"{url}/c/{cid}")
    gone = httpx.get(f"{url}/c/c-nope")

    assert ok.status_code == 200 and f'data-conversation="{cid}"' in ok.text and "甲對話" in ok.text
    assert gone.status_code == 404 and "找不到這個對話" in gone.text
    assert httpx.get(f"{url}/c/..%2Fetc").status_code == 404


def test_串流片段_你的訊息_agent_回答_結果卡都在(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = _analysis_world(folder)

    thread = httpx.get(f"{url}/ui/c/{cid}/thread", timeout=15)

    assert thread.status_code == 200
    html = thread.text
    assert "seed.example" in html and "長上下文會不會取代檢索" in html  # 連結卡用素材標題
    assert "<strong>兩個</strong>" in html
    assert "分析報告 v1・完成" in html
    assert 'data-open="report"' in html and 'data-job="a1"' in html
    assert "長上下文還撐不起大多數檢索場景" in html  # 報告第一個標題前的那一句話
    assert re.search(r"可以寫的題材 <span[^>]*>2</span> 個", html)
    assert "研究題材" not in html
    assert "US$0.31" in html
    assert 'data-latest-job="a1"' in html and 'data-latest-done="true"' in html


def test_跑著的工單是空位_進度列由事件填(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.job(cid, "a1", status="running", outcome=None)

    html = httpx.get(f"{url}/ui/c/{cid}/thread", timeout=15).text

    assert 'class="ch-progress"' in html and 'data-job="a1"' in html and "hidden" in html
    assert "ch-result" not in html


def test_串流片段_惡意內容不會變成標記(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    _seed_messages(
        folder,
        cid,
        [
            {"role": "user", "content": [{"type": "text", "text": "<img src=x onerror=alert(1)>"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "<script>alert(2)</script> [x](javascript:alert(3))"}]},
        ],
    )

    html = httpx.get(f"{url}/ui/c/{cid}/thread", timeout=15).text

    assert "<img" not in html and "<script>" not in html and 'href="javascript' not in html
    assert "&lt;img" in html


def test_沒有這個對話_片段回_404(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    assert httpx.get(f"{url}/ui/c/c-nope/thread").status_code == 404
    assert httpx.get(f"{url}/ui/c/c-nope/panel").status_code == 404
    assert httpx.get(f"{url}/ui/c/c-nope/article.md").status_code == 404


# --- 右側面板 -------------------------------------------------------------------------


def test_報告分頁_五節_查證排序_每條附來源(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "report"}).text

    titles = re.findall(r'<span class="ch-sec__n">\d</span>([^<]+)', html)
    assert [title.strip() for title in titles] == ["作者在說什麼", "查證", "作者的看法", "可以寫的題材", "來源清單"]
    assert "▶ 下一步" in html
    assert '<p class="ch-nextstep__lead">長上下文還撐不起' in html
    order = [html.index(label) for label in ("有爭議", "查不到", "有佐證")]
    assert order == sorted(order)
    # 報告裡沒有內部編號：來源用名字（ADR-0024）
    assert not re.search(r">S\d+ ", html)
    # 來源清單預設收起來
    assert re.search(r"<details[^>]*>\s*<summary[^>]*>.*?來源清單", html, flags=re.DOTALL)
    assert not re.search(r"<details[^>]*\bopen\b[^>]*>\s*<summary[^>]*>.*?來源清單", html, flags=re.DOTALL)
    assert 'href="https://pricing.example/list"' in html
    assert "10/13 15:01 抓取" in html
    assert 'data-verdict="disputed"' in html and 'data-verdict="unfound"' in html


def test_報告分頁_補查之後可以切版本_舊版留著(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.analysis(cid, "a2", parent="a1", version=2, minutes=5)

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "report", "v": "a1"}).text

    assert html.count("<option") == 2
    assert re.search(r'<option value="a1" selected>v1</option>', html)
    assert "v2（最新）" in html


def test_面板的版本參數不是這個對話的工單就當沒給(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")

    for hostile in ("../../outside/job", "no-such", ""):
        response = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "report", "v": hostile})
        assert response.status_code == 200
        assert "作者在說什麼" in response.text


def test_沒有報告_沒有文章_各有空狀態(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()

    assert "這個對話還沒有報告" in httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "report"}).text
    assert "這個對話還沒有文章" in httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article"}).text


def test_報告檔不見_說明讀不到哪個檔(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    (folder.data.job_dir("a1") / "stages" / "investigate" / "1" / "report.md").unlink()

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "report"}).text

    assert "讀不到報告 v1 的檔案" in html and "report.md" in html


@pytest.mark.promise("FR-UI-11")
def test_文章分頁_讀_article_md_角色文類字數重疊率都在(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1, overlaps={"原文": 6.0, "S2": 11.5})

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article"}).text

    assert "<h2>先算錢</h2>" in html
    assert "理論教學" in html and "me" in html
    assert "11.5%" in html and "S2 價目表" in html and "只顯示，不擋" in html
    assert "複製 Markdown" in html


@pytest.mark.promise("FR-UI-11")
def test_舊工單的文類_id_照原字顯示_畫面不壞(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    # 舊的 12 種文類不做對照（ADR-0026 §3.4）：舊工單的 opinion 照原字顯示，分頁照樣打得開
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1, genre="opinion")

    response = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article"})

    assert response.status_code == 200
    assert "<dt>文體</dt><dd>opinion" in response.text


def test_文章分頁_跟上一版對照(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1, minutes=1)
    folder.article(cid, "w2", parent="w1", text=ARTICLE_V2, version=2, minutes=2)

    plain = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article"}).text
    diff = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article", "diff": "1"}).text

    assert "跟 v1 對照" in plain and "ch-ins" not in plain
    assert '<ins class="ch-ins">' in diff and '<del class="ch-del">' in diff
    assert "新增的字" in diff


def test_文章分頁_未校準標出來(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", reason="未校準：角色 me 還沒有校準過的 explainer 文類風格")

    assert "未校準" in httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article"}).text


def test_複製_Markdown_給原文(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1)

    response = httpx.get(f"{url}/ui/c/{cid}/article.md", params={"v": "w1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == ARTICLE_V1


def test_複製要指定的那一版_對不上就是_404_不給別版(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", text=ARTICLE_V1)

    assert httpx.get(f"{url}/ui/c/{cid}/article.md", params={"v": "w-old"}).status_code == 404
    assert httpx.get(f"{url}/ui/c/{cid}/article.md").text == ARTICLE_V1


def test_花費分頁_每次工作一列_對話本身另列_合計(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.ledger("a1", [{"stage": "investigate", "tier": "strong", "model": "c", "cost_usd": 0.31}])
    folder.ledger(cid, [{"stage": "chat", "tier": "strong", "model": "c", "cost_usd": 0.04}], conversation=True)

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "cost"}).text

    assert "分析 v1" in html and "對話本身" in html
    assert "US$0.31" in html and "US$0.04" in html and "US$0.35" in html


# --- 帳單 -----------------------------------------------------------------------------


@pytest.mark.promise("FR-LG-02")
def test_帳單頁_三個數字_分項_對話列表(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation("長上下文")
    folder.analysis(cid, "a1")
    folder.ledger("a1", [{"stage": "investigate", "tier": "strong", "model": "c", "cost_usd": 0.25}])
    folder.ledger(cid, [{"stage": "chat", "tier": "strong", "model": "c", "cost_usd": 0.05}], conversation=True)

    page = httpx.get(f"{url}/bills", params={"month": "2026-10"})

    assert page.status_code == 200
    html = page.text
    assert "本月總花費" in html and "US$0.30" in html
    assert "1 份" in html and "0 篇" in html
    assert "對話本身" in html and "查證與找題材" in html
    assert f'href="/c/{cid}?tab=cost"' in html
    assert "2026-10" in html


def test_帳單頁_月份亂填就用這個月_空的月份有說明(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    bad = httpx.get(f"{url}/bills", params={"month": "../../x"})
    empty = httpx.get(f"{url}/bills", params={"month": "2026-01"})

    assert bad.status_code == 200 and "2026-10" in bad.text
    assert "這個月還沒有花費" in empty.text


def test_導覽只有聊天室_角色_帳單_設定(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    html = httpx.get(f"{url}/bills").text
    nav = re.search(r'<nav class="s0-nav".*?</nav>', html, re.S)

    assert nav is not None
    assert re.findall(r'href="([^"]+)"', nav.group(0)) == ["/", "/personas", "/bills", "/settings"]


# --- 上傳 -----------------------------------------------------------------------------


def _upload(url: str, name: str, raw: bytes) -> httpx.Response:
    return httpx.post(f"{url}/api/extract", json={"name": name, "data": base64.b64encode(raw).decode()})


@pytest.mark.promise("FR-IN-01")
def test_上傳_Markdown_純文字_PDF_抽成文字(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    md = _upload(url, "note.md", "# 標題\n\n內文".encode())
    txt = _upload(url, "C:\\temp\\x.TXT", "純文字".encode())
    pdf = _upload(url, "article.pdf", (PDF / "article.pdf").read_bytes())

    assert md.json() == {"name": "note.md", "text": "# 標題\n\n內文", "chars": 8}
    assert (txt.json()["name"], txt.json()["text"]) == ("x.TXT", "純文字")
    assert pdf.status_code == 200 and pdf.json()["chars"] > 50


@pytest.mark.promise("FR-IN-01")
def test_上傳_不收的格式_掃描的_PDF_空檔_壞資料_各有說明(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    assert _upload(url, "a.docx", b"x").status_code == 415
    scanned = _upload(url, "scan.pdf", (PDF / "scanned.pdf").read_bytes())
    assert scanned.status_code == 422 and "抽不到文字" in scanned.json()["error"]
    assert _upload(url, "empty.txt", b"  \n").status_code == 422
    assert _upload(url, "bin.txt", b"\xff\xfe\x00").status_code == 422
    bad = httpx.post(f"{url}/api/extract", json={"name": "a.txt", "data": "!!!"})
    assert bad.status_code == 400
    assert httpx.post(f"{url}/api/extract", json={"name": 1}).status_code == 400


def test_上傳太大就擋下(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    big = _upload(url, "big.txt", b"a" * (5 * 1024 * 1024 + 1))

    assert big.status_code == 413


# --- 靜態檔與安全 ---------------------------------------------------------------------


def test_靜態檔_樣式_腳本_字型都送得出來(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    for path in ("/static/css/chat.css", "/static/css/s0.css", "/static/css/tokens.css", "/static/css/fonts.css"):
        assert httpx.get(f"{url}{path}").status_code == 200, path
    for module in ("app", "state", "progress", "compose", "settings"):
        response = httpx.get(f"{url}/static/js/{module}.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]
    font = httpx.get(f"{url}/static/fonts/inter-latin-400-normal.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert httpx.get(f"{url}/static/../../pyproject.toml").status_code == 404


@pytest.mark.promise("NFR-SEC-02")
def test_頁面也只接受本機的請求(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()

    assert httpx.get(f"{url}/", headers={"host": "evil.example"}).status_code == 403
    assert httpx.get(f"{url}/", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert httpx.get(f"{url}/bills", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert httpx.get(f"{url}/ui/c/{cid}/panel", headers={"origin": "https://evil.example"}).status_code == 403
    # 同一台機器、同站不同埠的頁面（例如 localhost:5173 的舊做法）仍然不行：頁面要跟 API 同源
    same = httpx.get(f"{url}/", headers={"sec-fetch-site": "same-origin"})
    assert same.status_code == 200
    # 直接在網址列打開（沒有 Sec-Fetch-Site 或是 none）也行
    assert httpx.get(f"{url}/", headers={"sec-fetch-site": "none"}).status_code == 200


# --- 不在對話裡的工單（命令列開的）：唯讀看得到報告與文章 ----------------------------------------


def _loose_world(folder: ChatFolder) -> None:
    folder.analysis(None, "20261013-100000-aaaa")
    folder.ledger("20261013-100000-aaaa", [{"stage": "investigate", "tier": "strong", "model": "c", "cost_usd": 0.2}])
    folder.article(None, "20261013-110000-bbbb", parent="20261013-100000-aaaa", text=ARTICLE_V1)
    folder.article(None, "20261013-120000-cccc", parent="20261013-110000-bbbb", text=ARTICLE_V2, version=2)


def test_清單最後多一列不在對話裡的工單_沒有這種工單就不列(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    folder.conversation("甲")
    assert "不在對話裡的工單" not in httpx.get(f"{url}/ui/list").text

    _loose_world(folder)
    fragment = httpx.get(f"{url}/ui/list").text

    titles = re.findall(r'class="ch-row__title">([^<]+)<', fragment)
    assert titles == ["甲", "不在對話裡的工單"]
    assert 'href="/c/loose"' in fragment and "命令列開的單" in fragment


def test_不在對話裡的工單_頁面_串流_面板都讀得到_最新一次嘗試的文章(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    _loose_world(folder)
    # 今天真的長相：文章在 stages/rewrite/3/，不是第 1 次
    job_dir = folder.data.job_dir("20261013-120000-cccc")
    (job_dir / "stages" / "rewrite" / "1" / "article.md").rename(job_dir / "stages" / "rewrite" / "3.x")
    folder.write("20261013-120000-cccc", "stages/rewrite/3/article.md", ARTICLE_V2)

    page = httpx.get(f"{url}/c/loose")
    thread = httpx.get(f"{url}/ui/c/loose/thread", timeout=15).text
    report = httpx.get(f"{url}/ui/c/loose/panel", params={"tab": "report"}).text
    article = httpx.get(f"{url}/ui/c/loose/panel", params={"tab": "article", "diff": "1"}).text
    cost = httpx.get(f"{url}/ui/c/loose/panel", params={"tab": "cost"}).text
    copied = httpx.get(f"{url}/ui/c/loose/article.md")

    assert page.status_code == 200 and 'data-loose="true"' in page.text and "不在對話裡的工單" in page.text
    assert "分析報告 v1" in thread and "文章 v2" in thread
    assert "作者在說什麼" in report and "有爭議" in report
    assert "結論先講" in article and '<ins class="ch-ins">' in article  # 跟 v1 對照，沿著 parent
    assert "分析 v1" in cost and "US$0.20" in cost and "對話本身" not in cost
    assert copied.status_code == 200 and copied.text == ARTICLE_V2


def test_不在對話裡的頁面沒有輸入框和事件串流可用(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, host = world
    _loose_world(folder)

    page = httpx.get(f"{url}/c/loose").text

    assert re.search(r'<div class="ch-compose" hidden>', page)
    assert host.live_pid("loose") is None  # 沒有為它開 Pi


def test_換角色是新的一條鏈_面板的對照只跟自己的上一版比(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    cid = folder.conversation()
    folder.analysis(cid, "a1")
    folder.article(cid, "w1", parent="a1", persona="me", text=ARTICLE_V1, minutes=1)
    folder.article(cid, "w2", parent="w1", persona="b2b", text=ARTICLE_V2, version=2, minutes=2)

    html = httpx.get(f"{url}/ui/c/{cid}/panel", params={"tab": "article", "v": "w2"}).text

    assert "文章 2 v1" in html and "對照" not in html  # b2b 另開一篇，沒有上一版可比


# --- 上傳字數上限 ---------------------------------------------------------------------------


def test_上傳抽出來太長就說明字數與上限_不悄悄截掉(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, _, _ = world

    over = _upload(url, "long.txt", ("字" * 20_001).encode())
    exact = _upload(url, "ok.txt", ("字" * 20_000).encode())

    assert over.status_code == 413
    assert "20,001" in over.json()["error"] and "20,000" in over.json()["error"]
    assert exact.status_code == 200 and exact.json()["chars"] == 20_000


def test_面板的版本參數不能挑到別的對話的工單(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    mine, other = folder.conversation("甲"), folder.conversation("乙")
    folder.analysis(mine, "a1")
    folder.analysis(other, "b1")

    html = httpx.get(f"{url}/ui/c/{mine}/panel", params={"tab": "report", "v": "b1"}).text

    assert 'value="a1" selected' in html and "b1" not in html


def test_只有舊配方的單有花費_清單照樣有不在對話裡的列_花費分頁列得出來(world: tuple[str, ChatFolder, ConversationHost]) -> None:
    url, folder, _ = world
    folder.job(None, "20261013-090000-echo", recipe="echo")
    folder.ledger("20261013-090000-echo", [{"stage": "echo", "tier": "strong", "model": "c", "cost_usd": 0.05}])

    listed = httpx.get(f"{url}/ui/list").text
    cost = httpx.get(f"{url}/ui/c/loose/panel", params={"tab": "cost"}).text
    bills_page = httpx.get(f"{url}/bills", params={"month": "2026-10"}).text

    assert "不在對話裡的工單" in listed and "US$0.05" in listed
    assert "20261013-090000-echo" in cost and "US$0.05" in cost
    assert "US$0.05" in bills_page
