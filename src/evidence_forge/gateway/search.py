"""搜尋（v0.3 §3.4 的 `web_search`、#69）：用 Tavily 的 basic 搜尋，一次 1 credit。

一次搜尋的順序：

1. 查詢正規化（前後空白去掉、連續空白併成一個、Unicode NFC），連同參數寫成一份固定格式的
   請求本文（`SearchQuery.request_bytes`）。它的 hash 同時是帳本的 `request_hash`、
   快取的資料夾名稱、重播時對錄音的鍵（ADR-0005：鍵是「正規化後的查詢＋參數」）。
   請求本文不含金鑰，存進 `blobs/`。
2. 重播模式（`EF_GATEWAY_MODE=replay`）：只看錄音（`<錄音>/ledger.jsonl` 的 `search` 行
   ＋ `<錄音>/blobs/`），從不連網（NFR-COST-06）。同一段同一次第 n 次同樣的搜尋，回放錄音裡
   第 n 次得到的東西：成功的回結果；失敗的回一模一樣的狀態碼與錯誤訊息，帳本也記錄音時的狀態。
   沒錄過就報錯。
3. 快取：`cache/search/<請求 hash>/` 裡最新的一筆還在有效期限內，直接用；不扣 credits、
   不需要預算，帳本記 `cache_hit`。快取跨工單共用。
4. 先預留：這張單 `budget.tavily_credits` 剩下的不夠 1 credit 就拒絕（402），帳本記
   `budget_exceeded`（NFR-COST-01）。這一段不停：agent 照錯誤訊息用已經找到的資料收尾，
   後面的段照跑；全部段跑完時執行器看到帳本裡這一行，把單子以「未完成：預算用完」送進待審
   （`research.feature`〈搜尋額度用完就停〉、`runner/runner.py`）。
5. 送 `POST <tavily>/search`，金鑰放在 `Authorization` 標頭（每次用才讀檔）。429 照 ADR-0011 退避，
   次數有上限（跟模型呼叫共用 `rate_limit_retries` 與 `GatewayConfig.backoff_delay`）。
   退避時告示板寫「等限流」，等的時間記在帳本的 `queue_ms`（NFR-PAR-03、NFR-OBS-02）。
6. 結算：先把實際用掉的 credits 寫進帳本，再放掉預留。成功照 Tavily 回報的用量
   （`usage.credits`，沒回報就是 basic 的 1；比預留的多就記一筆警告）；Tavily 回 4xx／5xx、
   連不上不算；請求送出後逾時或斷線不知道 Tavily 算了沒有，照預留的算（寧可多算，不少算）。
   成功的結果寫一筆新的快取（舊的不覆寫），原始回應存進 `blobs/`。
7. 回給 agent：成功回結果清單。失敗回錯誤；這份錯誤（`SearchFailure`）也存進 `blobs/`、
   帳本的 `response_hash` 指向它（重播用：agent 收到錯誤之後照樣往下走，錯誤文字會進下一個模型請求）。
   Tavily 那一端的故障（限流到重試用完、出錯、沒回應、連不上、金鑰不能用或沒有金鑰檔）同時告訴執行器
   （告示板）：這一段交不出合格產出時它直接標失敗，不重試、不升級（NFR-RUN-05）。之後同一段同一次
   Tavily 好好回了一次，先前的故障就不算數。查詢本身不行（400）與 credits 用完（402）不是故障。

金鑰不進日誌、帳本、錯誤訊息、快取與 `blobs/`：錯誤訊息只寫狀態碼與我們自己的說明，
Tavily 回的錯誤細節只在 400（查詢本身有問題）時附上，而且先把金鑰字串遮掉。
"""

import asyncio
import hashlib
import json
import logging
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

import httpx
from pydantic import ValidationError

from evidence_forge.documents import read_job
from evidence_forge.gateway import ledger
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import BudgetExceeded, CreditGuard
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.gateway.credentials import MissingCredential, read_tavily_key
from evidence_forge.gateway.fetch import ttl_from_env
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry, LedgerStatus
from evidence_forge.models import SearchFailure, SearchRecord, SearchResult
from evidence_forge.write_once import write_once

