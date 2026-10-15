"""費用設定頁（`/settings`、`POST /api/settings/costs`）與帳單頁頂部的每月上限（#115、FR-LG-03、ADR-0027 §3.6）。

跟聊天室同一個程序、同源送出，真的開在 127.0.0.1 的空埠上；Pi 是假 Pi，這幾頁用不到它。
"""

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat_folder import RECIPES, ChatFolder, at
from chat_world import fake_chat_harness

from evidence_forge import cost_settings
from evidence_forge.chat.api import create_app
from evidence_forge.chat.host import ConversationHost
from evidence_forge.cost_settings import CostSettings
from evidence_forge.gateway.active import ActiveStages


@pytest.fixture
def world(tmp_path: Path, serve: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, ChatFolder]]:
    # 開發機上可能設了這個變數：測試從「沒設」開始
    monkeypatch.delenv("EF_MONTHLY_BUDGET_USD", raising=False)
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


def _spend(folder: ChatFolder, usd: float) -> None:
    """這個月（頁面用真的時鐘）花了 `usd`。"""
    cid = folder.conversation("花錢的對話")
    folder.analysis(cid, "a1")
    now = datetime.now().astimezone().isoformat()
    folder.ledger("a1", [{"ts": now, "stage": "investigate", "tier": "strong", "model": "c", "cost_usd": usd}])


def _changes(folder: ChatFolder) -> list[dict[str, Any]]:
    path = folder.data.root / "settings" / "changes.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


ALL_FIELDS = {
    "monthly_cap_usd": 12.5,
    "analysis_budget_usd": 0.75,
    "rewrite_budget_usd": 1.0,
    "image_cap_usd": 0.1,
}


