"""執行器：拿最舊的排隊中工單，照配方一段一段開 Pi，驗證產出，送進待審（v0.3 §5）。

每一段（S0.6）：
- 開 Pi 之前記下版本（`provenance.py`）；Pi 跑的時候把「在等什麼」寫進 `job.md`。
- Pi 結束後依序看：預算擋下 → `incomplete`；後端故障 → `failed`，不重試不升級；
  產出不合 schema、整理段的 C1／C5 沒過 → 同一級再試到 `max_attempts`，有 `escalate_to`
  就升一級再試一次，仍不行 → `failed`（附最後一次的錯誤）；
  二創段的 C2～C5 沒過 → 帶著結果回二創段修，到 `auto_fix` 次還沒過 → `incomplete`。
- 每一次（重試、升級、自動修）編號往上加，產出各自一個目錄；狀態寫在 `job.md` 的 `stage`，
  重開 `ef serve` 時從所在那段、同一次重跑，前面的請求命中閘道快取（NFR-RUN-04）。

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

from evidence_forge.checks import ChecksReport, SchemaError
from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.active import ActiveStages
from evidence_forge.gateway.aliases import MODELS_YAML
from evidence_forge.gateway.board import JobBoard
from evidence_forge.gateway.budget import LIMIT_REASONS
from evidence_forge.harness import Harness
from evidence_forge.jobkey import JobKey
from evidence_forge.models import (
    Event,
    Job,
    JobResult,
    JobStatus,
    Recipe,
    RecipeStage,
    StageProgress,
    Waiting,
)
from evidence_forge.paths import DataDir
from evidence_forge.runner import events, hard_checks, pi_launch, provenance
from evidence_forge.transitions import transition
from evidence_forge.write_once import write_once

TAIPEI = ZoneInfo("Asia/Taipei")

_log = logging.getLogger(__name__)


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def stage_prompt(
    recipe: Recipe, stage: RecipeStage, attempt: int, goal: str, fix: str = ""
) -> str:
    """這一段給 agent 的任務說明。只放段、次、目標、檔名，不放工單 id 與時間：
    同樣的素材與目標送出的請求要逐位元組相同，快取與重播才對得上。

    `fix` 是回二創段修時，上一次沒過的檢查（`hard_checks.fix_request`）。
    """
    output_dir = f"stages/{stage.id}/{attempt}/"
    outputs = "、".join(f"{output_dir}{name}" for name in stage.output)
    prompt = (
        f"配方「{recipe.name}」的「{stage.id}」段，第 {attempt} 次。\n"
        f"目標：{goal}\n"
        f"素材在 input/content.md。\n"
        f"照系統提示裡的 skill 完成這一段，用 write_file 寫出：{outputs}"
    )
    return f"{prompt}\n\n{fix}" if fix else prompt


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
BACKEND_NAMES = {"ollama": "Ollama", "anthropic": "Anthropic"}


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
                self._set(job_dir, doc, "review", result.reason, result=result)
                return
        self._set(
            job_dir, doc, "review", "全部段完成", result=JobResult(outcome="complete")
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
                # 二創的 C2～C5 沒過：帶著結果回這一段修，最多 auto_fix 次
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
            ),
            base_env=self._base_env(),
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
            )
        finally:
            self.stages.deactivate(key)
        limit = self.board.budget_stop(key) if self.board is not None else None
        if limit is not None:
            # 預算或本月額度用完：不是失敗，是沒做完（NFR-COST-01）
            reason = LIMIT_REASONS[limit]
            self._stage_event(job_dir, stage.id, attempt, reason)
            return _Tried(stop=JobResult(outcome="incomplete", reason=reason))
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
            )
        except hard_checks.ChecksUnavailable as exc:
            # 重跑模型修不好：不重試，帶著原因進待審
            reason = f"{stage.id} 段第 {attempt} 次的自動檢查沒辦法跑：{exc}"
            self._stage_event(job_dir, stage.id, attempt, reason)
            return _Tried(stop=JobResult(outcome="failed", reason=reason))
        if report is None or report.passed:
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
    ) -> pi_launch.PiRun:
        """跑 Pi；它跑的時候每隔一下看 Pi 的事件與閘道的告示板，把「在等什麼」寫進 job.md。"""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                pi_launch.run_pi,
                command,
                log_path=log_path,
                timeout_s=self.pi_timeout_s,
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
        """合併 Pi 的事件（正在跑哪個工具）與閘道的告示板（等 GPU、限流、模型），
        有變才改寫 job.md（不記事件：這是進度，不是狀態轉換）。"""
        previous = doc.job.waiting
        tool = open_tool(log_path)
        waiting: Waiting | None
        if tool is not None:
            same = previous is not None and previous.tool == tool
            since = previous.since if same and previous is not None else self.clock()
            waiting = Waiting(kind="tool", tool=tool, since=since)
        elif self.board is not None:
            waiting = self.board.waiting(job_dir.name)
        else:
            waiting = None
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
