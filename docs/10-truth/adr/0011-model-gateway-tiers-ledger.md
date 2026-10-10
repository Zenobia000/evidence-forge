# ADR-0011：自己寫模型閘道；固定規則分級、失敗才升級；全部記帳

> **狀態：** 已接受（S1 評估後重新檢視分級） | **日期：** 2026-10-10 | **決策者：** 專案擁有者（與架構 session 討論後拍板）
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **修正 ADR-0002**（模型分工）。**修正 ADR-0004** 的實作：冪等改由閘道的請求 hash 快取保證。

## 1. 背景與問題

- **上下文：** 創作者要本機優先：本機 Qwen 做大部分工作，只在關鍵處用雲端高階模型；模型要能動態切換、不被綁死；也要一份帳單，看到每個環節的用量、延遲、花費，並在換模型後確認品質沒退步。
- **問題：**
  1. 誰決定每一步用哪個模型？
  2. 預算、快取、記帳要放在哪，才不會綁在某個 agent 框架上？
- **為什麼要寫成 ADR：** 所有花錢的呼叫都經過這一層；模型分工決定成本結構。

## 2. 考量的選項

**派發方式：**

| 選項 | 結果 |
| :--- | :--- |
| 學習型派發器當骨幹（Sakana Fugu、TypeSafe Jev、Laya、NeMo Switchyard） | 不採用當骨幹。Fugu 的模型池全是雲端模型，不能用本機 Qwen，判斷過程看不到；Jev 閉源、要排隊申請；Laya 英文版一次只讀 512 token。之後可以接在閘道後面做 A/B 實驗 |
| **固定規則：配方的每段宣告等級，失敗才升級** | **採用**。每一步該用什麼本來就清楚；好查、好重現 |

**閘道：**

| 選項 | 結果 |
| :--- | :--- |
| LiteLLM proxy | 不採用。依標籤統計花費在它的程式裡標為企業版功能；花費紀錄要另外架 PostgreSQL |
| **自己寫薄閘道** | **採用**。只有兩個後端，而且 Ollama 從 v0.14 起支援 Anthropic Messages 格式，閘道等於「轉送＋記帳」 |

## 3. 決策

1. **三個等級，Pi 只認別名**：`local` → Ollama Qwen3.5 9B；`strong` → Claude Sonnet 5.5；`max` → Claude Opus 5.5。對照表在閘道設定檔，換模型只改這裡。
2. **預設分工：**
   - 整理（digest）、補料（research）、檢查（verify）用 `local`。
   - 二創（compose）用 `strong`。
   - `max` 只在升級時出現。
   - 實際分配寫在配方，S1 評估後再調。
3. **升級規則：**
   - 某段產出連續不合格達 `max_attempts` 次，且配方允許（`escalate_to`），才升一級，並記在帳本（`escalated_from`）。
   - **基礎設施故障不升級**：Ollama 沒開、網路斷，就停下並標 `failed`，不偷偷改用付費模型。
4. **閘道是唯一會花錢、會對外連線的地方。** 模型呼叫、搜尋、抓網頁、下載圖片都經過它，所以以下規則只做一次：
   - **預算先預留後結算**：每張工作單有美元與 Tavily credits 上限，不夠就拒絕，單子進待審並標 `incomplete`。
   - **快取**：抓取與搜尋跨工作單共用、有有效期限。模型請求與回應依 hash 存起來，同一段同一次嘗試內送出相同請求就直接回放，不重付。
   - **重播模式**：只回放存好的回應，給自動化測試與評估用，花費為零。
   - **本機 GPU 一次只服務一個請求**；雲端 429 退避重試。
5. **帳本**：每次呼叫寫一筆到該工作單的 `ledger.jsonl`，內容包括：段、等級、實際模型、token（含快取讀寫）、credits、金額、GPU 秒數、第一個字的時間、總延遲、排隊時間、狀態、請求與回應的 hash。帳單（`bill.md`、每月總表）都由帳本產生，不另外記。
6. **工作單身分**：Pi 用 `--api-key "job:<id>:<段>:<次>"` 呼叫閘道，閘道據此分帳；真正的 Anthropic 金鑰只在閘道。

## 4. 後果

- **正面：**
  - 換模型、換服務商不動 Pi 與 skill。
  - 預算、快取、帳本與 agent 框架無關，之後換核心也照樣有效。
  - 帳本同時是成本報表、延遲報表與評估的原始資料。
- **負面：**
  - 自己維護價目表與串流轉送（要解析串流裡的用量事件）。
  - 只支援 Anthropic Messages 格式的後端；要接其他格式時，在閘道加轉換。
  - 固定規則不會自己變聰明；要靠 S1 的評估資料手動調整配方。
- **影響範圍：** `src/evidence_forge/gateway/`、配方的 `tier`／`escalate_to` 欄位、PRD 的帳單、預算與評估需求。
- **重新評估觸發：** S1 評估顯示 `local` 在某段的品質明顯不足；或要接第三種模型格式；或想讓學習型派發器實際接手某段的派發。

## 5. 參考

- Ollama Anthropic 相容 API：<https://docs.ollama.com/api/anthropic-compatibility>
- LiteLLM 花費追蹤：<https://docs.litellm.ai/docs/proxy/cost_tracking>（2026-10-10 讀取）
- Thoughtworks 對 Sakana Fugu 的評論：<https://www.thoughtworks.com/insights/blog/generative-ai/sakana-ai-fugu-is-this-where-model-routing-should-live>
- TypeSafe Jev：<https://www.datacamp.com/blog/system-one-models-jev>；Laya：<https://github.com/NandhaKishorM/laya>
- Claude 定價：<https://platform.claude.com/docs/en/about-claude/pricing>
- 架構草案 v0.3 §4：`30-snapshot/plans/2026-10-10-architecture-draft-v0.3.md`
