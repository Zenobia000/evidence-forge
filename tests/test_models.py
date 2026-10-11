"""各檔案格式的模型：該擋的擋得住，寫出去再讀回來不變。"""

from typing import Any

import pytest
from pydantic import ValidationError

from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.models import Decision, Event, Item, Job


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
def test_status_outside_the_five_is_refused() -> None:
    with pytest.raises(ValidationError):
        Job.model_validate(_job(status="published"))


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize("outcome", ["complete", "incomplete", "failed"])
def test_review_carries_one_of_three_outcomes(outcome: str) -> None:
    job = Job.model_validate(
        _job(status="review", result={"outcome": outcome, "reason": "預算用完"})
    )
    assert job.result is not None and job.result.outcome == outcome


@pytest.mark.promise("NFR-RUN-02")
def test_review_without_a_result_is_refused() -> None:
    with pytest.raises(ValidationError, match="結果"):
        Job.model_validate(_job(status="review", result=None))


@pytest.mark.promise("NFR-RUN-02")
@pytest.mark.parametrize("outcome", ["incomplete", "failed"])
def test_incomplete_or_failed_needs_a_reason(outcome: str) -> None:
    with pytest.raises(ValidationError, match="原因"):
        Job.model_validate(
            _job(status="review", result={"outcome": outcome, "reason": " "})
        )


def test_complete_needs_no_reason() -> None:
    job = Job.model_validate(_job(status="review", result={"outcome": "complete"}))
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
        Job.model_validate(_job(statsu="approved"))


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


# --- jobs/<id>/review/decision.vN.json ---------------------------------------


def _decision(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action": "approve",
        "draft_version": 2,
        "created_at": "2026-10-10T15:00:00+08:00",
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("action", ["approve", "reject", "discard", "retry", "reedit"])
def test_the_five_review_actions_are_accepted(action: str) -> None:
    decision = Decision.model_validate(_decision(action=action, comment="開頭太長"))
    assert decision.action == action


def test_unknown_action_is_refused() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(_decision(action="publish"))


def test_reject_carries_comment_rerun_stage_and_the_reviewed_version() -> None:
    decision = Decision.model_validate(
        _decision(action="reject", comment="補一個反例", rerun_stage="compose")
    )
    assert decision.draft_version == 2
    assert Decision.model_validate_json(decision.model_dump_json()) == decision


def test_reject_without_a_comment_is_refused() -> None:
    with pytest.raises(ValidationError, match="意見"):
        Decision.model_validate(_decision(action="reject", comment="  "))


@pytest.mark.parametrize("action", ["approve", "discard", "retry"])
def test_only_reject_and_reedit_choose_a_stage_to_rerun(action: str) -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(_decision(action=action, rerun_stage="compose"))


def test_a_failed_job_with_no_draft_yet_can_be_retried() -> None:
    decision = Decision.model_validate(_decision(action="retry", draft_version=0))
    assert decision.draft_version == 0


# --- jobs/<id>/ledger.jsonl 的一行 -------------------------------------------

# NFR-OBS-01／v0.3 §4.4 列的欄位，照文件抄。
LEDGER_FIELDS = {
    "ts", "job_id", "stage", "attempt",
    "kind",
    "tier", "model", "escalated_from",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "credits", "cost_usd", "gpu_seconds",
    "ttft_ms", "latency_ms", "queue_ms",
    "status",
    "request_hash", "response_hash",
}  # fmt: skip


def _llm_call(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ts": "2026-10-10T14:03:20+08:00",
        "job_id": "2026-10-10-substack-abc123",
        "stage": "compose",
        "attempt": 1,
        "kind": "llm",
        "tier": "strong",
        "model": "claude-sonnet-5-5",
        "input_tokens": 1200,
        "output_tokens": 800,
        "cost_usd": 0.0156,
        "ttft_ms": 900,
        "latency_ms": 14000,
        "queue_ms": 0,
        "status": "ok",
        "request_hash": HASH_A,
        "response_hash": HASH_B,
    }
    data.update(changes)
    return data


@pytest.mark.promise("NFR-OBS-01")
def test_ledger_line_has_exactly_the_documented_fields() -> None:
    entry = LedgerEntry.model_validate(_llm_call())
    assert set(entry.model_dump()) == LEDGER_FIELDS


@pytest.mark.promise("NFR-OBS-01")
def test_ledger_line_round_trips_on_one_line() -> None:
    entry = LedgerEntry.model_validate(_llm_call())
    line = entry.model_dump_json()
    assert "\n" not in line
    assert LedgerEntry.model_validate_json(line) == entry


def test_usage_not_reported_counts_as_zero() -> None:
    entry = LedgerEntry.model_validate(_llm_call())
    assert (entry.cache_read_tokens, entry.cache_write_tokens) == (0, 0)
    assert (entry.credits, entry.gpu_seconds) == (0, 0)
    assert entry.escalated_from is None


def test_a_search_call_has_no_tier_or_model() -> None:
    entry = LedgerEntry.model_validate(
        _llm_call(
            kind="search",
            tier=None,
            model=None,
            input_tokens=0,
            output_tokens=0,
            credits=1,
            cost_usd=0,
            ttft_ms=None,
        )
    )
    assert entry.tier is None


def test_a_model_call_must_say_which_tier_and_model() -> None:
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate(_llm_call(tier=None))


def test_escalation_comes_from_a_lower_tier() -> None:
    entry = LedgerEntry.model_validate(_llm_call(escalated_from="local"))
    assert entry.escalated_from == "local"
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate(_llm_call(escalated_from="max"))


def test_a_failed_call_may_have_no_response() -> None:
    entry = LedgerEntry.model_validate(
        _llm_call(status="budget_exceeded", response_hash=None, ttft_ms=None)
    )
    assert entry.response_hash is None


@pytest.mark.parametrize(
    "field", ["input_tokens", "cost_usd", "latency_ms", "queue_ms", "gpu_seconds"]
)
def test_usage_and_spend_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate(_llm_call(**{field: -1}))


@pytest.mark.parametrize("field", ["cost_usd", "gpu_seconds"])
def test_spend_must_be_finite(field: str) -> None:
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate(_llm_call(**{field: float("inf")}))
