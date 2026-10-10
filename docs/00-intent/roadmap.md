# 路線圖

> 階段的唯一的家。票與 PR 引用階段編號（`R2`），不另寫一版。需求編號（`FR-xx`）的內容在 `10-truth/requirements/prd.md`。
> 原始版本：`30-snapshot/2026-10-10-prd-roadmap-v1.0.md` §11。

**和 v1.0 的差異（2026-10-10 使用者拍板）：** 反證迴圈 FR-11 從 R5 提前到 R2，優先級從 P1 改為 P0。理由：Brief 的主流程本來就畫了「證據不足最多回搜 2 輪」，Reviewer 提示詞也在 MVP 裡，排在 R5 會讓 MVP 的流程跟 Brief 對不上。

週次是建議，不是承諾；過不了 Exit Gate 就不進下一階段。

| 階段 | 建議週次 | 交付 | Exit Gate |
| :--- | :--- | :--- | :--- |
| **R0** 基線與契約 | 第 1 週 | 專案骨架、State Schema、Prompt registry、成本計量、20 篇種子測試集 | 同一輸入可重現；每次執行記下模型與 prompt 版本；基線報告完成（含實測的 Tavily credits 用量） |
| **R1** Research Scout | 第 2–3 週 | FR-01、FR-02：URL／References 擷取、去重、Qwen 候選問題；簡易 CLI 或 Web 表單 | 可存取的測試文章中，≥ 90% 保留原始來源與連結；候選問題可人工修改 |
| **R2** Research Director | 第 4–5 週 | FR-03、FR-04、FR-05、FR-06、FR-11：Opus 研究計畫、Tavily 定向追查、證據矩陣、反證迴圈、定題 Gate | 研究問題能追溯到原文；人工核准／否決與中斷恢復的測試全過；反證迴圈不超過 2 輪；**MVP 研究核心完成** |
| **R3** Editorial & Assets | 第 6–7 週 | FR-07、FR-08、FR-09：Sonnet 大綱與草稿、引用審核、素材清單檔、MDX 內容包 | 核心引用全部可定位；未核准的圖片不輸出；文章預覽可閱讀 |
| **R4** Publish Preview | 第 8 週 | FR-10、FR-12：執行紀錄、API、Git PR／CMS 草稿、頁面預覽、稽核紀錄 | 人工核准才能送預覽；不直接公開；文章與引用的版本可追蹤 |
| **R5** Quality Optimization | 第 9–10 週 | `brief.md` §5 的 A/B/C 實驗、Prompt 版本管理、選擇性呼叫 Opus | 20 篇的品質／成本基準完成；訂出實際預算與 Opus 的去留（ADR-0002 的重評點） |
| **Later** 量產期 | — | 多搜尋商、批次排程、RAG、跨文章連結、舊文過期提醒 | 依實測的漏搜率、文章庫規模、維運負荷再決定 |

MVP = R0～R3。R4 之後才接網站。
