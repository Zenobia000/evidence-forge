"""執行器：拿最舊的排隊中工單，照配方一段一段開 Pi，驗證產出，送進待審（v0.3 §5）。

每一段（S0.6）：
- 開 Pi 之前記下版本（`provenance.py`）；Pi 跑的時候把「在等什麼」寫進 `job.md`。
- Pi 結束後依序看：預算擋下 → `incomplete`；後端故障 → `failed`，不重試不升級；
  產出不合 schema、分析段的程式檢查沒過 → 同一級再試到 `max_attempts`，有 `escalate_to`
  就升一級再試一次，仍不行 → `failed`（附最後一次的錯誤）；
  重構段的程式檢查沒過 → 帶著結果回重構段修，到 `auto_fix` 次還沒過 → `incomplete`。
- 搜尋額度（Tavily credits）用完、或 Tavily 故障，都不擋下那一段：agent 用已經找到的資料收尾，
  後面的段照跑；全部段跑完時看帳本（`search_shortfalls`）：有被拒的搜尋，或某段最後一次碰到
  Tavily 故障之後沒再搜成功過，就以 `incomplete` 進待審，都沒有才是 `complete`。
- 每一次（重試、升級、自動修）編號往上加，產出各自一個目錄；狀態寫在 `job.md` 的 `stage`，
  重開 `ef serve` 時從所在那段、同一次重跑，前面的請求命中閘道快取（NFR-RUN-04）。
  鑰匙跟當機前一樣，所以登記之前先殺掉前一個 `ef serve` 留下的 Pi（SEAM-12）。

沒預料到的例外一律接住，工單帶著原因進待審，不卡在 `running`，迴圈也不停。
同時跑幾張是 `max_parallel`；開跑前檢查沒過就不認領（`preflight.py`）。
不套用審稿決定（S0.9）。`job.md` 與 `events.jsonl` 只有這裡寫（NFR-KEEP-02）。
"""

import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
from pydantic import ValidationError

from evidence_forge.checks import ChecksReport, SchemaError
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway import ledger
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import MODELS_YAML
from evidence_forge.gateway.blobs import BlobStore
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import CREDITS_REASON, LIMIT_REASONS
from evidence_forge.gateway.search import OUTAGE_STATUSES
from evidence_forge.harness import Harness
from evidence_forge.jobkey import JobKey
from evidence_forge.models import (
    Event,
    Job,
    JobResult,
    JobStatus,
    Recipe,
    RecipeStage,
    SearchFailure,
    StageProgress,
    Waiting,
)
from evidence_forge.paths import DataDir
from evidence_forge.runner import (
    events,
    hard_checks,
    pi_launch,
    provenance,
    sources,
    stage_inputs,
)
from evidence_forge.transitions import transition
from evidence_forge.write_once import write_once

TAIPEI = ZoneInfo("Asia/Taipei")

_log = logging.getLogger(__name__)


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def stage_prompt(
    recipe: Recipe,
    stage: RecipeStage,
    attempt: int,
    goal: str,
    fix: str = "",
    upstream: Sequence[str] = (),
    original: sources.Original | None = None,
    *,
    seed_in_job: bool = True,
    feedback: str | None = None,
) -> str:
    """這一段給 agent 的任務說明。只放段、次、目標、檔名，不放工單 id 與時間：
    同樣的素材與目標送出的請求要逐位元組相同，快取與重播才對得上。

    - `fix` 是回重構段修時，上一次沒過的檢查（`hard_checks.fix_request`）。
    - `upstream` 是前面各段合格那一次的產出（`upstream_outputs`）；第一段沒有，說明一個字都不多。
    - `original` 是原文的出處（`sources.original`）。原文有網址就多一行原文出處：分析報告的來源清單、
      文章結尾的參考來源都要列（PRD §5、§6）。貼上的文字、上傳的檔案沒有網址，分析工單一個字都不多
      （錄音的種子都是貼上的文字，錄好的請求照樣對得上）；重構工單讀不到原文（`seed_in_job` 是 False），
      所以沒有網址也照樣給標題。
    - `seed_in_job`：工單裡有原文全文（`input/content.md`）。重構工單沒有（FR-RW-01）。
    - `feedback` 是再改時創作者對上一版的一句意見（FR-RW-03）。
    """
    output_dir = f"stages/{stage.id}/{attempt}/"
    outputs = "、".join(f"{output_dir}{name}" for name in stage.output)
    material = "素材在 input/content.md。\n" if seed_in_job else ""
    earlier = f"前面各段的產出：{'、'.join(upstream)}\n" if upstream else ""
    credited = original is not None and (original.url or not seed_in_job)
    credit = f"原文出處：{original.credit}\n" if original is not None and credited else ""
    revise = f"創作者對上一版的意見：{feedback}\n" if feedback else ""
    prompt = (
        f"配方「{recipe.name}」的「{stage.id}」段，第 {attempt} 次。\n"
        f"目標：{goal}\n"
        f"{material}"
        f"{earlier}"
        f"{credit}"
        f"{revise}"
        f"照系統提示裡的 skill 完成這一段，用 write_file 寫出：{outputs}"
    )
    return f"{prompt}\n\n{fix}" if fix else prompt


