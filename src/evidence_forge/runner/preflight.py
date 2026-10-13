"""開跑前檢查（NFR-RUN-01、架構草案 v0.3 §5、測試藍圖 SEAM-10）。

每次 `ef serve` 啟動、每張單開跑前都查；任何一項沒過就不開跑，訊息開頭就是那一項的名字：

| 項目 | 什麼時候查 |
| :--- | :--- |
| 故障注入 | 一律：正式執行（不是重播模式）設了 `EF_GATEWAY_FAULT` 就擋 |
| Pi 沒裝 | 一律：`harness/pi/` 鎖版的 Pi 不在，每一段都開不了（#68） |
| 資料目錄不可寫 | 一律 |
| 硬碟空間 | 一律 |
| 閘道連不上 | 每張單開跑前（啟動時閘道才剛開） |
| Ollama 沒回應 | 正式執行、而且這張單的配方用到本機等級；模型沒載入就先載，逾時算進冷啟動的 75 秒 |
| Anthropic 金鑰 | 正式執行、而且配方的段或 `escalate_to` 用到雲端等級；金鑰只有閘道讀，所以問閘道 |

重播模式不會轉送任何請求，所以不查後端（CI 與 UAT 的重播也就不需要 Ollama）。
探測都可以換掉：測試給假的，不連真的服務。
"""

import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.models import Recipe

GIB = 1024**3
# 本機模型冷啟動約 75 秒（#10 第 6 題），載入的逾時要比它長
OLLAMA_LOAD_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Probes:
    """每一項怎麼查。回傳沒過的原因；過了回傳 None。"""

    # (Ollama 位址, 模型, 逾時秒數)
    ollama: Callable[[str, str, float], str | None]
    # 閘道位址
    gateway: Callable[[str], str | None]
    # 閘道位址：金鑰只有閘道讀（NFR-SEC-04），由閘道去問 Anthropic
    anthropic_key: Callable[[str], str | None]
    free_bytes: Callable[[Path], int]
    writable: Callable[[Path], str | None]


def _ollama(url: str, model: str, timeout_s: float) -> str | None:
    try:
        loaded = httpx.get(f"{url}/api/ps", timeout=5.0)
        loaded.raise_for_status()
        names = {
            str(entry.get(field_name))
            for entry in loaded.json().get("models", [])
            if isinstance(entry, dict)
            for field_name in ("name", "model")
        }
        if model in names:
            return None
        # 沒載入：送一個沒有提示詞的請求，Ollama 會把模型載起來
        response = httpx.post(
            f"{url}/api/generate", json={"model": model}, timeout=timeout_s
        )
    except httpx.HTTPError as exc:
        return f"連不上 {url}（{type(exc).__name__}）"
    except ValueError:
        return f"{url}/api/ps 回的不是 JSON"
    if response.status_code != 200:
        return f"模型 {model} 載不起來（HTTP {response.status_code}）"
    return None


def _gateway(url: str) -> str | None:
    try:
        response = httpx.get(f"{url}/health", timeout=5.0)
    except httpx.HTTPError as exc:
        return f"{url}（{type(exc).__name__}）"
    return (
        None if response.status_code == 200 else f"{url}（HTTP {response.status_code}）"
    )


def _anthropic_key(gateway_url: str) -> str | None:
    try:
        response = httpx.get(f"{gateway_url}/health/anthropic", timeout=20.0)
    except httpx.HTTPError as exc:
        return f"問不到閘道（{type(exc).__name__}）"
    if response.status_code == 200:
        return None
    try:
        return str(response.json()["error"])
    except (ValueError, KeyError, TypeError):
        return f"閘道回 HTTP {response.status_code}"


def _free_bytes(path: Path) -> int:
    # 資料目錄還沒建時，往上找到存在的那一層
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def _writable(path: Path) -> str | None:
    probe = path / "run" / f".write-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return f"{path}（{exc.strerror or type(exc).__name__}）"
    return None


def http_probes() -> Probes:
    return Probes(
        ollama=_ollama,
        gateway=_gateway,
        anthropic_key=_anthropic_key,
        free_bytes=_free_bytes,
        writable=_writable,
    )


@dataclass
class Preflight:
    config: GatewayConfig
    gateway_url: str
    probes: Probes = field(default_factory=http_probes)
    min_free_bytes: int = GIB
    ollama_timeout_s: float = OLLAMA_LOAD_TIMEOUT_S
    # 執行器要開的 Pi（`Harness.pi.pi_bin`）；None 表示不查（測試用假 Pi 的地方）
    pi_bin: Path | None = None

    def startup(self) -> list[str]:
        """`ef serve` 一啟動就查、跟配方無關的項目：沒過就不啟動。"""
        problems: list[str] = []
        if self.config.fault is not None and self.config.mode != "replay":
            problems.append(
                f"故障注入：正式執行不能設 EF_GATEWAY_FAULT（現在是 {self.config.fault}），"
                "它只能配 EF_GATEWAY_MODE=replay 用"
            )
        if self.pi_bin is not None and not self.pi_bin.is_file():
            problems.append(
                f"Pi 沒裝：找不到 {self.pi_bin}，"
                "請用 nvm 的 npm 在 harness/pi/ 跑 npm ci"
            )
        root = self.config.data.root
        problem = self.probes.writable(root)
        if problem is not None:
            problems.append(f"資料目錄不可寫：{problem}")
        free = self.probes.free_bytes(root)
        if free < self.min_free_bytes:
            problems.append(
                f"硬碟空間不夠：{root} 只剩 {free / GIB:.1f} GB，"
                f"至少要 {self.min_free_bytes / GIB:.1f} GB"
            )
        return problems

    def __call__(self, recipe: Recipe) -> list[str]:
        """這張單開跑前要過的全部項目。"""
        problems = self.startup()
        problem = self.probes.gateway(self.gateway_url)
        if problem is not None:
            problems.append(f"閘道連不上：{problem}")
        if self.config.mode == "replay":
            return problems
        backends = self._backends(recipe)
        for model in backends.get("ollama", ()):
            problem = self.probes.ollama(
                self.config.ollama_url, model, self.ollama_timeout_s
            )
            if problem is not None:
                problems.append(f"Ollama 沒回應：{problem}")
        if "anthropic" in backends:
            problem = self.probes.anthropic_key(self.gateway_url)
            if problem is not None:
                problems.append(f"Anthropic 金鑰：{problem}")
        return problems

    def _backends(self, recipe: Recipe) -> dict[str, list[str]]:
        """配方用到的等級（含 `escalate_to`）各打哪個後端、哪個模型。"""
        tiers: list[str] = []
        for stage in recipe.stages:
            for used in (stage.tier, stage.escalate_to):
                if used is not None and used not in tiers:
                    tiers.append(used)
        backends: dict[str, list[str]] = {}
        for tier in tiers:
            alias = self.config.aliases.get(tier)
            if alias is None:
                continue  # 配方檢查已經擋掉對照表沒有的等級（FR-RC-01）
            models = backends.setdefault(alias.backend, [])
            if alias.model not in models:
                models.append(alias.model)
        return backends
