"""抓網頁（v0.3 §3.4 的 `fetch_url`、FR-IN-01 的網址投入）：兩個入口共用這一個 `Fetcher`。

一次抓取的順序：
1. 網址格式（`url_guard.ascii_url`：檢查後轉成純 ASCII，中文網址也抓得到）。
2. 快取：`cache/fetch/<網址 hash>/` 裡最新的一筆還在有效期限內，就直接用，不連網。
3. 每一跳（含轉址）先看 robots.txt 准不准，再送請求。
4. 連線層（`_GuardedBackend`）在**每一次**開 TCP 連線時解析主機、確認每個位址都是公開位址，
   然後連的就是檢查過的那個 IP。轉址到新主機會開新連線，所以每一跳都會重新檢查。
5. 403、付費牆、robots、內網位址、429 退避後仍然 429：標讀不到並寫原因，不換身分、不換網址再試。
6. 讀得到的：原始本文存進 `blobs/`，抽出正文，寫一筆新的快取紀錄（舊的不覆寫）。

重播模式（`EF_GATEWAY_MODE=replay`）不走上面這些：閘道的 `fetch_url` 只回放錄音（`RecordedFetches`）。
投入時的抓取（`fetch_for_intake`）不受重播模式影響。

刻意用 httpcore 而不是 httpx：httpcore 讓我們換掉連線層，而且不會讀環境變數裡的 proxy
（走 proxy 的話，連線的是 proxy，位址檢查就形同虛設）。
"""

import hashlib
import json
import os
import re
import ssl
import time
import zlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpcore
from pydantic import ValidationError

from evidence_forge.extract import html_to_text, looks_paywalled
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.url_guard import (
    Resolver,
    UrlBlocked,
    ascii_url,
    resolve_public,
    system_resolver,
)
from evidence_forge.gateway import ledger
from evidence_forge.ledger_models import IntakeEntry, LedgerStatus
from evidence_forge.models import FetchRecord
from evidence_forge.paths import DataDir
from evidence_forge.write_once import write_once

DEFAULT_TTL = timedelta(days=7)
# 表明自己是誰，不假裝成瀏覽器；robots.txt 用前面那段名稱比對
USER_AGENT = "EvidenceForge/0.1 (+https://github.com/Zenobia000/evidence-forge)"
ROBOTS_AGENT = "EvidenceForge"

MAX_REDIRECTS = 5
MAX_BYTES = 10_000_000
# 429 最多再試幾次；每次等多久照 Retry-After，沒寫就 1、2 秒，最多等 30 秒
MAX_RETRIES = 2
MAX_RETRY_WAIT_S = 30.0

# 檢查過的 IP 與埠 → 實際要連的位址。正式就是原樣；測試用它把假的公開 IP 接到本機假伺服器。
Dial = Callable[[str, int], tuple[str, int]]


def _same(address: str, port: int) -> tuple[str, int]:
    return address, port


class _GuardedBackend(httpcore.NetworkBackend):
    """開每一條 TCP 連線之前先解析主機並檢查位址；連的就是檢查過的 IP。"""

    def __init__(self, resolver: Resolver, dial: Dial) -> None:
        self._inner = httpcore.SyncBackend()
        self._resolver = resolver
        self._dial = dial

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        last_error: Exception | None = None
        for address in resolve_public(host, self._resolver):
            target, target_port = self._dial(address, port)
            try:
                return self._inner.connect_tcp(
                    target, target_port, timeout, local_address, socket_options
                )
            except httpcore.ConnectError as exc:
                last_error = exc
        assert last_error is not None  # resolve_public 至少回一個位址
        raise last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise UrlBlocked("不連 Unix socket")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


