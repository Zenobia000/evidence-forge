"""錄 restructure 重播 fixture 用的假網路：搜尋與抓取回的都是為測試寫的文章（#70）。

ADR-0014 規定錄音與原文會進公開 repo，所以重播 fixture 不能錄真的第三方文章。
錄音時模型是真的（整理段本機、補料與二創段 Anthropic），只有網路是假的：

- `seed <EF_DATA>`：把 `pages/*.md`（自己寫的虛構文章）寫進那個資料目錄的抓取快取，
  補料段的 `fetch_url` 直接命中快取，不連網。
- `tavily --port <埠>`：在 127.0.0.1 開一個假的 Tavily `POST /search`，不管查什麼都回這幾篇
  （加上一篇只在搜尋結果裡、抓不到的候選）。閘道用 `EF_TAVILY_URL` 指到它。

重播時用不到這支：搜尋與抓取都從錄音回放（閘道的重播模式）。
錄音步驟見 `tests/integration/pipeline/test_restructure_replay.py` 的說明。
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, Request

from evidence_forge.gateway.fetch import cache_key
from evidence_forge.models import FetchRecord
from evidence_forge.paths import DataDir

PAGES = Path(__file__).parent / "pages"

# 只出現在搜尋結果、抓不到的候選（.example 的網域解析不到）：補料段要跳過它換下一篇
UNREACHABLE: dict[str, Any] = {
    "url": "https://paywalled-daily.example/library-hours-debate",
    "title": "圖書館該不該開到半夜？（訂閱會員限定）",
    "content": "這是為了測試寫的虛構搜尋結果。本文為訂閱會員限定，"
    "討論各地圖書館延長開放時間的爭議。",
    "score": 0.75,
}


def load_pages() -> list[dict[str, Any]]:
    """`pages/*.md`：frontmatter 是網址、標題、作者、發布日期，內文是正文。依檔名排。"""
    pages: list[dict[str, Any]] = []
    for path in sorted(PAGES.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta: dict[str, Any] = yaml.safe_load(front)
        pages.append({**meta, "text": body.strip()})
    return pages


def seed(data_root: Path) -> None:
    """把每一篇寫進抓取快取（`cache/fetch/<網址 hash>/`），有效期限照閘道預設的 7 天。"""
    data = DataDir(data_root)
    data.ensure()
    now = datetime.now(UTC)
    for page in load_pages():
        record = FetchRecord(
            url=page["url"],
            final_url=page["url"],
            status="ok",
            http_status=200,
            fetched_at=now,
            expires_at=now + timedelta(days=7),
            content_type="text/html",
            title=page["title"],
            author=page.get("author"),
            text=page["text"],
        )
        directory = data.fetch_cache / cache_key(page["url"])
        directory.mkdir(parents=True, exist_ok=True)
        name = now.strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        (directory / name).write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        print(f"cached {page['url']}")


def results() -> list[dict[str, Any]]:
    """搜尋結果：每篇第一段正文的開頭當摘要，分數照順序遞減；抓不到的那篇排第三。"""
    found: list[dict[str, Any]] = []
    for index, page in enumerate(load_pages()):
        # 跳過標題與「這是虛構文章」那一行，取第一段正文
        blocks = page["text"].split("\n\n")
        snippet = blocks[min(2, len(blocks) - 1)][:120]
        item: dict[str, Any] = {
            "url": page["url"],
            "title": page["title"],
            "content": snippet,
            "score": round(0.9 - index * 0.1, 2),
        }
        if page.get("published_date"):
            item["published_date"] = str(page["published_date"])
        found.append(item)
    found.insert(2, UNREACHABLE)
    return found


def tavily_app() -> FastAPI:
    app = FastAPI()

    @app.post("/search")
    async def search(request: Request) -> dict[str, Any]:
        body = await request.json()
        limit = int(body.get("max_results", 5))
        return {
            "query": body.get("query", ""),
            "results": results()[:limit],
            "response_time": 0.01,
            "usage": {"credits": 1},
        }

    return app


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    seeding = commands.add_parser("seed", help="把自寫文章寫進資料目錄的抓取快取")
    seeding.add_argument("data", type=Path)
    serving = commands.add_parser("tavily", help="開假的 Tavily")
    serving.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == "seed":
        seed(args.data)
    else:
        uvicorn.run(tavily_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
