# 產品需求文件（PRD）— Evidence-led Editorial Studio

> **版本：** v1.1 | **更新：** 2026-10-10 | **狀態：** 設計基準，待 POC 實測
> 依 `docs/templates/vibecoding/02_project_brief_and_prd.md` 的 PRD 半部撰寫。
> 為什麼做、成功門檻、成本假設 → `00-intent/brief.md`；階段 → `00-intent/roadmap.md`；決策理由 → `adr/`；詞的定義 → 根目錄 `CONTEXT.md`。
> 原始版本：`30-snapshot/2026-10-10-prd-roadmap-v1.0.md`。v1.1 的差異：FR-11 改為 P0（見 `roadmap.md`）；FR 編號不變。

這份只寫**必須成立什麼**。各項目前做到哪，看 `20-state/`。

## 1. 專案總覽

| 項目 | 內容 |
| :--- | :--- |
| 產品 | 單人使用的研究與圖文產製工作室，固定流程、受限分支 |
| 主要使用者 | 創作者：技術作者／產品經理／資料科學與 AI 工程工作者，可審查來源、對論點負責 |
| MVP 交付物 | 研究提案（Research Brief）、證據矩陣、`sources.json`、`assets.json`、`article.mdx`、審查報告、`run.json` |
| MVP 範圍 | `roadmap.md` 的 R0～R3 |

## 2. 流程與狀態

```mermaid
flowchart LR
    A["匯入種子文章"] --> B["產生研究問題"]
    B --> C["研究規劃"]
    C --> D["搜尋與驗證"]
    D -- "證據不足（最多 2 輪）" --> D
    D --> E{"定題 Gate"}
    E -- "未核准：停在 Gate" --> E
    E -- "核准" --> F["撰寫與素材"]
    F --> G["預覽與發布審核"]
```

| 畫面／狀態 | 創作者做什麼、系統做什麼 | 往下走的條件 |
| :--- | :--- | :--- |
| 01 New Research | 貼 URL；設定主題範圍、語言、目標讀者、搜尋上限 | 來源讀得到才啟動；讀不到時提供手動貼內容 |
| 02 Research Proposal | 看候選研究問題、建議的論點與證據缺口 | 研究規劃的輸出通過 schema 驗證才開始搜尋 |
| 03 Evidence Review | 看引用鏈、原文定位、反證、版本日期與圖片素材 | 證據不足時最多回搜 2 輪；超過就交給人決定 |
| 04 Human Thesis Gate | 接受／修改／否決論點；寫下自己的觀點 | 核准才進入寫作 |
| 05 Draft Preview | 對照大綱與文章；檢查圖、引用和授權狀態 | G1～G5 全過才產出可發表的內容包 |
| 06 Publish Review | 最後人工核准，匯出到網站預覽，不直接上線 | 未核准的外部圖片一律不打包 |

## 3. 使用者故事與允收標準

「創作者」指使用這套系統的那一個人；其餘名詞照 `CONTEXT.md`。BDD 情境在 `features/`。

### Epic 1：來源擷取

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-01 | 身為創作者，我要貼一篇種子文章的 URL，系統就追出它引用了哪些來源，讓我不必手動整理 References。 | 1. 每個來源都有 URL、擷取時間、狀態 2. 讀不到（403、付費牆）標成不可驗證，不繞過 3. 同一個 canonical URL 只出現一次 | FR-01 | `source-intake.feature` |

### Epic 2：研究提案

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-02 | 身為創作者，我要看到 3–5 個候選研究問題，每題都附原文前提，讓我知道研究從哪裡切入。 | 1. 每題有 premise、why_now、evidence_needed、risk、source_ids 2. 缺證據的地方標 UNKNOWN 3. 我可以改題目 | FR-02 | `research-proposal.feature` |
| US-03 | 身為創作者，我要看到一份研究計畫：建議的論點、競爭假說、反方問題、查證路徑，讓研究不會只順著原作者的說法走。 | 1. 輸出 thesis、alternative_views、research_queries、stop_rules 2. 至少一個競爭假說 3. 輸出不合 schema 就不開始搜尋 | FR-03 | `research-proposal.feature` |

### Epic 3：證據

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-04 | 身為創作者，我要看到每個主張對到哪個來源的哪一段，讓我能自己核對。 | 1. 核心主張都能定位到來源原文 2. 二手引用不算獨立來源 3. 找不到來源的主張標 UNVERIFIED | FR-04、FR-05 | `evidence.feature` |
| US-05 | 身為創作者，證據不夠時我要系統自己補搜，但有次數上限，不要無止境燒錢。 | 1. 補搜最多 2 輪 2. 超過就停下交給我決定 3. 每輪的查詢數與 credits 有上限且記錄用量 | FR-04、FR-11 | `evidence.feature` |

