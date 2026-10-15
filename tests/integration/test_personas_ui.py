"""角色頁（`/personas`、`/personas/<角色>`、`/api/personas…`，#116、ADR-0026 §3.3）。

跟聊天室同一個程序、同源送出，真的開在 127.0.0.1 的空埠上；Pi 是假 Pi，這幾頁用不到它。
ADR-0014：角色全是為測試編的；`CANARY` 放在不該被讀到的地方，出現在回應裡就是外洩。
"""

import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_folder import RECIPES, ChatFolder, at
from chat_world import fake_chat_harness

from evidence_forge.chat.api import create_app
from evidence_forge.chat.desk import Desk
from evidence_forge.chat.host import ConversationHost
from evidence_forge.documents import read_frontmatter
from evidence_forge.gateway.active import ActiveStages

CANARY = "暗號-不該被讀到"


@pytest.fixture
def world(tmp_path: Path, serve: Any) -> Iterator[tuple[str, ChatFolder]]:
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
            yield url, folder
    finally:
        host.close()


def _write(folder: ChatFolder, name: str, files: dict[str, str]) -> Path:
    root = folder.data.authors / name
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


FULL = {
    "voice.md": "---\nid: me\nname: 假筆名\ndefault_genre: essay\ncalibrated: false\n---\n\n## 語氣\n\n冷靜，**不誇張**。\n",
    "genres/essay.md": "---\ncalibrated: true\n---\n\n## 要多帶的價值\n\n假價值。\n",
    "craft/titles.md": "# 標題\n\n假的標題規則。\n",
    "themes.md": "---\nthemes: [education]\n---\n\n## 用哪幾個主題\n",
    "lessons.md": "# 規則\n\n- 2026-10-01｜第 1 篇｜甲 → 乙\n- 2026-10-02｜第 2 篇｜丙 → 丁\n",
    "brand.yaml": 'name: "假品牌"\npalette:\n  accent: "#123456"\n',
}


def _post(url: str, path: str, body: Any, **kwargs: Any) -> httpx.Response:
    return httpx.post(f"{url}{path}", json=body, **kwargs)


# --- 頁面 ---------------------------------------------------------------------


def test_導覽列有角色_在帳單與設定旁邊(world: tuple[str, ChatFolder]) -> None:
    url, _ = world
    for path in ("/personas", "/bills", "/settings"):
        html = httpx.get(f"{url}{path}").text
        assert '<a href="/personas"' in html
        assert html.index('href="/personas"') < html.index('href="/bills"') < html.index('href="/settings"')
    assert 'aria-current="page"' in re.search(r'<a href="/personas"[^>]*>', httpx.get(f"{url}/personas").text).group(0)  # type: ignore[union-attr]


def test_清單頁_沒有角色時說明怎麼辦_還是有新增表單(world: tuple[str, ChatFolder]) -> None:
    url, _ = world
    page = httpx.get(f"{url}/personas")
    assert page.status_code == 200
    assert "還沒有角色" in page.text
    assert 'id="persona-create"' in page.text


