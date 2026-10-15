"""repo 裡的 `harness/`：配方、skill、Pi 的設定與擴充（架構草案 v0.3 §2.1）。

其他模組要找這些檔，一律從 `Harness` 拿路徑，不自己拼。
"""

from dataclasses import dataclass
from pathlib import Path

from evidence_forge.runner.pi_launch import PiHarness

# 專案根目錄：本檔在 src/evidence_forge/ 底下，往上兩層（同 paths.py）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 主題清單的檔名：跟文體定義放在同一個資料夾（ADR-0026 §3.2）
THEMES_FILE = "themes.md"


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

    @property
    def genres(self) -> Path:
        """文體的通用定義（ADR-0026），一種一個 `<slug>.md`；同一個資料夾還放主題清單 `themes.md`。"""
        return self.root / "genres"

    @property
    def themes(self) -> Path:
        """主題清單（ADR-0026 §3.2）：8 個 slug 與一句通用說明。"""
        return self.genres / THEMES_FILE

    def genre_files(self) -> list[Path]:
        """7 種文體的定義檔，依檔名排序。主題清單跟它們放在同一個資料夾，但不是文體，要跳過。"""
        return sorted(p for p in self.genres.glob("*.md") if p.name != THEMES_FILE)

    def skill_dir(self, name: str) -> Path:
        return self.root / "skills" / name

    def schema_file(self, name: str) -> Path:
        """skill 的 `schema.json`：執行器用它驗這一段的產出（ADR-0009 §3 第 6 點）。"""
        return self.skill_dir(name) / "schema.json"


def default_harness() -> Harness:
    return Harness.at(_PROJECT_ROOT / "harness")
