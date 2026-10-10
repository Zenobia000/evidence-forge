# ADR-0001：工作流引擎用 LangGraph StateGraph，做成確定性節點圖

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者
> **後續修正：** 由 ADR-0009 取代——agent 核心改用 Pi，流程寫成配方與 skill；人工停點由 ADR-0013 拿掉。本文保留當時的理由。
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。

## 1. 背景與問題

- **上下文：** 一次研究執行要跑完「擷取 → 問題 → 規劃 → 搜尋驗證 → 定題 Gate → 寫作 → 匯出」，中間有一個要等人的關卡，還有一個最多 2 輪的回頭補搜（PRD §2、FR-06、FR-11）。
- **問題：** 定題 Gate 可能等上好幾天，程式中途會重啟。流程引擎要能在 Gate 停下、存檔，之後從同一個地方接著跑，而且每一步都要看得到狀態和花費。
- **驅動因素：** 人工 interrupt 與恢復、條件分支（補搜、否決）、checkpoint、可觀測的 State。
- **約束：** 單人專案，維運越少越好；MVP 不需要排程與批次。

## 2. 考量的選項

### 選項一：LangGraph StateGraph

- **描述：** 每個步驟是一個節點，轉移由程式規則決定；用 `interrupt()` 實作定題 Gate，用 checkpointer 依 `thread_id` 存檔與恢復。
- **優點：** interrupt、checkpoint、條件邊都是內建功能；State 可直接觀察。
- **缺點：** 要自己設計 State Schema、checkpoint 與冪等。節點恢復時會**從頭重跑**，`interrupt()` 之前的副作用會再執行一次（見 ADR-0004）。
- **成本／複雜度：** 中

### 選項二：Prefect 3

- **描述：** 以 flow／task 組流程。
- **優點：** 排程、批次、重試、ETL 成熟。
- **缺點：** 逐節點的 LLM 狀態與「停下等人審」的體驗要自己做。本專案 MVP 沒有排程需求，它的強項用不上。
- **成本／複雜度：** 中

### 選項三：Pydantic Graph

- **描述：** 小型、型別安全的圖。
- **優點：** 輕、型別嚴謹。
- **缺點：** 中斷恢復要另外接持久化；分支一多就要自己補功能。
- **成本／複雜度：** 低（起步）、高（補齊恢復之後）

### 選項四：多代理自主協作（Multi-Agent Swarm）

- **描述：** 讓幾個代理自己決定下一步交給誰。
- **缺點：** 轉移不可預測、花費沒有上限，和「每個 Run 有預算上限、不允許無限迴圈」（PRD §8）直接衝突。不列入考慮。

## 3. 決策

**選擇：** 選項一，LangGraph StateGraph，做成**確定性節點圖**。

**理由：** 定題 Gate 的「停下、存檔、幾天後接著跑」是這個產品的核心，LangGraph 把它當內建功能，其他選項都要自己補。節點內可以讓 Opus 產生有上限的查證任務，但**節點之間的轉移只由程式規則決定**，不交給模型。

## 4. 後果

- **正面：** 定題 Gate、補搜上限、否決後不寫稿，都能用圖的結構表達並測試。
- **負面：** 綁定 LangGraph 的 State 與 checkpoint 格式；節點重跑的特性要求副作用一律冪等（ADR-0004）。
- **影響範圍：** 所有節點的寫法、State Schema（ADR-0003）、測試方式。
- **重新評估觸發：** 出現排程或批次量產需求（`roadmap.md` 的 Later）；或 LangGraph 的 interrupt／checkpoint API 有不相容的大改。

## 5. 參考

- LangGraph Interrupts：<https://docs.langchain.com/oss/python/langgraph/interrupts>（2026-10-10 核實：恢復時整個節點從頭重跑）
- LangGraph Persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>
- Prefect Flows：<https://docs.prefect.io/v3/concepts/flows>
- Pydantic Graph：<https://pydantic.dev/docs/ai/graph/builder/>