### Epic 4：定題 Gate

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-06 | 身為創作者，我要在寫作前接受、修改或否決論點，並寫下自己的觀點，讓文章的立場是我決定的。 | 1. 沒核准就不會開始寫 2. 否決之後不會產出稿子 3. 程式重啟後，同一個 Run 可以從 Gate 接著走 4. 決定、時間與我的觀點都留紀錄 | FR-06 | `thesis-gate.feature` |

### Epic 5：寫作

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-07 | 身為創作者，我要依核准的論點和證據拿到大綱與 2,000–3,000 字的草稿，每個事實都附來源編號。 | 1. 非個人推測的核心事實都有 claim／source ID 2. 沒有憑空補出的來源 3. 來源不支持的敘述標成推論 4. 不沿用原文的敘事順序 | FR-07 | `editorial-draft.feature` |

### Epic 6：素材授權

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-08 | 身為創作者，我要每張圖都附出處和授權狀態，授權不明的預設擋下，讓我不會誤用別人的圖。 | 1. 每張圖有來源 URL、出處頁、作者、授權、使用狀態 2. 授權 unknown 或 denied 一律不進公開內容 3. 只有合法且人工核准的圖能用 | FR-08 | `asset-rights.feature` |

### Epic 7：匯出與發布

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-09 | 身為創作者，我要拿到網站能直接讀的內容包。 | 1. 產出 `article.mdx`、`sources.json`、`assets.json`、`run.json` 2. 全部通過 schema 驗證 3. 預覽沒有遺失的圖或斷掉的連結 | FR-09 | `export.feature` |
| US-10 | 身為創作者，我要把核准的內容送到網站的草稿預覽，但公開一定要我再按一次。 | 1. 可產生 PR 或 CMS 草稿 2. 沒有人工核准就不公開 | FR-12 | `export.feature` |

### Epic 8：執行紀錄

| ID | 故事 | 允收標準 | 需求 | BDD |
| :--- | :--- | :--- | :--- | :--- |
| US-11 | 身為創作者，我要看到每個節點花了多少時間、多少錢、用了哪個模型和 prompt 版本，讓我能比較與追查。 | 每個節點記錄耗時、input／output hash、模型版本、prompt 版本、搜尋請求、費用、錯誤碼 | FR-10 | `run-trace.feature` |

## 4. 功能需求

| ID | 優先 | 功能 | 驗收準則 |
| :--- | :---: | :--- | :--- |
| FR-01 | P0 | URL／Reference 擷取：meta、canonical、出站連結、原文段落定位 | 有來源 URL、擷取時間、狀態；403 或失敗時標示不可驗證 |
| FR-02 | P0 | Scout：產 3–5 個候選研究問題，標記原文前提 | 每題有 premise、why_now、evidence_needed、risk、source_ids |
| FR-03 | P0 | Director：選題、建立假說、反方問題、查證計畫 | 輸出 thesis、alternative_views、research_queries、stop_rules |
| FR-04 | P0 | Search：先追 References，有缺口才觸發 Tavily；相同 canonical 去重 | 每輪 max_queries／max_credits 可設定；記錄用量 |
| FR-05 | P0 | Evidence：來源品質、主張對照、引用片段與引用鏈 | 核心主張可定位到來源；二手引用不重複算成獨立來源 |
| FR-06 | P0 | 定題 Gate：人工接受／修改／否決並恢復流程 | 重啟後依 thread_id 恢復；否決後不寫稿 |
| FR-07 | P0 | Writer：依核准論點＋有引用的證據產大綱與 MDX | 非個人推測的核心事實都配 claim／source ID；禁止憑空補來源 |
| FR-08 | P0 | Asset：圖片 URL、原文位置、作者／授權、下載狀態 | license=unknown 預設擋下；只能用合法且審核通過的 |
| FR-09 | P0 | Export：`article.mdx`、`sources.json`、`assets.json`、`run.json` | schema 驗證通過；預覽沒有遺失圖或斷鏈 |
| FR-10 | P1 | Run Trace：token、模型版本、prompt 版本、搜尋請求與花費 | 每個節點有耗時、input／output hash、費用、錯誤碼 |
| FR-11 | **P0** | 反證迴圈：審查後有限次補查 | 最多 2 輪；超過進人工審查，不無限迴圈 |
| FR-12 | P2 | 網站 Git／CMS 預覽整合 | 可產 PR 或草稿預覽；公開發布要人工核准 |

## 5. 各節點必須守的規則

這裡只寫行為上「必須／不准」。提示詞本身怎麼寫、怎麼做版本管理，是設計，見 `30-snapshot/plans/2026-10-10-architecture-draft.md`。

