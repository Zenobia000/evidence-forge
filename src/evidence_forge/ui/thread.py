"""對話串流的畫面資料（IA §4）：Pi 的完整紀錄（`get_messages`）加上這個對話的工單，整理成一塊一塊。

一塊是下面其中一種（`Block.kind`）：

- `me`：你的訊息。網址顯示成連結卡（網域；抓到之後用素材項目的標題），附檔顯示檔名與字數。
- `agent`：agent 的回答（Markdown；原始 HTML 不放行）。
- `tool`：一行灰字的工具呼叫，可以展開看參數。
- `error`：開不成工的一行錯誤，原因照工具回的，再補一句能做什麼。
- `job`：工單。跑著的由畫面照 `ef_job` 事件填進度列；結束的是結果卡。

結果卡在開工那一次工具呼叫的結果後面；沒對到的（紀錄被清掉、Pi 以外的入口開的單）接在最後，每張工單都看得到。
附檔的格式 `〔附檔 檔名〕…〔附檔結束〕` 是畫面送出時自己組的（`static/js/compose.js`），這裡認得它。
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from markupsafe import Markup

from evidence_forge.paths import DataDir
from evidence_forge.ui import artifacts, markdown
from evidence_forge.ui.format import clock

ATTACHMENT = re.compile(r"〔附檔 (?P<name>[^〕\n]+)〕\n(?P<body>.*?)\n〔附檔結束〕", re.DOTALL)
_URL = re.compile(r"https?://[^\s<>\"'）」】]+")
_JOB_ID = re.compile(r"^job_id:\s*(\S+)", re.MULTILINE)
_NOT_STARTED = "NOT STARTED:"
# 工具呼叫的參數太長（貼了整篇文章）時只留開頭
_ARG_LIMIT = 300

TOOL_LABEL = {
    "analyze": "開始分析",
    "rewrite": "開始重構",
    "read_report": "讀報告",
    "read_article": "讀文章",
    "job_status": "看工單進度",
    "list_personas": "看有哪些角色",
}
ERROR_HELP = {
    "analyze": "可以改貼文字，或上傳檔案。",
    "rewrite": "",
}


@dataclass(frozen=True)
class Attachment:
    name: str
    chars: int


@dataclass(frozen=True)
class JobCard:
    job_id: str
    kind: str
    label: str
    # queued／running／done
    state: str
    outcome: str | None
    reason: str
    compact: bool
    can_open: bool
    spent_usd: float
    finished: datetime | None
    # 報告
    lead: str = ""
    doubtful: int = 0
    supported: int = 0
    unfound: int = 0
    topics: int = 0
    # 文章
    title: str = ""
    persona: str = ""
    genre: str = ""
    words: int = 0
    uncalibrated: bool = False
    overlap: artifacts.Overlap | None = None


@dataclass(frozen=True)
class Block:
    kind: str
    # me
    text: str = ""
    time: str = ""
    url: str | None = None
    domain: str | None = None
    link_title: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    # agent
    html: Markup = Markup("")
    # tool
    label: str = ""
    args: str = ""
    # error
    help: str | None = None
    # job
    job: JobCard | None = None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _stamp(message: dict[str, Any]) -> str:
    value = message.get("timestamp")
    if isinstance(value, (int, float)) and value > 0:
        try:
            return clock(datetime.fromtimestamp(value / 1000).astimezone())
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _ARG_LIMIT:
        return value[:_ARG_LIMIT] + f"…（共 {len(value)} 字）"
    return value


def _args(arguments: Any) -> str:
    if not isinstance(arguments, dict) or not arguments:
        return ""
    return json.dumps({k: _short(v) for k, v in arguments.items()}, ensure_ascii=False, indent=2)


def _me(
    data: DataDir, message: dict[str, Any], jobs: list[artifacts.JobInfo]
) -> Block | None:
    raw = _text_of(message.get("content"))
    attachments = [Attachment(m["name"], len(m["body"])) for m in ATTACHMENT.finditer(raw)]
    text = ATTACHMENT.sub("", raw).strip()
    if not text and not attachments:
        return None
    found = _URL.search(text)
    url = found.group(0).rstrip(".,;)") if found else None
    domain = urlsplit(url).hostname if url else None
    title = None
    if url:
        for info in jobs:
            item = artifacts.original_item(data, info.job)
            if item is not None and item.url == url:
                title = item.title
                break
    return Block("me", text=text, time=_stamp(message), url=url, domain=domain, link_title=title, attachments=attachments)


def _card(data: DataDir, info: artifacts.JobInfo, last: bool) -> JobCard:
    result = info.job.result
    base: dict[str, Any] = {
        "job_id": info.job_id,
        "kind": info.kind,
        "label": info.label,
        "state": info.job.status,
        "outcome": result.outcome if result else None,
        "reason": result.reason if result else "",
        "compact": not last,
        "spent_usd": info.spent_usd,
        "finished": artifacts.finished_at(data, info),
    }
    can_open = info.job.status == "done" and artifacts.has_output(data, info)
    if not can_open:
        return JobCard(can_open=False, **base)
    if info.kind == "analysis":
        report = artifacts.report_view(data, info)
        return JobCard(
            can_open=True,
            lead=report.lead,
            doubtful=report.doubtful,
            supported=report.supported,
            unfound=report.unfound,
            topics=len(report.topics),
            **base,
        )
    article = artifacts.article_view(data, info, None)
    return JobCard(
        can_open=True,
        title=article.title,
        persona=article.persona,
        genre=article.genre,
        words=article.words,
        uncalibrated=article.uncalibrated,
        overlap=article.overlap,
        **base,
    )


def build(
    data: DataDir, jobs: list[artifacts.JobInfo], messages: list[dict[str, Any]]
) -> list[Block]:
    cards = {info.job_id: _card(data, info, info is jobs[-1]) for info in jobs}
    placed: set[str] = set()
    blocks: list[Block] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            if (block := _me(data, message, jobs)) is not None:
                blocks.append(block)
        elif role == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("text", "")).strip():
                    blocks.append(Block("agent", html=markdown.render(str(part["text"]))))
                elif part.get("type") == "toolCall":
                    name = str(part.get("name", ""))
                    blocks.append(Block("tool", label=TOOL_LABEL.get(name, name), args=_args(part.get("arguments"))))
        elif role == "toolResult":
            text = _text_of(message.get("content"))
            name = str(message.get("toolName", ""))
            if text.startswith(_NOT_STARTED):
                blocks.append(
                    Block(
                        "error",
                        text=text[len(_NOT_STARTED) :].strip(),
                        help=ERROR_HELP.get(name) or None,
                    )
                )
                continue
            match = _JOB_ID.search(text)
            if name in ("analyze", "rewrite") and match and match.group(1) in cards:
                job_id = match.group(1)
                if job_id not in placed:
                    placed.add(job_id)
                    blocks.append(Block("job", job=cards[job_id]))
    for info in jobs:
        if info.job_id not in placed:
            blocks.append(Block("job", job=cards[info.job_id]))
    return blocks
