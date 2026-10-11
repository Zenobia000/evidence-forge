"""SEAM-02 的抓取那一半：`Fetcher` 對著本機的假網站（假 DNS＋假連線，不連外網）。

agent 的 `fetch_url` 與手動投入的網址都走這一個 `Fetcher`，所以 SSRF、robots、
403、付費牆、429、快取期限只在這裡測一次；兩個入口另外各測「有接上它」。
"""

import gzip
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from evidence_forge.gateway import fetch as fetch_module
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.paths import DataDir

if TYPE_CHECKING:
    from integration.conftest import FakeWeb

ARTICLE = """<html><head><title>Agents in production</title></head><body>
<nav>選單</nav><article><h1>Agents in production</h1><p>最難的是可觀測性。</p></article>
</body></html>"""

T0 = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    root = DataDir(tmp_path / "data")
    root.ensure()
    return root


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def fetcher(
    data: DataDir, fake_web: "FakeWeb", clock: Clock, sleeps: list[float]
) -> Fetcher:
    return Fetcher(
        data,
        resolver=fake_web.resolver,
        dial=fake_web.dial,
        clock=clock,
        sleep=sleeps.append,
    )


def _page(fake_web: "FakeWeb", host: str = "news.example", path: str = "/p/1") -> str:
    fake_web.route(host, "/robots.txt", 404)
    fake_web.route(host, path, body=ARTICLE)
    return f"http://{host}{path}"


# --- 讀得到 -----------------------------------------------------------------


@pytest.mark.promise("FR-IN-01")
def test_公開網頁_抽出正文_原始_HTML_依_hash_存進_blobs(
    fetcher: Fetcher, fake_web: "FakeWeb", data: DataDir
) -> None:
    url = _page(fake_web)
    outcome = fetcher.fetch(url)

    record = outcome.record
    assert (record.status, record.title) == ("ok", "Agents in production")
    assert record.text == "# Agents in production\n\n最難的是可觀測性。"
    assert record.raw_hash is not None
    assert (data.blobs / record.raw_hash).read_bytes() == ARTICLE.encode("utf-8")
    assert outcome.ledger_status == "ok"


@pytest.mark.promise("NFR-RUN-08")
def test_表明自己是誰_不假裝成瀏覽器(fetcher: Fetcher, fake_web: "FakeWeb") -> None:
    fetcher.fetch(_page(fake_web))
    agents = {agent for _, _, agent in fake_web.requests}
    assert agents == {
        "EvidenceForge/0.1 (+https://github.com/Zenobia000/evidence-forge)"
    }


def test_站內轉址照著走_記下最後的網址(fetcher: Fetcher, fake_web: "FakeWeb") -> None:
    _page(fake_web, path="/new")
    fake_web.route("news.example", "/old", 301, headers={"Location": "/new"})
    record = fetcher.fetch("http://news.example/old").record
    assert (record.status, record.final_url) == ("ok", "http://news.example/new")


# --- 中文網址（台灣創作者貼的網址常有中文字） ------------------------------------


