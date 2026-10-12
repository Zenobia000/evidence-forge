"""`ef chat`（#80 spike）：一個程序裡跑閘道、執行器、聊天主機與聊天網頁。

跟 `ef serve` 一樣先做開跑前檢查、閘道只綁 127.0.0.1；多了：

- 閘道接上 `Desk`，對話鑰匙能用 `analyze`／`read_report`／`job_status`。
- 聊天網頁開在 `--ui-port`（也只綁 127.0.0.1）。
- `--fake-anthropic <http://127.0.0.1:埠>`：spike 用，把雲端等級轉到本機的假後端，不花錢。
  只准本機位址；金鑰改讀 `--secrets` 指定的資料夾（放一個假的），真的金鑰不送去任何地方。
"""

import argparse
import dataclasses
import os
import sys
import threading
import time
from urllib.parse import urlsplit

import uvicorn

from evidence_forge.chat import web
from evidence_forge.chat.desk import Desk
from evidence_forge.chat.hub import ChatHub
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.app import create_app
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import config_from_env
from evidence_forge.harness import Harness
from evidence_forge.models import Recipe
from evidence_forge.paths import resolve_data_dir
from evidence_forge.runner.pi_launch import prepare_agent_dir
from evidence_forge.runner.preflight import Preflight
from evidence_forge.runner.runner import Runner, taipei_now


def _loopback(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"--fake-anthropic 只准本機的 http：{url}")
    return url.rstrip("/")


def run(args: argparse.Namespace, harness: Harness, recipes: dict[str, Recipe]) -> int:
    data = resolve_data_dir()
    env = dict(os.environ)
    if args.secrets:
        env["EF_SECRETS"] = args.secrets
    config = config_from_env(data, env)
    if args.fake_anthropic:
        config = dataclasses.replace(config, anthropic_url=_loopback(args.fake_anthropic))
    gateway_url = f"http://127.0.0.1:{args.port}"
    preflight = Preflight(config, gateway_url)
    problems = preflight.startup()
    if problems:
        print("ef chat：開跑前檢查沒過：\n" + "\n".join(f"- {p}" for p in problems), file=sys.stderr)
        return 2
    data.ensure()
    stages = ActiveStages()
    board = JobBoard(config.clock)
    desk = Desk(data, recipes, args.recipe, clock=taipei_now, board=board)
    gateway = uvicorn.Server(
        uvicorn.Config(
            create_app(config, stages, board, desk=desk),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    )
    threading.Thread(target=gateway.run, daemon=True).start()
    while not gateway.started:
        time.sleep(0.05)
    # 對話的 Pi 跟段的 Pi 共用 $EF_DATA/run/pi-agent/ 的 models.json（執行器開段前也會寫）
    prepare_agent_dir(harness.pi, data, gateway_url)
    runner = Runner(
        data=data,
        harness=harness,
        recipes=recipes,
        stages=stages,
        gateway_url=gateway_url,
        board=board,
        max_parallel=args.parallel,
        preflight=preflight,
    )
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            runner.start_available()
            for job_id in runner.finished():
                print(f"工單 {job_id} 跑完", flush=True)
            stop.wait(0.5)

    threading.Thread(target=loop, daemon=True).start()
    hub = ChatHub(
        data=data,
        harness=harness,
        stages=stages,
        gateway_url=gateway_url,
        desk=desk,
        clock=taipei_now,
        tier=args.tier,
    )
    hub.start()
    print(
        f"閘道 {gateway_url}（{config.mode}）；聊天 http://127.0.0.1:{args.ui_port}；"
        f"資料目錄 {data.root}；分析配方 {args.recipe}",
        flush=True,
    )
    try:
        uvicorn.run(
            web.create_app(hub, data, taipei_now),
            host="127.0.0.1",
            port=args.ui_port,
            log_level="warning",
        )
    finally:
        stop.set()
        hub.close()
        gateway.should_exit = True
    return 0


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> argparse.ArgumentParser:
    chat = commands.add_parser("chat", help="（spike）聊天室：閘道＋執行器＋對話 Pi＋網頁")
    chat.add_argument("--port", type=int, default=8787, help="閘道的埠")
    chat.add_argument("--ui-port", type=int, default=8790, help="聊天網頁的埠")
    chat.add_argument("--recipe", default="analyze", help="analyze 工具開的配方（預設 analyze）")
    chat.add_argument("--tier", default="strong", help="對話本身用哪一級（預設 strong）")
    chat.add_argument("--parallel", type=int, default=2)
    chat.add_argument("--harness", help="harness 資料夾（預設 repo 的 harness/）")
    chat.add_argument("--fake-anthropic", help="把雲端等級轉到本機的假後端（spike 用）")
    chat.add_argument("--secrets", help="金鑰資料夾（配 --fake-anthropic 放假金鑰）")
    return chat


__all__ = ["add_parser", "run"]