@dataclass(frozen=True)
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _Unavailable(Exception):
    """這次抓取讀不到；帶著原因與要記進帳本的狀態。"""

    def __init__(
        self,
        reason: str,
        status: LedgerStatus = "error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status: LedgerStatus = status
        self.http_status = http_status


@dataclass(frozen=True)
class FetchOutcome:
    """一次 `fetch` 的結果，加上記帳要的東西。"""

    record: FetchRecord
    ledger_status: LedgerStatus
    latency_ms: int
    # 這次呼叫的時間（快取命中時跟 record.fetched_at 不同）
    ts: datetime

    @property
    def cache_hit(self) -> bool:
        return self.ledger_status == "cache_hit"

    def store_blobs(self, blobs: BlobStore) -> tuple[str, str]:
        """把這次的請求與結果存進 `blobs/`，回傳兩者的 hash（帳本的 request_hash、response_hash）。"""
        return blobs.put(request_bytes(self.record.url)), blobs.put(
            self.record.model_dump_json().encode("utf-8")
        )


def request_bytes(url: str) -> bytes:
    """一次抓取的請求本文（存進 `blobs/`）；它的 hash 是帳本的 `request_hash`，也是重播對錄音的鍵。"""
    return json.dumps(
        {"tool": "fetch_url", "url": url.strip()}, ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class Replayed:
    """錄音裡的一次抓取：當時的結果，與帳本當時記的狀態。"""

    record: FetchRecord
    status: LedgerStatus


class RecordedFetches:
    """重播模式（`EF_GATEWAY_MODE=replay`）的 `fetch_url`：只看錄音，從不連網（NFR-COST-06）。

    錄音是一張工單的 `ledger.jsonl`（`kind: fetch` 的行）加上它指到的 `blobs/`，
    用請求的 hash（`request_bytes`）對。讀不到的網頁也照樣回放（同樣的原因），
    agent 看到的工具結果跟錄音時一字不差，之後的模型請求才對得上錄音。
    """

    def __init__(self, replay_dir: Path) -> None:
        self._blobs = BlobStore(replay_dir / "blobs")
        self._recorded: dict[str, tuple[str, LedgerStatus]] = {}
        for entry in ledger.read(replay_dir / "ledger.jsonl"):
            # 同一個網址錄到好幾次時用第一次的：結果一樣可以重現
            if entry.kind == "fetch" and entry.response_hash is not None:
                self._recorded.setdefault(
                    entry.request_hash, (entry.response_hash, entry.status)
                )

    def get(self, request_hash: str) -> Replayed | None:
        found = self._recorded.get(request_hash)
        raw = self._blobs.get(found[0]) if found is not None else None
        if found is None or raw is None:
            return None
        try:
            record = FetchRecord.model_validate_json(raw)
        except ValidationError:
            return None
        # 錄音時讀得到的（不論當時是真的抓還是快取命中），重播一律算快取命中
        return Replayed(record, "cache_hit" if record.status == "ok" else found[1])


def cache_key(url: str) -> str:
    """快取用的網址 hash：去掉前後空白與 # 之後的部分（# 不會送給網站）。"""
    parts = urlsplit(url.strip())
    return hashlib.sha256(urlunsplit(parts._replace(fragment="")).encode()).hexdigest()


def ttl_from_env(
    env: Mapping[str, str] | None = None, *, variable: str = "EF_FETCH_TTL_SECONDS"
) -> timedelta:
    """快取的有效期限（秒）：抓取讀 `EF_FETCH_TTL_SECONDS`、搜尋讀 `EF_SEARCH_TTL_SECONDS`，
    沒設就是 7 天。"""
    environ = os.environ if env is None else env
    raw = environ.get(variable, "").strip()
    if not raw:
        return DEFAULT_TTL
    seconds = float(raw)
    if seconds <= 0:
        raise ValueError(f"{variable} 要大於 0：{raw}")
    return timedelta(seconds=seconds)


def _decode(body: bytes, headers: Mapping[str, str]) -> str:
    """字元集：先看 Content-Type，再看 HTML 開頭的 <meta charset>，都沒有就當 UTF-8。"""
    header = re.search(r"charset=([\w.-]+)", headers.get("content-type", ""), re.I)
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", body[:4096], re.I)
    if header is not None:
        name = header.group(1)
    elif meta is not None:
        name = meta.group(1).decode("ascii")
    else:
        name = "utf-8"
    try:
        return body.decode(name, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _decompress(body: bytes, encoding: str) -> bytes:
    """我們送 Accept-Encoding: identity；網站還是壓縮了的話，常見的兩種自己解。

    解壓後一樣受 `MAX_BYTES` 限制（10MB 的 gzip 可以解成 10GB，不能整份解開）；
    壓縮內容壞掉就標讀不到。不少網站標 deflate 卻送沒有 zlib 標頭的原始 deflate，兩種都試。
    """
    if encoding in ("", "identity"):
        return body
    if encoding == "gzip":
        tries = [16 + zlib.MAX_WBITS]
    elif encoding == "deflate":
        tries = [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    else:
        raise _Unavailable(f"網站用了不支援的壓縮方式 {encoding}")
    for wbits in tries:
        try:
            return _inflate(body, wbits)
        except zlib.error:
            continue
    raise _Unavailable(f"網站的壓縮內容（{encoding}）壞了，解不開")


def _inflate(body: bytes, wbits: int) -> bytes:
    inflater = zlib.decompressobj(wbits)
    out = inflater.decompress(body, MAX_BYTES + 1)
    if len(out) > MAX_BYTES:
        raise _Unavailable(f"網頁解壓後超過 {MAX_BYTES} bytes，不抓")
    if not inflater.eof:
        raise zlib.error("壓縮內容不完整")
    return out


_REDIRECTS = (301, 302, 303, 307, 308)


def _redirect_location(response: "_Response") -> str | None:
    """轉址要去的網址；不是轉址就回 None。

    標頭照 HTTP 規格用 latin-1 解；有些網站在 Location 直接放 UTF-8 bytes，
    這裡還原成原本的字，交給 `ascii_url` 正確編碼。
    """
    location = response.headers.get("location")
    if response.status not in _REDIRECTS or not location:
        return None
    try:
        return location.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return location


def _retry_wait(headers: Mapping[str, str], attempt: int) -> float:
    try:
        wait = float(headers.get("retry-after", ""))
    except ValueError:
        wait = float(2**attempt)
    return min(max(wait, 0.0), MAX_RETRY_WAIT_S)


class Fetcher:
    def __init__(
        self,
        data: DataDir,
        *,
        ttl: timedelta = DEFAULT_TTL,
        resolver: Resolver = system_resolver,
        dial: Dial = _same,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 20.0,
    ) -> None:
        self.data = data
        self.ttl = ttl
        self.blobs = BlobStore(data.blobs)
        self._backend = _GuardedBackend(resolver, dial)
        self._clock = clock
        self._sleep = sleep
        self._timeout_s = timeout_s

    @classmethod
    def from_env(
        cls,
        data: DataDir,
        env: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "Fetcher":
        """`clock` 給閘道傳它自己的時鐘，抓取與模型呼叫在帳本上用同一個時間來源。"""
        if clock is None:
            return cls(data, ttl=ttl_from_env(env))
        return cls(data, ttl=ttl_from_env(env), clock=clock)

    # --- 對外 ---------------------------------------------------------------

    def fetch(self, url: str) -> FetchOutcome:
        started = time.monotonic()
        now = self._clock()
        url = url.strip()
        cached = self._cached(url, now)
        if cached is not None:
            return FetchOutcome(cached, "cache_hit", _elapsed_ms(started), now)
        try:
            record = self._fetch_live(url, now)
            status: LedgerStatus = "ok"
        except _Unavailable as exc:
            record = FetchRecord(
                url=url,
                status="unavailable",
                reason=exc.reason,
                http_status=exc.http_status,
                fetched_at=now,
            )
            status = exc.status
        return FetchOutcome(record, status, _elapsed_ms(started), now)

    # --- 快取 ---------------------------------------------------------------

    def _cached(self, url: str, now: datetime) -> FetchRecord | None:
        directory = self.data.fetch_cache / cache_key(url)
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                record = FetchRecord.model_validate_json(path.read_bytes())
            except (OSError, ValidationError):
                continue  # 寫到一半或壞掉的那筆跳過，看前一筆
            if record.status == "ok" and now < record.fetched_at + self.ttl:
                return record
            return None
        return None

    def _remember(self, record: FetchRecord) -> None:
        name = record.fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        try:
            write_once(
                self.data.fetch_cache / cache_key(record.url) / name,
                record.model_dump_json(indent=2),
            )
        except FileExistsError:
            pass  # 同一微秒抓了兩次：內容一樣，留先寫的那份

    # --- 連網 ---------------------------------------------------------------

    def _fetch_live(self, url: str, now: datetime) -> FetchRecord:
        try:
            with httpcore.ConnectionPool(
                ssl_context=ssl.create_default_context(),
                network_backend=self._backend,
                max_connections=4,
            ) as pool:
                final_url, response = self._follow(pool, url)
        except UrlBlocked as exc:
            raise _Unavailable(exc.reason) from None
        except httpcore.TimeoutException:
            raise _Unavailable("網站太久沒回應", "timeout") from None
        except (
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.UnsupportedProtocol,
            ssl.SSLError,
        ) as exc:
            raise _Unavailable(f"連不上網站：{type(exc).__name__} {exc}") from None
        return self._read(url, final_url, response, now)

    def _follow(self, pool: httpcore.ConnectionPool, url: str) -> tuple[str, _Response]:
        robots: dict[str, RobotFileParser] = {}
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            current = ascii_url(current)
            if not self._robots(pool, current, robots).can_fetch(ROBOTS_AGENT, current):
                raise _Unavailable("網站的 robots.txt 不准抓這一頁")
            response = self._get_with_backoff(pool, current)
            location = _redirect_location(response)
            if location is None:
                return current, response
            current = urljoin(current, location)
        raise _Unavailable(f"轉址超過 {MAX_REDIRECTS} 次")

    def _robots(
        self,
        pool: httpcore.ConnectionPool,
        url: str,
        seen: dict[str, RobotFileParser],
    ) -> RobotFileParser:
        """這個網站的 robots.txt（RFC 9309）：4xx 當作沒有限制，5xx 與連不上當作全部禁止。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in seen:
            return seen[origin]
        parser = RobotFileParser()
        target = f"{origin}/robots.txt"
        response = self._get(pool, target)
        for _ in range(MAX_REDIRECTS):
            location = _redirect_location(response)
            if location is None:
                break
            target = ascii_url(urljoin(target, location))
            response = self._get(pool, target)
        if 200 <= response.status < 300:
            parser.parse(_decode(response.body, response.headers).splitlines())
        elif 400 <= response.status < 500:
            parser.parse([])  # 沒有規則＝全部准
        else:
            parser.parse(["User-agent: *", "Disallow: /"])
        seen[origin] = parser
        return parser

    def _get_with_backoff(self, pool: httpcore.ConnectionPool, url: str) -> _Response:
        for attempt in range(MAX_RETRIES + 1):
            response = self._get(pool, url)
            if response.status != 429:
                return response
            if attempt < MAX_RETRIES:
                self._sleep(_retry_wait(response.headers, attempt))
        raise _Unavailable(
            "網站要求放慢（HTTP 429），重試後還是一樣", "rate_limited", 429
        )

    def _get(self, pool: httpcore.ConnectionPool, url: str) -> _Response:
        timeout = self._timeout_s
        with pool.stream(
            "GET",
            url,
            headers=[
                (b"User-Agent", USER_AGENT.encode()),
                (b"Accept", b"text/html,application/xhtml+xml,text/plain;q=0.9"),
                (b"Accept-Encoding", b"identity"),
            ],
            extensions={
                "timeout": {
                    "connect": timeout,
                    "read": timeout,
                    "write": timeout,
                    "pool": timeout,
                }
            },
        ) as response:
            headers = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in response.headers
            }
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise _Unavailable(f"網頁超過 {MAX_BYTES} bytes，不抓")
                chunks.append(chunk)
        body = _decompress(
            b"".join(chunks), headers.get("content-encoding", "").lower()
        )
        return _Response(response.status, headers, body)

    # --- 判讀 ---------------------------------------------------------------

    def _read(
        self, url: str, final_url: str, response: _Response, now: datetime
    ) -> FetchRecord:
        status = response.status
        if status in (401, 403):
            raise _Unavailable(
                f"網站拒絕存取（HTTP {status}），不繞過", http_status=status
            )
        if status == 402:
            raise _Unavailable("付費牆（HTTP 402），不繞過", http_status=status)
        if status in (404, 410):
            raise _Unavailable(f"找不到這一頁（HTTP {status}）", http_status=status)
        if not 200 <= status < 300:
            raise _Unavailable(f"網站回應 HTTP {status}", http_status=status)

        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        decoded = _decode(response.body, response.headers)
        if content_type in ("text/html", "application/xhtml+xml", ""):
            if looks_paywalled(decoded):
                raise _Unavailable(
                    "付費牆：網頁標明這篇不是免費閱讀，不繞過", http_status=status
                )
            page = html_to_text(decoded)
            title, author, text = page.title, page.author, page.text
        elif content_type == "text/plain":
            title, author, text = None, None, decoded.strip()
        else:
            raise _Unavailable(f"不支援的內容類型 {content_type}", http_status=status)
        if not text.strip():
            raise _Unavailable("網頁抽不到正文", http_status=status)

        record = FetchRecord(
            url=url,
            final_url=final_url,
            status="ok",
            http_status=status,
            fetched_at=now,
            expires_at=now + self.ttl,
            content_type=content_type or None,
            title=title,
            author=author,
            text=text,
            raw_hash=self.blobs.put(response.body),
        )
        self._remember(record)
        return record


def fetch_for_intake(fetcher: Fetcher, url: str) -> FetchOutcome:
    """投入時抓網址（`ef new --url`、UI 的貼網址）：跟 agent 的 `fetch_url` 同一個 `Fetcher`。

    那時還沒有工單，帳記在 `ledger/intake.jsonl`（`ledger.append_intake`），讀不到也記一行。
    """
    outcome = fetcher.fetch(url)
    request_hash, response_hash = outcome.store_blobs(fetcher.blobs)
    ledger.append_intake(
        fetcher.data,
        IntakeEntry(
            ts=outcome.ts,
            kind="fetch",
            url=outcome.record.url,
            latency_ms=outcome.latency_ms,
            status=outcome.ledger_status,
            request_hash=request_hash,
            response_hash=response_hash,
        ),
    )
    return outcome


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
