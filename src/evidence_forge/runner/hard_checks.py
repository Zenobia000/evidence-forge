"""執行器在段與段之間跑的硬檢查 C1～C5（FR-VF-01、架構草案 v0.3 §6、測試藍圖 SEAM-06）。

檢查交給 `checks/`，不交給模型：交給模型就等於由模型決定檢查做不做、結果怎麼回報。
哪一段跑哪幾項，看這一段用的 skill：

- `digest`（整理）：C1、C5。沒過算這段這次失敗，走重試與升級。
- `compose`（二創）：C2～C5。沒過帶著結果回二創段修，最多 `auto_fix` 次。C2 的引用編號、C3 比的正文
  都是種子加補料段放的來源（`sources.job_sources`，#70）。
- 其他 skill：不跑硬檢查，只驗 schema（`validate_outputs`）。

結果寫成 `checks/checks.vN.json`，只看執行器自己算的；agent 在輸出目錄寫的任何
`checks.json` 都不讀。檢查本身跑不動（原文是空的、schema 壞掉、路徑跑出工單資料夾）時丟
`ChecksUnavailable`：重跑模型修不好，執行器直接帶著原因送進待審。
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from evidence_forge.checks import (
    ChecksReport,
    load_check_settings,
    run_compose_checks,
    run_digest_checks,
    write_checks_report,
)
from evidence_forge.models import RecipeStage
from evidence_forge.paths import DataDir, JobDir
from evidence_forge.runner import sources

DIGEST_SKILL = "digest"
COMPOSE_SKILL = "compose"

# 原文（種子）在工單裡的位置。二創段的來源清單是種子加補料段放的來源（`sources.job_sources`）
ORIGINAL_SOURCE = sources.SEED

# 任務說明裡最多列幾條沒過的條目；全部都在 checks.vN.json
_MAX_LISTED = 10


class ChecksUnavailable(Exception):
    """硬檢查跑不動：問題在執行器或 skill 的設定，不在這次產出。"""


def has_hard_checks(stage: RecipeStage) -> bool:
    return stage.skill in (DIGEST_SKILL, COMPOSE_SKILL)


def fixable(stage: RecipeStage) -> bool:
    """沒過時回這一段修（二創），而不是算這次失敗（整理）。"""
    return stage.skill == COMPOSE_SKILL


def _output(stage: RecipeStage, suffix: str) -> str:
    for name in stage.output:
        if name.endswith(suffix):
            return name
    raise ChecksUnavailable(f"{stage.id} 段的配方沒有宣告 {suffix} 產出，沒辦法檢查")


def run_hard_checks(
    data: DataDir,
    job_dir: Path,
    stage: RecipeStage,
    attempt: int,
    schema: Path,
    clock: Callable[[], datetime],
) -> ChecksReport | None:
    """跑這一段這一次的硬檢查並寫成 `checks.vN.json`；這一段沒有硬檢查就回傳 None。"""
    if not has_hard_checks(stage):
        return None
    base = Path("stages") / stage.id / str(attempt)
    try:
        if stage.skill == DIGEST_SKILL:
            output = base / _output(stage, ".json")
            files: tuple[Path, ...] = (output,)
            entries = run_digest_checks(
                job_dir, output=output, schema=schema, source=ORIGINAL_SOURCE
            )
        else:
            draft = base / _output(stage, ".md")
            output = base / _output(stage, ".json")
            files = (draft, output)
            entries = run_compose_checks(
                job_dir,
                draft=draft,
                output=output,
                schema=schema,
                sources=sources.job_sources(job_dir),
                thresholds=load_check_settings(data).c3,
            )
    except (ValueError, OSError) as exc:
        # SchemaError、空的原文、跑出工單資料夾的路徑、壞掉的 checks.yaml 都在這裡
        raise ChecksUnavailable(str(exc)) from exc
    report = ChecksReport(
        stage=stage.id,
        attempt=attempt,
        files=tuple(path.as_posix() for path in files),
        created_at=clock(),
        entries=tuple(entries),
    )
    write_checks_report(job_dir, report)
    return report


def failures(report: ChecksReport) -> list[str]:
    """沒過的條目，一條一行：「C1 理由（位置）：出問題的文字」。"""
    lines: list[str] = []
    for entry in report.entries:
        if entry.status != "fail":
            continue
        where = ""
        if entry.location is not None:
            location = entry.location
            spot = location.pointer or (
                f"第 {location.line} 行" if location.line is not None else ""
            )
            where = f"（{location.file}{' ' + spot if spot else ''}）"
        excerpt = f"：{entry.excerpt}" if entry.excerpt else ""
        lines.append(f"{entry.check} {entry.reason}{where}{excerpt}")
    return lines


def fix_request(report: ChecksReport) -> str:
    """回二創段修時接在任務說明後面的那一段。只放檢查結果，不放時間與版本號：
    重開時同一次的任務說明要逐位元組相同，快取才對得上。"""
    listed = failures(report)
    more = len(listed) - _MAX_LISTED
    lines = [f"- {line}" for line in listed[:_MAX_LISTED]]
    if more > 0:
        lines.append(f"- 還有 {more} 條")
    return (
        f"上一次（第 {report.attempt} 次，產出在 stages/{report.stage}/{report.attempt}/）"
        f"的自動檢查沒過：{'、'.join(report.failed_checks)}。這一次要修好：\n"
        + "\n".join(lines)
    )


def latest_report(job_dir: Path, stage_id: str, before: int) -> ChecksReport | None:
    """這一段在第 `before` 次之前最後一份檢查結果（重開時找回自動修要帶的那一份）。"""
    found: ChecksReport | None = None
    directory = JobDir(job_dir).checks
    if not directory.is_dir():
        return None
    for path in directory.glob("checks.v*.json"):
        try:
            report = ChecksReport.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, ValidationError):
            continue
        if report.stage != stage_id or report.attempt >= before:
            continue
        if found is None or report.attempt > found.attempt:
            found = report
    return found
