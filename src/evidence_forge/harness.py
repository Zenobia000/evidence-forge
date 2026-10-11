"""repo 裡的 `harness/`：配方、skill、Pi 的設定與擴充（架構草案 v0.3 §2.1）。

其他模組要找這些檔，一律從 `Harness` 拿路徑，不自己拼。
"""

from dataclasses import dataclass
from pathlib import Path

from evidence_forge.runner.pi_launch import PiHarness

# 專案根目錄：本檔在 src/evidence_forge/ 底下，往上兩層（同 paths.py）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Harness:
    root: Path
    pi: PiHarness

    @classmethod
    def at(cls, root: Path) -> "Harness":
        pi_dir = root / "pi"
        return cls(
            root=root,
            pi=PiHarness(
                # 用 harness/pi/ 鎖定的版本（package.json＋lock 檔），不用全域安裝的 pi
                pi_bin=pi_dir / "node_modules" / ".bin" / "pi",
                system_prompt=pi_dir / "system-prompt.md",
                ef_tools=pi_dir / "extensions" / "ef-tools" / "index.ts",
                agent_template=pi_dir / "agent",
            ),
        )

    @property
    def recipes(self) -> Path:
        return self.root / "recipes"

    def skill_dir(self, name: str) -> Path:
        return self.root / "skills" / name


def default_harness() -> Harness:
    return Harness.at(_PROJECT_ROOT / "harness")
