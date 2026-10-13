"""各檔案格式的模型：該擋的擋得住，寫出去再讀回來不變。"""

from typing import Any

import pytest
from pydantic import ValidationError

from evidence_forge.models import Event, Item, Job


def _job(**changes: Any) -> dict[str, Any]:
    """一張合法的執行中工單（JSON 形狀），可以改幾個欄位。"""
    data: dict[str, Any] = {
        "id": "2026-10-10-manual-abc123",
        "recipe": "restructure",
        "feed": {
            "connector": "manual",
            "name": "manual",
            "item": "inbox/manual/abc123",
        },
        "goal": "給台灣 PM 看的 1,500 字觀點文",
        "budget": {"usd": 0.5, "tavily_credits": 20},
        "status": "running",
        "stage": {
            "id": "compose",
            "index": 3,
            "total": 4,
            "attempt": 1,
            "started_at": "2026-10-10T14:03:11+08:00",
        },
    }
    data.update(changes)
    return data


# --- job.md ---------------------------------------------------------------


def test_a_new_job_starts_with_no_draft_and_no_result() -> None:
    job = Job.model_validate(_job(status="queued", stage=None))
    assert job.versions.draft == 0
    assert job.result is None
    assert job.target_genre is None


@pytest.mark.promise("NFR-RUN-02")
def test_status_outside_the_three_is_refused() -> None:
    with pytest.raises(ValidationError):
        Job.model_validate(_job(status="published"))


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize("outcome", ["complete", "incomplete", "failed"])
def test_done_carries_one_of_three_outcomes(outcome: str) -> None:
    job = Job.model_validate(
        _job(status="done", result={"outcome": outcome, "reason": "預算用完"})
    )
    assert job.result is not None and job.result.outcome == outcome


@pytest.mark.promise("NFR-RUN-02")
def test_done_without_a_result_is_refused() -> None:
    with pytest.raises(ValidationError, match="結果"):
        Job.model_validate(_job(status="done", result=None))


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize("outcome", ["incomplete", "failed"])
def test_incomplete_or_failed_needs_a_reason(outcome: str) -> None:
    with pytest.raises(ValidationError, match="原因"):
        Job.model_validate(
            _job(status="done", result={"outcome": outcome, "reason": " "})
        )


def test_complete_needs_no_reason() -> None:
    job = Job.model_validate(_job(status="done", result={"outcome": "complete"}))
    assert job.result is not None and job.result.reason == ""


def test_only_a_running_job_can_be_waiting() -> None:
    waiting = {"kind": "gpu", "since": "2026-10-10T14:05:00+08:00"}
    assert Job.model_validate(_job(waiting=waiting)).waiting is not None
    with pytest.raises(ValidationError, match="執行中"):
        Job.model_validate(_job(status="queued", waiting=waiting))


def test_waiting_on_a_tool_names_the_tool() -> None:
    since = "2026-10-10T14:05:00+08:00"
    job = Job.model_validate(
        _job(waiting={"kind": "tool", "tool": "fetch_url", "since": since})
    )
    assert job.waiting is not None and job.waiting.tool == "fetch_url"
    with pytest.raises(ValidationError):
        Job.model_validate(_job(waiting={"kind": "tool", "since": since}))


def test_stage_index_cannot_exceed_total() -> None:
    stage = _job()["stage"] | {"index": 5, "total": 4}
    with pytest.raises(ValidationError):
        Job.model_validate(_job(stage=stage))


def test_times_without_a_timezone_are_refused() -> None:
    stage = _job()["stage"] | {"started_at": "2026-10-10T14:03:11"}
    with pytest.raises(ValidationError):
        Job.model_validate(_job(stage=stage))


def test_unknown_fields_are_refused_not_ignored() -> None:
    # 打錯字的欄位（statsu）不能被默默丟掉。
    with pytest.raises(ValidationError):
        Job.model_validate(_job(statsu="done"))


@pytest.mark.parametrize("amount", [float("inf"), float("nan")])
def test_budget_must_be_a_finite_amount(amount: float) -> None:
    # YAML 的 .inf 會被讀成無限大；無限大的預算等於沒有上限。
    with pytest.raises(ValidationError):
        Job.model_validate(_job(budget={"usd": amount}))


HASH_A = "a" * 64
HASH_B = "0123456789abcdef" * 4


# --- inbox/<訂閱源>/<項目>/item.json ------------------------------------------

ITEM_JSON = (
    """{
  "id": "abc123",
  "feed": "some-newsletter",
  "url": "https://example.com/p/agents",
  "title": "Agents in production",
  "author": "Jane Doe",
  "published_at": "2026-10-09T08:00:00+00:00",
  "fetched_at": "2026-10-10T14:00:00+08:00",
  "content_hash": "%s"
}"""
    % HASH_A
)


