"""事件紀錄 `jobs/<id>/events.jsonl`：只有執行器寫，只增不改（v0.3 §2.1）。"""

from pathlib import Path

from evidence_forge.models import Event


def append(job_dir: Path, event: Event) -> None:
    with (job_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")
