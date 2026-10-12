"""SEAM-02：閘道的 `fetch_url`、`run_script` 端點（跟 ef-tools 送的請求一模一樣）。

抓取本身的規則（SSRF、robots、403、付費牆、快取）在 `test_fetch.py`；
這裡只證明端點接上了同一個 `Fetcher`、帳本記到這張工單，以及 `run_script` 的範圍。
"""

import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.gateway.fetch import Fetcher
from evidence_forge.jobkey import JobKey
from evidence_forge.ledger_models import LedgerEntry
from evidence_forge.paths import DataDir

if TYPE_CHECKING:
    from integration.conftest import FakeWeb

JOB = "20261011-120000-aaaa1111"
# 閘道的時鐘：帳本上抓取、腳本與模型呼叫用同一個時間來源
NOW = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
KEY = JobKey(JOB, "digest", 1)

ARTICLE = "<article><h1>標題</h1><p>正文第一段。</p></article>"

COUNT_SCRIPT = """
import sys
text = sys.stdin.read()
print(len(text.split()), *sys.argv[1:])
"""


@dataclass
class Env:
    url: str
    data: DataDir
    skill_dir: Path
    stages: ActiveStages

    @property
    def job_dir(self) -> Path:
        return self.data.job_dir(JOB)

    def call(
        self, tool: str, key: JobKey | str = KEY, **body: object
    ) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}",
            json=body,
            headers={"x-api-key": str(key)},
            timeout=20,
        )

    def ledger(self) -> list[LedgerEntry]:
        path = self.job_dir / "ledger.jsonl"
        return [
            LedgerEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]


def _skill(root: Path) -> Path:
    skill = root / "skills" / "digest"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: digest\ndescription: test\nscripts: [count.py, slow.py, env.py]\n---\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "count.py").write_text(COUNT_SCRIPT, encoding="utf-8")
    (skill / "scripts" / "slow.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    (skill / "scripts" / "env.py").write_text(
        "import os, json\nprint(json.dumps(sorted(os.environ)))\n", encoding="utf-8"
    )
    # 沒列在清單上的腳本
    (skill / "scripts" / "unlisted.py").write_text("print('ran')\n", encoding="utf-8")
    # 別的 skill 的腳本
    other = root / "skills" / "other" / "scripts"
    other.mkdir(parents=True)
    (other / "evil.py").write_text("print('evil')\n", encoding="utf-8")
    return skill


@pytest.fixture
def env(
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
    fake_web: "FakeWeb",
) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.job_dir(JOB) / "input").mkdir(parents=True)
    (data.job_dir(JOB) / "input" / "content.md").write_text("a b c", encoding="utf-8")
    skill_dir = _skill(tmp_path / "harness")
    stages = ActiveStages()
    stages.activate(KEY, skill_dir=skill_dir)
    # 抓取要真的連（假的外網）：重播模式只回放錄音（見本檔最後的重播測試）
    config = GatewayConfig(data=data, mode="live", clock=lambda: NOW)
    fetcher = Fetcher(
        data, resolver=fake_web.resolver, dial=fake_web.dial, clock=config.clock
    )
    app = create_app(config, stages, fetcher=fetcher, script_timeout_s=1.0)
    with serve(app) as url:
        yield Env(url=url, data=data, skill_dir=skill_dir, stages=stages)


# --- fetch_url --------------------------------------------------------------


@pytest.mark.promise("NFR-SEC-03", "FR-LG-01")
def test_fetch_url_回正文_帳本記一筆_fetch(env: Env, fake_web: "FakeWeb") -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", body=ARTICLE)
    response = env.call("fetch_url", url="http://news.example/p/1")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["text"]) == ("ok", "# 標題\n\n正文第一段。")
    [entry] = env.ledger()
    assert (entry.kind, entry.stage, entry.attempt, entry.status) == (
        "fetch",
        "digest",
        1,
        "ok",
    )
    assert entry.response_hash is not None
    stored = json.loads((env.data.blobs / entry.response_hash).read_bytes())
    assert stored["text"] == "# 標題\n\n正文第一段。"


@pytest.mark.promise("NFR-SEC-03")
def test_fetch_url_擋內網_回讀不到與原因_帳本記_error(
    env: Env, fake_web: "FakeWeb"
) -> None:
    response = env.call("fetch_url", url="http://169.254.169.254/latest/meta-data/")

    body = response.json()
    assert body["status"] == "unavailable"
    assert "內網" in body["reason"]
    assert [e.status for e in env.ledger()] == ["error"]
    assert fake_web.dialed == []


