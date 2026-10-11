"""指令列 `ef`。

- `ef new --text "<內容>" --goal "<目標>"`：在 `inbox/manual/` 寫一個素材項目，開一張排隊中的工單。
- `ef serve`：常駐程序，裡面有閘道（只綁 127.0.0.1）與執行器（v0.3 §1：S0 先放同一個程序）。

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
from evidence_forge.gateway.config import config_from_env
from evidence_forge.harness import Harness, default_harness
from evidence_forge.models import Recipe
from evidence_forge.paths import resolve_data_dir
from evidence_forge.runner.intake import open_job
from evidence_forge.runner.recipes import RecipeError, load_recipes
from evidence_forge.runner.runner import Runner, taipei_now


def _harness(path: str | None) -> Harness:
    return Harness.at(Path(path).resolve()) if path else default_harness()


def _recipes(harness: Harness) -> dict[str, Recipe]:
    return load_recipes(harness, tiers=load_aliases().keys())


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
    job = open_job(
        data,
        recipe,
        text=args.text,
        goal=args.goal,
        now=taipei_now(),
        budget_usd=args.budget_usd,
    )
    print(job.id)
    return 0


def _serve(args: argparse.Namespace) -> int:
    harness = _harness(args.harness)
    recipes = _recipes(harness)
    data = resolve_data_dir()
    data.ensure()
    config = config_from_env(data)
    stages = ActiveStages()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, stages),
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
        gateway_url=f"http://127.0.0.1:{args.port}",
    )
    try:
        while True:
            job_id = runner.run_once()
            if job_id is not None:
                print(f"工單 {job_id} 已進待審", flush=True)
            elif args.once:
                return 0
            else:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ef", description="Evidence Forge")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="貼一段文字開一張工單")
    new.add_argument("--text", required=True, help="文章內容")
    new.add_argument("--goal", required=True, help="這張工單的目標")
    new.add_argument("--recipe", default="echo", help="配方名稱（預設 echo）")
    new.add_argument(
        "--budget-usd", type=float, default=0.5, help="這張工單的預算（美元）"
    )
    new.add_argument("--harness", help="harness 資料夾（預設 repo 的 harness/）")
    new.set_defaults(handler=_new)

    serve = commands.add_parser("serve", help="啟動閘道與執行器")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--interval", type=float, default=2.0, help="掃描間隔（秒）")
    serve.add_argument("--once", action="store_true", help="把排隊中的工單跑完就結束")
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
