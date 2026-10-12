"""補料段的來源（#70）：來源正文由執行器放進工單的 `input/sources/`，不由 agent 寫。

二創要綜合多個來源，C2、C3 也要拿稿子比每一個來源。來源正文要是 agent 自己寫的，
它就能捏一份「來源」讓 C2、C3 過關；所以補料段只交一份清單（`sources.json`：
編號、網址、標題、為什麼挑它），正文由執行器從閘道的紀錄取：

- agent 在補料段用 `fetch_url` 抓過的網頁，閘道都記在這張單的帳本（`kind: fetch`），
  抓到的結果（`FetchRecord`，跟抓取快取存的是同一份）存在 `blobs/`。
- 只認**這一段、這一次**讀得到的抓取：別張單抓過、留在共用快取裡的不算，
  前一次抓的也不算（這一次的 agent 沒讀過）。重播模式下閘道照樣記帳，所以重播也走同一條路。
- 清單裡任何一個網址這一次沒抓到過，就算這一次不合格（照重試規則），一個檔都不放。
- 看的是**實際抓到哪一份**，不只看清單上寫的網址：同一份網頁（要求的網址與轉址後的網址、
  或正文相同）不能列成兩個來源；種子本身也不算，短網址轉到種子、帶追蹤參數、鏡像站，
  只要轉址後的網址是種子的、或正文跟種子相同，一樣擋。

放好的檔（`input/sources/S<n>.md`）開頭幾行是編號、標題、網址、作者（有才寫）、抓取時間，
接著是正文。種子（創作者丟進來的那一篇，`input/content.md`）永遠是 `S1`；補料找到的從 `S2` 起。
跟其他參考資料一樣只增不改：重開時同一次重跑放的是同一份，沿用；內容不同就不合格。
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from evidence_forge.checks.text import normalize_for_match
from evidence_forge.gateway import ledger
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.models import FetchRecord, Item, RecipeStage
from evidence_forge.paths import DataDir
from evidence_forge.runner.stage_inputs import CONTENT, SOURCES_DIR, put_once

RESEARCH_SKILL = "research"
SEED_ID = "S1"
SEED = Path(CONTENT)

_PLACED = re.compile(r"^S([0-9]+)\.md$")


def collects_sources(stage: RecipeStage) -> bool:
    """這一段交的是來源清單，執行器要照它放正文。"""
    return stage.skill == RESEARCH_SKILL


def job_sources(job_dir: Path) -> dict[str, Path]:
    """這張單的來源：引用編號 → 正文的路徑（相對於工單資料夾），種子在最前面，其餘照編號。"""
    found = {SEED_ID: SEED}
    directory = job_dir / SOURCES_DIR
    if directory.is_dir():
        numbered = sorted(
            (int(match.group(1)), path.name)
            for path in directory.iterdir()
            if (match := _PLACED.match(path.name)) and path.is_file()
        )
        for number, name in numbered:
            found[f"S{number}"] = Path(SOURCES_DIR + name)
    return found


def seed_item(data: DataDir, item: str) -> Item | None:
    """種子的素材項目（`item.json`：網址、標題、內容 hash）；讀不到時是 None。

    貼上的文字、上傳的檔案沒有網址（`url` 是 None）。
    """
    try:
        return Item.model_validate_json((data.root / item / "item.json").read_bytes())
    except (OSError, ValidationError):
        return None


def _text_key(text: str) -> str:
    """比對「是不是同一篇」用的正文 hash：空白、標點、全形半形都不算差別。"""
    return hashlib.sha256(normalize_for_match(text).encode("utf-8")).hexdigest()


def _normalized(url: str) -> str:
    """比對用：去掉前後空白與 # 之後的部分，主機與協定不分大小寫，路徑結尾的 / 不算。"""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def fetched_pages(
    data: DataDir, job_dir: Path, stage_id: str, attempt: int
) -> dict[str, FetchRecord]:
    """這一段這一次讀得到的抓取：比對用的網址（要求的與轉址後的都算）→ 抓到的結果。"""
    blobs = BlobStore(data.blobs)
    pages: dict[str, FetchRecord] = {}
    for entry in ledger.read(job_dir / "ledger.jsonl"):
        if (
            entry.kind != "fetch"
            or entry.stage != stage_id
            or entry.attempt != attempt
            or entry.response_hash is None
        ):
            continue
        raw = blobs.get(entry.response_hash)
        if raw is None:
            continue
        try:
            record = FetchRecord.model_validate_json(raw)
        except ValidationError:
            continue
        if record.status != "ok" or not record.text.strip():
            continue
        for url in (record.url, record.final_url):
            if url:
                pages.setdefault(_normalized(url), record)
    return pages


