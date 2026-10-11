"""版本鎖在產出上（NFR-RUN-06、TC-RUNNER-06）：每一段每一次開 Pi 之前，執行器在
`jobs/<id>/provenance/<段>.<第幾次>.json` 記下當時用的模型對照、skill 的 git sha、配方版本。

放在 `stages/` 外面：`stages/` 只有 agent 經閘道寫（ADR-0010 §3 第 3 點），agent 的
`write_file` 也只准寫 `stages/<段>/<次>/`，碰不到這一份。開 Pi 之前寫、只增不改；重開
`ef serve` 重跑同一次時已經有了就不再寫，agent 列檔案看到的東西跟上次相同，快取才對得上。
git 查不到（不是 git repo、檔案還沒 commit）時 sha 是 null，另外記內容的 SHA-256，
有沒有改過一樣查得出來。
"""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from evidence_forge.harness import Harness
from evidence_forge.models import Recipe, RecipeStage, Tier
from evidence_forge.paths import JobDir


def record_path(job_dir: Path, stage: str, attempt: int) -> Path:
    """這一段這一次的版本紀錄放在哪裡。"""
    return JobDir(job_dir).provenance / f"{stage}.{attempt}.json"


def _git(directory: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def git_version(path: Path) -> dict[str, Any]:
    """最後一次改到 `path` 的 commit，與工作目錄裡有沒有還沒 commit 的改動。"""
    directory = path if path.is_dir() else path.parent
    sha = (_git(directory, "log", "-1", "--format=%H", "--", str(path)) or "").strip()
    status = _git(directory, "status", "--porcelain", "--", str(path))
    return {
        "git_sha": sha or None,
        "dirty": bool(status.strip()) if status is not None else None,
    }


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _sha256_tree(directory: Path) -> str | None:
    """資料夾裡每個檔的相對路徑與內容一起算一個 hash。"""
    if not directory.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def provenance(
    harness: Harness,
    recipe: Recipe,
    stage: RecipeStage,
    attempt: int,
    tier: Tier,
    models_file: Path,
) -> dict[str, Any]:
    raw = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
    models = {
        str(name): {"backend": entry.get("backend"), "model": entry.get("model")}
        for name, entry in raw.items()
        if isinstance(entry, dict)
    }
    skill_dir = harness.skill_dir(stage.skill)
    recipe_file = harness.recipes / f"{recipe.name}.md"
    return {
        "stage": stage.id,
        "attempt": attempt,
        "tier": tier,
        "escalated_from": stage.tier if tier != stage.tier else None,
        # 模型對照：別名對到哪個實際模型（models.yaml）
        "models": models,
        "models_sha256": _sha256_file(models_file),
        "skill": {
            "name": stage.skill,
            **git_version(skill_dir),
            "sha256": _sha256_tree(skill_dir),
        },
        "recipe": {
            "name": recipe.name,
            **git_version(recipe_file),
            "sha256": _sha256_file(recipe_file),
        },
    }


def render(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2) + "\n"
