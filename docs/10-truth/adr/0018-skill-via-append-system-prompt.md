# ADR-0018：skill 用 `--append-system-prompt` 放進系統提示，不用 Pi 的 `--skill`

> **狀態：** 已接受 | **日期：** 2026-10-11 | **決策者：** 專案擁有者（S0.3 實作時發現，T3 審查要求寫成 ADR）
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **修正 ADR-0009**：§3 第 3 點載入 skill 的做法（原本寫 `--no-skills --skill …`）。ADR-0009 的原意不變：每段只載入這段的 skill，系統提示用我們自己的。

## 1. 背景與問題

- **上下文：** ADR-0009 要執行器每段用 `--no-skills --skill <skill 資料夾>` 載入這段的 skill，同時用 `--system-prompt` 換掉 Pi 的系統提示，並用 `--no-builtin-tools` 關掉 Pi 全部的內建工具。
- **S0.3 實測發現三件事湊在一起時 skill 進不去。** 用 Pi `1.1.0` 實測，`--skill` 不會把 skill 的任何內容帶進送給模型的請求，連名稱和描述都沒有。原因推測是 Pi 的 skill 機制要靠兩樣東西，而我們兩樣都關掉了：
  - 預設的系統提示（裡面會列出有哪些 skill）。
  - 內建的讀檔工具（agent 要用它去讀 `SKILL.md`）。
- 結果是模型完全不知道這段要做什麼，只看得到使用者訊息。
- **為什麼要寫成 ADR：** 程式碼的做法跟 ADR-0009 寫的不一樣。照 `docs/README.md` 的規則，已接受的 ADR 不改內文，要寫一份新的蓋過去。

### 重現方式

在 `harness/pi/` 跑過 `npm ci` 之後：

1. 在 `127.0.0.1` 起一個只收請求的小伺服器：收到 POST 就把本文存檔，回 500。
2. 把 `harness/pi/agent/models.json` 複製到一個空資料夾，`baseUrl` 改成那個伺服器。
3. 用跟 `runner/pi_launch.py` 相同的旗標跑 Pi：
   - `PI_CODING_AGENT_DIR` 與 `HOME` 都指到那個資料夾。
   - `--system-prompt harness/pi/system-prompt.md --no-builtin-tools`。
   - 分別加上下表三種載入方式。
4. 看第一個請求的 `system` 與 `messages` 有沒有 `harness/skills/echo/SKILL.md` 的內容。

2026-10-11 實測結果：

| 載入方式 | `SKILL.md` 內容進了請求嗎 | `system` 長度（JSON 字元數） |
| :--- | :--- | :--- |
| `--no-skills --skill harness/skills/echo` | 沒有，連 `echo` 這個名字都沒有 | 906 |
| `--skill harness/skills/echo`（不加 `--no-skills`） | 沒有 | 906 |
| `--no-skills --append-system-prompt harness/skills/echo/SKILL.md` | 有，整份接在系統提示後面 | 1590 |

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 照 ADR-0009 用 `--skill` | 不採用：實測模型拿不到 skill |
| 不換系統提示，讓 Pi 自己列出 skill | 不採用：Pi 預設的系統提示是寫給有內建工具的 agent 用的；`--system-prompt` 是 ADR-0009 的決策 |
| 打開 Pi 內建的 `read`，讓 agent 自己去讀 `SKILL.md` | 不採用：Pi 沒有沙箱，內建工具會用 Pi 自己的權限讀整台機器。安全邊界靠的就是「沒有內建工具，所有路徑都由閘道判斷」（ADR-0009 §3 第 4 點） |
| 在 ef-tools 加一個讀 skill 的工具 | 不採用：多一個工具就多一條要守的路徑；而且 agent 每段第一件事都是讀 skill，不如直接放進提示 |
| **`--no-skills` 加上 `--append-system-prompt <這段的 SKILL.md>`** | **採用** |

## 3. 決策

1. **每段只把這段的 `SKILL.md` 放進系統提示**，用 `--append-system-prompt <SKILL.md 的路徑>`，接在我們自己的系統提示（`--system-prompt`）後面。
2. **`--no-skills` 照舊**，讓 Pi 不會自己去找其他 skill。
3. ADR-0009 的其他決定不變：
   - 每段一個 Pi 程序。
   - 系統提示用我們自己的。
   - 關掉全部內建工具，工具只有 ef-tools。
   - skill 照 Agent Skills 標準寫，產出由執行器用 `schema.json` 驗證。
4. 這組旗標由 `runner/pi_launch.py` 組出來，單元測試鎖住。

## 4. 後果

- **正面：**
  - 模型確定拿得到這段的 skill，不用靠 agent 自己去讀。
  - 少一次工具呼叫，本機 9B 模型也少一個走錯的機會。
  - 安全邊界不變：沒有新工具，也沒有打開內建工具。
- **負面（最重要的一點）：agent 只看得到 `SKILL.md` 這一份。**
  - `harness/skills/<名稱>/` 底下 `SKILL.md` 以外的檔（`templates/`、`scripts/`、範例），agent 讀不到。ef-tools 的 `read_file` 只能讀工單資料夾，讀不到 `harness/`。
  - ADR-0015 的文類骨架（`harness/genres/`）、轉換說明（`harness/conversions/`）、作家設定（`$EF_DATA/authors/`）也一樣讀不到。
  - 這些檔要由**執行器**放進工單資料夾（例如 `input/`），或由執行器組進提示。ADR-0015 §3 第 7 點本來就要求執行器把文類定義與作家設定複製進 `input/`，這份 ADR 把同一條規則延伸到 skill 的附帶檔。
  - `schema.json` 不受影響：它是給執行器驗證用的，agent 不用讀。
  - skill 作者要知道：`SKILL.md` 裡寫「去讀 `templates/x.md`」是沒用的，要寫成去讀工單資料夾裡的路徑。
  - Agent Skills 標準裡「需要時才讀附帶檔」的做法用不到；附帶檔全部事先放好，會多佔一點上下文。
- **影響範圍：** `src/evidence_forge/runner/pi_launch.py`、之後所有 skill 的寫法、#25（附帶檔要由執行器放進工單資料夾）、ADR-0009 頂部加一行修正說明。
- **重新評估觸發：**
  - Pi 升版（`harness/pi/package.json` 的版本改變），要重跑上面的重現方式。
  - 改成打開 Pi 的內建工具，或改用 Pi 自己的系統提示。

## 5. 參考

- ADR-0009（agent 核心用 Pi）§3 第 3、4 點；ADR-0015（文類 × 作家）§3 第 7 點
- Pi CLI 參數（`--skill`、`--no-skills`、`--append-system-prompt`）：<https://pi.dev/docs/latest/cli>
- 實作：`src/evidence_forge/runner/pi_launch.py`；測試：`tests/unit/test_pi_launch.py`
- PR #44 的 T3 審查第 1 輪（必修 1）
