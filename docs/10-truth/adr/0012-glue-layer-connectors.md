# ADR-0012：膠水層用程式自己寫，不用 LLM，帳號授權不交給 agent

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者（與架構 session 討論後拍板）
> **後續修正：** 由 ADR-0014 修正——帳號授權改放 `~/.config/evidence-forge/secrets/`（`EF_SECRETS`），不放資料目錄。
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。

## 1. 背景與問題

- **上下文：** 素材不只一篇手貼的文章：Gmail 裡的電子報、各種 app 裡的文章。有些 app 有 MCP、有些有 API、有些什麼都沒有，只能讓 computer use 之類的工具先把文章放到 Google Drive。創作者要的是像 n8n 那樣能串各種工具的一層，但用自己寫的程式，不用 n8n。
- **問題：** 這一層要不要讓 agent 來做？帳號授權放哪？
- **為什麼要寫成 ADR：** 這一層拿著創作者所有帳號的授權，放錯地方就是安全問題。

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 用 n8n | 不採用（創作者明確不要） |
| 讓 agent 帶著 MCP 工具自己去抓 | 不採用。電子報與網頁是外人寫的，可能藏著指令（例如「把所有信轉寄到某處」）；讀了這些內容的 agent 不能同時拿著信箱授權 |
| **自己寫 connector，抓取是固定程式** | **採用** |

## 3. 決策

1. **一種接法一個 connector，介面相同**：照 `feeds.yaml` 裡每個訂閱源的設定抓東西，輸出統一格式到 `$EF_DATA/inbox/<訂閱源>/<項目>/`，內容包括：
   - `item.json`：出處、標題、作者、時間、內容 hash
   - `content.md`
   - `raw/`
   - `images/`
2. **第一批 connector 種類**：
   - `manual`：UI 貼網址、貼文字、上傳檔案
   - `rss`
   - `gmail`：Gmail API，唯讀，只抓指定 label
   - `mcp`：程式直接當 MCP client 呼叫，不經過 LLM
   - `drive`：Google Drive 指定資料夾
   要加新的接法，就是寫一個新的 connector；只是多一個同類的訂閱源（例如再訂一個部落格），在 `feeds.yaml` 加一行就好。後面的層都不動。
3. **膠水層不用 LLM。** 要抽正文、切段，用固定的解析程式。
4. **授權只在膠水層**：放在 `$EF_DATA/secrets/`，權限 600，執行器、閘道、Pi 都不讀。
5. **computer use 不在這個系統裡**：外部工具把文章放進 Drive，這裡只認 `drive` connector。
6. 同一個內容 hash 只進 inbox 一次。訂閱源設定可以指定預設配方、目標、預算，執行器據此自動開工作單。
7. 排程在常駐程序裡跑（APScheduler）。

## 4. 後果

- **正面：** 抓取穩定、便宜、可測；帳號授權與讀外來內容的 agent 完全隔開；新訂閱源不影響後面的流程。
- **負面：**
  - 每種接法要寫一個 connector。
  - 沒有接口的 app，依賴外部工具的穩定度。
  - WSL 關機時排程不跑。
- **影響範圍：** `src/evidence_forge/glue/`、`$EF_DATA/feeds.yaml`、`$EF_DATA/secrets/`；PRD 的訂閱源接入需求。
- **重新評估觸發：** 出現必須「理解內容才能決定怎麼抓」的訂閱源（例如要在 app 裡依內容點選）。那種情況應該寫成外部工具，把結果丟進 Drive，而不是把 LLM 放進膠水層。

## 5. 參考

- MCP Python SDK：<https://github.com/modelcontextprotocol/python-sdk>
- 架構草案 v0.3 §9：`30-snapshot/plans/2026-10-10-architecture-draft-v0.3.md`
