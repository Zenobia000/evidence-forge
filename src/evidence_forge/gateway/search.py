"""搜尋（v0.3 §3.4 的 `web_search`、#69）：用 Tavily 的 basic 搜尋，一次 1 credit。

一次搜尋的順序：

1. 查詢正規化（前後空白去掉、連續空白併成一個、Unicode NFC），連同參數寫成一份固定格式的
   請求本文（`SearchQuery.request_bytes`）。它的 hash 同時是帳本的 `request_hash`、
   快取的資料夾名稱、重播時對錄音的鍵（ADR-0005：鍵是「正規化後的查詢＋參數」）。
   請求本文不含金鑰，存進 `blobs/`。
2. 重播模式（`EF_GATEWAY_MODE=replay`）：只看錄音（`<錄音>/ledger.jsonl` 裡成功的 `search` 行
   ＋ `<錄音>/blobs/`）；沒錄過就報錯，從不連網（NFR-COST-06）。
3. 快取：`cache/search/<請求 hash>/` 裡最新的一筆還在有效期限內，直接用；不扣 credits、
   不需要預算，帳本記 `cache_hit`。快取跨工單共用。
4. 先預留：這張單 `budget.tavily_credits` 剩下的不夠 1 credit 就拒絕，帳本記 `budget_exceeded`
   （NFR-COST-01）。不告訴執行器停掉這張單：搜尋額度用完時，補料段用已經找到的資料結束
   （`research.feature`〈搜尋額度用完就停〉）。
5. 送 `POST <tavily>/search`，金鑰放在 `Authorization` 標頭（每次用才讀檔）。429 照 ADR-0011 退避，
   次數有上限（跟模型呼叫共用 `rate_limit_retries`）。
6. 結算：先把實際用掉的 credits 寫進帳本，再放掉預留。成功照 Tavily 回報的用量
   （`usage.credits`，沒回報就是 basic 的 1）；Tavily 回 4xx／5xx、連不上不算；
   請求送出後逾時或斷線不知道 Tavily 算了沒有，照預留的算（寧可多算，不少算）。
   成功的結果寫一筆新的快取（舊的不覆寫），原始回應存進 `blobs/`。

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
from typing import Any

import httpx
from pydantic import ValidationError

from evidence_forge.documents import read_job
from evidence_forge.gateway import ledger
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.budget import BudgetExceeded, CreditGuard
from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.gateway.credentials import MissingCredential, read_tavily_key
from evidence_forge.gateway.fetch import ttl_from_env
from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry, LedgerStatus
from evidence_forge.models import SearchRecord, SearchResult
from evidence_forge.write_once import write_once

_log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
# basic 搜尋一次 1 credit（Tavily 計價）；預留就預留這麼多
BASIC_CREDITS = 1
DEFAULT_TIMEOUT_S = 30.0
# Tavily 回 400 時附上的錯誤細節最多留這麼長
_DETAIL_LIMIT = 200


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


@dataclass(frozen=True)
class _Live:
    """一次真的打 Tavily 的結果：拿到結果（`record`），或沒拿到的原因。"""

    status: LedgerStatus
    credits: int
    record: SearchRecord | None = None
    http_status: int = 502
    message: str = ""


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
        ttl: timedelta | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`ttl` 不傳就讀 `EF_SEARCH_TTL_SECONDS`（沒設是 7 天）。"""
        self._config = config
        self._ttl = (
            ttl if ttl is not None else ttl_from_env(variable="EF_SEARCH_TTL_SECONDS")
        )
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._blobs = BlobStore(config.data.blobs)
        self._guard = CreditGuard(config.data)
        self._recorded: dict[str, str] = {}
        self._recorded_blobs: BlobStore | None = None
        if config.mode == "replay":
            if config.replay_dir is None:
                raise ValueError("重播模式要指定錄音的位置（replay_dir）")
            self._recorded_blobs = BlobStore(config.replay_dir / "blobs")
            for entry in ledger.read(config.replay_dir / "ledger.jsonl"):
                # 同一個查詢錄到好幾次時用第一次的：結果一樣可以重現
                if (
                    entry.kind == "search"
                    and entry.status in ("ok", "cache_hit")
                    and entry.response_hash is not None
                ):
                    self._recorded.setdefault(entry.request_hash, entry.response_hash)

    # --- 對外 ---------------------------------------------------------------

    async def search(self, key: JobKey, query: SearchQuery) -> dict[str, Any]:
        """搜尋一次並記帳。拿不到結果丟 `ToolRejected`（帳本已經記好）。"""
        started = time.monotonic()
        now = self._config.clock()
        request_hash = self._blobs.put(query.request_bytes())

        if self._recorded_blobs is not None:
            record = self._replayed(request_hash)
            if record is None:
                self._record(key, "error", started, request_hash)
                raise ToolRejected(
                    500,
                    f"重播模式：沒有錄過這個搜尋（{query.query!r}，max_results={query.max_results}）",
                )
            return self._answer(key, record, "cache_hit", started, request_hash)

        cached = self._cached(request_hash, now)
        if cached is not None:
            return self._answer(key, cached, "cache_hit", started, request_hash)

        try:
            reservation = self._guard.reserve(
                key.job_id,
                job_credits=self._job_credits(key.job_id),
                credits=BASIC_CREDITS,
            )
        except BudgetExceeded as exc:
            self._record(key, "budget_exceeded", started, request_hash)
            raise ToolRejected(
                402,
                f"budget_exceeded：{exc.message}。不要再呼叫 web_search，"
                "用已經找到的資料完成這一段。",
            ) from None

        # 沒跑完就被打斷（取消、沒接住的例外）：請求可能已經送出，照預留的算
        live = _Live("error", reservation.credits)
        try:
            live = await self._live(query, now)
        finally:
            # 這裡沒有 await，被取消也會跑完：先記帳，再放掉預留
            unbooked = live.credits
            try:
                response_hash = (
                    self._keep(live.record) if live.record is not None else None
                )
                self._record(
                    key,
                    live.status,
                    started,
                    request_hash,
                    response_hash=response_hash,
                    credits=live.credits,
                )
                unbooked = 0
            finally:
                self._guard.release(reservation, unbooked_credits=unbooked)
        if live.record is None:
            raise ToolRejected(live.http_status, live.message)
        self._remember(request_hash, live.record)
        return agent_reply(live.record, cache_hit=False)

    # --- 記帳 ---------------------------------------------------------------

    def _answer(
        self,
        key: JobKey,
        record: SearchRecord,
        status: LedgerStatus,
        started: float,
        request_hash: str,
    ) -> dict[str, Any]:
        self._record(
            key, status, started, request_hash, response_hash=self._keep(record)
        )
        return agent_reply(record, cache_hit=True)

    def _keep(self, record: SearchRecord) -> str | None:
        """結果存進 `blobs/`，回傳 hash；存不進去記一筆警告、回 None（帳本照記）。"""
        try:
            return self._blobs.put(record.model_dump_json().encode("utf-8"))
        except OSError:
            _log.warning(
                "搜尋結果存不進 blobs/，帳本這一行不帶 response_hash", exc_info=True
            )
            return None

    def _record(
        self,
        key: JobKey,
        status: LedgerStatus,
        started: float,
        request_hash: str,
        *,
        response_hash: str | None = None,
        credits: int = 0,
    ) -> None:
        ledger.append(
            self._config.data.job_dir(key.job_id),
            LedgerEntry(
                ts=self._config.clock(),
                job_id=key.job_id,
                stage=key.stage,
                attempt=key.attempt,
                kind="search",
                credits=credits,
                latency_ms=_elapsed_ms(started),
                status=status,
                request_hash=request_hash,
                response_hash=response_hash,
            ),
        )

    def _job_credits(self, job_id: str) -> int:
        try:
            budget = read_job(self._config.data.job_dir(job_id) / "job.md").job.budget
        except (OSError, ValueError):
            # 讀不到工單的預算就當成 0：一律不放行
            return 0
        return budget.tavily_credits

    # --- 重播與快取 -----------------------------------------------------------

    def _replayed(self, request_hash: str) -> SearchRecord | None:
        assert self._recorded_blobs is not None
        digest = self._recorded.get(request_hash)
        raw = self._recorded_blobs.get(digest) if digest is not None else None
        if raw is None:
            return None
        try:
            return SearchRecord.model_validate_json(raw)
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

    async def _live(self, query: SearchQuery, now: datetime) -> _Live:
        try:
            api_key = read_tavily_key(self._config.secrets_dir)
        except MissingCredential as exc:
            return _Live("error", 0, message=f"web_search 不能用：{exc}")
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
                    return _Live(
                        "error", 0, message=f"連不上 Tavily（{type(exc).__name__}）"
                    )
                except httpx.TimeoutException:
                    return _Live(
                        "timeout",
                        BASIC_CREDITS,
                        http_status=504,
                        message=f"Tavily 超過 {self._timeout_s:.0f} 秒沒回應",
                    )
                except httpx.HTTPError as exc:
                    return _Live(
                        "error",
                        BASIC_CREDITS,
                        message=f"Tavily 連線中斷（{type(exc).__name__}）",
                    )
                if response.status_code != 429 or attempt == config.rate_limit_retries:
                    break
                await self._sleep(
                    self._backoff(response.headers.get("retry-after"), attempt)
                )
        return self._read(response, query, now, api_key)

    def _backoff(self, retry_after: str | None, attempt: int) -> float:
        config = self._config
        try:
            delay = float(retry_after) if retry_after is not None else -1.0
        except ValueError:
            delay = -1.0
        if delay < 0:
            delay = config.backoff_base_s * (2**attempt)
        return min(delay, config.backoff_max_s)

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
                return _Live("error", BASIC_CREDITS, message="Tavily 的回應讀不懂")
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
                    raw_hash=self._blobs.put(response.content),
                ),
            )
        if code == 429:
            return _Live(
                "rate_limited",
                0,
                http_status=429,
                message=(
                    f"Tavily 要求放慢（HTTP 429），重試 {self._config.rate_limit_retries} 次後還是一樣；"
                    "先用已經找到的資料"
                ),
            )
        if code in (401, 403):
            message = f"Tavily 金鑰無效或沒有權限（HTTP {code}）"
        elif code in (432, 433):
            message = f"Tavily 的方案額度用完了（HTTP {code}）"
        elif code == 400:
            message = (
                f"Tavily 不接受這個查詢（HTTP 400）：{self._detail(response, api_key)}"
            )
            return _Live("error", 0, http_status=400, message=message)
        elif code >= 500:
            message = f"Tavily 出錯（HTTP {code}），稍後再試"
        else:
            message = f"Tavily 回 HTTP {code}"
        return _Live("error", 0, message=message)

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
