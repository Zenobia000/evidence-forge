"""執行器在段與段之間跑的程式檢查（PRD v3.0 §7、FR-VF-01、架構草案 v0.3 §6、測試藍圖 SEAM-06）。

檢查交給 `checks/`，不交給模型：交給模型就等於由模型決定檢查做不做、結果怎麼回報。
哪一段跑哪幾項，看這一段在配方裡宣告的**產出檔名**，不看 skill 的名稱（#78）：
產出檔名是 skill 與執行器之間的約定（`report.md` 就是分析報告），skill 換名字、
或新配方用別的 skill 交同一種產出，檢查照樣會跑，不會因為名字沒登記而默默漏掉。

| 宣告的產出 | 檢查 | 沒過 |
| :--- | :--- | :--- |
| `report.md`（分析報告） | 五節依序、`sources.json` 合 schema、引用的編號在清單上 | 這段這次失敗，走重試與升級 |
| `brief.md` | 六節都在、`brief.json` 合 schema | 這段這次失敗，走重試與升級 |
| `draft.md`（二創稿，舊配方） | C2～C5 | 帶著結果回這一段修，最多 `auto_fix` 次 |
| `claims.json`（整理，舊配方） | C1、C5 | 這段這次失敗，走重試與升級 |

一段同時宣告好幾種時，照表的順序取第一個。都沒有的段不跑程式檢查，只驗 schema（`validate_outputs`）。
`sources.json` 的網址是不是這一段真的抓過的，由 `sources.py` 查（看閘道的帳本）。
二創段的 C2 引用編號、C3 比的正文都是種子加補料段放的來源（`sources.job_sources`，#70）；
稿子註腳裡照抄的出處（標題、網址）C3 不算。

結果寫成 `checks/checks.vN.json`，只看執行器自己算的；agent 在輸出目錄寫的任何
`checks.json` 都不讀。檢查本身跑不動（原文是空的、schema 壞掉、路徑跑出工單資料夾）時丟
`ChecksUnavailable`：重跑模型修不好，執行器直接帶著原因結束這張單。
"""

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from evidence_forge.checks import (
    ChecksReport,
    load_check_settings,
    run_brief_checks,
    run_compose_checks,
    run_digest_checks,
    run_report_checks,
    write_checks_report,
)
from evidence_forge.checks.text import normalize_url
from evidence_forge.models import Item, RecipeStage
from evidence_forge.paths import DataDir, JobDir
from evidence_forge.runner import sources

# 產出檔名 → 檢查；順序就是一段宣告好幾種時的優先順序（見上表）
REPORT_OUTPUT = "report.md"
BRIEF_OUTPUT = "brief.md"
DRAFT_OUTPUT = "draft.md"
CLAIMS_OUTPUT = "claims.json"
# 分析報告的檢查從前面各段的產出找這一份，拿 brief 的關鍵主張
BRIEF_JSON = "brief.json"
_CHECKED_OUTPUTS = (REPORT_OUTPUT, BRIEF_OUTPUT, DRAFT_OUTPUT, CLAIMS_OUTPUT)

# 原文（種子）在工單裡的位置。二創段的來源清單是種子加補料段放的來源（`sources.job_sources`）
ORIGINAL_SOURCE = sources.SEED

# 任務說明裡最多列幾條沒過的條目；全部都在 checks.vN.json
_MAX_LISTED = 10


class ChecksUnavailable(Exception):
    """硬檢查跑不動：問題在執行器或 skill 的設定，不在這次產出。"""


def checked_output(stage: RecipeStage) -> str | None:
    """這一段要跑哪一組檢查：宣告的產出裡第一個認得的檔名；都不認得就是 None。"""
    return next((name for name in _CHECKED_OUTPUTS if name in stage.output), None)


def has_hard_checks(stage: RecipeStage) -> bool:
    return checked_output(stage) is not None


def fixable(stage: RecipeStage) -> bool:
    """沒過時回這一段修（二創稿），而不是算這次失敗。"""
    return checked_output(stage) == DRAFT_OUTPUT


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
    seed: Item | None = None,
    upstream: Sequence[str] = (),
) -> ChecksReport | None:
    """跑這一段這一次的硬檢查並寫成 `checks.vN.json`；這一段沒有硬檢查就回傳 None。

    `seed` 是種子的素材項目（`sources.seed_item`）：二創段用它的標題、網址認出 S1 的註腳，
    分析報告的來源清單可以列它的網址。`upstream` 是前面各段合格那一次的產出（`runner.upstream_outputs`）：
    分析報告從裡面的 `brief.json` 拿關鍵主張，檢查每條剛好一筆查證。
    """
    checked = checked_output(stage)
    if checked is None:
        return None
    base = Path("stages") / stage.id / str(attempt)
    try:
        if checked == CLAIMS_OUTPUT:
            output = base / CLAIMS_OUTPUT
            files: tuple[Path, ...] = (output,)
            entries = run_digest_checks(
                job_dir, output=output, schema=schema, source=ORIGINAL_SOURCE
            )
        elif checked == BRIEF_OUTPUT:
            document = base / BRIEF_OUTPUT
            output = base / _output(stage, ".json")
            files = (document, output)
            entries = run_brief_checks(job_dir, document, output=output, schema=schema)
        elif checked == REPORT_OUTPUT:
            document = base / REPORT_OUTPUT
            output = base / _output(stage, ".json")
            files = (document, output)
            # 報告的來源清單只能列這一段這一次真的抓成功的網頁，或原文的網址
            allowed = set(sources.fetched_pages(data, job_dir, stage.id, attempt))
            if seed is not None and seed.url:
                allowed.add(normalize_url(seed.url))
            brief = next(
                (Path(p) for p in upstream if Path(p).name == BRIEF_JSON), None
            )
            entries = run_report_checks(
                job_dir,
                document,
                output=output,
                schema=schema,
                allowed_urls=allowed,
                brief=brief,
            )
        else:
            draft = base / DRAFT_OUTPUT
            output = base / _output(stage, ".json")
            files = (draft, output)
            found = sources.job_sources(job_dir, seed)
            entries = run_compose_checks(
                job_dir,
                draft=draft,
                output=output,
                schema=schema,
                sources={sid: source.path for sid, source in found.items()},
                thresholds=load_check_settings(data).c3,
                footnotes={sid: source.references for sid, source in found.items()},
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
