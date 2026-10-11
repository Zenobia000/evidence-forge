"""Evidence Forge。

各模組（架構草案 v0.3 §2.1）：

- `paths`：資料目錄（`EF_DATA`）在哪裡、裡面的固定位置。
- `models`：資料夾契約，每種檔案的格式（`job.md`、配方、`item.json`、
  `events.jsonl`、決定檔、帳本）。
- `documents`：讀寫「frontmatter＋Markdown」的檔（`job.md`、配方）。
- `transitions`：工單狀態的轉換表。
- `write_once`：只增不改的寫檔工具。

之後的元件各自一個子套件：`glue/`、`runner/`、`gateway/`、`checks/`、`ui/`、`evals/`。
"""