def test_清單頁_列出每個角色_顯示名稱_篇數_缺什麼(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    _write(folder, "bare", {"voice.md": "只有聲音\n"})
    cid = folder.conversation()
    folder.article(cid, "w1", persona="me")
    folder.article(cid, "w2", persona="me", parent="w1", version=2)
    assert _post(url, "/api/personas/me/rename", {"display_name": "我自己"}).status_code == 200

    html = httpx.get(f"{url}/personas").text

    assert 'href="/personas/me"' in html and "我自己" in html
    assert 'href="/personas/bare"' in html
    me = html[html.index('data-persona="me"') : html.index('data-persona="bare"')] if html.index('data-persona="me"') < html.index('data-persona="bare"') else html[html.index('data-persona="me"') :]
    assert "1 篇" in me and "2 版" in me
    bare = html[html.index('data-persona="bare"') :]
    assert "手藝" in bare and "品牌設定" in bare  # 缺的標出來
    assert "每一部分都有" in me


def test_詳細頁_每個檔唯讀顯示_Markdown_轉成_HTML_brand_是表格(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)

    page = httpx.get(f"{url}/personas/me")

    assert page.status_code == 200
    html = page.text
    assert "<h2>語氣</h2>" in html and "<strong>不誇張</strong>" in html
    assert "假價值" in html and "假的標題規則" in html
    assert "palette.accent" in html and "#123456" in html and "假品牌" in html  # brand.yaml 的表
    assert "<table" in html
    assert "2026-10-02｜第 2 篇｜丙 → 丁" in html
    assert html.index("丙 → 丁") < html.index("甲 → 乙")  # lessons 新的在前
    assert "校準過" in html
    # 唯讀：沒有任何編輯檔案的入口
    assert "<textarea" not in html and 'contenteditable' not in html


def test_詳細頁_缺的部分標出來_並說明用什麼預設(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "bare", {"voice.md": "只有聲音\n"})

    html = httpx.get(f"{url}/personas/bare").text

    assert "缺了 5 個部分，先用通用預設" in html
    assert "用通用預設：" in html
    assert "headline-punchline-system.md" in html  # craft 缺時的預設
    assert "用中性的預設值" in html  # brand 缺時的預設
    assert "標「未校準」" in html  # genres 缺時的預設


def test_詳細頁_沒有聲音_標必要(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "novoice", {"themes.md": "x"})
    html = httpx.get(f"{url}/personas/novoice").text
    assert "必要" in html


def test_詳細頁_原始_HTML_不放行(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "xss", {"voice.md": "## 語氣\n\n<script>alert(1)</script>\n"})
    html = httpx.get(f"{url}/personas/xss").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_詳細頁_連到角色資料夾外面的檔_不讀_內容不外洩(world: tuple[str, ChatFolder], tmp_path: Path) -> None:
    url, folder = world
    outside = tmp_path / "outside.md"
    outside.write_text(CANARY, encoding="utf-8")
    root = _write(folder, "me", FULL)
    (root / "themes.md").unlink()
    (root / "themes.md").symlink_to(outside)
    (root / "lessons.md").unlink()
    (root / "lessons.md").symlink_to(outside)
    (root / "craft" / "leak.md").symlink_to(outside)

    page = httpx.get(f"{url}/personas/me")

    assert page.status_code == 200
    assert CANARY not in page.text
    assert "指到角色資料夾之外" in page.text


# --- 路徑穿越 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/personas/%2e%2e",
        "/personas/..%2Fsecret",
        "/personas/%2e%2e%2f%2e%2e%2fetc",
        "/personas/a%2Fb",
        "/personas/.hidden",
        "/personas/_template",
        "/personas/ghost",
        "/personas/a%00b",
    ],
)
def test_詳細頁_角色名是路徑穿越或不存在_404_不讀任何檔(world: tuple[str, ChatFolder], tmp_path: Path, path: str) -> None:
    url, folder = world
    (folder.data.root / "secret").mkdir()
    (folder.data.root / "secret" / "voice.md").write_text(CANARY, encoding="utf-8")
    (folder.data.authors / ".hidden").mkdir(parents=True, exist_ok=True)
    (folder.data.authors / ".hidden" / "voice.md").write_text(CANARY, encoding="utf-8")

    response = httpx.get(f"{url}{path}")

    assert response.status_code in (400, 404)
    assert CANARY not in response.text


