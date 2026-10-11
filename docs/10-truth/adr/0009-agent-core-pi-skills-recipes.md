# ADR-0009：agent 核心用 Pi，流程寫成配方與 skill

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者（與架構 session 討論後拍板）
> **後續修正：** §3 第 3 點載入 skill 的做法由 ADR-0018 修正——改用 `--no-skills --append-system-prompt <SKILL.md>`；agent 讀不到 `SKILL.md` 以外的附帶檔，要由執行器放進工單資料夾。
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **取代 ADR-0001**（LangGraph 確定性節點圖）。**修正 ADR-0002** 的「節點」概念：模型分工改由 ADR-0011 決定。

## 1. 背景與問題

- **上下文：** ADR-0001 把流程做成 LangGraph 的固定節點圖，人工停點用 `interrupt()`，狀態靠 checkpointer。創作者實際用 CLI 一條一條指令操作過類似的設計，覺得被流程綁死：每多一種做法就要改程式，中間又處處要停下等人。
- **創作者要的是：** 像 Claude Code 那樣，把流程寫成 skill 技能包（Markdown），系統讀了 metadata 就知道怎麼驅動；給目標之後中間全自動，只審結果。
- **為什麼要寫成 ADR：** agent 核心是整個系統的骨架，換掉它要重寫執行方式、工具、測試。

## 2. 考量的選項

| 選項 | 優點 | 缺點 | 結果 |
| :--- | :--- | :--- | :--- |
| 維持 LangGraph 固定圖 | 每步可預測 | 流程寫死在程式；停點機制（interrupt、checkpoint）是為「中間等人」設計，新需求用不到 | 不採用 |
| Claude Agent SDK | Python；技能、子 agent、hook、預算上限都內建 | 底下是 Claude Code，系統提示與工具定義很大、為 Claude 設計；主力換成本機 9B 時浪費上下文、容易亂叫工具 | 不採用（寫稿段也不另外接，避免兩套核心） |
| 自己寫 Python agent 迴圈 | 完全可控、同語言 | 要自己做串流、多服務商、skill 載入、事件輸出 | 備案 |
| **Pi** | 預設系統提示很小且可整份替換；支援 15 家以上服務商含 Ollama；原生讀 Agent Skills 格式；有非互動與 JSON 事件模式；能關掉全部內建工具、只用自訂工具 | TypeScript；本身不做沙箱；預算與權限要自己補 | **採用** |

## 3. 決策

1. **agent 核心用 Pi。** 整個系統只有這一個核心，寫稿段也由 Pi 經閘道呼叫 Claude API。
2. **流程寫成配方（recipe）。** 一份 Markdown，frontmatter 列出有哪幾段、每段用哪個 skill、哪一級模型、產出哪些檔、退件時從哪段重跑。要新的產出方式就新增一份配方，不改程式。
3. **每段一個 Pi 程序。** 執行器照配方逐段呼叫 `pi --print --mode json`，只載入這段的 skill（`--no-skills --skill …`），系統提示用我們自己的（`--system-prompt`），上下文保持短。
4. **關掉 Pi 的全部內建工具**（`--no-builtin-tools`），工具只由我們的薄擴充 `ef-tools` 提供；每個工具把請求轉給閘道，規則（路徑限制、只增不改、預算、快取）都在閘道執行（ADR-0010、ADR-0011）。
5. **skill 照 Agent Skills 標準寫**（`SKILL.md`＋可選的 `scripts/`、`templates/`、`schema.json`）。之後要換核心，skill 可以直接帶走。
6. 每段的產出由執行器用該 skill 的 `schema.json` 驗證；不合格就重試或升級，不靠 agent 自己說「做完了」。

## 4. 後果

- **正面：**
  - 流程改成改文件；加一種產出不用動程式。
  - 系統提示、工具、模型完全由我們決定，可控性比內建大量行為的框架高。
  - 段與段之間只透過檔案交接，可以單獨重跑或評估任何一段。
- **負面：**
  - 多一種語言（TypeScript）。範圍限制在一個轉送用的擴充，邏輯全放 Python。Node 用 nvm 安裝，不碰系統套件。
  - Pi 不做沙箱，安全完全靠「關掉內建工具＋閘道檢查路徑」；這一層要有測試。
  - agent 每次走的路不一定相同，可重現性改由「錄下所有回應、可重播」來保證（ADR-0011）。
- **影響範圍：** `harness/`（配方、skill、Pi 設定與擴充）、`src/evidence_forge/runner/`、PRD 的流程與功能需求。
- **重新評估觸發：** Pi 的非互動介面或擴充 API 出現不相容的大改；或 S0 實測顯示 Pi 的事件輸出不足以追蹤進度。

## 5. 參考

- Pi CLI 參數（`--print`、`--mode json`、`--system-prompt`、`--skill`、`--no-builtin-tools`、`-e`）：<https://pi.dev/docs/latest/cli>（2026-10-10 讀取）
- Claude Agent SDK skills：<https://code.claude.com/docs/en/agent-sdk/skills>
- 架構草案 v0.3 §3：`30-snapshot/plans/2026-10-10-architecture-draft-v0.3.md`
