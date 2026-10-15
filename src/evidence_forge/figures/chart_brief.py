"""證據帳本（`charts/evidence.json`）與每張圖的規格（`charts/fig-N.brief.json`）的檢查。

只檢查結構與互相的引用：帳本裡每筆 claim 該有的欄位、brief 引用的 claim 與錨點存不存在。
它不判斷來源是真是假、數字算得對不對、畫出來的圖長怎樣——那些是人看的。

錯誤一律回成字串清單（空清單就是通過），不丟例外、不印東西，呼叫的人自己決定怎麼呈現。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

Json = dict[str, Any]

TITLE_MAX = 30  # 結論標題的字數上限
KINDS = ("observed", "derived", "forecast", "hypothesis")
RELATIONS = ("direct-label", "difference", "reference", "callout", "guide", "correspondence", "hypothesis")
REQUIRED = (
    "version", "exhibit", "reader_question", "takeaway", "evidence_ids", "evidence_mode",
    "candidates", "selection_reason", "engine", "canvas", "panels", "annotations",
    "source_notes", "outputs",
)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def validate_evidence(ledger: Json) -> tuple[list[str], dict[str, Json]]:
    """檢查證據帳本；回傳 (錯誤清單, {claim_id: claim})。"""
    errors: list[str] = []
    claims = ledger.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["evidence: claims must be a nonempty list"], {}
    by_id: dict[str, Json] = {}
    for c in claims:
        cid = c.get("claim_id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not _nonempty(cid) or cid in by_id:
            errors.append(f"evidence: claim_id missing or duplicated: {cid!r}")
            continue
        by_id[cid] = c
        where = f"evidence[{cid}]"
        if not _nonempty(c.get("statement")):
            errors.append(f"{where}: statement required")
        kind = c.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: kind must be one of {KINDS}")
        if kind in ("observed", "forecast") and not _nonempty(c.get("source_location")):
            errors.append(f"{where}: {kind} claims need source_location")
        if kind == "derived":
            if not _nonempty(c.get("formula")):
                errors.append(f"{where}: derived claims need formula")
            if not _str_list(c.get("input_claim_ids")) or not c["input_claim_ids"]:
                errors.append(f"{where}: derived claims need input_claim_ids (a nonempty list of claim ids)")
        if kind == "hypothesis" and c.get("values"):
            errors.append(f"{where}: hypotheses must not carry values")
        values = c.get("values")
        if values is not None:
            if not isinstance(values, dict) or not values or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in values.values()
            ):
                errors.append(f"{where}: values must be a nonempty {{label: number}} object")
            for key in ("unit", "period", "population"):
                if not _nonempty(c.get(key)):
                    errors.append(f"{where}: claims with values need {key} (write 原文未列 if the source omits it)")
    for cid, c in by_id.items():
        refs = c.get("input_claim_ids")
        # 型別不對的上面已經報過；這裡只查字串的引用，不讓 dict／list 進成員測試
        if _str_list(refs):
            for ref in refs:
                if ref not in by_id:
                    errors.append(f"evidence[{cid}]: input_claim_id {ref} not in ledger")
        elif refs is not None and c.get("kind") != "derived":
            errors.append(f"evidence[{cid}]: input_claim_ids must be a list of claim ids")
    return errors, by_id


def _check_exhibit(brief: Json, errors: list[str]) -> None:
    ex = brief.get("exhibit")
    if not isinstance(ex, dict):
        errors.append("exhibit must be an object")
        return
    if not isinstance(ex.get("number"), int) or isinstance(ex["number"], bool) or ex["number"] < 1:
        errors.append("exhibit.number must be a positive integer")
    for key in ("title", "subtitle", "source"):
        if not _nonempty(ex.get(key)):
            errors.append(f"exhibit.{key} required")
    if _nonempty(ex.get("title")) and len(ex["title"]) > TITLE_MAX:
        errors.append(f"exhibit.title is {len(ex['title'])} chars; keep the action title within {TITLE_MAX}")


def _check_panels(brief: Json, errors: list[str]) -> set[str]:
    """檢查面板；回傳所有錨點 id，標註的 anchor_id 要對得上。"""
    anchors: set[str] = set()
    panel_ids: set[str] = set()
    panels = brief.get("panels")
    if not isinstance(panels, list) or not panels:
        errors.append("panels must be nonempty")
        return anchors
    for panel in panels:
        if not isinstance(panel, dict):
            errors.append("panel must be an object")
            continue
        for key in ("id", "form", "metric", "unit", "scale", "anchor_ids"):
            if key not in panel:
                errors.append(f"Panel missing {key}")
        pid = panel.get("id")
        if not isinstance(pid, str) or not _nonempty(pid) or pid in panel_ids:
            errors.append("Panel IDs must be unique nonempty strings")
        else:
            panel_ids.add(pid)
        refs = panel.get("anchor_ids")
        if not isinstance(refs, list) or not all(_nonempty(x) for x in refs):
            errors.append("anchor_ids must be a list of nonempty strings")
        else:
            for anchor in refs:
                if anchor in anchors:
                    errors.append(f"Duplicate anchor ID: {anchor}")
                anchors.add(anchor)
    return anchors


def _check_annotations(brief: Json, anchors: set[str], ids: list[str], errors: list[str]) -> None:
    annotations = brief.get("annotations")
    if not isinstance(annotations, list):
        errors.append("annotations must be a list")
        return
    if not annotations and not brief.get("annotation_decision"):
        errors.append("Empty annotations require annotation_decision")
    if sum(1 for a in annotations if isinstance(a, dict) and a.get("relation") == "callout") > 1:
        errors.append("At most one callout (the 'so what' box) per chart")
    seen: set[str] = set()
    for annotation in annotations:
        if not isinstance(annotation, dict):
            errors.append("annotation must be an object")
            continue
        for key in ("id", "text", "claim_ids", "anchor_id", "relation"):
            if key not in annotation:
                errors.append(f"Annotation missing {key}")
        aid = annotation.get("id")
        if not isinstance(aid, str) or not _nonempty(aid) or aid in seen:
            errors.append("Annotation IDs must be unique nonempty strings")
        else:
            seen.add(aid)
        if annotation.get("relation") not in RELATIONS:
            errors.append(f"Annotation {aid}: relation must be one of {RELATIONS}")
        anchor = annotation.get("anchor_id")
        if not isinstance(anchor, str) or anchor not in anchors:
            errors.append(f"Annotation {aid}: anchor_id not found in panels (it must be a string naming a panel anchor)")
        claims = annotation.get("claim_ids")
        if not _str_list(claims) or not all(x in ids for x in claims):
            errors.append(f"Annotation {aid}: claim_ids must be listed in evidence_ids")


def validate_brief(brief: Json, ledger: dict[str, Json] | None) -> list[str]:
    """檢查一張圖的 brief。`ledger` 是帳本的 {claim_id: claim}；沒給就不查 claim 的引用。"""
    errors = [f"Missing field: {key}" for key in REQUIRED if key not in brief]
    if brief.get("version") != "1.0":
        errors.append("version must be 1.0")
    for key in ("reader_question", "takeaway", "selection_reason"):
        if not _nonempty(brief.get(key)):
            errors.append(f"{key} must be a nonempty string")

    _check_exhibit(brief, errors)

    if brief.get("engine") not in ("matplotlib", "svg", "hybrid"):
        errors.append("engine must be matplotlib, svg or hybrid")
    mode = brief.get("evidence_mode")
    if mode not in ("quantitative", "qualitative"):
        errors.append("evidence_mode must be quantitative or qualitative")

    raw_ids = brief.get("evidence_ids")
    ids: list[str] = []
    if isinstance(raw_ids, list) and all(isinstance(x, str) for x in raw_ids):
        ids = list(raw_ids)
    else:
        errors.append("evidence_ids must be a list of strings")
    if mode == "quantitative" and not ids:
        errors.append("Quantitative charts require evidence_ids")
    if ledger is not None:
        for cid in ids:
            if cid not in ledger:
                errors.append(f"evidence_id {cid} not in evidence.json")
        if mode == "quantitative" and ids and not any(ledger.get(cid, {}).get("values") for cid in ids):
            errors.append("Quantitative chart cites no claim with values")

    candidates = brief.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        errors.append("candidates must be nonempty")
    else:
        if len(candidates) < 2 and not brief.get("candidate_exception"):
            errors.append("At least two candidates or candidate_exception required")
        for item in candidates:
            if not isinstance(item, dict) or any(not item.get(k) for k in ("form", "benefit", "risk")):
                errors.append("Each candidate requires form, benefit, risk")

    canvas = brief.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("canvas must be an object")
    else:
        for key in ("width", "height"):
            value = canvas.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"canvas.{key} must be positive")
        if canvas.get("unit") not in ("px", "in", "mm"):
            errors.append("canvas.unit must be px, in or mm")

    anchors = _check_panels(brief, errors)
    _check_annotations(brief, anchors, ids, errors)

    if not isinstance(brief.get("source_notes"), list):
        errors.append("source_notes must be a list")
    outputs = brief.get("outputs")
    if not isinstance(outputs, dict) or not all(_nonempty(outputs.get(k)) for k in ("preview", "vector")):
        errors.append("outputs requires preview and vector paths")
    return errors


@dataclass
class ChartsReport:
    """整個 `charts/` 資料夾的檢查結果。沒有錯誤才算通過。"""

    errors: list[str] = field(default_factory=list)
    briefs_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_object(path: Path) -> Json:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return data


def validate_charts(target: Path, evidence_path: Path | None = None) -> ChartsReport:
    """檢查一個 `charts/` 資料夾（全部 `fig-*.brief.json` 加 `evidence.json`），或單一 brief 檔。

    給資料夾：帳本預設是同資料夾的 `evidence.json`；給單一 brief：帳本預設在它旁邊。
    有 brief 卻沒有帳本是錯：沒有帳本，brief 引用的 claim 就無從檢查，不能當作通過。
    """
    report = ChartsReport()
    if target.is_dir():
        briefs = sorted(target.glob("fig-*.brief.json"))
        evidence_path = evidence_path or target / "evidence.json"
    else:
        briefs = [target]
        evidence_path = evidence_path or target.parent / "evidence.json"
    if not briefs:
        report.errors.append(f"no fig-*.brief.json in {target}")
        return report

    ledger: dict[str, Json] | None = None
    try:
        if evidence_path.exists():
            ev_errors, ledger = validate_evidence(_load_object(evidence_path))
            report.errors.extend(f"{evidence_path.name}: {e}" for e in ev_errors)
        else:
            report.errors.append(f"{evidence_path} not found; briefs cite claims that cannot be checked")
        for path in briefs:
            report.errors.extend(f"{path.name}: {e}" for e in validate_brief(_load_object(path), ledger))
            report.briefs_checked += 1
    except (OSError, ValueError) as exc:  # json.JSONDecodeError 也是 ValueError
        report.errors.append(str(exc))
    return report
