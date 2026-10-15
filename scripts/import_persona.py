"""把創作者的寫作系統（zip 或解開的資料夾）拆進角色資料夾 `$EF_DATA/authors/<角色>/`（#106、ADR-0026 §3.3）。

用法（在 repo 根目錄）：

    EF_DATA=<資料目錄> uv run python scripts/import_persona.py <zip 或資料夾> [--persona me] [--dry-run]

角色資料夾已經有東西時，先整份備份到 `$EF_DATA/authors/.backup/<角色>-<時間>/`，才換掉匯入要寫的檔；
什麼都不刪。只印檔名與備份位置，不印內容。做法與規則見 `evidence_forge.persona_import`。
"""

import sys

from evidence_forge.persona_import import main

if __name__ == "__main__":
    sys.exit(main())