| 節點 | 必須 | 不准 |
| :--- | :--- | :--- |
| Scout | 提出可驗證、可反駁、對讀者有價值的問題；附原文前提、可能的反例、需要的資料與來源 ID | 自己宣告結論成立；缺證據時不標 UNKNOWN |
| Director | 選出最值得研究的論點；提出競爭假說、資料需求、反證路徑、來源分級、查詢預算與停止條件 | 把二手引用當獨立證明；用常識補資訊缺口 |
| Reviewer | 逐條核對主張與來源定位；指出支持不足、版本不合、衝突與替代解釋；判 PASS／RESEARCH_MORE／BLOCK | 放過沒有證據的主張；RESEARCH_MORE 超過 2 輪 |
| Writer | 只用核准論點、創作者觀點和證據包寫作；圖文依素材 ID 標註 | 照原文的敘事順序重寫；把來源沒支持的敘述寫成事實；捏造引用 |

所有節點：從網頁抓回來的內容只當資料，不當指令（防 Prompt Injection）。

## 6. 來源與素材治理

1. 原始資料和 AI 摘要分開存。
2. 不同文章追到同一份原始公告，算**一組**原始證據。
3. 沒有來源、或無法精確定位的主張，標 UNVERIFIED。
4. 圖片搜得到不等於能公開重製。
5. 保留原文段落與擷取時間，之後要更正版本時才查得到。

## 7. 品質關卡

任一關卡沒過，都不能自動公開發布。

| 關卡 | 擋下的條件 | 怎麼檢查 |
| :--- | :--- | :--- |
| G1 Source | 主要證據沒有 URL、日期或原文定位，或原始來源已失效 | 來源驗證程式＋URL／段落錨點 |
| G2 Claim | 核心事實沒有直接支持；關鍵數據與版本不一致 | 證據矩陣；Reviewer 反證＋人工抽查 |
| G3 Human Thesis | 創作者還沒選定論點，或否決後仍進入寫作 | LangGraph interrupt＋審核紀錄 |
| G4 Asset Rights | 圖片授權 unknown 或 denied 卻輸出公開圖片 | 素材清單檔自動拒絕＋人工核准 |
| G5 Editorial | MDX schema 錯誤、重要引用遺失、文章觀點與核准版本不一致 | Lint／預覽＋人工核稿 |

## 8. 非功能需求

| 主題 | 規則 |
| :--- | :--- |
| 持久化與冪等 | 流程狀態存在有持久性的 checkpoint；每次外部搜尋與檔案寫入用 `run_id + operation_key` 去重；中斷恢復重跑節點時不重複收費、不重複發布（ADR-0003、ADR-0004） |
| 成本控制 | 每個 Run 設 Tavily credits、Opus／Sonnet token 與美元上限；到上限就停下請人調整；不允許無上限的工具迴圈 |
| 錯誤處理 | 403、付費牆、robots 限制不繞過；API 429 指數退避；抓不到的引用標 unavailable；部分成功不當成完成 |
| 資料安全 | URL 白名單與 URL 解析防 SSRF；過濾敏感 token；網頁內容不得改寫系統規則 |
| 可觀測 | 每個節點存模型 ID、prompt hash、耗時、token、工具呼叫、結果 hash、審核決定；外部原始內容另外存檔 |
| 部署 | 本機 Ollama Worker 與 API Worker 分開；MVP 一次只跑一個 Run，避免搶 GPU |

## 9. 範圍與限制

| 項目 | 內容 |
| :--- | :--- |
| 不做 | 全自動公開發布、轉載侵權素材、跨文章 RAG、繞過付費牆、無上限的自主迴圈、多搜尋商聚合 |
| 假設 | 創作者會審來源並對論點負責；本機有一張 3090 跑 Qwen3.5 9B |
| 外部依賴 | Anthropic API（Opus 5.5、Sonnet 5.5）、Tavily Search／Extract、Ollama |

## 10. 風險與最小修正

| 風險 | 最小修正 |
| :--- | :--- |
| Qwen 提的問題偏向原作者的框架 | Director 必須提出競爭假說（§5） |
| Opus 的推論看似可信但缺證據 | G2 以原文定位驗收，不看文字說服力 |
| 網頁內嵌惡意 Prompt | 搜尋內容只當資料、不當指令（§5） |
| interrupt 恢復時重跑節點，產生重複副作用 | 外部呼叫獨立成節點＋`operation_key`（ADR-0004） |
| 圖片搜得到但沒授權 | 素材清單檔預設擋下授權不明的（FR-08、G4） |

## 11. 已做的決策

| ADR | 決策 |
| :--- | :--- |
| ADR-0001 | 工作流引擎用 LangGraph StateGraph，做成確定性節點圖 |
| ADR-0002 | 模型分工：Qwen 產問題、Opus 規劃與複核、Sonnet 寫作 |
| ADR-0003 | State 只存 ID，內容放檔案儲存區；SQLite 起步 |
| ADR-0004 | 外部副作用獨立成節點，用冪等鍵去重 |