@pytest.mark.promise("FR-LG-03")
def test_設定頁_沒有設定檔_不設每月上限_本月花費_供應商後台連結_預設數字(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _spend(folder, 0.42)

    page = httpx.get(f"{url}/settings")

    assert page.status_code == 200
    html = page.text
    assert "未設每月上限" in html
    assert "US$0.42" in html
    for console in ("console.anthropic.com", "platform.openai.com", "app.tavily.com"):
        assert f'href="https://{console}' in html, console
    # 單張預算照現在的預設 US$0.5、生圖每張 US$0.25
    assert 'name="analysis_budget_usd" value="0.5"' in html
    assert 'name="rewrite_budget_usd" value="0.5"' in html
    assert 'name="image_cap_usd" value="0.25"' in html
    assert re.search(r'value="none"[^>]*checked', html)
    assert "/static/js/settings.js" in html


@pytest.mark.promise("FR-LG-03")
def test_存設定_寫進設定檔_每個欄位記一筆改動_頁面顯示新的值(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _spend(folder, 2.5)

    saved = httpx.post(f"{url}/api/settings/costs", json=ALL_FIELDS)

    assert saved.status_code == 200
    assert saved.json()["settings"] == ALL_FIELDS
    assert cost_settings.load(folder.data).settings == CostSettings(**ALL_FIELDS)
    assert [(c["field"], c["old"], c["new"]) for c in _changes(folder)] == [
        ("monthly_cap_usd", None, 12.5),
        ("analysis_budget_usd", 0.5, 0.75),
        ("rewrite_budget_usd", 0.5, 1.0),
        ("image_cap_usd", 0.25, 0.1),
    ]
    html = httpx.get(f"{url}/settings").text
    assert 'name="monthly_cap_usd" value="12.5"' in html
    assert "剩 US$10.00" in html


@pytest.mark.promise("FR-LG-03")
def test_存設定_每月上限可以改回不設_只給要改的欄位也行(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    cost_settings.save(folder.data, CostSettings(monthly_cap_usd=10.0, rewrite_budget_usd=0.9), now=at())

    saved = httpx.post(f"{url}/api/settings/costs", json={"monthly_cap_usd": None})

    assert saved.status_code == 200
    assert cost_settings.load(folder.data).settings == CostSettings(rewrite_budget_usd=0.9)


@pytest.mark.promise("FR-LG-03")
@pytest.mark.parametrize(
    "body",
    [
        {"monthly_cap_usd": -1},
        {"analysis_budget_usd": "1.5"},
        {"rewrite_budget_usd": True},
        {"image_cap_usd": None},
        {"monthly_cap_usd": 1e400},
        {"monthly_budget": 5},
        [1, 2],
    ],
    ids=["負數", "字串", "布林", "生圖上限不能不設", "無限大", "不認得的欄位", "不是物件"],
)
def test_存設定_不合法的值都拒絕_設定檔與改動紀錄不動(world: tuple[str, ChatFolder], body: Any) -> None:
    url, folder = world

    refused = httpx.post(
        f"{url}/api/settings/costs",
        content=json.dumps(body).replace("Infinity", "1e400"),
        headers={"content-type": "application/json"},
    )

    assert refused.status_code == 400
    assert refused.json()["error"]
    assert not (folder.data.root / "settings" / "costs.json").exists()
    assert _changes(folder) == []


@pytest.mark.promise("NFR-SEC-02")
def test_設定頁與存設定只接受本機同源的請求_表單偷送也擋(world: tuple[str, ChatFolder]) -> None:
    url, folder = world

    assert httpx.get(f"{url}/settings", headers={"host": "evil.example"}).status_code == 403
    assert httpx.get(f"{url}/settings", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    cross = httpx.post(f"{url}/api/settings/costs", json={"monthly_cap_usd": None}, headers={"origin": "https://evil.example"})
    assert cross.status_code == 403
    # 別的網站用不需要預檢的表單送：不是 application/json 就擋
    form = httpx.post(f"{url}/api/settings/costs", data={"monthly_cap_usd": "0"})
    assert form.status_code == 415
    assert not (folder.data.root / "settings" / "costs.json").exists()


@pytest.mark.promise("FR-LG-03")
def test_設定檔壞掉_設定頁標出來_數字用預設值(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    broken = folder.data.root / "settings" / "costs.json"
    broken.parent.mkdir(parents=True)
    broken.write_text('{"monthly_cap_usd": "很多"}', encoding="utf-8")

    page = httpx.get(f"{url}/settings")

    assert page.status_code == 200
    assert "costs.json 讀不懂" in page.text
    assert "未設每月上限" in page.text
    assert 'name="analysis_budget_usd" value="0.5"' in page.text


@pytest.mark.promise("FR-LG-03")
def test_環境變數蓋過每月上限_設定頁與帳單照它算並說明(
    world: tuple[str, ChatFolder], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, folder = world
    _spend(folder, 1.0)
    monkeypatch.setenv("EF_MONTHLY_BUDGET_USD", "3")

    settings = httpx.get(f"{url}/settings").text
    bills = httpx.get(f"{url}/bills").text

    assert "EF_MONTHLY_BUDGET_USD" in settings
    assert "剩 US$2.00" in settings and "剩 US$2.00" in bills


# --- 帳單頁頂部 --------------------------------------------------------------------------


@pytest.mark.promise("FR-LG-02", "FR-LG-03")
def test_帳單頁頂部_沒設每月上限就寫未設(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _spend(folder, 0.3)

    html = httpx.get(f"{url}/bills").text

    assert "未設每月上限" in html
    assert 'href="/settings"' in html


@pytest.mark.promise("FR-LG-02", "FR-LG-03")
def test_帳單頁頂部_有設每月上限就顯示剩多少_超過就是剩_0(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    _spend(folder, 0.3)
    cost_settings.save(folder.data, CostSettings(monthly_cap_usd=1.0), now=at())

    html = httpx.get(f"{url}/bills").text
    assert "每月上限 US$1.00" in html and "剩 US$0.70" in html

    cost_settings.save(folder.data, CostSettings(monthly_cap_usd=0.1), now=at())
    assert "剩 US$0.00" in httpx.get(f"{url}/bills").text


def test_帳單頁看以前的月份_不顯示剩多少(world: tuple[str, ChatFolder]) -> None:
    url, folder = world
    cost_settings.save(folder.data, CostSettings(monthly_cap_usd=1.0), now=at())

    html = httpx.get(f"{url}/bills", params={"month": "2026-01"}).text

    assert "剩 US$" not in html


def test_設定頁的欄位名跟設定檔的欄位一一對得上() -> None:
    from evidence_forge.ui.costs import FIELD_LABELS

    # 設定檔多一個欄位卻忘了在這裡給名字，存設定就會把它當成「不認得的欄位」拒絕
    assert set(FIELD_LABELS) == set(CostSettings.model_fields)
