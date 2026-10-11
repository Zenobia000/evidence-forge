"""執行器：拿最舊的排隊中工單，照配方一段一段開 Pi，驗證產出，送進待審（v0.3 §5）。

S0.3 是打通的最薄一層：每段只跑一次，不重試、不升級、不自動修（S0.6）；
不套用審稿決定（S0.9）；沒有開跑前檢查與同時張數上限（S0.6）。
`job.md` 的狀態與 `events.jsonl` 只有這裡寫（NFR-KEEP-02）。
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema

from evidence_forge.documents import JobDocument, read_job, write_job
from evidence_forge.gateway.active import ActiveStages
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
)
from evidence_forge.paths import DataDir
from evidence_forge.runner import events, pi_launch
from evidence_forge.transitions import transition

TAIPEI = ZoneInfo("Asia/Taipei")


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def stage_prompt(recipe: Recipe, stage: RecipeStage, attempt: int, goal: str) -> str:
    """這一段給 agent 的任務說明。只放段、次、目標、檔名，不放工單 id 與時間：
    同樣的素材與目標送出的請求要逐位元組相同，快取與重播才對得上。"""
    output_dir = f"stages/{stage.id}/{attempt}/"
    outputs = "、".join(f"{output_dir}{name}" for name in stage.output)
    return (
        f"配方「{recipe.name}」的「{stage.id}」段，第 {attempt} 次。\n"
        f"目標：{goal}\n"
        f"素材在 input/content.md。\n"
        f"照系統提示裡的 skill 完成這一段，用 write_file 寫出：{outputs}"
    )


def validate_outputs(stage_dir: Path, stage: RecipeStage, schema_path: Path) -> list[str]:
    """檢查這一段宣告的產出：每個檔都要在、不能是空的；`.json` 檔要符合 skill 的 schema。

    回傳問題清單，空的表示合格。schema.json 驗的是這一段的 JSON 產出。
    """
    schema = (
        json.loads(schema_path.read_text(encoding="utf-8"))
        if schema_path.is_file()
        else None
    )
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
            if schema is not None:
                error = jsonschema.exceptions.best_match(
                    jsonschema.Draft202012Validator(schema).iter_errors(document)
                )
                if error is not None:
                    problems.append(f"{name} 不合 schema：{error.message}")
    return problems


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
    # S0.3 一律關掉思考；S0.4 改由閘道依 models.yaml 決定（#10 第 2、6 題）
    thinking: str = "off"

    # --- 狀態只在這裡改 ---------------------------------------------------

    def _set(
        self,
        job_dir: Path,
        doc: JobDocument,
        status: JobStatus,
        message: str,
        **changes: object,
    ) -> JobDocument:
        target = transition(doc.job.status, status)
        job = doc.job.model_copy(update={"status": target, **changes})
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

    def _stage_event(self, job_dir: Path, stage: str, attempt: int, message: str) -> None:
        events.append(
            job_dir,
            Event(ts=self.clock(), kind="stage", stage=stage, attempt=attempt, message=message),
        )

    # --- 掃描與執行 ---------------------------------------------------------

    def next_queued(self) -> str | None:
        """最舊的排隊中工單；工單 id 開頭是開單時間，依 id 排就是依時間排。"""
        if not self.data.jobs.is_dir():
            return None
        for job_dir in sorted(self.data.jobs.iterdir()):
            job_md = job_dir / "job.md"
            if job_md.is_file() and read_job(job_md).job.status == "queued":
                return job_dir.name
        return None

    def run_once(self) -> str | None:
        """跑一張排隊中的工單到待審；沒有排隊中的工單回傳 None。"""
        job_id = self.next_queued()
        if job_id is None:
            return None
        job_dir = self.data.job_dir(job_id)
        doc = read_job(job_dir / "job.md")
        recipe = self.recipes.get(doc.job.recipe)
        if recipe is None:
            # 開單時配方還在、之後被刪了；直接以失敗進待審，不卡住佇列
            doc = self._set(job_dir, doc, "running", "開始")
            self._set(
                job_dir, doc, "review", "配方不存在",
                result=JobResult(outcome="failed", reason=f"找不到配方 {doc.job.recipe}"),
            )
            return job_id

        pi_launch.prepare_agent_dir(self.harness.pi, self.data, self.gateway_url)
        attempt = 1
        for index, stage in enumerate(recipe.stages, start=1):
            progress = StageProgress(
                id=stage.id, index=index, total=len(recipe.stages),
                attempt=attempt, started_at=self.clock(),
            )
            doc = self._set(job_dir, doc, "running", f"開始 {stage.id} 段", stage=progress)
            problems = self._run_stage(job_dir, recipe, stage, attempt, doc.job.goal)
            if problems:
                reason = f"{stage.id} 段第 {attempt} 次失敗：" + "；".join(problems)
                self._set(
                    job_dir, doc, "review", reason,
                    result=JobResult(outcome="failed", reason=reason),
                )
                return job_id
        self._set(job_dir, doc, "review", "全部段完成", result=JobResult(outcome="complete"))
        return job_id

    def _run_stage(
        self, job_dir: Path, recipe: Recipe, stage: RecipeStage, attempt: int, goal: str
    ) -> list[str]:
        key = JobKey(job_dir.name, stage.id, attempt)
        stage_dir = job_dir / "stages" / stage.id / str(attempt)
        stage_dir.mkdir(parents=True, exist_ok=True)
        skill_dir = self.harness.skill_dir(stage.skill)
        command = pi_launch.build_pi_command(
            harness=self.harness.pi,
            data=self.data,
            key=key,
            tier=stage.tier,
            thinking=self.thinking,
            skill_file=skill_dir / "SKILL.md",
            gateway_url=self.gateway_url,
            prompt=stage_prompt(recipe, stage, attempt, goal),
            base_env=self._base_env(),
        )
        self._stage_event(job_dir, stage.id, attempt, "開 Pi")
        # 鑰匙只在 Pi 活著的這段時間有效；run_pi 回來時 Pi 一定已經結束（或被殺掉）
        self.stages.activate(key)
        try:
            result = pi_launch.run_pi(
                command,
                log_path=self.data.run / "pi-logs" / f"{key.job_id}-{stage.id}-{attempt}.jsonl",
                timeout_s=self.pi_timeout_s,
            )
        finally:
            self.stages.deactivate(key)
        if result.timed_out:
            self._stage_event(job_dir, stage.id, attempt, "Pi 逾時")
            return [f"Pi 超過 {self.pi_timeout_s:.0f} 秒沒結束，已停止"]
        if result.returncode != 0:
            self._stage_event(job_dir, stage.id, attempt, f"Pi 結束碼 {result.returncode}")
            return [f"Pi 結束碼 {result.returncode}：{result.stderr_tail.strip()[-500:]}"]
        problems = validate_outputs(stage_dir, stage, skill_dir / "schema.json")
        self._stage_event(
            job_dir, stage.id, attempt, "產出合格" if not problems else "產出不合格"
        )
        return problems

    def _base_env(self) -> Mapping[str, str]:
        return os.environ if self.base_env is None else self.base_env