_log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
# basic 搜尋一次 1 credit（Tavily 計價）；預留就預留這麼多
BASIC_CREDITS = 1
DEFAULT_TIMEOUT_S = 30.0
# Tavily 回 400 時附上的錯誤細節最多留這麼長
_DETAIL_LIMIT = 200
# 告示板上的後端代號（執行器的 `BACKEND_NAMES` 把它寫成「Tavily」）
TAVILY = "tavily"
# 閘道回這些狀態碼，表示 Tavily 那一端出了事：429 限流到重試用完、502 出錯／連不上／金鑰不能用、
# 504 沒回應。重跑這一段或升級模型都修不好，所以也告訴執行器（模組說明第 7 點）
OUTAGE_STATUSES = frozenset({429, 502, 504})


@dataclass(frozen=True)
class SearchQuery:
    """一次搜尋要送的東西（已經正規化）。"""

    query: str
    max_results: int

    def request_bytes(self) -> bytes:
        """固定格式的請求本文：鍵排序、不加空白。同樣的查詢＋參數一定得到同樣的位元組。"""
        request = {
            "tool": "web_search",
            "query": self.query,
            "max_results": self.max_results,
            "search_depth": "basic",
        }
        return json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(self.request_bytes()).hexdigest()


def normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFC", query).split())


def parse_query(body: dict[str, Any]) -> SearchQuery:
    """ef-tools 送來的參數；不合格丟 `ToolRejected(400)`。"""
    query = body.get("query")
    if not isinstance(query, str) or not normalize_query(query):
        raise ToolRejected(400, "web_search 要有 query（不能是空的）")
    max_results = body.get("max_results", DEFAULT_MAX_RESULTS)
    # bool 也是 int 的子類別，要先排除
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_RESULTS_LIMIT
    ):
        raise ToolRejected(
            400, f"max_results 要是 1 到 {MAX_RESULTS_LIMIT} 的整數：{max_results!r}"
        )
    return SearchQuery(normalize_query(query), max_results)


def agent_reply(record: SearchRecord, *, cache_hit: bool) -> dict[str, Any]:
    """回給 ef-tools 的內容。結果清單只有 url、title、content、score，發布日期有才給。"""
    results = []
    for found in record.results:
        item: dict[str, Any] = {
            "url": found.url,
            "title": found.title,
            "content": found.content,
            "score": found.score,
        }
        if found.published_date:
            item["published_date"] = found.published_date
        results.append(item)
    return {
        "query": record.query,
        "results": results,
        "searched_at": record.searched_at.isoformat(),
        "cache_hit": cache_hit,
    }


@dataclass
class _Call:
    """一次搜尋從進來到寫完帳本的狀態。"""

    key: JobKey
    request_hash: str
    started: float
    # 等 Tavily 限流退避的時間（秒），記在帳本的 queue_ms
    queue_s: float = 0.0


@dataclass(frozen=True)
class _Live:
    """一次真的打 Tavily 的結果：帳本要記的狀態與 credits，以及回給 agent 的結果或錯誤。"""

    status: LedgerStatus
    credits: int
    outcome: SearchRecord | SearchFailure


def _failed(
    status: LedgerStatus, credits: int, http_status: int, message: str
) -> _Live:
    return _Live(status, credits, SearchFailure(status=http_status, error=message))


