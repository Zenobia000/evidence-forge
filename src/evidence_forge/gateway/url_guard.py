"""抓網頁之前的網址與位址檢查（NFR-SEC-03，擋 SSRF）。

agent 的 `fetch_url` 與手動投入的網址共用這一份規則，不准另寫一份。

規則：
- 網址只收 `http`、`https`，要有主機名稱，不准帶帳號密碼。
- 判斷的是 **DNS 解析之後** 的 IP，不是網址上的字：`internal.example` 解析到 10.0.0.5 一樣擋。
- 解析出好幾個位址時，只要有一個不是公開位址就整個擋（連線時可能選到那一個）。
- 公開位址的定義交給標準庫的 `is_global`（私有、回環、link-local、共用位址、保留位址
  都不算），再加上多播；IPv6 裡包著 IPv4 的（映射、相容、NAT64、6to4、Teredo）
  要把裡面那個 IPv4 也拿出來判斷，不然 `::ffff:127.0.0.1` 會被當成公開位址。

連線時真正用的 IP 就是這裡檢查過的那一個（`gateway/fetch.py` 的連線層），
不會檢查完再解析一次，避免 DNS rebinding。轉址的每一跳都重新走一遍。
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

# 主機名稱 → IP 字串清單。測試注入假的解析器，正式用系統的 DNS。
Resolver = Callable[[str], list[str]]

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# 路徑與查詢字串裡，除了這些以外的字元（中文、空白等）都要百分比編碼；已經是 %XX 的不動
_URL_SAFE = "!#$%&'()*+,/:;=?@[]~"

# NAT64 的兩段前綴：後 32 位元就是要連的 IPv4
_NAT64 = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
# 舊式的「IPv4 相容」IPv6 位址（::a.b.c.d），已廢棄，但有些系統還會照著連
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


class UrlBlocked(Exception):
    """這個網址不抓；`reason` 是給人看的原因。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_url(url: str) -> SplitResult:
    """網址格式檢查；不通過丟 `UrlBlocked`。不碰 DNS。"""
    try:
        parts = urlsplit(url.strip())
        hostname = parts.hostname
        _ = parts.port  # 埠號不是數字或超出範圍時，這裡會丟 ValueError
    except ValueError:
        raise UrlBlocked(f"網址格式不對：{url}") from None
    if parts.scheme not in ("http", "https"):
        raise UrlBlocked(f"只抓 http 與 https 的網址：{url}")
    if not hostname:
        raise UrlBlocked(f"網址沒有主機名稱：{url}")
    if parts.username is not None or parts.password is not None:
        raise UrlBlocked("網址不能帶帳號密碼")
    return parts


def ascii_url(url: str) -> str:
    """檢查網址格式（`check_url`），再轉成連線用的純 ASCII 網址。

    台灣的網址常有中文：主機名稱用 IDNA（`新聞.tw` → `xn--...`），路徑與查詢字串做
    百分比編碼；`#` 之後只在瀏覽器裡用，不送出。不通過丟 `UrlBlocked`。
    """
    parts = check_url(url)
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UrlBlocked(f"主機名稱不合規格：{host}") from None
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        netloc += f":{parts.port}"
    path = quote(parts.path, safe=_URL_SAFE) or "/"
    query = quote(parts.query, safe=_URL_SAFE)
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _embedded_ipv4(address: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv6 位址裡包著的 IPv4（映射、相容、NAT64、6to4、Teredo）。"""
    found: list[ipaddress.IPv4Address] = []
    if address.ipv4_mapped is not None:
        found.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        found.append(address.sixtofour)
    if address.teredo is not None:
        found.extend(address.teredo)
    if address in _IPV4_COMPATIBLE or any(address in net for net in _NAT64):
        found.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return found


def _is_public(address: _IPAddress) -> bool:
    if address.is_multicast or not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return all(_is_public(inner) for inner in _embedded_ipv4(address))
    return True


def is_public_address(address: str) -> bool:
    """這個 IP 是不是可以連的公開位址。看不懂的字串一律不是。"""
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return _is_public(parsed)


def system_resolver(host: str) -> list[str]:
    """用系統的 DNS 解析主機名稱；解析不到回空清單。"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def resolve_public(host: str, resolver: Resolver) -> list[str]:
    """解析主機名稱，確認每一個位址都是公開位址，回傳可以連的 IP。

    網址上直接寫 IP 就不經解析器。不通過丟 `UrlBlocked`。
    """
    literal = host.strip("[]")
    try:
        ipaddress.ip_address(literal.split("%", 1)[0])
        addresses = [literal]
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise UrlBlocked(f"解析不到主機 {host}")
    blocked = [address for address in addresses if not is_public_address(address)]
    if blocked:
        raise UrlBlocked(f"{host} 指到內網或保留位址（{', '.join(blocked)}），不抓")
    return addresses
