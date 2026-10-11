"""SEAM-12、SEAM-02：agent 的工具鑰匙與讀寫範圍。

直接用工單鑰匙打閘道的 `/tools/<名稱>`，跟 ef-tools 送的請求一模一樣。
`write_file` 只准寫 `stages/<這一段>/<這一次>/`：只限制在工單資料夾內的話，
原文裡的提示注入就能讓 agent 寫出 `review/decision.v1.json`，自己核准自己。
"""

import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from evidence_forge.gateway.app import ActiveStages, GatewayConfig, create_app
from evidence_forge.jobkey import JobKey
from evidence_forge.paths import DataDir

JOB = "20261011-120000-aaaa1111"
OTHER_JOB = "20261011-120000-bbbb2222"
KEY = JobKey(JOB, "echo", 2)


@dataclass
class Env:
    url: str
    data: DataDir
    stages: ActiveStages

    @property
    def job_dir(self) -> Path:
        return self.data.job_dir(JOB)

    def call(self, tool: str, key: JobKey | str, **body: object) -> httpx.Response:
        return httpx.post(
            f"{self.url}/tools/{tool}",
            json=body,
            headers={"x-api-key": str(key)},
            timeout=10,
        )


@pytest.fixture
def env(
    tmp_path: Path, serve: Callable[[Any], AbstractContextManager[str]]
) -> Iterator[Env]:
    data = DataDir(tmp_path / "data")
    data.ensure()
    for job_id in (JOB, OTHER_JOB):
        job_dir = data.job_dir(job_id)
        (job_dir / "input").mkdir(parents=True)
        (job_dir / "input" / "content.md").write_text("原文", encoding="utf-8")
        (job_dir / "job.md").write_text("---\nstatus: running\n---\n", encoding="utf-8")
        (job_dir / "stages" / "echo" / "1").mkdir(parents=True)
        (job_dir / "stages" / "echo" / "2").mkdir(parents=True)
    stages = ActiveStages()
    stages.activate(KEY)
    # 工具端點不碰模型；錄音是空的，任何模型請求都會被拒
    config = GatewayConfig(
        data=data, mode="replay", replay_dir=tmp_path / "no-recording"
    )
    app = create_app(config, stages)
    with serve(app) as url:
        yield Env(url=url, data=data, stages=stages)


# --- write_file：只准寫這一段、這一次的目錄 --------------------------------


@pytest.mark.promise("NFR-SEC-02", "NFR-KEEP-02")
def test_寫這一段這一次的目錄成功(env: Env) -> None:
    response = env.call(
        "write_file", KEY, path="stages/echo/2/summary.json", content='{"a": 1}'
    )
    assert response.status_code == 200
    written = env.job_dir / "stages" / "echo" / "2" / "summary.json"
    assert written.read_text(encoding="utf-8") == '{"a": 1}'


@pytest.mark.promise("NFR-SEC-02", "NFR-KEEP-02")
@pytest.mark.parametrize(
    "path",
    [
        "review/decision.v1.json",
        "job.md",
        "events.jsonl",
        "ledger.jsonl",
        "stages/compose/2/draft.md",  # 別的段
        "stages/echo/1/summary.json",  # 同一段較早那一次
        "stages/echo/2",  # 目錄本身
        "stages/echo/summary.json",
        "input/content2.md",
    ],
)
def test_寫這一段這一次以外的地方一律拒絕(env: Env, path: str) -> None:
    response = env.call("write_file", KEY, path=path, content="注入")
    assert response.status_code == 403
    assert not (env.job_dir / path).is_file() or path == "job.md"
    assert (
        (env.job_dir / "job.md").read_text(encoding="utf-8").endswith("running\n---\n")
    )


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "path",
    [
        f"../{OTHER_JOB}/stages/echo/2/x.md",
        "stages/echo/2/../../../review/decision.v1.json",
        "/tmp/evil.md",
        "stages\\echo\\2\\x.md",
        "",
    ],
)
def test_上一層_絕對路徑與反斜線都拒絕(env: Env, path: str) -> None:
    response = env.call("write_file", KEY, path=path, content="注入")
    assert response.status_code in (400, 403)
    assert not (env.data.job_dir(OTHER_JOB) / "stages" / "echo" / "2" / "x.md").exists()
    assert not (env.job_dir / "review").exists()


@pytest.mark.promise("NFR-SEC-02")
def test_經過符號連結寫出去拒絕(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, env.job_dir / "stages" / "echo" / "2" / "link")
    response = env.call(
        "write_file", KEY, path="stages/echo/2/link/x.md", content="注入"
    )
    assert response.status_code == 403
    assert list(outside.iterdir()) == []


