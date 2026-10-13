"""指令列 `ef`。

- `ef new --url <網址> | --text "<內容>" | --file <md|txt|pdf>`：先抓取與抽文字，在
  `inbox/manual/` 寫一個素材項目，開一張排隊中的工單；配方預設 `analyze`（先分析，#78）；可帶 `--goal`、`--recipe`、
  `--genre`、`--budget-usd`、`--budget-credits`（Tavily credits；沒給就用配方各段的總和）。同一份素材、同樣的目標已有工單就印那一張的 id，不另開。
  網址讀不到（403、付費牆、robots、內網位址）不開單，結束碼 1，提示改用 `--text` 或 `--file`。
- `ef serve`：常駐程序，裡面有閘道（只綁 127.0.0.1）與執行器（v0.3 §1：S0 先放同一個程序）。
  啟動時與每張單開跑前做開跑前檢查（NFR-RUN-01，含 `harness/pi/` 的 Pi 有沒有裝）；同時最多跑 `--parallel` 張（預設 2）；
  重開後先接著跑上次沒跑完的單（NFR-RUN-04）。`--once` 跑完就結束，被開跑前檢查擋下時結束碼 1。

兩個指令啟動時都先讀全部配方，有一份不能用就拒絕並點名（FR-RC-01）。
"""

import argparse
import sys
import threading
import time
from pathlib import Path

import uvicorn

from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import load_aliases
from evidence_forge.gateway.app import create_app
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.config import config_from_env
from evidence_forge.harness import Harness, default_harness
from evidence_forge.models import Recipe
from evidence_forge.paths import resolve_data_dir
from evidence_forge.runner.intake import (
    PastedText,
    Source,
    SourceUnreadable,
    UploadedFile,
    WebPage,
    open_job,
)
from evidence_forge.runner.preflight import Preflight
from evidence_forge.runner.recipes import RecipeError, load_recipes
from evidence_forge.runner.runner import Runner, taipei_now


def _harness(path: str | None) -> Harness:
    return Harness.at(Path(path).resolve()) if path else default_harness()


def _recipes(harness: Harness) -> dict[str, Recipe]:
    return load_recipes(harness, tiers=load_aliases().keys())


def _source(args: argparse.Namespace) -> Source:
    if args.url is not None:
        return WebPage(args.url)
    if args.file is not None:
        path = Path(args.file)
        try:
            return UploadedFile(path.name, path.read_bytes())
        except OSError as exc:
            raise ValueError(f"讀不到檔案 {path}：{exc.strerror}") from None
    return PastedText(args.text)


def _credits(value: str) -> int:
    """`--budget-credits`：0 以上的整數。"""
    try:
        credits = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"要是整數：{value}") from None
    if credits < 0:
        raise argparse.ArgumentTypeError(f"不能是負的：{value}")
    return credits


def _new(args: argparse.Namespace) -> int:
    recipes = _recipes(_harness(args.harness))
    recipe = recipes.get(args.recipe)
    if recipe is None:
        print(
            f"沒有配方 {args.recipe}（有：{', '.join(sorted(recipes))}）",
            file=sys.stderr,
        )
        return 2
    data = resolve_data_dir()
    data.ensure()
    try:
        intake = open_job(
            data,
            recipe,
            _source(args),
            goal=args.goal,
            now=taipei_now(),
            budget_usd=args.budget_usd,
            budget_credits=args.budget_credits,
            genre=args.genre,
        )
    except SourceUnreadable as exc:
        print(
            f"ef new：讀不到這個網址：{exc.reason}\n"
            "沒有開工單。請改用 --text 貼上全文，或用 --file 上傳檔案。",
            file=sys.stderr,
        )
        return 1
    if not intake.created:
        print(
            f"已有工單 {intake.job.id}（同一份素材、同樣的目標），沒有另開",
            file=sys.stderr,
        )
    print(intake.job.id)
    return 0


def _serve(args: argparse.Namespace) -> int:
    harness = _harness(args.harness)
    recipes = _recipes(harness)
    if args.parallel < 1:
        raise ValueError(f"--parallel 至少是 1：{args.parallel}")
    data = resolve_data_dir()
    config = config_from_env(data)
    gateway_url = f"http://127.0.0.1:{args.port}"
    preflight = Preflight(config, gateway_url, pi_bin=harness.pi.pi_bin)
    problems = preflight.startup()
    if problems:
        # 故障注入、資料目錄不可寫、硬碟滿：怎麼等都不會自己好，直接不啟動
        print(
            "ef serve：開跑前檢查沒過，不啟動：\n"
            + "\n".join(f"- {problem}" for problem in problems),
            file=sys.stderr,
        )
        return 2
    data.ensure()
    stages = ActiveStages()
    # 閘道寫、執行器讀：等待狀態與預算擋下的原因
    board = JobBoard(config.clock)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, stages, board),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            print(f"閘道沒有啟動（埠 {args.port} 被佔用？）", file=sys.stderr)
            return 1
        time.sleep(0.05)
    print(
        f"閘道 http://127.0.0.1:{args.port}（{config.mode}）；資料目錄 {data.root}",
        flush=True,
    )
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
    blocked: str | None = None
    try:
        while True:
            # 重開之後先接著跑沒跑完的單，再照開單順序拿排隊單，同時最多 --parallel 張
            started = runner.start_available()
            for job_id in runner.finished():
                print(f"工單 {job_id} 已進待審", flush=True)
            if runner.blocked != blocked:
                blocked = runner.blocked
                if blocked is not None:
                    print(f"ef serve：{blocked}", file=sys.stderr, flush=True)
            if args.once and not started and not runner.running():
                if blocked is not None:
                    return 1
                return 0
            time.sleep(0.1 if args.once else args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ef", description="Evidence Forge")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="投入網址、文字或檔案，開一張工單")
    source = new.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="文章網址（先抓回正文再開單）")
    source.add_argument("--text", help="文章內容")
    source.add_argument("--file", help="Markdown、純文字或 PDF 檔")
    new.add_argument("--goal", help="這張工單的目標（沒填就用配方的預設目標）")
    new.add_argument("--recipe", default="analyze", help="配方名稱（預設 analyze）")
    new.add_argument("--genre", help="目標文類（例：explainer）")
    new.add_argument(
        "--budget-usd", type=float, default=0.5, help="這張工單的預算（美元）"
    )
    new.add_argument(
        "--budget-credits",
        type=_credits,
        help="這張工單的 Tavily credits 上限（預設：配方各段 tavily_credits 的總和）",
    )
    new.add_argument("--harness", help="harness 資料夾（預設 repo 的 harness/）")
    new.set_defaults(handler=_new)

    serve = commands.add_parser("serve", help="啟動閘道與執行器")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--interval", type=float, default=2.0, help="掃描間隔（秒）")
    serve.add_argument("--once", action="store_true", help="把排隊中的工單跑完就結束")
    serve.add_argument(
        "--parallel", type=int, default=2, help="同時跑幾張工單（預設 2）"
    )
    serve.add_argument("--harness", help="harness 資料夾（預設 repo 的 harness/）")
    serve.set_defaults(handler=_serve)

    args = parser.parse_args(argv)
    try:
        result: int = args.handler(args)
    except (RecipeError, ValueError) as exc:
        print(f"ef {args.command}：{exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    sys.exit(main())
