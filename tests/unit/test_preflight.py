"""SEAM-10（單元）：開跑前檢查（NFR-RUN-01）。探測全部是假的，不連 Ollama、閘道或 Anthropic。

每一項各自弄壞一次，斷言不開跑而且訊息點名那一項；金鑰只在配方用到雲端等級時才查。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from evidence_forge.gateway.config import GatewayConfig
from evidence_forge.models import Recipe
from evidence_forge.paths import DataDir
from evidence_forge.runner.preflight import Preflight, Probes

GIB = 1024**3


@dataclass
class FakeProbes:
    """每一項回傳什麼由測試設定；記下被呼叫的參數。"""

    ollama_problem: str | None = None
    gateway_problem: str | None = None
    key_problem: str | None = None
    writable_problem: str | None = None
    free: int = 50 * GIB
    calls: list[tuple[str, object]] = field(default_factory=list)

    def probes(self) -> Probes:
        def ollama(url: str, model: str, timeout_s: float) -> str | None:
            self.calls.append(("ollama", (url, model, timeout_s)))
            return self.ollama_problem

        def gateway(url: str) -> str | None:
            self.calls.append(("gateway", url))
            return self.gateway_problem

        # 金鑰只有閘道讀（NFR-SEC-04）：真的探測是問閘道，這裡只拿到閘道的位址
        def anthropic_key(gateway_url: str) -> str | None:
            self.calls.append(("anthropic", gateway_url))
            return self.key_problem

        def free_bytes(path: Path) -> int:
            return self.free

        def writable(path: Path) -> str | None:
            return self.writable_problem

        return Probes(
            ollama=ollama,
            gateway=gateway,
            anthropic_key=anthropic_key,
            free_bytes=free_bytes,
            writable=writable,
        )

    def called(self, name: str) -> bool:
        return any(call[0] == name for call in self.calls)


def _recipe(tier: str = "local", escalate_to: str | list[str] | None = None) -> Recipe:
    return Recipe.model_validate(
        {
            "name": "r",
            "description": "test",
            "stages": [
                {
                    "id": "s",
                    "skill": "echo",
                    "tier": tier,
                    "output": ["a.json"],
                    **({"escalate_to": escalate_to} if escalate_to else {}),
                }
            ],
            "on_feedback": "s",
        }
    )


def _preflight(
    tmp_path: Path, fake: FakeProbes, mode: str = "live", fault: str | None = None
) -> Preflight:
    config = GatewayConfig(
        data=DataDir(tmp_path / "data"),
        mode=mode,  # type: ignore[arg-type]
        replay_dir=tmp_path / "recording" if mode == "replay" else None,
        fault=fault,  # type: ignore[arg-type]
        secrets_dir=tmp_path / "secrets",
    )
    return Preflight(config, "http://127.0.0.1:8787", probes=fake.probes())


@pytest.mark.promise("NFR-RUN-01")
def test_全部正常_只用本機等級_可以開跑_也不查金鑰(tmp_path: Path) -> None:
    fake = FakeProbes(key_problem="金鑰無效")
    assert _preflight(tmp_path, fake)(_recipe()) == []
    assert fake.called("ollama") and fake.called("gateway")
    assert not fake.called("anthropic")


@pytest.mark.promise("NFR-RUN-01", "NFR-RUN-05")
def test_Ollama_沒回應_不開跑並點名這一項(tmp_path: Path) -> None:
    fake = FakeProbes(ollama_problem="連不上 http://127.0.0.1:11434")
    problems = _preflight(tmp_path, fake)(_recipe())
    assert len(problems) == 1 and problems[0].startswith("Ollama 沒回應")


@pytest.mark.promise("NFR-RUN-01")
def test_查_Ollama_時逾時算進模型冷啟動的_75_秒(tmp_path: Path) -> None:
    fake = FakeProbes()
    _preflight(tmp_path, fake)(_recipe())
    _, model, timeout_s = next(
        cast(tuple[str, str, float], args)
        for name, args in fake.calls
        if name == "ollama"
    )
    assert model == "qwen3.5:9b"
    assert timeout_s > 75


@pytest.mark.promise("NFR-RUN-01")
@pytest.mark.parametrize(
    ("tier", "escalate_to"),
    [
        ("strong", None),
        ("local", "strong"),
        ("max", None),
        ("fast", None),
        ("local", ["fast", "strong"]),
    ],
)
def test_配方用到雲端等級_金鑰無效就不開跑(
    tmp_path: Path, tier: str, escalate_to: str | list[str] | None
) -> None:
    fake = FakeProbes(key_problem="金鑰無效（HTTP 401）")
    problems = _preflight(tmp_path, fake)(_recipe(tier, escalate_to))
    assert [p for p in problems if p.startswith("Anthropic 金鑰")] != []


@pytest.mark.promise("NFR-RUN-01")
def test_正式執行設了故障注入_不開跑並點名(tmp_path: Path) -> None:
    fake = FakeProbes()
    problems = _preflight(tmp_path, fake, fault="rate_limit")(_recipe())
    assert any("EF_GATEWAY_FAULT" in p for p in problems)


def test_重播模式可以配故障注入_也不查後端(tmp_path: Path) -> None:
    fake = FakeProbes(ollama_problem="不該被問到", key_problem="不該被問到")
    preflight = _preflight(tmp_path, fake, mode="replay", fault="bad_output")
    assert preflight(_recipe("strong")) == []
    assert not fake.called("ollama") and not fake.called("anthropic")


@pytest.mark.promise("NFR-RUN-01")
def test_硬碟空間不夠_不開跑並點名(tmp_path: Path) -> None:
    fake = FakeProbes(free=100 * 1024**2)
    problems = _preflight(tmp_path, fake)(_recipe())
    assert len(problems) == 1 and problems[0].startswith("硬碟空間")


@pytest.mark.promise("NFR-RUN-01")
def test_資料目錄不可寫_不開跑並點名(tmp_path: Path) -> None:
    fake = FakeProbes(writable_problem="Permission denied")
    problems = _preflight(tmp_path, fake)(_recipe())
    assert any(p.startswith("資料目錄不可寫") for p in problems)


@pytest.mark.promise("NFR-RUN-01")
def test_閘道連不上_不開跑並點名(tmp_path: Path) -> None:
    fake = FakeProbes(gateway_problem="Connection refused")
    problems = _preflight(tmp_path, fake)(_recipe())
    assert len(problems) == 1 and problems[0].startswith("閘道連不上")


def test_啟動時的檢查只看跟配方無關的項目(tmp_path: Path) -> None:
    fake = FakeProbes(ollama_problem="x", writable_problem="Permission denied")
    problems = _preflight(tmp_path, fake, fault="backend_down").startup()
    assert any("EF_GATEWAY_FAULT" in p for p in problems)
    assert any(p.startswith("資料目錄不可寫") for p in problems)
    assert not fake.called("ollama")


@pytest.mark.promise("NFR-RUN-01")
def test_Pi_沒裝_啟動就擋_訊息說要在_harness_pi_跑_npm_ci(tmp_path: Path) -> None:
    # #68：沒裝 Pi 時 `ef serve` 照樣啟動，等第一張單開 Pi 才失敗，同素材同目標的單又被去重
    preflight = _preflight(tmp_path, FakeProbes())
    preflight.pi_bin = tmp_path / "harness" / "pi" / "node_modules" / ".bin" / "pi"
    problems = preflight.startup()
    assert len(problems) == 1
    assert problems[0].startswith("Pi 沒裝")
    assert "npm ci" in problems[0] and str(preflight.pi_bin) in problems[0]
    # 每張單開跑前也查（事件紀錄寫得到原因）
    assert preflight(_recipe())[0].startswith("Pi 沒裝")


def test_Pi_裝好了就不擋(tmp_path: Path) -> None:
    preflight = _preflight(tmp_path, FakeProbes())
    preflight.pi_bin = tmp_path / "pi"
    preflight.pi_bin.write_text("", encoding="utf-8")
    assert preflight.startup() == []
