"""commit／push 之前掃創作者的私人內容指紋（#126）。

用法（在 repo 根目錄；只要 python3，不需要 uv 環境，git 的 hook 在哪裡都跑得起來）：

    python3 scripts/brand_guard.py staged [--worktree]   掃暫存區（git commit 之前）
    python3 scripts/brand_guard.py push -- origin HEAD   掃 git push 要推出去的 commit
    python3 scripts/brand_guard.py pre-push <遠端>        git 原生 pre-push，標準輸入是 ref 清單
    git diff | python3 scripts/brand_guard.py diff       掃標準輸入的 unified diff
    python3 scripts/brand_guard.py files <檔>...          整檔掃描
    python3 scripts/brand_guard.py stats                 只印指紋來源與數量

exit 0 乾淨、1 有命中（只印哪個檔哪一行，不印命中的字）、2 掃不了（呼叫端要當成擋下）。
指紋怎麼來、限制是什麼，見 `evidence_forge.brand_guard`。
"""

import sys
from pathlib import Path

# 不靠 `uv sync` 裝好的套件：直接把 src/ 放進搜尋路徑，所以系統的 python3 就能跑。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evidence_forge.brand_guard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