def upstream_outputs(job_dir: Path, recipe: Recipe, stage: RecipeStage) -> list[str]:
    """這一段之前每一段最後一次的產出路徑（相對於工單資料夾）。

    一段跑到合格才會往下一段走，之後也不會再多一次，所以編號最大的那一次就是合格的那一次。
    """
    found: list[str] = []
    for earlier in recipe.stages:
        if earlier.id == stage.id:
            break
        stage_root = job_dir / "stages" / earlier.id
        attempts = (
            [
                int(p.name)
                for p in stage_root.iterdir()
                if p.is_dir() and p.name.isdigit()
            ]
            if stage_root.is_dir()
            else []
        )
        if attempts:
            last = max(attempts)
            found.extend(
                f"stages/{earlier.id}/{last}/{name}" for name in earlier.output
            )
    return found


def validate_outputs(
    stage_dir: Path, stage: RecipeStage, schema_path: Path
) -> list[str]:
    """檢查這一段宣告的產出：每個檔都要在、不能是空的；`.json` 檔要符合 skill 的 schema。

    回傳問題清單，空的表示合格。schema.json 驗的是這一段的 JSON 產出。
    skill 的 schema 不在或壞了丟 `SchemaError`：重跑模型修不好，執行器直接送進待審。
    不在的時候不能當成「不用驗」，不然任何不是空的 JSON 都會被當成合格。
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"skill 的 schema {schema_path} 不存在") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"skill 的 schema {schema_path} 讀不出來：{exc}") from exc
    problems: list[str] = []
    for name in stage.output:
        path = stage_dir / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"{name} 沒有產出")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            problems.append(f"{name} 是空的")
            continue
        if name.endswith(".json"):
            try:
                document = json.loads(text)
            except json.JSONDecodeError as exc:
                problems.append(f"{name} 不是合法的 JSON：{exc.msg}")
                continue
            error = jsonschema.exceptions.best_match(
                jsonschema.Draft202012Validator(schema).iter_errors(document)
            )
            if error is not None:
                problems.append(f"{name} 不合 schema：{error.message}")
    return problems


@dataclass(frozen=True)
class _Tried:
    """一段一次的結果：產出的問題（照重試規則）、要直接進待審的結果、要回去修的檢查結果。"""

    problems: list[str] = field(default_factory=list)
    stop: JobResult | None = None
    fix: ChecksReport | None = None


def open_tool(log_path: Path) -> str | None:
    """Pi 的事件裡開始了、還沒結束的工具（最後開始的那個）；沒有就是 None。

    Pi 還在寫這個檔，最後一行可能只寫了一半，讀不懂的行略過。
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    running: dict[str, str] = {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("toolName")
        call = str(event.get("toolCallId", name))
        if event.get("type") == "tool_execution_start" and isinstance(name, str):
            running[call] = name
        elif event.get("type") == "tool_execution_end":
            running.pop(call, None)
    return list(running.values())[-1] if running else None


# 後端代號 → 待審原因裡給人看的名字（「失敗：Ollama 沒回應」）
BACKEND_NAMES = {"ollama": "Ollama", "anthropic": "Anthropic", "tavily": "Tavily"}


def search_shortfalls(data: DataDir, job_dir: Path) -> list[str]:
    """搜尋沒做完整的原因（給待審看），照先後排；沒有就是空的。從帳本判斷：

    - credits 不夠被拒（`search` 的 `budget_exceeded`）：`CREDITS_REASON`。
    - Tavily 故障（429 用完、逾時、連不上、金鑰或方案、5xx；閘道回 agent 的狀態碼在
      `OUTAGE_STATUSES`），而且那一段最後一次在故障之後沒有再搜成功過：產出就算合格，
      也只是用手上的資料湊的（#70）。查詢本身被 Tavily 退回的 400 不算。

    看帳本而不是告示板：這件事到全部段跑完才用得上，中間可能重開過 `ef serve`（告示板會清空）；
    重播時閘道也照錄音記同樣的行，結果跟錄音時一樣。
    """
    blobs = BlobStore(data.blobs)
    refused: list[str] = []
    entries = [e for e in ledger.read(job_dir / "ledger.jsonl") if e.kind == "search"]
    last_attempt: dict[str, int] = {}
    for entry in entries:
        last_attempt[entry.stage] = max(last_attempt.get(entry.stage, 0), entry.attempt)
    # 每一段最後一次：還沒恢復的 Tavily 故障（沒有就是 None）
    outages: dict[str, str | None] = {}
    for entry in entries:
        if entry.status == "budget_exceeded" and entry.stage not in refused:
            refused.append(entry.stage)
        if entry.attempt != last_attempt[entry.stage]:
            continue
        if entry.status in ("ok", "cache_hit"):
            outages[entry.stage] = None
            continue
        failure = _search_failure(blobs, entry.response_hash)
        status = failure.status if failure is not None else None
        if entry.status in ("rate_limited", "timeout") or (
            entry.status == "error" and (status is None or status in OUTAGE_STATUSES)
        ):
            outages[entry.stage] = (
                failure.error if failure is not None else "原因沒有記下來"
            )
    notes: list[str] = []
    if refused:
        notes.append(f"{CREDITS_REASON}：{'、'.join(refused)} 段用已經找到的資料完成")
    for stage_id, message in outages.items():
        if message is not None:
            notes.append(f"Tavily 故障（{message}）：{stage_id} 段用已經找到的資料完成")
    return notes


def _search_failure(blobs: BlobStore, digest: str | None) -> SearchFailure | None:
    raw = blobs.get(digest) if digest is not None else None
    if raw is None:
        return None
    try:
        return SearchFailure.model_validate_json(raw)
    except ValidationError:
        return None


def writing_mark(job_dir: Path, job: Job, recipe: Recipe) -> str | None:
    """寫文章的配方（有一段交 `article.md`）跑完時，要標在結果上的「未校準」；其他配方是 None。"""
    writes = any(
        hard_checks.checked_output(stage) == hard_checks.ARTICLE_OUTPUT
        for stage in recipe.stages
    )
    return stage_inputs.uncalibrated(job_dir, job) if writes else None


def pi_error(events: Sequence[Mapping[str, object]]) -> str | None:
    """Pi 事件裡最後一個失敗的模型回合的錯誤訊息；沒有就是 None。

    Pi `--mode json` 在模型呼叫失敗時送一個 `message_end`，助理訊息的 `stopReason`
    是 `error`（或被中斷的 `aborted`），原因在 `errorMessage`。
    """
    for event in reversed(events):
        message = event.get("message")
        if event.get("type") != "message_end" or not isinstance(message, dict):
            continue
        if message.get("stopReason") in ("error", "aborted"):
            text = message.get("errorMessage")
            return str(text) if text else str(message.get("stopReason"))
    return None


@dataclass
class Runner:
    data: DataDir
    harness: Harness
    recipes: Mapping[str, Recipe]
    stages: ActiveStages
    gateway_url: str
    clock: Callable[[], datetime] = taipei_now
    base_env: Mapping[str, str] | None = None
    pi_timeout_s: float = 900.0
    # 思考由閘道依 models.yaml 改寫（S0.4），Pi 送什麼都不採用；這裡固定送 off，
    # 只是讓同樣的輸入送出逐位元組相同的請求，快取與重播才對得上
    thinking: str = "off"
    # 跟閘道共用的告示板：Pi 被預算擋下而結束時，從這裡知道是哪一道上限（`ef serve` 接上）
    board: JobBoard | None = None
    # 同時跑幾張單（NFR-PAR-01）；`start_available` 照這個數開執行緒
    max_parallel: int = 2
    # 開跑前檢查：給這張單的配方，回傳沒過的項目（空的表示可以開跑）。`ef serve` 接上
    preflight: Callable[[Recipe], list[str]] | None = None
    # Pi 跑的時候多久看一次 Pi 的事件與閘道的告示板，更新 job.md 的「在等什麼」
    progress_interval_s: float = 1.0
    # 模型對照表；每份段產出記下它的內容（NFR-RUN-06）
    models_file: Path = MODELS_YAML

    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _inflight: set[str] = field(default_factory=set, init=False, repr=False)
    _threads: dict[str, threading.Thread] = field(
        default_factory=dict, init=False, repr=False
    )
    _finished: list[str] = field(default_factory=list, init=False, repr=False)
    _agent_ready: bool = field(default=False, init=False, repr=False)
    # 已經報過的事：壞掉的 job.md、開跑前檢查擋下的原因（同一件事不重複寫）
    _reported: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    # 開跑前檢查擋下時的原因；沒擋就是 None（`ef serve --once` 據此結束）
    blocked: str | None = field(default=None, init=False)

    # --- 狀態只在這裡改 ---------------------------------------------------

    def _set(
        self,
        job_dir: Path,
        doc: JobDocument,
        status: JobStatus,
        message: str,
        **changes: object,
    ) -> JobDocument:
        """改狀態並記一行事件。換狀態（或進下一次）時「在等什麼」一律清掉。"""
        target = transition(doc.job.status, status)
        job = doc.job.model_copy(update={"status": target, "waiting": None, **changes})
        updated = JobDocument(job=Job.model_validate(job.model_dump()), notes=doc.notes)
        write_job(job_dir / "job.md", updated)
        events.append(
            job_dir,
            Event(
                ts=self.clock(),
                kind="status",
                from_status=doc.job.status,
                to_status=target,
                message=message,
            ),
        )
        return updated

    def _stage_event(
        self, job_dir: Path, stage: str, attempt: int, message: str
    ) -> None:
        events.append(
            job_dir,
            Event(
                ts=self.clock(),
                kind="stage",
                stage=stage,
                attempt=attempt,
                message=message,
            ),
        )

    def _error_event(self, job_dir: Path, message: str) -> None:
        events.append(job_dir, Event(ts=self.clock(), kind="error", message=message))

    # --- 掃描 ---------------------------------------------------------------

    def _statuses(self) -> list[tuple[str, JobDocument]]:
        """所有讀得到的工單，依 id（開單時間）排。`job.md` 壞掉的略過並記一次 log，
        不讓一張壞單擋住整個佇列。"""
        if not self.data.jobs.is_dir():
            return []
        found: list[tuple[str, JobDocument]] = []
        for job_dir in sorted(self.data.jobs.iterdir()):
            job_md = job_dir / "job.md"
            if not job_md.is_file():
                continue  # 正在開單（job.md 是最後才寫的）
            try:
                found.append((job_dir.name, read_job(job_md)))
            except (OSError, ValueError) as exc:
                if self._reported.get(job_dir.name) != "broken":
                    self._reported[job_dir.name] = "broken"
                    _log.warning(
                        "工單 %s 的 job.md 讀不出來，略過：%s", job_dir.name, exc
                    )
        return found

    def next_job(self) -> str | None:
        """下一張該跑的：先接重開前沒跑完的（`running`、不在這個程序裡跑的），
        再拿最舊的排隊單（NFR-RUN-04、NFR-PAR-01）。"""
        jobs = [
            (job_id, doc)
            for job_id, doc in self._statuses()
            if job_id not in self._inflight
        ]
        for wanted in ("running", "queued"):
            for job_id, doc in jobs:
                if doc.job.status == wanted:
                    return job_id
        return None

    def next_queued(self) -> str | None:
        """最舊的排隊中工單；工單 id 開頭是開單時間，依 id 排就是依時間排。"""
        for job_id, doc in self._statuses():
            if doc.job.status == "queued" and job_id not in self._inflight:
                return job_id
        return None

    def _claim(self) -> str | None:
        """認領下一張；開跑前檢查沒過就不認領，把原因寫進那張單的事件紀錄（同樣的原因只寫一次）。"""
        job_id = self.next_job()
        if job_id is None:
            self.blocked = None
            return None
        if self.preflight is not None:
            try:
                problems = self._preflight_for(job_id)
            except Exception as exc:
                # 探測本身出錯（後端回了奇怪的東西、讀不到 job.md）：當成沒過，
                # 不讓整個 `ef serve` 帶著 traceback 停掉
                _log.warning("工單 %s 的開跑前檢查出錯", job_id, exc_info=True)
                problems = [f"開跑前檢查本身出錯：{type(exc).__name__}：{exc}"]
            if problems:
                message = "開跑前檢查沒過，不開跑：" + "；".join(problems)
                self.blocked = message
                if self._reported.get(job_id) != message:
                    self._reported[job_id] = message
                    self._error_event(self.data.job_dir(job_id), message)
                    _log.warning("工單 %s %s", job_id, message)
                return None
        self.blocked = None
        self._reported.pop(job_id, None)
        with self._lock:
            self._inflight.add(job_id)
        return job_id

    def _preflight_for(self, job_id: str) -> list[str]:
        assert self.preflight is not None
        recipe = self.recipes.get(
            read_job(self.data.job_dir(job_id) / "job.md").job.recipe
        )
        # 配方不見的單照樣認領：執行時直接以失敗進待審，不卡住佇列
        return self.preflight(recipe) if recipe is not None else []

    # --- 執行 ---------------------------------------------------------------

    def run_once(self) -> str | None:
        """在這條執行緒跑一張單到待審；沒有可以跑的單（或開跑前檢查沒過）回傳 None。"""
        job_id = self._claim()
        if job_id is None:
            return None
        try:
            self.run_job(job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)
        return job_id

    def start_available(self) -> list[str]:
        """有空位就認領最舊的單，各開一條執行緒跑；回傳這次開始的工單（NFR-PAR-01）。"""
        started: list[str] = []
        while True:
            with self._lock:
                if len(self._inflight) >= self.max_parallel:
                    break
            job_id = self._claim()
            if job_id is None:
                break
            thread = threading.Thread(
                target=self._worker, args=(job_id,), name=f"job-{job_id}", daemon=True
            )
            with self._lock:
                self._threads[job_id] = thread
            thread.start()
            started.append(job_id)
        return started

    def _worker(self, job_id: str) -> None:
        try:
            self.run_job(job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)
                self._threads.pop(job_id, None)
                self._finished.append(job_id)

    def running(self) -> list[str]:
        """這個程序裡正在跑的工單。"""
        with self._lock:
            return sorted(self._inflight)

    def finished(self) -> list[str]:
        """上次問到現在跑完（進待審）的工單。"""
        with self._lock:
            done, self._finished = self._finished, []
        return done

    def wait(self, timeout_s: float | None = None) -> None:
        """等這個程序裡正在跑的單都跑完（測試與 `ef serve` 結束時用）。"""
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout_s)

    def run_job(self, job_id: str) -> None:
        """跑一張單到待審。任何沒預料到的例外都接住：工單帶著原因進待審（失敗），
        不卡在 `running`，也不讓整個執行器停下。"""
        try:
            self._run_job(job_id)
        except Exception as exc:
            _log.exception("工單 %s 跑到一半出錯", job_id)
            self._crash(job_id, f"執行器出錯：{type(exc).__name__}：{exc}")

    def _crash(self, job_id: str, reason: str) -> None:
        job_dir = self.data.job_dir(job_id)
        try:
            doc = read_job(job_dir / "job.md")
            if doc.job.status == "queued":
                doc = self._set(job_dir, doc, "running", "開始")
            if doc.job.status == "running":
                self._set(
                    job_dir,
                    doc,
                    "review",
                    reason,
                    result=JobResult(outcome="failed", reason=reason[:2000]),
                )
        except Exception:
            # 連 job.md 都寫不進去（硬碟滿、檔案壞掉）：留給下一輪的開跑前檢查與人處理
            _log.exception("工單 %s 沒辦法標成失敗", job_id)

    def _ensure_agent_dir(self) -> None:
        # 幾條執行緒同時跑：Pi 的設定資料夾只準備一次（暫存檔名用程序編號，不能同時寫）
        with self._lock:
            if not self._agent_ready:
                pi_launch.prepare_agent_dir(
                    self.harness.pi, self.data, self.gateway_url
                )
                self._agent_ready = True

    def _run_job(self, job_id: str) -> None:
        job_dir = self.data.job_dir(job_id)
        doc = read_job(job_dir / "job.md")
        recipe = self.recipes.get(doc.job.recipe)
        if recipe is None:
            # 開單時配方還在、之後被刪了；直接以失敗進待審，不卡住佇列
            if doc.job.status == "queued":
                doc = self._set(job_dir, doc, "running", "開始")
            self._set(
                job_dir,
                doc,
                "review",
                "配方不存在",
                result=JobResult(
                    outcome="failed", reason=f"找不到配方 {doc.job.recipe}"
                ),
            )
            return

        try:
            # agent 讀不到 harness/ 與作家設定：開段之前放進工單的 input/（ADR-0015、ADR-0018）
            stage_inputs.prepare_inputs(self.harness, self.data, job_dir, doc.job)
        except stage_inputs.InputError as exc:
            if doc.job.status == "queued":
                doc = self._set(job_dir, doc, "running", "開始")
            reason = f"參考資料放不進工單：{exc}"
            self._set(
                job_dir,
                doc,
                "review",
                reason,
                result=JobResult(outcome="failed", reason=reason),
            )
            return

        self._ensure_agent_dir()
        start, resumed = 0, None
        if doc.job.status == "running" and doc.job.stage is not None:
            # 重開 `ef serve`：從所在那段、同一次重跑，前面的請求全部命中閘道快取（v0.3 §4.3）
            ids = [stage.id for stage in recipe.stages]
            if doc.job.stage.id in ids:
                start = ids.index(doc.job.stage.id)
                resumed = doc.job.stage.model_copy(update={"started_at": self.clock()})
                self._set_aside(job_dir, resumed)
        for index, stage in enumerate(recipe.stages, start=1):
            if index <= start:
                continue
            progress = (
                resumed
                if resumed is not None
                else StageProgress(
                    id=stage.id,
                    index=index,
                    total=len(recipe.stages),
                    attempt=1,
                    started_at=self.clock(),
                    tier=stage.tier,
                )
            )
            resumed = None
            doc, result = self._run_stage(job_dir, doc, recipe, stage, progress)
            if result is not None:
                if result.outcome == "incomplete":
                    # 後面的段自己沒做完：搜尋沒做完整、未校準的事也一起寫，創作者才看得到全貌
                    notes = search_shortfalls(self.data, job_dir)
                    if note := writing_mark(job_dir, doc.job, recipe):
                        notes.append(note)
                    if notes:
                        result = JobResult(
                            outcome="incomplete",
                            reason="；".join([result.reason, *notes])[:2000],
                        )
                self._set(job_dir, doc, "review", result.reason, result=result)
                return
        notes = search_shortfalls(self.data, job_dir)
        # 寫文章的配方：角色在目標文類還沒校準，結果上標「未校準」（ADR-0015 §3.6、FR-RW-02）
        marks = [note] if (note := writing_mark(job_dir, doc.job, recipe)) else []
        if notes:
            # 搜尋額度用完或 Tavily 故障：那一段用已經找到的資料收尾、後面照跑，
            # 但這張單沒做完（NFR-COST-01、#70）
            reason = "；".join([*notes, *marks])
            result = JobResult(outcome="incomplete", reason=reason)
            self._set(job_dir, doc, "review", reason, result=result)
            return
        self._set(
            job_dir,
            doc,
            "review",
            "全部段完成",
            result=JobResult(outcome="complete", reason="；".join(marks)),
        )

    def _set_aside(self, job_dir: Path, progress: StageProgress) -> None:
        """重跑同一次之前，把當掉那次寫了一半的產出搬到 `run/interrupted/`。

        寫檔只增不改，不搬開的話 Pi 重寫同一個檔會被拒，請求就跟上次不同、快取接不上。
        搬到工單資料夾外：agent 列檔案時看不到，送出的請求才跟上次逐位元組相同。
        """
        stage_dir = job_dir / "stages" / progress.id / str(progress.attempt)
        if not stage_dir.exists():
            return
        parking = self.data.run / "interrupted" / job_dir.name
        parking.mkdir(parents=True, exist_ok=True)
        number = 1
        while (parking / f"{progress.id}-{progress.attempt}-{number}").exists():
            number += 1
        target = parking / f"{progress.id}-{progress.attempt}-{number}"
        shutil.move(stage_dir, target)
        self._stage_event(
            job_dir,
            progress.id,
            progress.attempt,
            f"重開：當掉那次寫了一半的產出搬到 {target.relative_to(self.data.root)}",
        )

    def _run_stage(
        self,
        job_dir: Path,
        doc: JobDocument,
        recipe: Recipe,
        stage: RecipeStage,
        progress: StageProgress,
    ) -> tuple[JobDocument, JobResult | None]:
        """跑一段到合格，或到重試與升級都用完（NFR-RUN-03、ADR-0011 §3.3）。

        同一級最多試 `max_attempts` 次；還不合格而配方有 `escalate_to`，升一級再試一次；
        再不合格就回傳失敗的結果。每一次（含升級）的編號往上加，產出各自一個目錄。
        回傳 None 表示這一段合格。
        """
        while True:
            tier = progress.tier or stage.tier
            escalated = tier != stage.tier
            message = f"開始 {stage.id} 段" + (
                f"第 {progress.attempt} 次（{tier}）" if progress.attempt > 1 else ""
            )
            doc = self._set(job_dir, doc, "running", message, stage=progress)
            tried = self._attempt(job_dir, doc, recipe, stage, progress)
            label = f"{stage.id} 段第 {progress.attempt} 次"
            if tried.stop is not None:
                return doc, tried.stop
            problems = tried.problems
            if not problems and tried.fix is not None:
                # 重構文章的程式檢查沒過：帶著結果回這一段修，最多 auto_fix 次
                failed = "、".join(tried.fix.failed_checks)
                if progress.fixes >= recipe.auto_fix:
                    return doc, JobResult(
                        outcome="incomplete",
                        reason=f"自動檢查沒過：{failed}（{label}，"
                        f"已自動修 {progress.fixes} 次）",
                    )
                self._stage_event(
                    job_dir,
                    stage.id,
                    progress.attempt,
                    f"{label}的自動檢查沒過：{failed}，回 {stage.id} 段修",
                )
                progress = progress.model_copy(
                    update={
                        "attempt": progress.attempt + 1,
                        "failures": 0,
                        "fixes": progress.fixes + 1,
                        "started_at": self.clock(),
                    }
                )
                continue
            if not problems:
                return doc, None
            failures = progress.failures + 1
            if not escalated and failures < recipe.max_attempts:
                next_tier = tier
            elif not escalated and stage.escalate_to is not None:
                next_tier = stage.escalate_to
                failures = 0
            else:
                reason = f"{label}失敗：" + "；".join(problems)
                return doc, JobResult(outcome="failed", reason=reason)
            self._stage_event(
                job_dir,
                stage.id,
                progress.attempt,
                f"{label}不合格，"
                + (f"升級到 {next_tier} 再試" if next_tier != tier else "同一級再試")
                + "："
                + "；".join(problems),
            )
            progress = progress.model_copy(
                update={
                    "attempt": progress.attempt + 1,
                    "tier": next_tier,
                    "failures": failures,
                    "started_at": self.clock(),
                }
            )

    def _attempt(
        self,
        job_dir: Path,
        doc: JobDocument,
        recipe: Recipe,
        stage: RecipeStage,
        progress: StageProgress,
    ) -> "_Tried":
        """開 Pi 跑這一段的這一次，驗產出、跑硬檢查。

        預算擋下、後端故障、檢查跑不動：不重試，直接帶著結果回去（`stop`）。
        其他問題交給呼叫端照重試規則處理。
        """
        attempt = progress.attempt
        tier = progress.tier or stage.tier
        key = JobKey(job_dir.name, stage.id, attempt)
        stage_dir = job_dir / "stages" / stage.id / str(attempt)
        stage_dir.mkdir(parents=True, exist_ok=True)
        skill_dir = self.harness.skill_dir(stage.skill)
        # 種子的素材項目：查證段認出種子、任務說明的原文出處、重構段的出處檢查與重疊率都用它
        seed = sources.seed_item(self.data, doc.job.feed.item)
        original = sources.original(self.data, job_dir, doc.job.feed.item)
        record = provenance.record_path(job_dir, stage.id, attempt)
        if not record.exists():
            # 開 Pi 之前寫：版本鎖在這一次的產出上（NFR-RUN-06）。放在 stages/ 外，
            # stages/ 只有 agent 寫（ADR-0010），agent 也碰不到這一份
            write_once(
                record,
                provenance.render(
                    provenance.provenance(
                        self.harness, recipe, stage, attempt, tier, self.models_file
                    )
                ),
            )
        command = pi_launch.build_pi_command(
            harness=self.harness.pi,
            data=self.data,
            key=key,
            tier=tier,
            thinking=self.thinking,
            skill_file=skill_dir / "SKILL.md",
            gateway_url=self.gateway_url,
            prompt=stage_prompt(
                recipe,
                stage,
                attempt,
                doc.job.goal,
                self._fix(job_dir, stage, progress),
                upstream_outputs(job_dir, recipe, stage),
                original,
                seed_in_job=(job_dir / sources.SEED).is_file(),
                feedback=doc.job.feedback,
            ),
            base_env=self._base_env(),
            web_search=stage.tavily_credits > 0,
        )
        # 重開 `ef serve` 重跑同一次時鑰匙跟當機前一模一樣：前一個 `ef serve` 被硬殺時
        # 留下、還活著的 Pi 要在登記之前殺掉，不然它的請求與寫檔會被接受（SEAM-12）
        leftover = pi_launch.kill_leftover(self.data, key)
        if leftover is not None:
            self._stage_event(
                job_dir,
                stage.id,
                attempt,
                f"前一次留下的 Pi 還在跑（程序群組 {leftover}），已停止",
            )
        self._stage_event(job_dir, stage.id, attempt, f"開 Pi（{tier}）")
        # 鑰匙只在 Pi 活著的這段時間有效；run_pi 回來時 Pi 一定已經結束（或被殺掉）
        self.stages.activate(
            key,
            skill_dir=skill_dir,
            escalated_from=stage.tier if tier != stage.tier else None,
        )
        try:
            result = self._run_pi(
                job_dir,
                doc,
                command,
                self.data.run / "pi-logs" / f"{key.job_id}-{stage.id}-{attempt}.jsonl",
                pi_launch.pid_file(self.data, key),
            )
        finally:
            self.stages.deactivate(key)
        limit = self.board.budget_stop(key) if self.board is not None else None
        if limit is not None:
            # 預算或本月額度用完：不是失敗，是沒做完（NFR-COST-01）
            reason = LIMIT_REASONS[limit]
            self._stage_event(job_dir, stage.id, attempt, reason)
            return _Tried(stop=JobResult(outcome="incomplete", reason=reason))
        placement: sources.Placement | None = None
        if result.timed_out:
            self._stage_event(job_dir, stage.id, attempt, "Pi 逾時")
            problems = [f"Pi 超過 {self.pi_timeout_s:.0f} 秒沒結束，已停止"]
        elif result.returncode != 0:
            self._stage_event(
                job_dir, stage.id, attempt, f"Pi 結束碼 {result.returncode}"
            )
            problems = [
                f"Pi 結束碼 {result.returncode}：{result.stderr_tail.strip()[-500:]}"
            ]
        else:
            try:
                problems = validate_outputs(
                    stage_dir, stage, self.harness.schema_file(stage.skill)
                )
            except SchemaError as exc:
                reason = f"{stage.id} 段第 {attempt} 次的產出沒辦法驗：{exc}"
                self._stage_event(job_dir, stage.id, attempt, reason)
                return _Tried(stop=JobResult(outcome="failed", reason=reason))
            if not problems and sources.collects_sources(stage):
                # 交來源清單的段：照 sources.json 算好要放的來源正文；網址這一次沒抓過就算不合格（#70）。
                # 等程式檢查也過了才放（見下面），檢查沒過重試時才換得了來源
                placement = sources.plan_sources(
                    self.data,
                    job_dir,
                    stage,
                    attempt,
                    seed=seed,
                )
                problems = list(placement.problems)
            error = pi_error(result.events)
            if problems and error is not None:
                # Pi 的模型呼叫失敗時結束碼還是 0，真正的原因只在它的事件裡
                problems = [f"Pi 回報模型呼叫失敗：{error}", *problems]
        outage = self.board.outage(key) if self.board is not None else None
        if problems and outage is not None:
            # 這一次沒有合格產出、途中又碰到後端故障：算基礎設施故障，不重試、不升級，
            # 直接標失敗（ADR-0011 §3.3、NFR-RUN-05）。產出合格的話以產出為準：
            # Pi 碰到一次 5xx 會自己重試，重試成功不該丟掉付過錢的產出
            name = BACKEND_NAMES.get(outage.backend, outage.backend)
            reason = f"{name} 沒回應（{stage.id} 段第 {attempt} 次：{outage.message}）"
            self._stage_event(job_dir, stage.id, attempt, reason)
            return _Tried(stop=JobResult(outcome="failed", reason=reason))
        if problems:
            self._stage_event(job_dir, stage.id, attempt, "產出不合格")
            return _Tried(problems)
        try:
            report = hard_checks.run_hard_checks(
                self.data,
                job_dir,
                stage,
                attempt,
                self.harness.schema_file(stage.skill),
                self.clock,
                original,
                upstream_outputs(job_dir, recipe, stage),
            )
        except hard_checks.ChecksUnavailable as exc:
            # 重跑模型修不好：不重試，帶著原因進待審
            reason = f"{stage.id} 段第 {attempt} 次的自動檢查沒辦法跑：{exc}"
            self._stage_event(job_dir, stage.id, attempt, reason)
            return _Tried(stop=JobResult(outcome="failed", reason=reason))
        if report is None or report.passed:
            if placement is not None:
                sources.put_sources(job_dir, placement)
            self._stage_event(job_dir, stage.id, attempt, "產出合格")
            return _Tried()
        self._stage_event(
            job_dir,
            stage.id,
            attempt,
            f"自動檢查沒過：{'、'.join(report.failed_checks)}",
        )
        if hard_checks.fixable(stage):
            return _Tried(fix=report)
        return _Tried(hard_checks.failures(report))

    def _run_pi(
        self,
        job_dir: Path,
        doc: JobDocument,
        command: pi_launch.PiCommand,
        log_path: Path,
        pid_path: Path,
    ) -> pi_launch.PiRun:
        """跑 Pi；它跑的時候每隔一下看 Pi 的事件與閘道的告示板，把「在等什麼」寫進 job.md。"""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                pi_launch.run_pi,
                command,
                log_path=log_path,
                timeout_s=self.pi_timeout_s,
                pid_path=pid_path,
            )
            current = doc
            while True:
                try:
                    return future.result(timeout=self.progress_interval_s)
                except FuturesTimeout:
                    pass
                try:
                    current = self._show_waiting(job_dir, current, log_path)
                except Exception:
                    # 進度只是給人看的，更新失敗不能拖垮這一段
                    _log.warning("工單 %s 的進度更新失敗", job_dir.name, exc_info=True)

    def _show_waiting(
        self, job_dir: Path, doc: JobDocument, log_path: Path
    ) -> JobDocument:
        """合併閘道的告示板（等 GPU、限流、模型）與 Pi 的事件（正在跑哪個工具），
        有變才改寫 job.md（不記事件：這是進度，不是狀態轉換）。"""
        previous = doc.job.waiting
        # 告示板優先：閘道知道得比較細。搜尋被 Tavily 限流時，Pi 的事件只說 web_search 在跑，
        # 告示板才說它在等限流
        waiting = self.board.waiting(job_dir.name) if self.board is not None else None
        tool = open_tool(log_path) if waiting is None else None
        if tool is not None:
            same = previous is not None and previous.tool == tool
            since = previous.since if same and previous is not None else self.clock()
            waiting = Waiting(kind="tool", tool=tool, since=since)
        if waiting == previous:
            return doc
        updated = JobDocument(
            job=doc.job.model_copy(update={"waiting": waiting}), notes=doc.notes
        )
        write_job(job_dir / "job.md", updated)
        return updated

    def _fix(self, job_dir: Path, stage: RecipeStage, progress: StageProgress) -> str:
        """自動修的那幾次，任務說明要帶上一次沒過的檢查；從 `checks/` 讀回來，
        重開 `ef serve` 之後同一次的任務說明才會一樣。"""
        if progress.fixes == 0:
            return ""
        report = hard_checks.latest_report(job_dir, stage.id, progress.attempt)
        return hard_checks.fix_request(report) if report is not None else ""

    def _base_env(self) -> Mapping[str, str]:
        return os.environ if self.base_env is None else self.base_env
