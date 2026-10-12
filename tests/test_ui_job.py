"""工單詳情 `/jobs/<id>`：六個分頁、資訊欄、各種空與錯誤狀態（S0 UI IA §4、§7、SEAM-08）。"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from evidence_forge.ui.app import create_app
from ui_folder import Folder


def client_for(folder: Folder) -> TestClient:
    return TestClient(create_app(folder.data, now=lambda: folder.now))


def selected_tab(html: str) -> str:
    match = re.search(r'id="tab-(\w+)"[^>]*aria-selected="true"', html)
    assert match, "沒有選中的分頁"
    return match.group(1)


def panel(html: str) -> str:
    match = re.search(r'<div role="tabpanel".*?</aside>', html, re.DOTALL)
    assert match
    return match.group(0)


REVIEW_COMPLETE: dict[str, Any] = {"outcome": "complete"}


@pytest.mark.promise("FR-UI-01")
@pytest.mark.parametrize(
    ("status", "result", "expected"),
    [
        ("running", None, "events"),
        ("queued", None, "events"),
        ("review", {"outcome": "complete"}, "draft"),
        ("review", {"outcome": "incomplete", "reason": "預算用完"}, "checks"),
        ("review", {"outcome": "failed", "reason": "Ollama 沒回應"}, "events"),
        ("approved", {"outcome": "complete"}, "draft"),
    ],
)
def test_default_tab_depends_on_status_and_result(
    folder: Folder, status: str, result: dict[str, str] | None, expected: str
) -> None:
    stage = folder.stage(1, 3, "digest", 5) if status == "running" else None
    folder.job("j", status=status, title="t", result=result, stage=stage)

    html = client_for(folder).get("/jobs/j").text

    assert selected_tab(html) == expected


@pytest.mark.promise("FR-UI-01")
def test_all_six_tabs_are_links_with_a_tab_query(folder: Folder) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)

    html = client_for(folder).get("/jobs/j").text

    tabs = re.findall(r'role="tab" id="tab-(\w+)" href="/jobs/j\?tab=(\w+)"', html)
    assert tabs == [
        (t, t) for t in ("draft", "digest", "sources", "checks", "bill", "events")
    ]
    labels = re.findall(r'role="tab"[^>]*>(.*?)(?: <span|</a>)', html)
    assert labels == ["稿子", "整理結果", "來源與圖", "檢查結果", "帳單", "事件紀錄"]


def test_explicit_tab_query_overrides_the_default(folder: Folder) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)

    assert selected_tab(client_for(folder).get("/jobs/j?tab=bill").text) == "bill"
    assert selected_tab(client_for(folder).get("/jobs/j?tab=nonsense").text) == "draft"


@pytest.mark.promise("FR-UI-01")
def test_info_side_shows_status_goal_recipe_genre_spend_and_version(
    folder: Folder,
) -> None:
    folder.job(
        "j",
        status="review",
        title="t",
        result={"outcome": "incomplete", "reason": "預算用完"},
        goal="寫給剛開始做 RAG 的工程師",
        target_genre="explainer",
        draft_version=2,
        budget_usd=2.0,
    )
    folder.ledger("j", [{"cost_usd": 0.38}])

    html = client_for(folder).get("/jobs/j").text
    side = html[html.index("<aside") :]

    for expected in (
        "待審",
        "未完成：預算用完",
        "寫給剛開始做 RAG 的工程師",
        "restructure",
        "解說文",
        "未校準",
        "US$0.38 / 2.00",
        "v2",
    ):
        assert expected in side


def test_running_job_shows_stage_strip_from_the_recipe(
    folder: Folder, tmp_path: Any
) -> None:
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "restructure.md").write_text(
        "---\nname: restructure\ndescription: d\nstages:\n"
        "  - { id: digest, skill: digest, tier: local, output: [digest.md] }\n"
        "  - { id: compose, skill: compose, tier: strong, output: [draft.md] }\n"
        "  - { id: verify, skill: verify, tier: local, output: [checks.json] }\n"
        "on_feedback: compose\n---\n說明\n",
        encoding="utf-8",
    )
    folder.job(
        "j",
        status="running",
        title="t",
        stage=folder.stage(2, 3, "compose", seconds_ago=72),
    )
    folder.ledger("j", [{"stage": "digest", "latency_ms": 52_000}])
    app = create_app(folder.data, recipes_dir=recipes, now=lambda: folder.now)

    html = TestClient(app).get("/jobs/j").text

    states = re.findall(r'<li class="s0-stage" data-state="(\w+)"', html)
    assert states == ["done", "running", "todo"]
    assert "完成・0:52・本機" in html
    assert "進行中・1:12・雲端（強）" in html
    assert "還沒到" in html


@pytest.mark.promise("FR-UI-01")
def test_draft_tab_renders_markdown_and_lets_you_pick_a_version(folder: Folder) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE, draft_version=2)
    folder.draft("j", 1, "# 第一版\n\n舊的內容")
    folder.draft("j", 2, "# 第二版\n\n新的內容[1]")
    client = client_for(folder)

    latest = client.get("/jobs/j?tab=draft").text
    older = client.get("/jobs/j?tab=draft&v=1").text

    assert "<h1>第二版</h1>" in latest and "新的內容" in latest
    assert "<h1>第一版</h1>" in older and "第二版" not in panel(older)
    assert "v2（最新）" in latest and "v1" in latest


def test_draft_html_from_the_model_is_never_executed(folder: Folder) -> None:
    # 稿子由 LLM 照別人的文章寫成，不可信。
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE, draft_version=1)
    folder.draft("j", 1, "<script>alert(1)</script>\n\n[點我](javascript:alert(2))")

    body = panel(client_for(folder).get("/jobs/j?tab=draft").text)

    assert "<script>" not in body
    assert 'href="javascript:' not in body


def test_draft_images_are_not_rendered_so_opening_a_draft_never_phones_home(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE, draft_version=1)
    folder.draft("j", 1, "正文\n\n![追蹤](https://tracker.example/p.png)")

    body = panel(client_for(folder).get("/jobs/j?tab=draft").text)

    assert "<img" not in body


def test_item_path_in_job_md_cannot_point_outside_the_data_dir(
    folder: Folder, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "item.json").write_text(
        json.dumps(
            {
                "id": "x",
                "feed": "manual",
                "title": "資料目錄外面的標題",
                "fetched_at": folder.at(1).isoformat(),
                "content_hash": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    folder.job("j", status="queued")  # 沒有 title：不會蓋出 inbox 項目
    md = folder.data.job_dir("j") / "job.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace(
            "item: inbox/manual/j", f"item: {outside}"
        ),
        encoding="utf-8",
    )

    html = client_for(folder).get("/jobs/j").text

    assert "資料目錄外面的標題" not in html


def test_polling_a_running_job_keeps_the_draft_version_being_looked_at(
    folder: Folder,
) -> None:
    folder.job(
        "j",
        status="running",
        title="t",
        stage=folder.stage(3, 3, "compose", 5),
        draft_version=2,
    )
    folder.draft("j", 1, "舊")
    folder.draft("j", 2, "新")

    html = client_for(folder).get("/jobs/j?tab=draft&v=1").text

    assert 'hx-get="/jobs/j?tab=draft&amp;v=1"' in html


def test_digest_tab_lists_genre_claims_with_quotes_and_directions(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.json_file(
        "j",
        "stages/digest/1/claims.json",
        {"claims": [{"id": "OLD", "text": "舊的一輪"}]},
    )
    folder.json_file(
        "j",
        "stages/digest/2/claims.json",
        {
            "genre": {
                "primary": "opinion",
                "secondary": ["tutorial"],
                "confidence": 0.81,
            },
            "claims": [
                {
                    "id": "C-01",
                    "kind": "fact",
                    "text": "換模型後客訴沒有下降",
                    "quote": "原文一句話",
                    "location": 3,
                },
                {
                    "id": "C-02",
                    "kind": "opinion",
                    "text": "公開評測集不代表自己的使用者",
                    "quote": "另一句",
                },
            ],
            "research_directions": ["其他團隊自建評測集的維護成本"],
        },
    )

    body = panel(client_for(folder).get("/jobs/j?tab=digest").text)

    assert "主：觀點文" in body and "次：教學文" in body and "信心 0.81" in body
    assert "C-01" in body and "事實" in body and "換模型後客訴沒有下降" in body
    assert "「原文一句話」" in body and "原文 ¶3" in body
    assert "觀點" in body
    assert "其他團隊自建評測集的維護成本" in body
    assert "舊的一輪" not in body  # 只看最新一次嘗試


def test_digest_tab_shows_research_directions_written_by_the_digest_skill(
    folder: Folder,
) -> None:
    # digest skill 的 schema（S0.8）：研究方向是 {question, evidence_needed}
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.json_file(
        "j",
        "stages/digest/1/claims.json",
        {
            "claims": [],
            "research_directions": [
                {"question": "剩貨率怎麼算", "evidence_needed": "店家的每日產量紀錄"}
            ],
        },
    )

    body = panel(client_for(folder).get("/jobs/j?tab=digest").text)

    assert "剩貨率怎麼算" in body and "店家的每日產量紀錄" in body


def test_sources_tab_shows_where_the_article_came_from(folder: Folder) -> None:
    folder.job("j", status="review", title="一篇文章", result=REVIEW_COMPLETE)

    body = panel(client_for(folder).get("/jobs/j?tab=sources").text)

    assert "https://example.test/j" in body
    assert "2026-10-11 14:00" in body  # 抓取時間（NOW 往前 30 分鐘）


@pytest.mark.promise("FR-UI-01")
def test_checks_tab_groups_fail_warn_pass_with_three_different_shapes(
    folder: Folder,
) -> None:
    folder.job(
        "j",
        status="review",
        title="t",
        result={"outcome": "incomplete", "reason": "C3 照抄，修了 1 次仍沒過"},
    )
    folder.checks(
        "j",
        [
            {"check": "C1", "status": "pass", "reason": "7 條引句都在原文裡"},
            {"check": "C7", "status": "warn", "reason": "結論沒有寫出判斷條件"},
            {
                "check": "C3",
                "status": "fail",
                "reason": "重疊 62%，門檻 40%",
                "location": {"file": "drafts/draft.v1.md", "line": 12},
                "excerpt": "照抄的那一段",
            },
        ],
    )
    html = client_for(folder).get("/jobs/j").text  # 未完成 → 預設就是檢查結果
    body = panel(html)

    assert "2 項要你看：1 項沒過、1 項提醒" in body
    notes = re.findall(r'class="s0-groupnote">(.*?)<', body)
    assert notes == ["沒過", "提醒", "過"]
    shapes = re.findall(
        r'data-result="(\w+)"><span class="s0-check__icon"><svg[^>]*>(.*?)</svg>', body
    )
    assert dict(shapes).keys() == {"fail", "warn", "pass"}
    assert len({path for _, path in shapes}) == 3  # 三種圖示的形狀各不相同
    assert "drafts/draft.v1.md:12" in body and "照抄的那一段" in body
    assert "C3 照抄，修了 1 次仍沒過" in body
    assert "2 項要看" in html.split('id="tab-checks"')[1].split("</a>")[0]


def test_checks_tab_marks_attention_even_when_another_tab_is_open(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.checks("j", [{"check": "C7", "status": "warn", "reason": "r"}])

    html = client_for(folder).get("/jobs/j?tab=bill").text

    assert "1 項要看" in html


def test_checks_tab_says_all_passed_when_nothing_needs_attention(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.checks("j", [{"check": "C1", "status": "pass", "reason": "ok"}])

    body = panel(client_for(folder).get("/jobs/j?tab=checks").text)

    assert "C1～C7 全部通過" in body


def test_bill_tab_sums_the_ledger_per_stage_attempt_with_escalation(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE, budget_usd=2.0)
    folder.ledger(
        "j",
        [
            {
                "stage": "digest",
                "attempt": 1,
                "cost_usd": 0.0,
                "latency_ms": 49_000,
                "gpu_seconds": 41,
            },
            {
                "stage": "compose",
                "attempt": 1,
                "tier": "strong",
                "model": "claude-sonnet-5-5",
                "cost_usd": 0.15,
                "latency_ms": 100_000,
            },
            {
                "stage": "compose",
                "attempt": 2,
                "tier": "max",
                "escalated_from": "strong",
                "model": "claude-opus-5-5",
                "cost_usd": 0.23,
                "latency_ms": 88_000,
            },
        ],
    )

    body = panel(client_for(folder).get("/jobs/j?tab=bill").text)

    assert "整理" in body and "0:49" in body and "41" in body
    assert "二創（第 2 次）" in body and "雲端（強）→雲端（最強）" in body
    assert "合計（預算 US$2.00）" in body and "US$0.38" in body


def test_events_tab_lists_newest_first_and_names_the_status_move(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.events(
        "j",
        [
            folder.status_event("j", "running", minutes_ago=5, frm="queued"),
            {
                "ts": folder.at(4).isoformat(),
                "kind": "stage",
                "stage": "compose",
                "attempt": 2,
                "message": "自動修",
            },
            folder.status_event("j", "review", minutes_ago=1, frm="running"),
        ],
    )

    body = panel(client_for(folder).get("/jobs/j?tab=events").text)

    times = re.findall(r'<td class="s0-mono">(\d\d:\d\d:\d\d)</td>', body)
    assert times == ["14:29:00", "14:26:00", "14:25:00"]
    assert "執行中→待審" in body and "第 2 次嘗試" in body and "二創" in body


def test_events_tab_caps_the_list_and_points_to_the_file(folder: Folder) -> None:
    folder.job("j", status="running", title="t", stage=folder.stage(1, 3, "digest", 5))
    folder.events(
        "j",
        [
            {
                "ts": folder.at(seconds_ago=n).isoformat(),
                "kind": "stage",
                "message": f"m{n}",
            }
            for n in range(60)
        ],
    )

    body = panel(client_for(folder).get("/jobs/j?tab=events").text)

    assert body.count("<tr><td") == 50
    assert "只列最新 50 筆，共 60 筆" in body and "events.jsonl" in body


@pytest.mark.promise("FR-UI-01")
@pytest.mark.parametrize(
    ("tab", "status", "result", "title"),
    [
        ("draft", "running", None, "稿子還沒寫"),
        ("draft", "review", {"outcome": "failed", "reason": "x"}, "沒有稿子"),
        ("digest", "queued", None, "還沒跑到整理段"),
        ("checks", "running", None, "還沒跑到檢查段"),
        ("bill", "queued", None, "還沒有花費"),
        ("events", "queued", None, "還沒有事件"),
    ],
)
def test_a_tab_with_nothing_to_show_says_why(
    folder: Folder, tab: str, status: str, result: dict[str, str] | None, title: str
) -> None:
    stage = folder.stage(1, 3, "digest", 5) if status == "running" else None
    folder.job("j", status=status, title="t", result=result, stage=stage)

    assert title in panel(client_for(folder).get(f"/jobs/j?tab={tab}").text)


def test_a_broken_checks_file_is_reported_not_hidden(folder: Folder) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    path = folder.data.job_dir("j") / "checks"
    path.mkdir()
    (path / "checks.v1.json").write_text("{壞掉", encoding="utf-8")

    response = client_for(folder).get("/jobs/j?tab=checks")

    assert response.status_code == 200
    assert "讀不懂這一頁的資料" in response.text and "不是合法的 JSON" in response.text


def test_a_checks_file_with_no_entries_says_so_instead_of_claiming_all_passed(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.checks("j", [])

    body = panel(client_for(folder).get("/jobs/j?tab=checks").text)

    assert "檢查結果是空的" in body and "全部通過" not in body


def test_a_checks_file_that_breaks_the_contract_is_reported_with_the_field(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    # C6 是軟檢查，契約不准 fail
    folder.checks("j", [{"check": "C6", "status": "fail", "reason": "r"}])

    response = client_for(folder).get("/jobs/j?tab=checks")

    assert "讀不懂這一頁的資料" in response.text and "格式讀不懂" in response.text


@pytest.mark.promise("FR-UI-01")
def test_unknown_job_is_a_404_page_that_names_the_id(folder: Folder) -> None:
    response = client_for(folder).get("/jobs/nope")

    assert response.status_code == 404
    assert "找不到這張單" in response.text and "nope" in response.text
    assert 'href="/"' in response.text


@pytest.mark.parametrize("job_id", [".hidden", "a%2Fb", "%2e%2e", "..%2F..%2Fetc"])
def test_job_ids_that_could_leave_the_jobs_folder_are_just_not_found(
    folder: Folder, job_id: str
) -> None:
    folder.job("real", status="queued", title="t")

    response = client_for(folder).get(f"/jobs/{job_id}")

    assert response.status_code == 404


def test_broken_job_md_gets_its_own_error_page(folder: Folder) -> None:
    bad = folder.data.job_dir("bad")
    bad.mkdir(parents=True)
    (bad / "job.md").write_text("這不是 frontmatter", encoding="utf-8")

    response = client_for(folder).get("/jobs/bad")

    assert response.status_code == 500
    assert "讀不懂" in response.text


@pytest.mark.promise("FR-UI-01")
def test_htmx_tab_switch_gets_only_the_job_view_and_pushes_the_url(
    folder: Folder,
) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    client = client_for(folder)

    fragment = client.get("/jobs/j?tab=bill", headers={"HX-Request": "true"})
    restore = client.get(
        "/jobs/j?tab=bill",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )

    assert "<html" not in fragment.text and 'id="job-view"' in fragment.text
    assert "<html" in restore.text
    assert fragment.headers["Vary"] == "HX-Request"
    assert 'hx-push-url="true"' in fragment.text


@pytest.mark.promise("NFR-OBS-02")
def test_running_and_queued_views_poll_every_5_seconds_but_finished_ones_do_not(
    folder: Folder,
) -> None:
    folder.job(
        "run", status="running", title="t", stage=folder.stage(1, 3, "digest", 5)
    )
    folder.job("q", status="queued", title="t")
    folder.job("rev", status="review", title="t", result=REVIEW_COMPLETE)
    folder.job("ok", status="approved", title="t", result=REVIEW_COMPLETE)
    client = client_for(folder)

    def polls(job_id: str) -> bool:
        return 'hx-trigger="every 5s"' in client.get(f"/jobs/{job_id}").text

    assert polls("run") and polls("q")
    assert not polls("rev") and not polls("ok")


@pytest.mark.promise("FR-UI-01")
def test_viewing_a_job_changes_nothing_in_the_data_dir(folder: Folder) -> None:
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE, draft_version=1)
    folder.draft("j", 1, "# 稿子")
    before = {p: p.read_bytes() for p in folder.data.root.rglob("*") if p.is_file()}
    client = client_for(folder)

    for tab in ("draft", "digest", "sources", "checks", "bill", "events"):
        client.get(f"/jobs/j?tab={tab}")

    after = {p: p.read_bytes() for p in folder.data.root.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("tab", ["draft", "digest", "sources", "checks", "events", "bill"])
def test_重構工單沒有整理結果與稿子_舊詳情頁每個分頁照樣打得開(
    folder: Folder, tab: str
) -> None:
    # #79：重構工單沒有 claims.json 也沒有 drafts/，檢查結果只有 C5 與只顯示的 C3（warn）。
    # 舊 UI 不重新設計（#86 會換掉），但不能壞
    folder.job("j", status="review", title="t", result=REVIEW_COMPLETE)
    folder.checks(
        "j",
        [
            {"check": "C5", "status": "pass", "reason": "結尾的參考來源列了原文出處"},
            {"check": "C3", "status": "warn", "reason": "原文：8-gram 重疊率 12.0%"},
        ],
    )

    response = client_for(folder).get(f"/jobs/j?tab={tab}")

    assert response.status_code == 200
