"""看板 `/`：五欄、卡片內容、待審張數、輪詢（S0 UI IA §3、SEAM-08）。

一律用 `TestClient` 對著手寫的假資料夾渲染，不經過執行器。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evidence_forge.paths import DataDir
from ui_folder import Folder
from evidence_forge.ui.app import create_app


def client_for(folder: Folder) -> TestClient:
    return TestClient(create_app(folder.data, now=lambda: folder.now))


def column(html: str, name: str) -> str:
    """取出 `data-col="<name>"` 那一欄的 HTML。"""
    match = re.search(
        rf'<section class="s0-col" data-col="{name}".*?</section>', html, re.DOTALL
    )
    assert match, f"找不到 {name} 欄"
    return match.group(0)


@pytest.mark.promise("FR-UI-01")
def test_a_queued_job_shows_up_in_the_queued_column_with_its_title(
    folder: Folder,
) -> None:
    folder.job("job-a", status="queued", title="向量資料庫的索引怎麼選")

    html = client_for(folder).get("/").text

    assert "向量資料庫的索引怎麼選" in column(html, "queued")


@pytest.mark.promise("NFR-RUN-02")
def test_board_has_five_columns_in_flow_order(folder: Folder) -> None:
    folder.job("j", status="queued", title="x")
    html = client_for(folder).get("/").text

    order = re.findall(r'<section class="s0-col" data-col="(\w+)"', html)
    assert order == ["queued", "running", "review", "approved", "discarded"]
    headings = re.findall(r'<h2 class="s0-col__title"[^>]*>.*?</svg>(.*?)</h2>', html)
    assert headings == ["排隊", "執行中", "待審", "通過", "丟棄"]


@pytest.mark.promise("NFR-OBS-02")
def test_running_card_shows_stage_elapsed_waiting_and_spend(folder: Folder) -> None:
    folder.job(
        "run-1",
        status="running",
        title="長上下文會不會取代檢索",
        stage=folder.stage(3, 4, "compose", seconds_ago=72),
        waiting={"kind": "model", "since": folder.at(seconds_ago=30).isoformat()},
    )
    folder.ledger("run-1", [{"cost_usd": 0.21, "stage": "digest"}])

    card = column(client_for(folder).get("/").text, "running")

    assert "3／4" in card
    assert "二創" in card
    assert "1:12" in card  # 這段跑了 72 秒
    assert "等模型回應" in card
    assert "US$0.21 / 2.00" in card


@pytest.mark.promise("NFR-OBS-02")
@pytest.mark.parametrize(
    ("waiting", "label"),
    [
        ({"kind": "gpu"}, "等 GPU"),
        ({"kind": "rate_limit"}, "等限流"),
        ({"kind": "model"}, "等模型回應"),
        ({"kind": "tool", "tool": "fetch_url"}, "抓取網頁中"),
        ({"kind": "tool", "tool": "mystery_tool"}, "執行工具中：mystery_tool"),
    ],
)
def test_running_card_says_what_it_is_waiting_for(
    folder: Folder, waiting: dict[str, str], label: str
) -> None:
    folder.job(
        "run-1",
        status="running",
        title="t",
        stage=folder.stage(1, 3, "digest", seconds_ago=5),
        waiting={**waiting, "since": folder.at(seconds_ago=3).isoformat()},
    )

    assert label in column(client_for(folder).get("/").text, "running")


@pytest.mark.promise("NFR-OBS-02")
def test_running_card_without_waiting_shows_no_waiting_badge(folder: Folder) -> None:
    folder.job(
        "run-1",
        status="running",
        title="t",
        stage=folder.stage(1, 3, "digest", seconds_ago=5),
    )

    card = column(client_for(folder).get("/").text, "running")

    assert "等" not in card.replace("等待", "")


def test_queued_card_shows_how_long_it_has_waited_and_rerun_note(
    folder: Folder,
) -> None:
    folder.job("q-1", status="queued", title="小模型做摘要的評測方法", draft_version=1)
    folder.events("q-1", [folder.status_event("q-1", "queued", minutes_ago=3)])

    card = column(client_for(folder).get("/").text, "queued")

    assert "排隊" in card and "3 分鐘" in card
    assert "手動" in card
    assert "退件重跑，將產生 v2" in card


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize(
    ("result", "text"),
    [
        ({"outcome": "complete"}, "完成"),
        ({"outcome": "incomplete", "reason": "預算用完"}, "未完成：預算用完"),
        ({"outcome": "failed", "reason": "Ollama 沒回應"}, "失敗：Ollama 沒回應"),
    ],
)
def test_review_card_shows_result_label_with_reason_cost_and_version(
    folder: Folder, result: dict[str, str], text: str
) -> None:
    folder.job("r-1", status="review", title="t", result=result, draft_version=2)
    folder.ledger("r-1", [{"cost_usd": 0.29}])

    card = column(client_for(folder).get("/").text, "review")

    assert text in card
    assert "US$0.29" in card
    assert "v2" in card
    assert f'data-tone="{result["outcome"]}"' in card


@pytest.mark.promise("FR-UI-01")
def test_page_title_and_review_column_show_the_number_waiting_for_review(
    folder: Folder,
) -> None:
    for n in range(3):
        folder.job(
            f"r-{n}", status="review", title=f"t{n}", result={"outcome": "complete"}
        )
    folder.job("q", status="queued", title="q")

    html = client_for(folder).get("/").text

    assert "<title>(3) Evidence Forge</title>" in html
    assert "3 張等你" in column(html, "review")
    assert "待審 3" in html


def test_page_title_has_no_number_when_nothing_waits(folder: Folder) -> None:
    folder.job("q", status="queued", title="q")

    html = client_for(folder).get("/").text

    assert "<title>Evidence Forge</title>" in html


@pytest.mark.promise("FR-UI-01")
def test_approved_and_discarded_columns_show_only_the_latest_20(folder: Folder) -> None:
    for n in range(25):
        folder.job(f"a-{n:02d}", status="approved", title=f"通過第{n:02d}篇")
        folder.events(
            f"a-{n:02d}",
            [folder.status_event(f"a-{n:02d}", "approved", minutes_ago=100 - n)],
        )

    card = column(client_for(folder).get("/").text, "approved")

    assert card.count('class="s0-card"') == 20
    assert "通過第24篇" in card and "通過第04篇" not in card
    assert "只顯示最近 20 張" in card
    assert ">25<" in card  # 欄頭張數是全部


def test_review_card_flags_low_genre_confidence_and_uncalibrated_target(
    folder: Folder,
) -> None:
    folder.job(
        "r-1",
        status="review",
        title="t",
        result={"outcome": "complete"},
        target_genre="explainer",
    )
    folder.json_file(
        "r-1",
        "stages/digest/1/claims.json",
        {"genre": {"primary": "opinion", "confidence": 0.4}, "claims": []},
    )

    card = column(client_for(folder).get("/").text, "review")

    assert "文類判斷信心低" in card
    assert "目標文類未校準" in card


def test_calibrated_target_genre_and_confident_digest_raise_no_reminder(
    folder: Folder,
) -> None:
    style = folder.data.authors / "me" / "genres"
    style.mkdir(parents=True)
    (style / "explainer.md").write_text("風格", encoding="utf-8")
    folder.job(
        "r-1",
        status="review",
        title="t",
        result={"outcome": "complete"},
        target_genre="explainer",
    )
    folder.json_file(
        "r-1",
        "stages/digest/1/claims.json",
        {"genre": {"primary": "explainer", "confidence": 0.86}, "claims": []},
    )

    card = column(client_for(folder).get("/").text, "review")

    assert "文類判斷信心低" not in card
    assert "未校準" not in card


@pytest.mark.promise("FR-UI-01")
def test_empty_data_dir_shows_empty_state_with_new_job_button(folder: Folder) -> None:
    html = client_for(folder).get("/").text

    assert "還沒有工單" in html
    assert 'href="/jobs/new"' in html


@pytest.mark.promise("FR-UI-01")
def test_missing_data_dir_says_which_path_and_why(tmp_path: Path) -> None:
    missing = DataDir(tmp_path / "does-not-exist")
    app = create_app(missing)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "讀不到資料目錄" in response.text
    assert str(missing.root) in response.text
    assert "資料夾不存在" in response.text


def test_one_broken_job_md_does_not_take_the_board_down(folder: Folder) -> None:
    folder.job("good", status="queued", title="好的那張")
    bad = folder.data.job_dir("bad")
    bad.mkdir(parents=True)
    (bad / "job.md").write_text("這不是 frontmatter", encoding="utf-8")

    html = client_for(folder).get("/").text

    assert "好的那張" in html
    assert "有一張單讀不懂" in html and "bad" in html


@pytest.mark.promise("FR-UI-01")
def test_board_fragment_polls_every_5_seconds_and_refreshes_title_and_nav(
    folder: Folder,
) -> None:
    folder.job("r", status="review", title="t", result={"outcome": "complete"})
    client = client_for(folder)

    page = client.get("/").text
    fragment = client.get("/board").text

    assert 'hx-trigger="every 5s"' in page
    assert 'hx-get="/board"' in page
    assert "<html" not in fragment
    assert "<title>(1) Evidence Forge</title>" in fragment
    assert 'id="nav-count"' in fragment and 'hx-swap-oob="true"' in fragment


@pytest.mark.promise("FR-UI-01")
def test_viewing_the_board_changes_nothing_in_the_data_dir(folder: Folder) -> None:
    folder.job("r", status="review", title="t", result={"outcome": "complete"})
    folder.job(
        "run", status="running", title="t2", stage=folder.stage(1, 3, "digest", 5)
    )
    before = {p: p.read_bytes() for p in folder.data.root.rglob("*") if p.is_file()}
    client = client_for(folder)

    client.get("/")
    client.get("/board")

    after = {p: p.read_bytes() for p in folder.data.root.rglob("*") if p.is_file()}
    assert after == before