def _is_seed(
    url: str, record: FetchRecord | None, seed_urls: set[str], seed_text: str
) -> bool:
    """清單上這一筆是不是種子本身：寫的網址、實際抓的網址或轉址後的網址是種子的，或正文跟種子相同。"""
    urls = [url] + ([record.url, record.final_url or ""] if record is not None else [])
    if any(u and _normalized(u) in seed_urls for u in urls):
        return True
    return record is not None and _text_key(record.text) == seed_text


def render(source_id: str, record: FetchRecord, title: str) -> str:
    """`input/sources/S<n>.md` 的內容。標題用網頁自己的，網頁沒有才用 agent 寫的。"""
    lines = [
        f"來源編號：{source_id}",
        f"標題：{record.title or title}",
        f"網址：{record.final_url or record.url}",
    ]
    if record.author:
        lines.append(f"作者：{record.author}")
    lines.append(f"抓取時間：{record.fetched_at.isoformat()}")
    return "\n".join(lines) + "\n\n---\n\n" + record.text.strip() + "\n"


def _listed(stage_dir: Path, stage: RecipeStage) -> list[dict[str, object]] | str:
    """`sources.json` 裡的來源清單；讀不出來回傳原因。schema 已經驗過，這裡只防萬一。"""
    name = next((n for n in stage.output if n.endswith(".json")), None)
    if name is None:
        return f"{stage.id} 段的配方沒有宣告 JSON 產出，沒有來源清單"
    try:
        document = json.loads((stage_dir / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{name} 讀不出來：{exc}"
    listed = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(listed, list) or not all(isinstance(x, dict) for x in listed):
        return f"{name} 沒有來源清單（sources）"
    return listed


def place_sources(
    data: DataDir,
    job_dir: Path,
    stage: RecipeStage,
    attempt: int,
    *,
    seed: Item | None,
) -> list[str]:
    """照補料段這一次的 `sources.json` 放來源正文；回傳問題清單，空的表示放好了。

    `seed` 是種子的素材項目（`seed_item`），用它的網址認出種子；正文則跟工單的
    `input/content.md`（開單時從同一份素材複製進來，內容 hash 就是 `seed.content_hash`）比。
    先全部檢查，全部沒問題才放：不會只放一半。
    """
    listed = _listed(job_dir / "stages" / stage.id / str(attempt), stage)
    if isinstance(listed, str):
        return [listed]
    pages = fetched_pages(data, job_dir, stage.id, attempt)
    seed_urls = {_normalized(seed.url)} if seed is not None and seed.url else set()
    seed_text = _text_key((job_dir / SEED).read_text(encoding="utf-8"))
    problems: list[str] = []
    planned: list[tuple[str, str]] = []
    # 已經列過的網頁：正文 hash → 列在哪個編號
    seen: dict[str, str] = {}
    for number, source in enumerate(listed, start=2):
        expected = f"S{number}"
        source_id, url = str(source.get("id", "")), str(source.get("url", ""))
        if source_id != expected:
            problems.append(
                f"來源編號要從 S2 依序編，第 {number - 1} 筆應該是 {expected}，寫的是 {source_id}"
            )
            continue
        record = pages.get(_normalized(url))
        if _is_seed(url, record, seed_urls, seed_text):
            problems.append(f"{source_id} 是種子本身（{url}），不算補充來源")
            continue
        if record is None:
            problems.append(
                f"{source_id} 的網址 {url} 這一次沒有用 fetch_url 抓到正文，不能當來源"
            )
            continue
        page = _text_key(record.text)
        if page in seen:
            problems.append(
                f"{source_id} 跟 {seen[page]} 重複：是同一份網頁（{url}），不能列成兩個來源"
            )
            continue
        seen[page] = source_id
        relative = f"{SOURCES_DIR}{source_id}.md"
        content = render(source_id, record, str(source.get("title", "")))
        existing = job_dir / relative
        if existing.exists() and existing.read_text(encoding="utf-8") != content:
            problems.append(f"{relative} 已經放過另一份正文，同一張單的來源只放一次")
            continue
        planned.append((relative, content))
    if problems:
        return problems
    for relative, content in planned:
        # 跟其他參考資料同一套放法：暫存檔加 os.link，原子、已存在就沿用
        put_once(job_dir, relative, content)
    return []