def _results(payload: object) -> tuple[SearchResult, ...] | None:
    """Tavily 的 `results`；格式不對就回 None。`answer`、`images`、`raw_content` 一律不收。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None
    found: list[SearchResult] = []
    for row in payload["results"]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            return None
        score = row.get("score")
        published = row.get("published_date")
        found.append(
            SearchResult(
                url=row["url"],
                title=str(row.get("title") or ""),
                content=str(row.get("content") or ""),
                score=float(score)
                if isinstance(score, int | float) and not isinstance(score, bool)
                else None,
                published_date=published
                if isinstance(published, str) and published
                else None,
            )
        )
    return tuple(found)


def _reported_credits(payload: dict[str, Any]) -> int:
    """Tavily 回報這次用了幾個 credits（`usage.credits`）；沒回報或讀不懂就是 basic 的 1。"""
    usage = payload.get("usage")
    value = usage.get("credits") if isinstance(usage, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return BASIC_CREDITS
    return value


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class Searcher:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        board: JobBoard | None = None,
        ttl: timedelta | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`board` 要跟執行器共用（`create_app` 傳它的那一個）；不傳就自己開一個，執行器讀不到。

        `ttl` 不傳就讀 `EF_SEARCH_TTL_SECONDS`（沒設是 7 天）。
        """
        self._config = config
        self._board = board if board is not None else JobBoard(config.clock)
        self._ttl = (
            ttl if ttl is not None else ttl_from_env(variable="EF_SEARCH_TTL_SECONDS")
        )
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._blobs = BlobStore(config.data.blobs)
        self._guard = CreditGuard(config.data)
        # 錄音：（段, 次, 請求 hash）→ 照帳本順序的（狀態, 回應 hash）
        # 回放不了的行（沒有 response_hash）也佔一個位置（None），後面的第 n 次才不會錯位
        self._recorded: dict[ledger.CallKey, list[tuple[LedgerStatus, str | None]]] = {}
        # 重播時同一張單、同一段、同一次、同一個搜尋已經回放過幾次（第 n 次回放第 n 個）
        self._seen: dict[tuple[str, str, int, str], int] = {}
        self._recorded_blobs: BlobStore | None = None
        if config.mode == "replay":
            if config.replay_dir is None:
                raise ValueError("重播模式要指定錄音的位置（replay_dir）")
            self._recorded_blobs = BlobStore(config.replay_dir / "blobs")
            for entry in ledger.read(config.replay_dir / "ledger.jsonl"):
                if entry.kind == "search":
                    self._recorded.setdefault(
                        (entry.stage, entry.attempt, entry.request_hash), []
                    ).append((entry.status, entry.response_hash))

    # --- 對外 ---------------------------------------------------------------

    async def search(self, key: JobKey, query: SearchQuery) -> dict[str, Any]:
        """搜尋一次並記帳。拿不到結果丟 `ToolRejected`（帳本已經記好）。"""
        call = _Call(key, query.request_hash, time.monotonic())
        self._store(query.request_bytes(), "搜尋的請求本文")
        if self._recorded_blobs is not None:
            return self._replay(call, query)

        now = self._config.clock()
        cached = self._cached(call.request_hash, now)
        if cached is not None:
            self._record(call, "cache_hit", cached)
            return agent_reply(cached, cache_hit=True)

        try:
            reservation = self._guard.reserve(
                key.job_id,
                job_credits=self._job_credits(key.job_id),
                credits=BASIC_CREDITS,
            )
        except BudgetExceeded as exc:
            refused = SearchFailure(
                status=402,
                error=f"budget_exceeded：{exc.message}。不要再呼叫 web_search，"
                "用已經找到的資料完成這一段。",
            )
            self._record(call, "budget_exceeded", refused)
            self._refuse(call, refused)

        live: _Live | None = None
        try:
            live = await self._live(call, query, now)
        finally:
            # 這裡沒有 await，被取消也會跑完：先記帳，再放掉預留。
            # 沒跑完就被打斷（取消、沒接住的例外）：請求可能已經送出，照預留的算；agent 什麼都沒收到
            status: LedgerStatus = live.status if live is not None else "error"
            credits = live.credits if live is not None else reservation.credits
            unbooked = credits
            try:
                self._record(
                    call,
                    status,
                    live.outcome if live is not None else None,
                    credits=credits,
                )
                unbooked = 0
            finally:
                self._guard.release(reservation, unbooked_credits=unbooked)
        if live.credits > reservation.credits:
            # 帳本照 Tavily 算的記；多出來的可能讓這張單超過上限，不能默默吞掉
            _log.warning(
                "Tavily 這次算了 %d credits，比預留的 %d 多（工單 %s）",
                live.credits,
                reservation.credits,
                key.job_id,
            )
        if isinstance(live.outcome, SearchFailure):
            self._refuse(call, live.outcome)
        # Tavily 這次好好回了：同一段同一次先前碰到的 Tavily 故障是暫時的
        self._board.recovered(key, TAVILY)
        self._remember(call.request_hash, live.outcome)
        return agent_reply(live.outcome, cache_hit=False)

    # --- 回給 agent 與記帳 ------------------------------------------------------

    def _refuse(self, call: _Call, failure: SearchFailure) -> NoReturn:
        """把錯誤回給 agent（帳本要先記好）；Tavily 那一端的故障也告訴執行器。"""
        if failure.status in OUTAGE_STATUSES:
            self._board.report_outage(call.key, TAVILY, failure.error)
        raise ToolRejected(failure.status, failure.error)

    def _record(
        self,
        call: _Call,
        status: LedgerStatus,
        outcome: SearchRecord | SearchFailure | None = None,
        *,
        credits: int = 0,
    ) -> None:
        """記一行帳。回給 agent 的結果或錯誤存進 `blobs/`，`response_hash` 指向它。"""
        response_hash = (
            self._store(outcome.model_dump_json().encode("utf-8"), "回給 agent 的內容")
            if outcome is not None
            else None
        )
        key = call.key
        ledger.append(
            self._config.data.job_dir(key.job_id),
            LedgerEntry(
                ts=self._config.clock(),
                job_id=key.job_id,
                stage=key.stage,
                attempt=key.attempt,
                kind="search",
                credits=credits,
                latency_ms=_elapsed_ms(call.started),
                queue_ms=int(call.queue_s * 1000),
                status=status,
                request_hash=call.request_hash,
                response_hash=response_hash,
            ),
        )

    def _store(self, content: bytes, what: str) -> str | None:
        """存進 `blobs/`，回傳 hash；存不進去（磁碟滿之類）記一筆警告、回 None。

        原文是事後查帳與重播用的，帳本那一行才是錢：不能因為原文存不進去就不記帳、不給結果。
        """
        try:
            return self._blobs.put(content)
        except OSError:
            _log.warning("%s存不進 blobs/", what, exc_info=True)
            return None

    def _job_credits(self, job_id: str) -> int:
        try:
            budget = read_job(self._config.data.job_dir(job_id) / "job.md").job.budget
        except (OSError, ValueError):
            # 讀不到工單的預算就當成 0：一律不放行
            return 0
        return budget.tavily_credits

    # --- 重播與快取 -----------------------------------------------------------

    def _replay(self, call: _Call, query: SearchQuery) -> dict[str, Any]:
        """重播模式（模組說明第 2 點）：從不連網，也不需要預算。"""
        found = self._recording(call)
        if found is None:
            missing = SearchFailure(
                status=500,
                error=f"重播模式：沒有錄過這個搜尋（{query.query!r}，max_results={query.max_results}）",
            )
            self._record(call, "error", missing)
            self._refuse(call, missing)
        status, outcome = found
        if isinstance(outcome, SearchFailure):
            # 帳本照錄音時的狀態記（例如 budget_exceeded）：執行器才做出跟錄音時一樣的判斷
            self._record(call, status, outcome)
            self._refuse(call, outcome)
        if status == "ok":
            # 錄音裡這一次真的打了 Tavily 而且成功：先前的 Tavily 故障跟錄音時一樣不算數
            self._board.recovered(call.key, TAVILY)
        self._record(call, "cache_hit", outcome)
        return agent_reply(outcome, cache_hit=True)

    def _recording(
        self, call: _Call
    ) -> tuple[LedgerStatus, SearchRecord | SearchFailure] | None:
        """錄音裡同一段同一次第 n 次同樣的搜尋得到什麼；沒錄過或讀不懂是 None。"""
        assert self._recorded_blobs is not None
        key = call.key
        counter = (key.job_id, key.stage, key.attempt, call.request_hash)
        nth = self._seen.get(counter, 0)
        self._seen[counter] = nth + 1
        found = self._recorded.get((key.stage, key.attempt, call.request_hash), [])
        if nth >= len(found):
            return None
        status, digest = found[nth]
        # 錄音時結果沒存進 blobs/ 的那一次：回放不了，照沒錄過報錯
        raw = self._recorded_blobs.get(digest) if digest is not None else None
        if raw is None:
            return None
        try:
            if status in ("ok", "cache_hit"):
                return status, SearchRecord.model_validate_json(raw)
            return status, SearchFailure.model_validate_json(raw)
        except ValidationError:
            return None

    def _cached(self, request_hash: str, now: datetime) -> SearchRecord | None:
        directory = self._config.data.search_cache / request_hash
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                record = SearchRecord.model_validate_json(path.read_bytes())
            except (OSError, ValidationError):
                continue  # 寫到一半或壞掉的那筆跳過，看前一筆
            return record if now < record.searched_at + self._ttl else None
        return None

    def _remember(self, request_hash: str, record: SearchRecord) -> None:
        name = record.searched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        try:
            write_once(
                self._config.data.search_cache / request_hash / name,
                record.model_dump_json(indent=2),
            )
        except FileExistsError:
            pass  # 同一微秒搜了兩次：留先寫的那份
        except OSError:
            # 快取寫不進去不影響這次的結果，只是下次要再付一次
            _log.warning("搜尋快取寫不進去", exc_info=True)

    # --- 連網 ---------------------------------------------------------------

    async def _live(self, call: _Call, query: SearchQuery, now: datetime) -> _Live:
        try:
            api_key = read_tavily_key(self._config.secrets_dir)
        except MissingCredential as exc:
            return _failed("error", 0, 502, f"web_search 不能用：{exc}")
        payload = {
            "query": query.query,
            "max_results": query.max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        config = self._config
        url = f"{config.tavily_url}/search"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_s, connect=5.0)
        ) as client:
            for attempt in range(config.rate_limit_retries + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    # 沒連上就沒送出：不算 credits。只帶例外的種類，例外訊息可能含請求細節
                    return _failed(
                        "error", 0, 502, f"連不上 Tavily（{type(exc).__name__}）"
                    )
                except httpx.TimeoutException:
                    return _failed(
                        "timeout",
                        BASIC_CREDITS,
                        504,
                        f"Tavily 超過 {self._timeout_s:.0f} 秒沒回應",
                    )
                except httpx.HTTPError as exc:
                    return _failed(
                        "error",
                        BASIC_CREDITS,
                        502,
                        f"Tavily 連線中斷（{type(exc).__name__}）",
                    )
                if response.status_code != 429 or attempt == config.rate_limit_retries:
                    break
                await self._back_off(
                    call,
                    config.backoff_delay(response.headers.get("retry-after"), attempt),
                )
        return self._read(response, query, now, api_key)

    async def _back_off(self, call: _Call, delay: float) -> None:
        """Tavily 回 429：等一下再送。告示板寫「等限流」，等的時間算進帳本的 `queue_ms`。"""
        job_id = call.key.job_id
        self._board.wait(job_id, "rate_limit")
        waited = time.monotonic()
        try:
            await self._sleep(delay)
        finally:
            call.queue_s += time.monotonic() - waited
            # 等完就清掉：接著重送的這段時間，執行器從 Pi 的事件看到的是「執行工具中」
            self._board.done(job_id)

    def _read(
        self, response: httpx.Response, query: SearchQuery, now: datetime, api_key: str
    ) -> _Live:
        code = response.status_code
        if code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            results = _results(payload)
            if results is None or not isinstance(payload, dict):
                # 200 但讀不懂：Tavily 多半已經算了，照預留的算
                return _failed("error", BASIC_CREDITS, 502, "Tavily 的回應讀不懂")
            credits = _reported_credits(payload)
            return _Live(
                "ok",
                credits,
                SearchRecord(
                    query=query.query,
                    max_results=query.max_results,
                    searched_at=now,
                    expires_at=now + self._ttl,
                    results=results,
                    credits=credits,
                    raw_hash=self._store(response.content, "Tavily 的原始回應"),
                ),
            )
        if code == 429:
            return _failed(
                "rate_limited",
                0,
                429,
                f"Tavily 要求放慢（HTTP 429），重試 {self._config.rate_limit_retries} 次後還是一樣；"
                "先用已經找到的資料",
            )
        if code == 400:
            return _failed(
                "error",
                0,
                400,
                f"Tavily 不接受這個查詢（HTTP 400）：{self._detail(response, api_key)}",
            )
        if code in (401, 403):
            message = f"Tavily 金鑰無效或沒有權限（HTTP {code}）"
        elif code in (432, 433):
            message = f"Tavily 的方案額度用完了（HTTP {code}）"
        elif code >= 500:
            message = f"Tavily 出錯（HTTP {code}），稍後再試"
        else:
            message = f"Tavily 回 HTTP {code}"
        return _failed("error", 0, 502, message)

    @staticmethod
    def _detail(response: httpx.Response, api_key: str) -> str:
        """Tavily 400 的錯誤說明（查詢太長之類），截短、遮掉金鑰。"""
        try:
            detail: object = response.json().get("detail", "")
        except (ValueError, AttributeError):
            detail = response.text
        if isinstance(detail, dict):
            detail = detail.get("error", "")
        text = str(detail).replace(api_key, "[redacted]")
        return text[:_DETAIL_LIMIT] or "沒有說明"