@pytest.mark.promise("FR-IN-01")
def test_路徑有中文字的網址_編成百分比編碼送出(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    _page(fake_web, path="/wiki/%E5%8F%B0%E7%81%A3")
    record = fetcher.fetch("http://news.example/wiki/台灣").record
    assert (record.status, record.title) == ("ok", "Agents in production")


@pytest.mark.promise("FR-IN-01")
def test_中文網域名稱_用_IDNA_查_DNS_與連線(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    host = "新聞.example".encode("idna").decode("ascii")
    _page(fake_web, host=host)
    record = fetcher.fetch("http://新聞.example/p/1").record
    assert record.status == "ok"
    assert fake_web.hits(host, "/p/1") == 1


@pytest.mark.promise("FR-IN-01")
def test_轉址的_Location_帶原始_UTF_8_字元_照樣走得到(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    _page(fake_web, path="/%E6%96%B0")
    # 有些網站在 Location 直接放 UTF-8 bytes（不合規格但常見）
    raw = "/新".encode().decode("latin-1")
    fake_web.route("news.example", "/old", 301, headers={"Location": raw})
    record = fetcher.fetch("http://news.example/old").record
    assert (record.status, record.final_url) == (
        "ok",
        "http://news.example/%E6%96%B0",
    )


# --- 壓縮的回應：解壓有上限，壞的壓縮檔標讀不到 -----------------------------------


@pytest.mark.promise("NFR-SEC-03")
def test_解壓後超過上限_標讀不到_不把整份解開(
    fetcher: Fetcher, fake_web: "FakeWeb", monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_module, "MAX_BYTES", 1000)
    fake_web.route("news.example", "/robots.txt", 404)
    bomb = gzip.compress(b"\0" * 100_000)
    fake_web.route(
        "news.example", "/p/1", body=bomb, headers={"Content-Encoding": "gzip"}
    )
    record = fetcher.fetch("http://news.example/p/1").record
    assert record.status == "unavailable"
    assert "解壓" in record.reason


@pytest.mark.promise("NFR-RUN-08")
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_壞掉的壓縮內容_標讀不到(
    fetcher: Fetcher, fake_web: "FakeWeb", encoding: str
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route(
        "news.example",
        "/p/1",
        body=b"this is not compressed",
        headers={"Content-Encoding": encoding},
    )
    record = fetcher.fetch("http://news.example/p/1").record
    assert record.status == "unavailable"
    assert "壓縮" in record.reason


@pytest.mark.promise("FR-IN-01")
def test_deflate_沒有_zlib_標頭的也解得開(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    packer = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = packer.compress(ARTICLE.encode("utf-8")) + packer.flush()
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route(
        "news.example", "/p/1", body=raw, headers={"Content-Encoding": "deflate"}
    )
    record = fetcher.fetch("http://news.example/p/1").record
    assert (record.status, record.title) == ("ok", "Agents in production")


# --- 快取（NFR-COST-03） ------------------------------------------------------


@pytest.mark.promise("NFR-COST-03")
def test_期限內同一個網址不重抓_第二次是快取命中(
    fetcher: Fetcher, fake_web: "FakeWeb", clock: Clock
) -> None:
    url = _page(fake_web)
    fetcher.fetch(url)
    clock.now = T0 + timedelta(days=7) - timedelta(seconds=1)
    second = fetcher.fetch(url)

    assert fake_web.hits("news.example", "/p/1") == 1
    assert second.ledger_status == "cache_hit"
    assert second.record.text == "# Agents in production\n\n最難的是可觀測性。"


@pytest.mark.promise("NFR-COST-03", "NFR-KEEP-01")
def test_過期才重抓_舊的那一筆留著不覆寫(
    fetcher: Fetcher, fake_web: "FakeWeb", clock: Clock, data: DataDir
) -> None:
    url = _page(fake_web)
    fetcher.fetch(url)
    clock.now = T0 + timedelta(days=7)
    again = fetcher.fetch(url)

    assert fake_web.hits("news.example", "/p/1") == 2
    assert again.ledger_status == "ok"
    records = sorted((data.cache / "fetch").glob("*/*.json"))
    assert len(records) == 2


@pytest.mark.promise("NFR-COST-03")
def test_快取紀錄寫著抓取時間與到期時間_預設相差_7_天(
    fetcher: Fetcher, fake_web: "FakeWeb", data: DataDir
) -> None:
    record = fetcher.fetch(_page(fake_web)).record
    assert record.expires_at is not None
    assert record.expires_at - record.fetched_at == timedelta(days=7)
    assert len(list((data.cache / "fetch").glob("*/*.json"))) == 1


@pytest.mark.promise("NFR-COST-03")
def test_有效期限是設定值(
    data: DataDir, fake_web: "FakeWeb", clock: Clock, sleeps: list[float]
) -> None:
    short = Fetcher(
        data,
        ttl=timedelta(minutes=1),
        resolver=fake_web.resolver,
        dial=fake_web.dial,
        clock=clock,
        sleep=sleeps.append,
    )
    url = _page(fake_web)
    short.fetch(url)
    clock.now = T0 + timedelta(minutes=1)
    assert short.fetch(url).ledger_status == "ok"
    assert fake_web.hits("news.example", "/p/1") == 2


# --- SSRF（NFR-SEC-03） ------------------------------------------------------


@pytest.mark.promise("NFR-SEC-03")
def test_DNS_解析到私有位址_不連線(fetcher: Fetcher, fake_web: "FakeWeb") -> None:
    fake_web.dns["internal.example"] = ["10.0.0.5"]
    record = fetcher.fetch("http://internal.example/admin").record

    assert record.status == "unavailable"
    assert "內網" in record.reason
    assert fake_web.dialed == []


@pytest.mark.promise("NFR-SEC-03")
@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:127.0.0.1]/",
        "http://internal.example/",
    ],
)
def test_轉址到內網_那一跳重新檢查後擋下(
    fetcher: Fetcher, fake_web: "FakeWeb", target: str
) -> None:
    fake_web.dns["internal.example"] = ["192.168.0.10"]
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/go", 302, headers={"Location": target})
    record = fetcher.fetch("http://news.example/go").record

    assert record.status == "unavailable"
    assert "內網" in record.reason
    assert set(fake_web.dialed) == {"93.184.216.34"}


@pytest.mark.promise("NFR-SEC-03")
def test_直接貼本機服務的網址_一個請求都不送(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    # 假網站就開在 127.0.0.1：真的連得上，所以「沒收到請求」證明是被擋下，不是連不到
    record = fetcher.fetch(f"http://127.0.0.1:{fake_web.port}/bills").record
    assert record.status == "unavailable"
    assert fake_web.requests == []


@pytest.mark.promise("NFR-SEC-03")
def test_不是_http_的網址不抓(fetcher: Fetcher) -> None:
    record = fetcher.fetch("file:///etc/passwd").record
    assert (record.status, record.text) == ("unavailable", "")


# --- 讀不到：不繞過（NFR-RUN-08） ---------------------------------------------


@pytest.mark.promise("NFR-RUN-08")
def test_robots_禁止就不抓那一頁(fetcher: Fetcher, fake_web: "FakeWeb") -> None:
    fake_web.route(
        "news.example",
        "/robots.txt",
        body="User-agent: *\nDisallow: /p/\n",
        headers={"Content-Type": "text/plain"},
    )
    fake_web.route("news.example", "/p/1", body=ARTICLE)
    record = fetcher.fetch("http://news.example/p/1").record

    assert record.status == "unavailable"
    assert "robots" in record.reason
    assert fake_web.hits("news.example", "/p/1") == 0


@pytest.mark.promise("NFR-RUN-08")
def test_403_標讀不到_只試一次_不換身分重試(
    fetcher: Fetcher, fake_web: "FakeWeb"
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", 403, body="forbidden")
    outcome = fetcher.fetch("http://news.example/p/1")

    assert outcome.record.status == "unavailable"
    assert "403" in outcome.record.reason
    assert outcome.ledger_status == "error"
    assert fake_web.hits("news.example", "/p/1") == 1


@pytest.mark.promise("NFR-RUN-08")
def test_付費牆_標讀不到(fetcher: Fetcher, fake_web: "FakeWeb") -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route(
        "news.example",
        "/paid",
        body='<script type="application/ld+json">{"isAccessibleForFree": false}'
        "</script><article><p>只看得到前兩段</p></article>",
    )
    fake_web.route("news.example", "/402", 402)
    for path in ("/paid", "/402"):
        record = fetcher.fetch(f"http://news.example{path}").record
        assert record.status == "unavailable"
        assert "付費牆" in record.reason


@pytest.mark.promise("NFR-RUN-08")
def test_讀不到的結果不進快取_下次再試(
    fetcher: Fetcher, fake_web: "FakeWeb", data: DataDir
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", 403)
    fetcher.fetch("http://news.example/p/1")
    fetcher.fetch("http://news.example/p/1")
    assert fake_web.hits("news.example", "/p/1") == 2
    assert list((data.cache / "fetch").glob("*/*.json")) == []


@pytest.mark.promise("NFR-RUN-08")
def test_429_照_Retry_After_退避重試(
    fetcher: Fetcher, fake_web: "FakeWeb", sleeps: list[float]
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", 429, headers={"Retry-After": "3"})
    fake_web.route("news.example", "/p/1", body=ARTICLE)
    record = fetcher.fetch("http://news.example/p/1").record

    assert record.status == "ok"
    assert sleeps == [3.0]


@pytest.mark.promise("NFR-RUN-08")
def test_一直_429_就標讀不到_帳本記限流(
    fetcher: Fetcher, fake_web: "FakeWeb", sleeps: list[float]
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", 429)
    outcome = fetcher.fetch("http://news.example/p/1")

    assert outcome.record.status == "unavailable"
    assert outcome.ledger_status == "rate_limited"
    assert len(sleeps) == 2
