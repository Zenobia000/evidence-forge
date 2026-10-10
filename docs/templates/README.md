# docs/templates/ — 外來的文件範本

這裡放的是**別人寫好的範本**，只拿來抄結構。專案自己的內容一律寫進 `00-intent/`、`10-truth/`、`30-snapshot/`，不寫在這裡。

| 子目錄 | 來源 | 取得時的版本 |
| --- | --- | --- |
| `vibecoding/` | `Zenobia000/claude-Godzilla-z` 的 `VibeCoding_Workflow_Templates/` | `main@0b5267b`（2026-10-10），純檔案複製，沒有 git 連結 |

規則：

- **不改範本內文。** 要不同的寫法，就在專案文件裡寫；範本保持原樣，之後才比得出上游改了什麼。
- 範本裡寫死的路徑（例：`.claude/PLAYBOOK.md`、`docs/adr/`）是它原本那個 repo 的，對不到本 repo 時照 `docs/README.md` 的「一件事只有一個家」放。
- 用不到的範本可以整份刪，要用時再從來源拿。
