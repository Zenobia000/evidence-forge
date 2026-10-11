# ADR-0003：State 只存 ID，內容放檔案儲存區；SQLite 起步

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者
> **後續修正：** 由 ADR-0010 再修正——儲存改成一張工單一個資料夾，不用 PostgreSQL、不用 checkpoint。ID 規則不變。
> **後續修正：** 由 ADR-0006 修正——checkpoint 與 metadata 改用 PostgreSQL（為了多個 Run 並行）。「State 只存 ID、內容放檔案儲存區」不變。
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。

## 1. 背景與問題

- **上下文：** 一次研究執行會抓回很多原文全文、圖片和長篇草稿。LangGraph 每走一步就存一次 checkpoint（ADR-0001）。
- **問題：** 全文和圖片放進 State，每個 checkpoint 都會複製一份，資料庫很快膨脹，恢復也變慢。另外，PRD §6 要求「原始資料和 AI 摘要分開存」、「保留原文段落與擷取時間」，這些需要可定址、不會被覆寫的儲存。
- **驅動因素：** checkpoint 輕量；原文可回溯；內容依 hash 去重。
- **約束：** 單人、單機起步；MVP 一次只跑一個 Run。

## 2. 考量的選項

### 選項一：內容全放 State

- **優點：** 最簡單，一個地方查全部。
- **缺點：** checkpoint 膨脹；原文和摘要混在一起，違反 PRD §6。
- **成本／複雜度：** 低（起步）、高（之後要拆）

### 選項二：State 只放輕量 JSON 與 ID，內容放檔案儲存區

- **描述：** 原始擷取、圖片、MDX 依內容 hash 存成檔案；State 只放 `source_id`、`asset_id`、狀態與小型結構化結果。
- **優點：** checkpoint 小；原文不可變、可回溯；同一份內容自然去重。
- **缺點：** 讀內容要多一次查表；要處理「State 指到的檔案不存在」的情況。
- **成本／複雜度：** 中

### 選項三：一開始就用 PostgreSQL＋物件儲存

- **優點：** 多使用者、併發都準備好了。
- **缺點：** 單人 MVP 多了兩個要維運的服務，現在用不到。
- **成本／複雜度：** 高

## 3. 決策

**選擇：** 選項二，State 與內容分開。起步用 **SQLite**（checkpoint 與 metadata）＋**本機檔案系統**（內容），內容依 hash 定址。

**理由：** 選項一之後一定要拆，拆的時候所有節點都要改；選項三的成本現在付不出好處。選項二的界線（State 只放 ID）一開始就畫好，之後從 SQLite 換 PostgreSQL、從本機檔案換物件儲存，都不用動節點的寫法。

## 4. 後果

- **正面：** checkpoint 小、恢復快；原文的擷取時間與 hash 一直保留著，可以支援之後的版本更正。
- **負面：** LangGraph 官方文件把 `SqliteSaver` 定位在本機開發用；正式多使用者時要換 `PostgresSaver`。
- **影響範圍：** State Schema、所有讀寫內容的節點、匯出（FR-09）。
- **重新評估觸發：** 要同時跑多個 Run、要多使用者，或 SQLite 寫入鎖成為瓶頸時，換 PostgreSQL checkpointer、物件儲存與佇列。

## 5. 參考

- LangGraph Persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>（2026-10-10 核實：`SqliteSaver` 為本機檔案儲存、適合開發；正式環境用 `PostgresSaver`；記憶體版重啟即遺失）
