# 路線圖

> 階段的唯一的家。票與 PR 引用階段編號（`R2`），不另寫一版。需求編號（`FR-xx`）的內容在 `10-truth/requirements/prd.md`。
> 原始版本：`30-snapshot/2026-10-10-prd-roadmap-v1.0.md` §11。

**和 v1.0 的差異（2026-10-10 使用者拍板）：**

- 反證迴圈 FR-11 從 R5 提前到 R2，優先級 P1 → P0。Brief 的主流程本來就畫了「證據不足最多回搜 2 輪」，Reviewer 提示詞也在 MVP 裡。
- 執行紀錄 FR-10 從 R4 提前到 R0，優先級 P1 → P0。MVP 匯出的 `run.json` 內容就來自它，R0 本來也要做成本計量。
- R0 多了「開跑前檢查」與「CI 重播整條流程」（PRD NFR-08、NFR-11）：之後每一階段都靠它們證明「每次都跑得動」。
- R0 起就是多 worker 並行的架構（ADR-0006）：PostgreSQL＋Redis 用 Docker 跑，Celery 排工作，GPU 與 API 依資源限流。
- Tavily 用量實測從 R0 移到 R2：搜尋功能 R2 才做，R0 量不到真實用量。
- 操作介面：R1 用 CLI，R2 起用 Web UI 並套用設計系統。網站還沒架，R4 的網站草稿預覽形式在 R3 結束前決定。

週次是建議，不是承諾；過不了 Exit Gate 就不進下一階段。

| 階段 | 建議週次 | 交付 | Exit Gate |
| :--- | :--- | :--- | :--- |
| **R0** 基線與契約 | 第 1 週 | 專案骨架、State Schema、Prompt registry、FR-10 執行紀錄與成本計量、操作紀錄與快取（ADR-0004、0005）、Docker Compose（PostgreSQL、Redis）、Celery worker 與資源限流（ADR-0006）、開跑前檢查、CI 重播骨架、20 篇種子測試集 | 同一輸入可重現；每次執行記下模型與 prompt 版本；同一節點跑兩次只收一次錢；兩個 Run 同時跑時 GPU 不被同時占用；殺掉 worker 後工作會被重送且不重扣錢；基線報告完成 |
| **R1** Research Scout | 第 2–3 週 | FR-01、FR-02：URL／References 擷取、去重、Qwen 候選問題；CLI 操作 | 可存取的測試文章中，≥ 90% 保留原始來源與連結；候選問題可人工修改 |
| **R2** Research Director | 第 4–5 週 | FR-03、FR-04、FR-05、FR-06、FR-11：Opus 研究計畫、Tavily 定向追查、證據矩陣、反證迴圈、定題 Gate；Web UI（套用設計系統） | 研究問題能追溯到原文；人工核准／否決與中斷恢復的測試全過；反證迴圈不超過 2 輪；實測每篇 Tavily credits 用量並更新 `brief.md` 的成本假設；**MVP 研究核心完成** |
| **R3** Editorial & Assets | 第 6–7 週 | FR-07、FR-08、FR-09：Sonnet 大綱與草稿、引用審核、素材清單檔、MDX 內容包、本機預覽 | 核心引用全部可定位；未核准的圖片不輸出；本機預覽可閱讀；改論點後只重跑寫作 |
| **R4** Publish Preview | 第 8 週 | FR-12：API、網站草稿預覽、稽核紀錄 | 人工核准才能送網站草稿預覽；不直接公開；文章與引用的版本可追蹤 |
| **R5** Quality Optimization | 第 9–10 週 | `brief.md` §5 的 A/B/C 實驗（共用證據快照、走 Batch API）、Prompt 版本管理、選擇性呼叫 Opus | 20 篇的品質／成本基準完成；訂出實際預算與 Opus 的去留（ADR-0002 的重評點） |
| **Later** 量產期 | — | 多搜尋商、批次排程、RAG、跨文章連結、舊文過期提醒 | 依實測的漏搜率、文章庫規模、維運負荷再決定 |

MVP = R0～R3。R4 之後才接網站。