@pytest.mark.promise("NFR-COST-03")
def test_fetch_url_第二次是快取命中_帳本記_cache_hit(
    env: Env, fake_web: "FakeWeb"
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", body=ARTICLE)
    env.call("fetch_url", url="http://news.example/p/1")
    env.call("fetch_url", url="http://news.example/p/1")

    assert [e.status for e in env.ledger()] == ["ok", "cache_hit"]
    assert fake_web.hits("news.example", "/p/1") == 1


@pytest.mark.promise("NFR-SEC-02")
def test_fetch_url_要正在跑的鑰匙(env: Env, fake_web: "FakeWeb") -> None:
    response = env.call(
        "fetch_url", JobKey(JOB, "digest", 9), url="http://news.example/p/1"
    )
    assert response.status_code == 403
    assert fake_web.requests == []


# --- run_script -------------------------------------------------------------


@pytest.mark.promise("NFR-SEC-02", "FR-LG-01")
def test_run_script_執行清單上的腳本_帳本記一筆_script(env: Env) -> None:
    response = env.call(
        "run_script", script="count.py", args=["x", "$HOME"], stdin="a b c"
    )

    assert response.status_code == 200
    body = response.json()
    # 參數原樣傳給腳本，不經 shell：$HOME 沒有被展開
    assert (body["exit_code"], body["stdout"]) == (0, "3 x $HOME\n")
    [entry] = env.ledger()
    assert (entry.kind, entry.status, entry.ts) == ("script", "ok", NOW)


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "script",
    [
        "unlisted.py",
        "../other/scripts/evil.py",
        "/bin/sh",
        "count.py; ls",
        "",
        "../SKILL.md",
    ],
)
def test_run_script_不在清單上的一律拒絕(env: Env, script: str) -> None:
    response = env.call("run_script", script=script)
    assert response.status_code == 403
    assert not (env.job_dir / "ledger.jsonl").exists()


@pytest.mark.promise("NFR-SEC-02")
def test_run_script_清單上的名字指到符號連結也拒絕(env: Env) -> None:
    target = env.skill_dir / "scripts" / "count.py"
    target.unlink()
    os.symlink(env.skill_dir.parent / "other" / "scripts" / "evil.py", target)
    assert env.call("run_script", script="count.py").status_code == 403


@pytest.mark.promise("NFR-SEC-02")
def test_run_script_這一段沒有登記_skill_就不能跑腳本(env: Env) -> None:
    key = JobKey(JOB, "compose", 1)
    env.stages.activate(key)
    assert env.call("run_script", key, script="count.py").status_code == 403


@pytest.mark.promise("FR-LG-01")
def test_run_script_超過時間就停掉_帳本記_timeout(env: Env) -> None:
    body = env.call("run_script", script="slow.py").json()
    assert body["timed_out"] is True
    assert [e.status for e in env.ledger()] == ["timeout"]


@pytest.mark.promise("NFR-SEC-04")
def test_run_script_拿不到閘道的環境變數(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 腳本跟閘道在同一台機器，但金鑰之類的環境變數不能傳下去
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-xxxx")
    names = json.loads(env.call("run_script", script="env.py").json()["stdout"])
    assert "ANTHROPIC_API_KEY" not in names


# --- 重播 -------------------------------------------------------------------


def _record(env: Env, tmp_path: Path) -> Path:
    """照錄音的做法：把工單的 ledger.jsonl 與它指到的 blobs/ 複製出來。"""
    recording = tmp_path / "recording"
    (recording / "blobs").mkdir(parents=True)
    shutil.copy(env.job_dir / "ledger.jsonl", recording / "ledger.jsonl")
    for entry in env.ledger():
        for digest in (entry.request_hash, entry.response_hash):
            if digest is not None:
                shutil.copy(env.data.blobs / digest, recording / "blobs" / digest)
    return recording


@pytest.mark.promise("NFR-COST-06")
def test_重播模式的_fetch_url_從錄音回放_不連網_沒錄過就報錯(
    env: Env,
    fake_web: "FakeWeb",
    tmp_path: Path,
    serve: Callable[[Any], AbstractContextManager[str]],
) -> None:
    fake_web.route("news.example", "/robots.txt", 404)
    fake_web.route("news.example", "/p/1", body=ARTICLE)
    live = env.call("fetch_url", url="http://news.example/p/1").json()
    blocked = env.call("fetch_url", url="http://169.254.169.254/latest/").json()
    recording = _record(env, tmp_path)
    requests = len(fake_web.requests)

    # 重播用另一個資料目錄：沒有快取，只能靠錄音
    data = DataDir(tmp_path / "replay-data")
    data.ensure()
    (data.job_dir(JOB) / "input").mkdir(parents=True)
    stages = ActiveStages()
    stages.activate(KEY)
    config = GatewayConfig(
        data=data, mode="replay", replay_dir=recording, clock=lambda: NOW
    )
    fetcher = Fetcher(
        data, resolver=fake_web.resolver, dial=fake_web.dial, clock=config.clock
    )
    with serve(create_app(config, stages, fetcher=fetcher)) as url:
        replay = Env(url=url, data=data, skill_dir=env.skill_dir, stages=stages)
        replayed = replay.call("fetch_url", url=" http://news.example/p/1 ").json()
        unavailable = replay.call(
            "fetch_url", url="http://169.254.169.254/latest/"
        ).json()
        missing = replay.call("fetch_url", url="http://news.example/p/2")

    assert len(fake_web.requests) == requests
    # 回給 agent 的內容跟錄音時一樣（抓取時間也一樣），之後的模型請求才對得上錄音
    assert replayed == {**live, "cache_hit": True}
    assert unavailable == {**blocked, "cache_hit": False}
    assert missing.status_code == 500
    assert "重播" in missing.json()["error"]
    assert [(e.kind, e.status) for e in replay.ledger()] == [
        ("fetch", "cache_hit"),
        ("fetch", "error"),
        ("fetch", "error"),
    ]
    assert replay.ledger()[0].response_hash == env.ledger()[0].response_hash
    assert replay.ledger()[2].response_hash is None
