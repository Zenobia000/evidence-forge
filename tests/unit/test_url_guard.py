"""NFR-SEC-03：抓網頁前的網址與位址判斷（單元，不連網）。

位址判斷看的是 DNS 解析之後的 IP，不是網址上寫的字；網址上的主機名稱交給假的解析器。
"""

import pytest

from evidence_forge.gateway.url_guard import (
    UrlBlocked,
    check_url,
    is_public_address,
    resolve_public,
)

BLOCKED = [
    "127.0.0.1",  # 回環
    "127.8.9.10",
    "::1",
    "10.0.0.1",  # 10/8
    "10.255.255.254",
    "172.16.0.1",  # 172.16/12
    "172.31.255.254",
    "192.168.1.1",  # 192.168/16
    "169.254.169.254",  # 雲端中繼資料（AWS、GCP、Azure）
    "169.254.0.1",  # link-local
    "100.100.100.200",  # 阿里雲中繼資料（100.64/10 共用位址）
    "fd00:ec2::254",  # AWS 的 IPv6 中繼資料
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 私有（ULA）
    "0.0.0.0",
    "::",
    "224.0.0.1",  # 多播
    "ff02::1",
    "::ffff:127.0.0.1",  # IPv4 映射的 IPv6
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    "::127.0.0.1",  # 舊式 IPv4 相容位址
    "64:ff9b::7f00:1",  # NAT64 包著 127.0.0.1
    "64:ff9b::a9fe:a9fe",  # NAT64 包著 169.254.169.254
    "2002:7f00:1::",  # 6to4 包著 127.0.0.1
]


@pytest.mark.promise("NFR-SEC-03")
@pytest.mark.parametrize("address", BLOCKED)
def test_內網_回環_link_local_中繼資料位址都不算公開位址(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.promise("NFR-SEC-03")
@pytest.mark.parametrize(
    "address", ["93.184.216.34", "8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8"]
)
def test_公開位址放行(address: str) -> None:
    assert is_public_address(address)


@pytest.mark.promise("NFR-SEC-03")
def test_主機名稱解析到私有位址就擋() -> None:
    def resolver(host: str) -> list[str]:
        assert host == "internal.example"
        return ["10.0.0.5"]

    with pytest.raises(UrlBlocked, match="內網"):
        resolve_public("internal.example", resolver)


@pytest.mark.promise("NFR-SEC-03")
def test_解析出好幾個位址_只要有一個是私有的就擋() -> None:
    # 只檢查第一個的話，連線時可能選到後面那個私有位址
    with pytest.raises(UrlBlocked):
        resolve_public("mixed.example", lambda _: ["93.184.216.34", "127.0.0.1"])


@pytest.mark.promise("NFR-SEC-03")
def test_網址直接寫_IP_也照樣判斷_不靠解析器() -> None:
    def resolver(host: str) -> list[str]:
        raise AssertionError("IP 不該再丟給解析器")

    with pytest.raises(UrlBlocked):
        resolve_public("169.254.169.254", resolver)
    with pytest.raises(UrlBlocked):
        resolve_public("::1", resolver)
    assert resolve_public("93.184.216.34", resolver) == ["93.184.216.34"]


@pytest.mark.promise("NFR-SEC-03")
def test_解析不到就算讀不到() -> None:
    with pytest.raises(UrlBlocked, match="解析"):
        resolve_public("nowhere.example", lambda _: [])


@pytest.mark.promise("NFR-SEC-03")
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "gopher://example.com/",
        "http://user:pass@example.com/",
        "http:///nohost",
        "not a url",
        "",
    ],
)
def test_只收_http_與_https_而且不准帶帳號密碼(url: str) -> None:
    with pytest.raises(UrlBlocked):
        check_url(url)


def test_一般的_https_網址通過格式檢查() -> None:
    assert check_url("https://example.com/p/agents?x=1#top").hostname == "example.com"
