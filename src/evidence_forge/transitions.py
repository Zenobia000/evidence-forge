"""工單狀態的轉換表（ADR-0019 §3.4，原本 ADR-0010 的五種已收成三種）。

純函式，不碰檔案。執行器改 `job.md` 的狀態之前先呼叫 `transition()`，
不在表上的轉換一律丟 `InvalidTransition`，不默默接受。
"""

from evidence_forge.models import JobStatus

# 新建的工單一律從 queued 開始，那不是轉換，不在表裡。
ALLOWED_TRANSITIONS: frozenset[tuple[JobStatus, JobStatus]] = frozenset(
    {
        ("queued", "running"),  # 執行器有空位
        ("running", "running"),  # 進到下一段
        ("running", "done"),  # 做完、預算用完、出錯，都結束，用 result 區分
        ("running", "queued"),  # 當機後放回排隊（ADR-0019 §3.4 保留的那一條）
    }
)


class InvalidTransition(ValueError):
    """要求的狀態轉換不在表上。"""


def transition(current: JobStatus, target: JobStatus) -> JobStatus:
    """確認 `current → target` 合法並回傳 `target`；不合法就丟 `InvalidTransition`。"""
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"工單狀態不能從 {current} 轉成 {target}")
    return target