@pytest.mark.promise("NFR-SEC-02")
def test_寫到既有的符號連結拒絕(env: Env, tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    os.symlink(target, env.job_dir / "stages" / "echo" / "2" / "out.md")
    response = env.call("write_file", KEY, path="stages/echo/2/out.md", content="注入")
    assert response.status_code in (403, 409)
    assert not target.exists()


@pytest.mark.promise("NFR-KEEP-01", "NFR-SEC-02")
def test_同名第二次寫入拒絕_原檔不動(env: Env) -> None:
    path = "stages/echo/2/summary.json"
    assert env.call("write_file", KEY, path=path, content="第一版").status_code == 200
    response = env.call("write_file", KEY, path=path, content="第二版")
    assert response.status_code == 409
    assert (env.job_dir / path).read_text(encoding="utf-8") == "第一版"


# --- 鑰匙：只接受正在跑的那一段、那一次 ----------------------------------------


STALE_KEYS = {
    # 第 2 次已經開始，第 1 次的 Pi 還沒死透，拿著第 1 次的鑰匙
    "前一次": JobKey(JOB, "echo", 1),
    # 別段
    "別段": JobKey(JOB, "compose", 2),
    # 別張工單（沒有在跑）
    "別張工單": JobKey(OTHER_JOB, "echo", 2),
}


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize("name", sorted(STALE_KEYS))
@pytest.mark.parametrize(
    ("tool", "body"),
    [
        ("read_file", {"path": "input/content.md"}),
        ("list_files", {"path": "."}),
        ("write_file", {"path": "stages/echo/1/late.md", "content": "晚到"}),
    ],
)
def test_不是正在跑的鑰匙_讀寫全部拒絕(
    env: Env, name: str, tool: str, body: dict[str, str]
) -> None:
    response = env.call(tool, STALE_KEYS[name], **body)
    assert response.status_code == 403
    assert not (env.job_dir / "stages" / "echo" / "1" / "late.md").exists()


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    ("tool", "body"),
    [
        ("read_file", {"path": "input/content.md"}),
        ("list_files", {"path": "."}),
        ("write_file", {"path": "stages/echo/2/late.md", "content": "晚到"}),
    ],
)
def test_已經結束的工單拿舊鑰匙_讀寫全部拒絕(
    env: Env, tool: str, body: dict[str, str]
) -> None:
    env.stages.deactivate(KEY)
    response = env.call(tool, KEY, **body)
    assert response.status_code == 403
    assert not (env.job_dir / "stages" / "echo" / "2" / "late.md").exists()


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize("key", ["", "sk-ant-xyz", "job:../x:echo:1", "job:a:b:0"])
def test_格式不對的鑰匙拒絕(env: Env, key: str) -> None:
    assert env.call("read_file", key, path="job.md").status_code == 403


# --- read_file、list_files：限制在工單資料夾內 -------------------------------


def test_讀工單資料夾裡的檔(env: Env) -> None:
    response = env.call("read_file", KEY, path="input/content.md")
    assert response.status_code == 200
    assert response.json()["content"] == "原文"


@pytest.mark.promise("NFR-SEC-02")
@pytest.mark.parametrize(
    "path", [f"../{OTHER_JOB}/input/content.md", "/etc/hostname", "input/../../x"]
)
def test_讀工單資料夾外拒絕(env: Env, path: str) -> None:
    assert env.call("read_file", KEY, path=path).status_code in (400, 403)


@pytest.mark.promise("NFR-SEC-02")
def test_經過符號連結讀出去拒絕(env: Env) -> None:
    other_input = env.data.job_dir(OTHER_JOB) / "input"
    os.symlink(other_input, env.job_dir / "input" / "peek")
    response = env.call("read_file", KEY, path="input/peek/content.md")
    assert response.status_code == 403


def test_讀不存在的檔回_404(env: Env) -> None:
    assert env.call("read_file", KEY, path="input/nope.md").status_code == 404


def test_列出資料夾_依名稱排序_資料夾帶斜線(env: Env) -> None:
    response = env.call("list_files", KEY, path=".")
    assert response.status_code == 200
    assert response.json()["entries"] == ["input/", "job.md", "stages/"]


@pytest.mark.promise("NFR-SEC-02")
def test_列出工單資料夾外拒絕(env: Env) -> None:
    assert env.call("list_files", KEY, path="..").status_code in (400, 403)
