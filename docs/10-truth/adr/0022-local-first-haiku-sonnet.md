# ADR-0022：本機優先，雲端只用 Haiku 與 Sonnet，失敗一級一級往上

> **狀態：** 已接受 | **日期：** 2026-10-13 | **決策者：** 專案擁有者
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **修正：** ADR-0011 §3.1（等級多一級 `fast`）、§3.2（預設分工）、§3.3（升級從「升一級」變成「照升級鏈一級一級往上」）。
> ADR-0019 的流程不變，只改每一段先用哪個模型。

## 1. 背景與問題

- **上下文：** 每月雲端上限 US$10，10 月到 2026-10-13 已經花了約 US$9.5。照 PRD v3.0（NFR-COST-07）的分工，查證、重構、對話都直接用 `strong`（Claude Sonnet 5.5）；錄音裡的短文，查證一次約 US$0.07、重構一次約 US$0.07，真的文章更長、更貴，剩下的額度跑不了幾次。
- **創作者的話（2026-10-13）：** 「能用地端模型就用地端，先用 LOCAL；雲端模型只用 Haiku 和 Sonnet。」
- **問題：**
  1. 每一段先用什麼、失敗之後換什麼？
  2. Opus（`max`）還要不要？
  3. 本機模型的上下文比雲端小很多，輸入塞不下時怎麼辦？
- **為什麼要寫成 ADR：** 這改了花錢的結構與每一段的預設模型；要改回來，配方、閘道對照表、價目表、PRD 都要動。

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 維持現狀，查證、重構直接用 Sonnet | 不採用。剩下的額度撐不到月底 |
| 每段只用本機，不升級 | 不採用。9B 模型做查證與長文重構常常交不出合格的產出，工作就卡住 |
| 本機失敗直接升 Sonnet | 不採用。Haiku 5.5 的價錢是 Sonnet 的二十分之一，很多本機做不好的工作 Haiku 就做得好 |
| **本機 → Haiku → Sonnet，一級各試一次** | **採用**。先試免費的，再試很便宜的，最後才用貴的 |

## 3. 決策

1. **多一個等級 `fast`，對到 Claude Haiku 5.5。** 由低到高是 `local` < `fast` < `strong` < `max`。
   - `fast`：`claude-haiku-5-5`，`max_tokens: 16000`，思考用 adaptive（Haiku 5.5 只有 adaptive，`budget_tokens`、非預設的 temperature／top_p／top_k、assistant prefill 都會被拒；Pi 的請求本來就不帶這些）。
   - 價錢：提示 100K token 以下每百萬 token 輸入 $0.10、輸出 $0.50；超過 100K，整個請求改成 $0.50／$2.50。提示 = 輸入＋快取寫入＋快取讀取。快取寫入照慣例是輸入價的 1.25 倍、讀取 0.1 倍。預留金額照估的輸入決定用哪一段價；估值偏高，所以有可能超過門檻就用長提示價。
2. **配方只用 `local`、`fast`、`strong`。** `max`（Opus 5.5）留在對照表與價目表，只為了舊工單的帳本與狀態還讀得進來，或手動指定；配方不再寫它。
3. **升級鏈。** `escalate_to` 可以寫一級（舊寫法，照樣能用）或一串，一串要一級比一級高，而且都比這一段的等級高。執行器的規則：
   - 這一段的等級試滿 `max_attempts` 次；
   - 還不合格，照升級鏈的順序一級各試一次；
   - 鏈的最後一級也不合格，這一段才算失敗。
   - 程式檢查沒過「回去修」（`auto_fix`）用的是當下那一級，不另外升級。
   - 帳本的 `escalated_from` 記這一段在配方上的等級（例：`fast` 那次與 `strong` 那次都記 `local`）。
   - 基礎設施故障照舊不升級（ADR-0011 §3.3）。
4. **分析與重構的每一段都是 `tier: local, escalate_to: [fast, strong]`。** 查證段每一次最多搜 6 次，四次嘗試（本機兩次、`fast`、`strong` 各一次），搜尋額度從 12 提高到 24。
5. **本機模型的上下文放不下就算這一次失敗，不讓它被默默截掉。** Ollama 遇到超過上下文的提示會丟掉前面、只讀後半段，產出看起來正常但內容是缺的。
   - 閘道對照表的 `local` 寫 `context_window: 32768`，要跟 Ollama 容器的 `OLLAMA_CONTEXT_LENGTH` 一樣。
   - 閘道轉送前估提示有多長（位元組數 ÷ 3；錄音裡 Qwen 平均 3.6 個位元組一個 token，所以這樣估偏多），加上 `max_tokens` 超過上下文就不送，回請求錯誤。
   - 這不是後端故障：執行器當成這一次不合格，照升級鏈往上。
6. **對話的 Pi 預設用 `fast`。** 對話主機（PR #94）原本預設 `strong`，這份 ADR 合併之後改成 `fast`。

## 4. 後果

- **正面：**
  - 大部分工作在本機做完不花錢；本機做不好時多半 Haiku 就夠，用到 Sonnet 的次數少很多。
  - 舊配方、舊工單、舊帳本都照樣讀得進來。
- **負面：**
  - 本機失敗的那幾次要花時間（查證段本機可能跑兩次才升級），一次分析等得比較久。
  - 本機的上下文要靠人把 Ollama 容器設對；容器開得比對照表小，閘道就擋不住被截掉的提示。
  - 估提示長度是粗估：估多了會白白升級（多花一點錢），估少的情況目前沒看過。
  - 錄音裡的請求帶著等級別名，現有的錄音是照舊等級錄的；重播測試先用錄音當時的配方等級，之後要照新配方重錄。
- **影響範圍：** `src/evidence_forge/models.py`（`Tier`、`RecipeStage.escalate_to`）、`gateway/models.yaml`、`gateway/prices.yaml`、`gateway/prices.py`、`gateway/aliases.py`、`gateway/messages.py`、`runner/runner.py`、`runner/preflight.py`、`harness/pi/agent/models.json`、`harness/recipes/analyze.md`、`harness/recipes/rewrite.md`、PRD 的 FR-MD-01 與 NFR-COST-07。