def test_詳細頁_原樣送出的點點_不被當成角色名_也不讀任何檔(world: tuple[str, ChatFolder]) -> None:
    # httpx 會先把 `/personas/..` 化簡成 `/`，所以這裡用 socket 把請求行原樣送出
    url, folder = world
    (folder.data.root / "secret").mkdir()
    (folder.data.root / "secret" / "voice.md").write_text(CANARY, encoding="utf-8")
    port = int(url.rsplit(":", 1)[1])
    for target in ("/personas/..", "/personas/../secret", "/personas/./", "/personas//"):
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
            raw.sendall(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
            reply = b""
            while chunk := raw.recv(65536):
                reply += chunk
        status = int(reply.split(b" ", 2)[1])
        assert status in (200, 307, 400, 404), (target, status)  # 200 是聊天室外框、不是角色頁
        assert CANARY.encode() not in reply
        assert b'data-display-name' not in reply  # 不是某個角色的詳細頁


def test_詳細頁_角色資料夾是符號連結_404_不讀它指到的地方(world: tuple[str, ChatFolder], tmp_path: Path) -> None:
    url, folder = world
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.md").write_text(CANARY, encoding="utf-8")
    folder.data.authors.mkdir(exist_ok=True)
    (folder.data.authors / "evil").symlink_to(outside, target_is_directory=True)

    response = httpx.get(f"{url}/personas/evil")
    listing = httpx.get(f"{url}/personas")

    assert response.status_code == 404
    assert CANARY not in response.text and CANARY not in listing.text
    assert 'href="/personas/evil"' not in listing.text  # 清單也不給連結


# --- 清單 API 跟對話的 list_personas 是同一份 ---------------------------------


def test_api_personas_就是對話_list_personas_的清單_多一欄顯示名稱(
    world: tuple[str, ChatFolder], tmp_path: Path
) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    _write(folder, "bare", {"voice.md": "x"})
    _post(url, "/api/personas/me/rename", {"display_name": "我自己"})

    reply = httpx.get(f"{url}/api/personas")

    desk = Desk(folder.data, RECIPES, "analyze", clock=lambda: at())
    from_chat = desk.personas()["personas"]  # 閘道的 list_personas 工具回的就是這份
    assert reply.status_code == 200
    stripped = [{k: v for k, v in p.items() if k != "display_name"} for p in reply.json()["personas"]]
    assert stripped == from_chat
    assert {p["name"]: p["display_name"] for p in reply.json()["personas"]} == {"bare": None, "me": "我自己"}


def test_清單頁與清單_api_外面檔案的預設文體不外洩_連結資料夾與連結檔都不讀(
    world: tuple[str, ChatFolder], tmp_path: Path
) -> None:
    url, folder = world
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.md").write_text("---\ndefault_genre: 暗號-外面的文類\n---\n", encoding="utf-8")
    folder.data.authors.mkdir(exist_ok=True)
    (folder.data.authors / "linked").symlink_to(outside, target_is_directory=True)
    real = _write(folder, "real", {"themes.md": "x"})
    (real / "voice.md").symlink_to(outside / "voice.md")

    page = httpx.get(f"{url}/personas")
    api = httpx.get(f"{url}/api/personas")

    for response in (page, api):
        assert response.status_code == 200
        assert "暗號" not in response.text
        assert "預設文體 暗號" not in response.text
    assert [p["default_genre"] for p in api.json()["personas"]] == [None, None]
    assert [p.get("note") for p in api.json()["personas"]] == [None, None]


# --- 新增 ---------------------------------------------------------------------


def test_新增_從範本_201_建了聲音檔與顯示名稱_然後頁面打得開(world: tuple[str, ChatFolder]) -> None:
    url, folder = world

    reply = _post(url, "/api/personas", {"name": "writer-2", "display_name": "第二支筆"})

    assert reply.status_code == 201
    assert reply.json() == {"persona": "writer-2", "files": ["voice.md", "persona.json"]}
    assert read_frontmatter(folder.data.authors / "writer-2" / "voice.md")["id"] == "writer-2"
    page = httpx.get(f"{url}/personas/writer-2")
    assert page.status_code == 200 and "第二支筆" in page.text
    assert "writer-2" in httpx.get(f"{url}/personas").text
    # 對話的 list_personas 也看得到它：兩邊是同一份清單
    names = [p["name"] for p in httpx.get(f"{url}/api/personas").json()["personas"]]
    assert names == ["writer-2"]


def test_新增_已經有這個名字_409_既有的檔一個字都不動(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    root = _write(folder, "me", FULL)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    for body in ({"name": "me"}, {"name": "me", "from": "me"}, {"name": "me", "display_name": "新的"}):
        reply = _post(url, "/api/personas", body)
        assert reply.status_code == 409
        assert "不會蓋掉" in reply.json()["error"]

    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_新增_複製既有角色_帶走檔案_各自獨立(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    source = _write(folder, "me", FULL)

    reply = _post(url, "/api/personas", {"name": "me-2", "from": "me", "display_name": "副本"})

    assert reply.status_code == 201
    assert reply.json()["persona"] == "me-2" and reply.json()["files"] == len(FULL)
    copy = folder.data.authors / "me-2"
    assert (copy / "craft" / "titles.md").read_bytes() == (source / "craft" / "titles.md").read_bytes()
    assert read_frontmatter(copy / "voice.md")["id"] == "me-2"
    # 複本與原角色互不影響
    (copy / "craft" / "titles.md").write_text("改過了", encoding="utf-8")
    assert "假的標題規則" in (source / "craft" / "titles.md").read_text(encoding="utf-8")
    assert "副本" in httpx.get(f"{url}/personas/me-2").text


def test_新增_複製時帶不走的符號連結_回報出來(world: tuple[str, ChatFolder], tmp_path: Path) -> None:
    url, folder = world
    outside = tmp_path / "outside.md"
    outside.write_text(CANARY, encoding="utf-8")
    source = _write(folder, "me", FULL)
    (source / "craft" / "leak.md").symlink_to(outside)

    reply = _post(url, "/api/personas", {"name": "me-2", "from": "me"})

    assert reply.status_code == 201
    assert reply.json()["skipped_links"] == ["craft/leak.md"]
    assert not (folder.data.authors / "me-2" / "craft" / "leak.md").exists()


@pytest.mark.parametrize("name", ["..", "../x", "a/b", "", ".x", "_x", "a b", "x" * 200, "小明", "a..b"])
def test_新增_名字不合規則_400_什麼都沒建(world: tuple[str, ChatFolder], tmp_path: Path, name: str) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    before = sorted(p.name for p in folder.data.authors.iterdir())

    for body in ({"name": name}, {"name": name, "from": "me"}):
        reply = _post(url, "/api/personas", body)
        assert reply.status_code == 400, body
        assert "error" in reply.json()

    assert sorted(p.name for p in folder.data.authors.iterdir()) == before
    assert not (tmp_path / "x").exists() and not (folder.data.root / "x").exists()


def test_新增_複製來源不合規則或不存在_不建目標(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    assert _post(url, "/api/personas", {"name": "dup", "from": "ghost"}).status_code == 404
    assert _post(url, "/api/personas", {"name": "dup", "from": "../me"}).status_code == 400
    assert _post(url, "/api/personas", {"name": "dup", "from": "/etc"}).status_code == 400
    assert not (folder.data.authors / "dup").exists()


@pytest.mark.parametrize(
    "body",
    [{}, {"name": 5}, {"name": None}, {"name": "a", "from": 5}, {"name": "a", "display_name": ""}, {"name": "a", "display_name": "x" * 41}],
)
def test_新增_欄位型別或內容不對_400(world: tuple[str, ChatFolder], body: dict[str, Any]) -> None:
    url, folder = world
    reply = _post(url, "/api/personas", body)
    assert reply.status_code == 400
    assert not folder.data.authors.exists() or not (folder.data.authors / "a").exists()


def test_新增_請求不是_JSON_物件_400(world: tuple[str, ChatFolder]) -> None:
    url, _ = world
    assert httpx.post(f"{url}/api/personas", content="不是 json", headers={"content-type": "application/json"}).status_code == 400
    assert _post(url, "/api/personas", ["name"]).status_code == 400


# --- 改顯示名稱 ---------------------------------------------------------------


def test_改顯示名稱_只改畫面上的名字_資料夾名字與檔案不動(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    root = _write(folder, "me", FULL)
    voice = (root / "voice.md").read_bytes()

    reply = _post(url, "/api/personas/me/rename", {"display_name": " 新名字 "})

    assert reply.status_code == 200
    assert reply.json() == {"persona": "me", "display_name": "新名字"}
    assert sorted(p.name for p in folder.data.authors.iterdir()) == ["me"]
    assert (root / "voice.md").read_bytes() == voice
    assert "新名字" in httpx.get(f"{url}/personas/me").text
    assert "新名字" in httpx.get(f"{url}/personas").text


@pytest.mark.parametrize("body", [{}, {"display_name": ""}, {"display_name": "   "}, {"display_name": "x" * 41}, {"display_name": "a\nb"}, {"display_name": 5}])
def test_改顯示名稱_不合規則_400_沒有寫檔(world: tuple[str, ChatFolder], body: dict[str, Any]) -> None:
    url, folder = world
    root = _write(folder, "me", FULL)
    assert _post(url, "/api/personas/me/rename", body).status_code == 400
    assert not (root / "persona.json").exists()


@pytest.mark.parametrize("name", ["ghost", "..", ".hidden", "a%2Fb", "%2e%2e"])
def test_改顯示名稱_角色不存在或名字不合規則_沒有寫任何檔(world: tuple[str, ChatFolder], tmp_path: Path, name: str) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    reply = _post(url, f"/api/personas/{name}/rename", {"display_name": "x"})
    assert reply.status_code in (400, 404)
    assert not list(tmp_path.rglob("persona.json"))


def test_改顯示名稱_角色資料夾是符號連結_不寫到它指的地方(world: tuple[str, ChatFolder], tmp_path: Path) -> None:
    url, folder = world
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.data.authors.mkdir(exist_ok=True)
    (folder.data.authors / "evil").symlink_to(outside, target_is_directory=True)
    assert _post(url, "/api/personas/evil/rename", {"display_name": "x"}).status_code == 400
    assert not (outside / "persona.json").exists()


# --- 只收本機、同源、JSON -----------------------------------------------------


def test_只收本機_別的主機名稱與跨站來源都擋(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    for method, path, body in (
        ("GET", "/personas", None),
        ("GET", "/personas/me", None),
        ("GET", "/api/personas", None),
        ("POST", "/api/personas", {"name": "evil"}),
        ("POST", "/api/personas/me/rename", {"display_name": "evil"}),
    ):
        for headers in ({"host": "evil.example"}, {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            reply = httpx.request(method, f"{url}{path}", json=body, headers=headers)
            assert reply.status_code == 403, (method, path, headers)
    assert not (folder.data.authors / "evil").exists()
    assert not (folder.data.authors / "me" / "persona.json").exists()


def test_POST_不是_JSON_就拒絕_表單偷送不行(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    for path, data in (("/api/personas", {"name": "evil"}), ("/api/personas/me/rename", {"display_name": "evil"})):
        assert httpx.post(f"{url}{path}", data=data).status_code == 415
        assert httpx.post(f"{url}{path}", content="{}", headers={"content-type": "text/plain"}).status_code == 415
    assert not (folder.data.authors / "evil").exists()


def test_建立與改名的回應不含資料目錄的絕對路徑(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _write(folder, "me", FULL)
    texts = [
        _post(url, "/api/personas", {"name": "me"}).text,
        _post(url, "/api/personas", {"name": "..", }).text,
        _post(url, "/api/personas/ghost/rename", {"display_name": "x"}).text,
        httpx.get(f"{url}/personas/ghost").text,
    ]
    assert all(str(folder.data.root) not in text for text in texts)