def test_item_json_round_trips() -> None:
    item = Item.model_validate_json(ITEM_JSON)
    assert item.content_hash == HASH_A
    assert Item.model_validate_json(item.model_dump_json()) == item


def test_pasted_text_has_no_url_author_or_publish_time() -> None:
    item = Item.model_validate(
        {
            "id": "def456",
            "feed": "manual",
            "title": "貼上的文字",
            "fetched_at": "2026-10-10T14:00:00+08:00",
            "content_hash": HASH_B,
        }
    )
    assert (item.url, item.author, item.published_at) == (None, None, None)


@pytest.mark.parametrize("bad_hash", ["ABC", "A" * 64, "sha256:" + "a" * 64, "a" * 63])
def test_content_hash_is_64_lowercase_hex(bad_hash: str) -> None:
    with pytest.raises(ValidationError):
        Item.model_validate_json(ITEM_JSON.replace(HASH_A, bad_hash))


UNREADABLE = {
    "id": "url-0123456789abcdef",
    "feed": "manual",
    "url": "https://example.com/paid",
    "title": "https://example.com/paid",
    "fetched_at": "2026-10-11T12:00:00+08:00",
    "unavailable": True,
    "unavailable_reason": "付費牆（HTTP 402），不繞過",
}


@pytest.mark.promise("FR-IN-01")
def test_讀不到的素材項目_有原因_沒有內容_hash() -> None:
    item = Item.model_validate(UNREADABLE)
    assert (item.unavailable, item.content_hash) == (True, None)
    assert Item.model_validate_json(item.model_dump_json()) == item


@pytest.mark.promise("FR-IN-01")
def test_讀不到一定要寫原因() -> None:
    with pytest.raises(ValidationError):
        Item.model_validate({**UNREADABLE, "unavailable_reason": "  "})


def test_讀得到的素材項目一定要有內容_hash_而且不寫讀不到的原因() -> None:
    readable = {k: v for k, v in UNREADABLE.items() if not k.startswith("unavailable")}
    with pytest.raises(ValidationError):
        Item.model_validate(readable)
    with pytest.raises(ValidationError):
        Item.model_validate(
            {**readable, "content_hash": HASH_A, "unavailable_reason": "多寫的"}
        )


# --- jobs/<id>/events.jsonl 的一行 -------------------------------------------


def test_status_event_line_round_trips() -> None:
    line = (
        '{"ts":"2026-10-10T14:03:11+08:00","kind":"status",'
        '"from_status":"queued","to_status":"running"}'
    )
    event = Event.model_validate_json(line)
    assert (event.from_status, event.to_status) == ("queued", "running")
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_event_line_has_no_newline_inside() -> None:
    event = Event.model_validate(
        {
            "ts": "2026-10-10T14:03:11+08:00",
            "kind": "error",
            "stage": "digest",
            "attempt": 2,
            "message": "Ollama 沒回應\n第二行",
        }
    )
    assert "\n" not in event.model_dump_json()


def test_creating_a_job_is_a_status_event_from_nothing() -> None:
    event = Event.model_validate(
        {"ts": "2026-10-10T14:00:00+08:00", "kind": "status", "to_status": "queued"}
    )
    assert event.from_status is None


def test_status_event_needs_the_new_status() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate({"ts": "2026-10-10T14:00:00+08:00", "kind": "status"})


def test_only_status_events_carry_statuses() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "ts": "2026-10-10T14:00:00+08:00",
                "kind": "stage",
                "to_status": "running",
            }
        )


# --- 舊資料：review／approved／discarded 一律讀成 done（ADR-0019 §3.4） -----------


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize("old", ["review", "approved", "discarded"])
def test_old_statuses_load_as_done(old: str) -> None:
    job = Job.model_validate(_job(status=old, result={"outcome": "complete"}))
    assert job.status == "done"


@pytest.mark.promise("NFR-RUN-02")
def test_old_status_events_load_and_read_as_done() -> None:
    event = Event.model_validate(
        {
            "ts": "2026-10-10T14:00:00+08:00",
            "kind": "status",
            "from_status": "review",
            "to_status": "approved",
        }
    )
    assert (event.from_status, event.to_status) == ("done", "done")


def test_old_decision_events_still_load() -> None:
    event = Event.model_validate(
        {"ts": "2026-10-10T14:00:00+08:00", "kind": "decision", "message": "通過"}
    )
    assert event.kind == "decision"
