# 測試藍圖

> 由 /test-blueprint 產生與修訂。手改條目視為藍圖失真 — 要改，走修訂提案。
> 本次核准：首次產生，2026-10-11；創作者合併這份文件的 PR 即核准／來源：規格 #18（S0 實驗：核心迴路。依 2026-10-11 的兩次修訂：架構審查〔硬檢查由執行器直接跑、C4 最小版、`write_file` 只寫這一段這一次、月上限在閘道〕與缺口回覆〔投入先抓再開單、門檻與期限的預設值、重啟沿用嘗試編號、配方格式不合就拒絕、測試用的故障注入、同目標怎麼比、投入抓取的帳本、平均成本的算法〕）、`requirements/prd.md` v2.0、`requirements/features/` 的 `intake`、`digest`、`compose`、`review`、`runner`、`ledger`、S0 開發排程計畫（`30-snapshot/plans/2026-10-11-s0-development-plan.md`）、S0 UI 資訊架構（`30-snapshot/plans/design/2026-10-11-s0-ui-ia.md`）、架構草案 v0.3

這份文件回答三件事：S0 答應的每一件事（#18 的 39 條承諾）由哪一層測試證明、模組之間哪些邊界（縫）最容易出錯所以要測、CI 什麼時候跑哪一層。它是計畫；實際跑出來的紅綠由 S0.13 之後 CI 產生的 `docs/test-status.md` 記錄，兩份不合併。

追溯表「測試」欄寫「未寫」的，代表還沒有任何測試宣告那條承諾；每一條測試是否存在，以 `docs/test-status.md` 為準，不回頭改這裡。

## 層佈局

| 層 | 在這個專案測什麼 | 誰落地 | CI 時段 |
| --- | --- | --- | --- |
| 靜態 | 型別（`mypy --strict`）、lint（`ruff`）——`ci.yml` 已在跑。S0.13 再加：承諾標記的編號必須出現在本藍圖的追溯表、測試不准把閘道切到真的呼叫模式、repo 不准出現金鑰樣式字串、每類檔只有一個寫入者（見〈交給 S0.13（#30）〉） | `ci.yml` 現有兩步；其餘交給 S0.13（#30） | presubmit |
| 單元 | 不碰行程、不碰網路的純 Python：資料夾契約的 Pydantic 模型與路徑解析、只增不改的寫檔工具、配方 frontmatter 解析、C1～C5 各自的判斷、預算預留與結算的算術、帳本一筆的欄位、帳單彙總、`pi_launch` 組出的指令、內網位址判斷、決定檔的挑選規則、工單狀態轉換、快取有效期限（注入時鐘） | 各實作票用 /tdd | presubmit |
| 整合 | 縫清單上的每一條邊界：真的檔案系統（pytest 的 `tmp_path`，在 WSL 原生的 `/tmp`）、真的本機 HTTP（只准 `127.0.0.1`）、假的 Pi（一支照 Pi 的 JSON 事件格式輸出、並真的打閘道的小程式）、假的 Ollama／Anthropic（本機 stub，能回串流、429、逾時、連不上） | 各實作票用 /tdd | presubmit（全套，見〈CI 政策〉偏離 2） |
| 整合：重播整條流程 | 真的 Pi＋`ef-tools`＋閘道重播模式，固定小案例從投入跑到待審（NFR-RUN-07）。用 `@pytest.mark.pipeline` 標，CI 拆成獨立 job，因為要裝 Node 與鎖版的 Pi | S0.13 | presubmit（獨立 job，只在 PR 上跑） |
| 驗收 | `docs/10-truth/uat-cases.md` 全清單：真的模型、真的瀏覽器、創作者判定 | /uat-cases → 創作者在 S0.14 實跑（證據放 `docs/10-truth/uat/`） | exit-gate（S0 Exit Gate 前跑全清單） |

### 承諾標記：每個測試檔共用的接頭

測試用 pytest 的 marker 宣告它證明哪條承諾，編號照 PRD v2.0（`FR-*`、`NFR-*`），一個測試可以宣告多條：

```python
import pytest

@pytest.mark.promise("NFR-COST-02", "NFR-RUN-04")
def test_重啟後前幾次呼叫回放不收費(tmp_path: Path) -> None:
    ...
```

- **層由目錄決定，不另外標**：`tests/unit/` 是單元、`tests/integration/` 是整合；重播整條流程放 `tests/integration/pipeline/`，再加 `@pytest.mark.pipeline`。
- **進報告的方式**：`tests/conftest.py` 用一個 autouse fixture 讀 `request.node.iter_markers("promise")`，對每個編號呼叫 `record_property("promise", "<ID>")`。`pytest --junitxml=...` 的報告裡每個 testcase 就帶著 `<property name="promise" value="FR-IN-01"/>`，S0.13 的現況產生器只讀這個。
- **註冊**：`pyproject.toml` 的 `[tool.pytest.ini_options]` 加 `markers = ["promise(*ids): 這個測試證明哪幾條承諾", "pipeline: 真的 Pi＋重播的整條流程"]` 與 `--strict-markers`。由**第一張寫測試的票（S0.2）**一起加，因為它的第一個測試就要用。
- **零付費呼叫的保證**（NFR-COST-06）：同一個 `conftest.py` 用 autouse fixture 把 `EF_GATEWAY_MODE` 設成 `replay`，並擋掉所有不是 `127.0.0.1` 的連線。第一張會發出模型請求的票（S0.3）加上這兩條。
- **故障注入**：閘道讀環境變數 `EF_GATEWAY_FAULT`（`rate_limit`、`bad_output`、`backend_down`），只在測試與 UAT 用，重播模式下也有效；設了這個變數時開跑前檢查拒絕正式執行（#18〈測試用的故障注入〉）。整合測試優先用它，而不是在每個測試裡各自造一個故障 stub。
- **驗收層不用這個標記**：驗收案例的「來源」欄就是它證明的承諾，產生器讀 `uat-cases.md` 的「最近判定」。

## 縫清單

/tdd 讀這張表：實作票碰到哪條縫，就在那條縫上寫整合測試。現況全部是「未測」，因為還沒有程式。

| 縫 | 服務的承諾 | 怎麼測（測試替身） | 落地票 | 現況 |
| --- | --- | --- | --- | --- |
| SEAM-01 執行器 ↔ Pi 程序：`runner/pi_launch.py` 組的指令（`-nc`、`--no-builtin-tools`、`PI_CODING_AGENT_DIR`、stdin 接 `/dev/null`、明確的 thinking、`--api-key "job:…"`）、JSON 事件解析、結束碼 | NFR-SEC-02、NFR-SEC-04、NFR-OBS-02、NFR-RUN-03、NFR-RUN-04、FR-RC-01 | 單元：斷言組出的 argv 與 env（少一個旗標就紅）。整合：假 Pi 輸出事件與各種結束碼；真 Pi 只在 pipeline job | S0.3、S0.6 | 未測 |
| SEAM-02 `ef-tools` ↔ 閘道工具端點 `/tools/<名稱>`：`read_file`、`list_files`、`fetch_url`、`run_script`（`write_file` 的寫入範圍另列 SEAM-12） | NFR-SEC-02、NFR-SEC-03、NFR-RUN-08、NFR-COST-03、FR-LG-01、NFR-KEEP-01 | 整合：直接打閘道端點；讀取時 `../another-job/`、符號連結、絕對路徑都要被擋；`run_script` 只准清單上的腳本；`fetch_url` 打本機 stub 回 403、付費牆頁、robots 禁止、429；抓取快取的有效期限是設定值（預設 7 天），用注入的時鐘斷言期限內不重抓、過期才重抓 | S0.3、S0.5 | 未測 |
| SEAM-03 閘道 ↔ 模型後端（Ollama、Anthropic）：別名對照 `models.yaml`、`/v1/messages?beta=true`、串流、改寫 `max_tokens` 與 thinking、429 退避、GPU 旗號、連不上不轉雲端 | FR-MD-01、NFR-PAR-02、NFR-PAR-03、NFR-RUN-05、NFR-COST-07、NFR-SEC-04、NFR-RUN-08 | 整合：兩個本機 stub 扮演 Ollama 與 Anthropic；同時送兩個 `local` 請求，stub 端最多看到 1 個；`EF_GATEWAY_FAULT=rate_limit` 時退避並把等待記在帳本；`EF_GATEWAY_FAULT=backend_down` 或 stub 關掉時標失敗，斷言 Anthropic stub 收到 0 個請求 | S0.3、S0.4 | 未測 |
| SEAM-04 閘道內：預算（每張單＋每月上限）↔ 帳本 ↔ 快取與重播（`blobs/`） | NFR-COST-01、NFR-COST-02、NFR-COST-06、FR-LG-01、NFR-OBS-01、NFR-KEEP-01、NFR-KEEP-02 | 單元：預留、結算、退回的算術；月上限跨工單加總，月份依 Asia/Taipei 的日曆月切（測 UTC 已跨月、台北還沒跨月的那幾個小時）。整合：工單預算不夠回 `budget_exceeded`、結果「未完成：預算用完」；本月累計加上這次預留超過月上限（設定值，預設 US$10）回 `budget_exceeded`、結果「未完成：本月額度用完」；兩種都斷言 stub 沒收到請求；同段同次的相同請求記 `cache_hit`、stub 沒收到；重播模式遇到沒錄過的請求報錯、不轉送 | S0.4 | 未測 |
| SEAM-05 執行器 ↔ 配方與 skill（`harness/recipes/*.md` frontmatter、`schema.json`、`max_attempts`、`escalate_to`） | FR-RC-01、NFR-RUN-03、FR-MD-01、NFR-RUN-06、NFR-COST-07 | 整合：測試用的配方放在 fixture 目錄，新增一份不改程式就能跑；配方缺欄位、skill 不存在、等級不在對照表時，`ef serve` 啟動與開單都拒絕，訊息點名哪一份、哪一欄；`EF_GATEWAY_FAULT=bad_output`（或假 Pi）連續產出不合 schema，斷言重試次數與升級後的等級 | S0.3、S0.6、S0.8 | 未測 |
| SEAM-06 執行器 ↔ 自動檢查 C1～C5：執行器在段與段之間直接呼叫程式，整理段後跑 C1、C5，二創段後跑 C2～C5；`verify` skill 只做 C6、C7，碰不到硬檢查 | FR-VF-01、FR-DG-01、FR-CP-01 | 單元：C1～C5 各自的過與不過（捏造引句、對不到來源的引用編號、超過門檻的重疊、稿子裡有不在 `assets.json` 的圖、不合 schema）。整合：用假 Pi 產出，斷言整理段後 C1 或 C5 沒過算該段這次失敗，走重試與升級，用完仍沒過是 `failed`；二創段後 C2～C5 沒過回二創段修，到 `auto_fix` 上限是 `incomplete` 並列出沒過的檢查；斷言硬檢查的結果不是從任何 Pi 程序的產出讀來的 | S0.7、S0.6、S0.8 | 未測 |
| SEAM-07 執行器 ↔ 審稿決定檔 `review/decision.vN.json` | FR-RV-01、FR-RV-02、NFR-COST-04、NFR-KEEP-02、NFR-RUN-02 | 整合：獨佔建立、撞號換下一號；`draft_version` 對不上的標「已失效」；兩份同版本的決定只套用第一份；退件後上游段的帳本沒有新的 `llm` 筆數 | S0.9、S0.12 | 未測 |
| SEAM-08 UI ↔ 資料夾：只讀 `job.md`、`events.jsonl`、`ledger.jsonl`、`bill.md`、稿子；只寫決定檔與 `inbox/manual/` | FR-UI-01、NFR-OBS-02、NFR-KEEP-02、FR-LG-02、FR-RV-02、FR-DG-02、FR-CP-02、FR-RV-01 | 整合：FastAPI `TestClient` 對著 fixture 資料夾渲染四頁；送出審稿與新增後，比對資料夾前後只多了這兩類檔；成品區的 Windows 路徑用 `wslpath -w` 的規則換算（CI 上沒有 `wslpath`，測換算規則本身） | S0.11、S0.12 | 未測 |
| SEAM-09 投入 ↔ inbox：網頁表單、`ef new --text`、上傳 MD／純文字／PDF → 先抓取與抽文字 → `inbox/manual/<項目>/item.json`、內容 hash → 開單 | FR-IN-01、NFR-RUN-08、NFR-SEC-03、NFR-COST-03、FR-CP-03 | 整合：三種投入各開一張排隊中的單；同一個內容 hash 只建一份素材項目，重複投入沿用；同素材＋同目標已有未丟棄的工單時回傳那張、不開新單（比的是實際生效的目標：空白就換成配方預設目標，去掉前後空白後逐字比；只差中間一個字就算不同）；目標不同開新單；那張已丟棄就開新單；網址 403、付費牆、robots、內網位址時不開單，素材項目標讀不到並寫原因；投入時的每一次抓取（讀得到與讀不到都算）由閘道記在 `$EF_DATA/ledger/intake.jsonl`，不寫進任何工單的帳本 | S0.5、S0.12 | 未測 |
| SEAM-10 執行器 ↔ 環境與閘道狀態：開跑前檢查（Ollama 與模型、閘道、Anthropic 金鑰、硬碟、`EF_DATA` 可寫、正式執行時 `EF_GATEWAY_FAULT` 沒設）、閘道的等待狀態（等 GPU、等限流、等模型回應、執行工具；閘道在呼叫進行中就知道，帳本是呼叫結束才寫）合進 `job.md` 進度欄位、同時張數上限、重啟恢復 | NFR-RUN-01、NFR-RUN-04、NFR-RUN-05、NFR-PAR-01、NFR-OBS-02、NFR-COST-02 | 整合：每一項檢查各自弄壞一次，斷言不開跑且訊息點名那一項；跑到一半殺掉 `ef serve` 再起，斷言從同一段、同一個嘗試編號重跑 | S0.6 | 未測 |
| SEAM-11 帳本 ↔ 帳單：各單的 `ledger.jsonl` 與 `ledger/intake.jsonl`（閘道寫）→ `bill.md` 與每月總表（帳單產生器讀） | FR-LG-02、NFR-OBS-01 | 單元：固定的帳本 fixture 算出固定的數字（含退件重跑的花費、本機金額 0、跨月依 Asia/Taipei）；每篇通過文章的平均成本＝本月全部花費（通過、丟棄、失敗、待審中的單，加上 `intake.jsonl`）÷ 本月通過篇數，fixture 裡要有丟棄與失敗的單與投入抓取，斷言它們都進了分子；本月沒有通過的單時不除以零；帳本有一行壞掉時報行號、其餘照算 | S0.10 | 未測 |
| SEAM-12 agent 的 `write_file` ↔ 工單資料夾的寫入範圍：只准寫 `stages/<目前的段>/<目前的嘗試>/`，已存在就拒寫 | NFR-SEC-02、NFR-KEEP-01、NFR-KEEP-02 | 整合：用 `job:<id>:<段>:<次>` 的鑰匙直接打 `/tools/write_file`，寫 `review/decision.v1.json`、`job.md`、`events.jsonl`、`ledger.jsonl`、別段的目錄、同段較早那次嘗試的目錄、`../another-job/`、符號連結，全部被拒且檔案不出現；寫本段本次目錄成功；同名第二次寫被拒 | S0.3 | 未測 |

## 追溯表

#18 的 39 條承諾，一條一列。工作包對到的 issue：S0.2＝#19、S0.3＝#20、S0.4＝#22、S0.5＝#23、S0.6＝#24、S0.7＝#21、S0.8＝#25、S0.9＝#26、S0.10＝#27、S0.11＝#28、S0.12＝#29、S0.13＝#30、S0.14＝#31。「落地票」照 S0 開發排程計畫 §4 的需求欄；落地票合併之後，該列才進未實作門（見〈未實作豁免清單〉）。「測試」欄現在都是「未寫」；驗收案例編號見 `uat-cases.md`。

| 承諾 | 標題 | 層 | 時段 | 落地票 | 測試／案例 |
| --- | --- | --- | --- | --- | --- |
| FR-IN-01 | 先抓再開單；同 hash 一份素材；同素材＋同目標（實際生效的目標，去前後空白後逐字相同）回傳既有工單；讀不到不繞過、不開單 | 單元＋整合（SEAM-09）＋驗收 | presubmit／exit-gate | S0.5、S0.12 | 未寫／TC-INTAKE-01、02、03、04、05、06、07、08、09、10、11、12、14 |
| FR-DG-01 | 整理出主張（逐字引句＋位置、轉述者）、觀點、結構、素材、研究方向 | 單元（schema）＋整合（SEAM-06、pipeline）＋驗收 | presubmit／exit-gate | S0.8 | 未寫／TC-DIGEST-01、02、TC-COMPOSE-01、TC-CHECK-01 |
| FR-DG-02 | 先判主文類與信心（0～1），低於門檻（預設 0.6）時待審提醒 | 單元＋整合（SEAM-08）＋驗收 | presubmit／exit-gate | S0.8、S0.11 | 未寫／TC-DIGEST-03、04 |
| FR-CP-01 | 依目標、文類骨架、整理結果寫 Markdown，事實附引用編號 | 整合（SEAM-06、pipeline）＋驗收 | presubmit／exit-gate | S0.8 | 未寫／TC-COMPOSE-01、05 |
| FR-CP-02 | 二創一律載入作家聲音＋目標文類風格；沒有風格檔標「未校準」 | 單元（段輸入組裝）＋整合（SEAM-08）＋驗收 | presubmit／exit-gate | S0.8、S0.11 | 未寫／TC-COMPOSE-02、04 |
| FR-CP-03 | 目標文類可指定，沒指定用作家預設，記在 `job.md` | 單元＋整合（SEAM-09）＋驗收 | presubmit／exit-gate | S0.2、S0.8 | 未寫／TC-COMPOSE-02、03 |
| FR-VF-01 | C1～C5（C4 最小版）由執行器直接跑：整理段後 C1、C5 沒過走重試與升級，用完是 `failed`；二創段後 C2～C5 沒過回二創修，到上限是 `incomplete`；C6、C7 只提醒 | 單元＋整合（SEAM-06）＋驗收 | presubmit／exit-gate | S0.7、S0.6、S0.8 | 未寫／TC-COMPOSE-01、TC-CHECK-01、02、03、04 |
| FR-RV-01 | 通過、退件（意見＋重跑段）、丟棄、失敗重試；決定寫成檔案由執行器套用 | 單元＋整合（SEAM-07、08）＋驗收 | presubmit／exit-gate | S0.9、S0.12 | 未寫／TC-REVIEW-01、02、03、04、05、06、09 |
| FR-RV-02 | 每次都是新版本、舊版不覆寫；通過後可再改 | 單元＋整合（SEAM-07、08）＋驗收 | presubmit／exit-gate | S0.9、S0.12 | 未寫／TC-REVIEW-02、07、08 |
| NFR-COST-04 | 退件從選定段重跑，上游沿用不重付 | 整合（SEAM-07）＋驗收 | presubmit／exit-gate | S0.9 | 未寫／TC-REVIEW-02、04 |
| FR-RC-01 | 新增一份配方就能用，執行器不改程式；配方格式不合時啟動與開單都拒絕並點名 | 整合（SEAM-01、05）＋驗收 | presubmit／exit-gate | S0.3、S0.8 | 未寫／TC-RUNNER-07、11 |
| FR-MD-01 | `local`／`strong`／`max` 別名在閘道；連續不合格才升級；基礎設施故障不升級 | 單元＋整合（SEAM-03、05）＋驗收 | presubmit／exit-gate | S0.4、S0.6 | 未寫／TC-RUNNER-04、09、TC-GATEWAY-04 |
| FR-LG-01 | 每次模型、抓取、腳本呼叫在該單 `ledger.jsonl` 記一筆；還沒有工單的投入抓取記在 `$EF_DATA/ledger/intake.jsonl`；都只有閘道寫 | 單元＋整合（SEAM-02、04）＋驗收 | presubmit／exit-gate | S0.4、S0.5 | 未寫／TC-LEDGER-01、02 |
| FR-LG-02 | 單張 `bill.md`、每月總表（含訂閱源分項，S0 只有「手動」）、每篇通過文章的平均成本＝本月全部花費（含丟棄、失敗的單與投入抓取）÷ 本月通過篇數，全由帳本算 | 單元（SEAM-11）＋整合（SEAM-08、11）＋驗收 | presubmit／exit-gate | S0.10、S0.12 | 未寫／TC-BILL-01、02、03 |
| NFR-COST-01 | 雲端呼叫先預留；工單預算或本月額度（預設 US$10，Asia/Taipei 日曆月）不夠就拒絕，「未完成：預算用完／本月額度用完」 | 單元＋整合（SEAM-04）＋驗收 | presubmit／exit-gate | S0.4 | 未寫／TC-LEDGER-02、03 |
| NFR-COST-02 | 同段同次相同請求回放，當機重跑不重付 | 整合（SEAM-04、10）＋驗收 | presubmit／exit-gate | S0.4、S0.6 | 未寫／TC-RUNNER-03 |
| NFR-COST-03 | 同網址抓取跨工單共用快取，期限是設定值（預設 7 天） | 單元（注入時鐘）＋整合（SEAM-02、09）＋驗收 | presubmit／exit-gate | S0.5 | 未寫／TC-INTAKE-02、05、13 |
| NFR-COST-06 | 自動化測試一律重播，不打付費 API | 靜態（S0.13）＋整合（SEAM-04、pipeline）＋驗收 | presubmit／exit-gate | S0.4、S0.13 | 未寫／TC-GATEWAY-05 |
| NFR-COST-07 | 預設配方整理與檢查用 `local`，雲端只在 `strong` 段與升級 | 單元（讀 `restructure` 配方）＋整合（SEAM-03、05）＋驗收 | presubmit／exit-gate | S0.8 | 未寫／TC-GATEWAY-04 |
| NFR-RUN-01 | 開跑前檢查五項，不過就不開跑並點名 | 單元＋整合（SEAM-10）＋驗收 | presubmit／exit-gate | S0.6 | 未寫／TC-RUNNER-01、02、10 |
| NFR-RUN-02 | 五種狀態；待審附結果與原因 | 單元（狀態轉換）＋整合（SEAM-07）＋驗收 | presubmit／exit-gate | S0.2、S0.6 | 未寫／TC-REVIEW-06、TC-RUNNER-08、TC-UI-01 |
| NFR-RUN-03 | 每段產出用 schema 驗；不合格重試、到上限升級、仍失敗進待審 | 整合（SEAM-01、05）＋驗收 | presubmit／exit-gate | S0.6 | 未寫／TC-RUNNER-09 |
| NFR-RUN-04 | 重啟後執行中的單從所在段重跑，沿用同一次嘗試的編號 | 整合（SEAM-01、10）＋驗收 | presubmit／exit-gate | S0.6 | 未寫／TC-RUNNER-03 |
| NFR-RUN-05 | Ollama 沒回應、網路斷就停下標失敗，不改用付費模型 | 整合（SEAM-03、10）＋驗收 | presubmit／exit-gate | S0.4、S0.6 | 未寫／TC-RUNNER-04 |
| NFR-RUN-06 | 模型對照、skill git sha、配方版本記在每份產出 | 單元＋整合（SEAM-05）＋驗收 | presubmit／exit-gate | S0.6 | 未寫／TC-RUNNER-06 |
| NFR-RUN-07 | CI 用固定小案例＋錄好的回應從投入跑到待審 | 整合（pipeline）＋驗收 | presubmit（pipeline job）／exit-gate | S0.13 | 未寫／TC-GATEWAY-05 |
| NFR-RUN-08 | 403、付費牆、robots 不繞過；429 退避重試 | 整合（SEAM-02、03、09）＋驗收 | presubmit／exit-gate | S0.5、S0.4 | 未寫／TC-INTAKE-02、06、07、TC-GATEWAY-02 |
| NFR-KEEP-01 | 原始抓取、模型請求與回應、每一版產出與稿子都不覆寫 | 單元（寫檔工具）＋整合（SEAM-02、04、12）＋驗收 | presubmit／exit-gate | S0.2 | 未寫／TC-REVIEW-02、05、TC-GATEWAY-06 |
| NFR-KEEP-02 | 一個檔一個寫入者 | 靜態（S0.13）＋整合（SEAM-04、07、08、12）＋驗收 | presubmit／exit-gate | S0.2、S0.3、S0.9、S0.12 | 未寫／TC-REVIEW-09、TC-GATEWAY-06 |
| NFR-KEEP-04 | 資料目錄由 `EF_DATA` 指定，預設 `data/`，不進 repo | 單元（路徑解析）＋靜態（S0.13：`.gitignore` 含 `data/`）＋驗收 | presubmit／exit-gate | S0.2 | 未寫／TC-UI-04、05 |
| NFR-PAR-01 | 多張同時跑，張數是設定值（預設 2） | 整合（SEAM-10）＋驗收 | presubmit／exit-gate | S0.6 | 未寫／TC-RUNNER-05 |
| NFR-PAR-02 | 本機 GPU 一次一個請求，排隊時間入帳 | 整合（SEAM-03）＋驗收 | presubmit／exit-gate | S0.4 | 未寫／TC-GATEWAY-01 |
| NFR-PAR-03 | 雲端依速率上限退避，等待時間入帳 | 整合（SEAM-03）＋驗收 | presubmit／exit-gate | S0.4 | 未寫／TC-GATEWAY-02 |
| NFR-SEC-02 | agent 沒有 shell 與內建讀寫檔；工具只能碰該單資料夾，寫只能寫這一段這一次的目錄 | 單元（SEAM-01 argv）＋整合（SEAM-01、02、12、pipeline）＋驗收 | presubmit／exit-gate | S0.3 | 未寫／TC-DIGEST-05、TC-GATEWAY-06 |
| NFR-SEC-03 | 抓網頁擋內網（SSRF）；外來內容只當資料 | 單元（位址判斷）＋整合（SEAM-02、09）＋驗收 | presubmit／exit-gate | S0.5 | 未寫／TC-INTAKE-08、TC-DIGEST-05 |
| NFR-SEC-04 | 雲端金鑰只在閘道，不進 repo、不進日誌 | 單元（SEAM-01 env）＋整合（SEAM-01、03、金絲雀金鑰掃描）＋靜態（S0.13）＋驗收 | presubmit／exit-gate | S0.3、S0.4 | 未寫／TC-GATEWAY-03 |
| NFR-OBS-01 | 帳本每筆欄位齊全 | 單元（帳本一筆的模型）＋整合（SEAM-04、11）＋驗收 | presubmit／exit-gate | S0.2、S0.4 | 未寫／TC-LEDGER-01 |
| NFR-OBS-02 | 看板顯示第幾段、跑多久、在等什麼（GPU、限流、模型回應、執行工具），資料來自 Pi 事件與閘道 | 整合（SEAM-01、08、10）＋驗收 | presubmit／exit-gate | S0.6、S0.11 | 未寫／TC-GATEWAY-01、02、TC-UI-02、03 |
| FR-UI-01 | 四頁；只讀資料夾，只寫決定檔與新增工單的素材 | 整合（SEAM-08）＋驗收 | presubmit／exit-gate | S0.11、S0.12 | 未寫／TC-INTAKE-09、TC-REVIEW-07、TC-UI-01、04 |

## 缺口清單

這些不是「還沒寫測試」（那是全部 39 條），而是**照現在的寫法，有一部分沒辦法被任何一層證明**，或規格裡少了測試需要的一個數字或一條規則。每一條都要有人補答，測試才寫得下去。

#18 在 2026-10-11 的缺口回覆裡補上了文類信心門檻、快取期限、C1 用完的結果、網址投入的順序、重啟沿用嘗試編號、月上限的規則、配方格式不合的處理與故障注入；那幾條（原 G-01、G-03、G-06、G-07、G-08、G-11、G-12）不再列；之後又補上平均成本的算法、同目標怎麼比、投入抓取記在哪一份帳本（原 G-13、G-14、G-15）。編號都不重用。

- **G-02 FR-VF-01 的 C3 照抄門檻還沒定**（Q-28，S0.14 用實際稿子量）。單元測試用可設定的門檻寫，不寫死數字；帳單要標示目前用的是暫定值。
- **G-04 NFR-SEC-03 的「外來內容只當資料」有一半是模型行為。** 重播模式只會回放錄好的回應，證明不了模型不會被文章裡的指令帶走。自動化測得到的是結構那一半（沒有 shell、工具只碰自己的資料夾、`write_file` 只寫這一段這一次、`fetch_url` 擋內網）；行為那一半**接受只在驗收時抽樣**（TC-DIGEST-05、TC-GATEWAY-06），不再找自動化的證明。
- **G-05 NFR-RUN-07 要在 CI 裝 Node 與鎖版的 Pi。** 這份藍圖核准時，GitHub Actions 沒有任何一次 run 可以佐證「CI 時段」欄；在 CI 真的跑過之前，本機指令是唯一的證據（見〈CI 政策〉偏離 1）。
- **G-09 NFR-KEEP-02 的「一個檔一個寫入者」在執行期測不完整。** 整合測試只能抽查幾條路徑；要全面就得靠 S0.13 的靜態檢查（只有 `gateway/` 會寫 `ledger.jsonl`、只有 `runner/` 會寫 `job.md` 與 `events.jsonl`、只有 `ui/` 會寫 `review/`）。
- **G-10 FR-DG-01、FR-CP-01、FR-CP-02 的「品質」那一半，S0 只有創作者的判斷。** 欄位齊不齊、有沒有載入聲音檔測得到；整理得好不好、像不像創作者，S0 只能靠驗收時人看，S1 的評估才有數字。

## 未實作豁免清單

存量債的界線。列在這裡的承諾缺測試只軟報，不擋 merge；未列的一律硬擋。

**只減不增。** 移除一條走修訂提案；新增一條沒有合法途徑 — 新承諾缺測試就是擋下來的那一刻該補的東西。

| 承諾 | 缺的層 | 列入日期 |
| --- | --- | --- |
| （無） | — | 2026-10-11 |

**為什麼是空的，而不是把 39 條全列進來。** 豁免清單是給「已經有程式、但測試欠著」的存量債用的。這個 repo 還沒有產品程式，沒有存量債；39 條全是隨 #18 新來的承諾。照規則，新承諾缺測試應該擋，但現在擋等於每一顆 PR 都紅。把 39 條全豁免也不對：清單只減不增，等於 S0 整個階段都不會被這道門擋到。

所以 S0 這樣處理未實作門：**每條承諾跟著它的落地票進門。**

1. **落地票合併之前**：那條承諾在現況報告裡是「⬜ 未實作（排程中：S0.x）」，只報不擋。這不是豁免，是還沒輪到。
2. **落地票合併的那一刻起**：那條承諾在它被指派的單元或整合層至少要有一個帶 `@pytest.mark.promise("<ID>")` 的測試，否則擋。一條承諾掛兩張票時（例如 FR-RV-01 掛 S0.9、S0.12），第一張合併就進門，第二張只能加測試、不能拿掉。
3. **S0.13 合併之前沒有機器執行這條規則**：由每張票收尾的 /code-review 對照追溯表檢查「這張票的承諾都有標記」，缺了就是必修。S0.13 合併之後改由 CI 讀 junit 報告執行，同一條規則。
4. **驗收層不進這道門**：驗收案例由 `uat-cases.md` 的「最近判定」報告，S0 Exit Gate 前全清單跑一次。

## CI 政策

- 時段：presubmit＝靜態全套＋單元＋**整合全套**＋pipeline（獨立 job，只在 PR 上跑）；merge＝不另設，靠分支保護要求同一組 checks 全綠；periodic＝無；exit-gate＝`uat-cases.md` 全清單，S0.14 由創作者實跑。
- flake：禁自動重試（預設）。測試裡不准用 `sleep` 等真實時間，時間一律注入；並行相關的測試用 stub 的計數而不是計時來斷言。
- 覆蓋率：追溯覆蓋為完成定義；行覆蓋僅儀表，不設門檻。
- 未實作門：每條承諾跟著它的落地票進門（見上一節）；S0.13 之前由 /code-review 執行，之後由 CI 執行。
- 現況報告：`docs/test-status.md`，CI 於 merge 後生成——**產生器由 S0.13（#30）寫**；產生器存在之前，未實作門照〈未實作豁免清單〉第 3 點執行。
- 證據保留：驗收證據放 `docs/10-truth/uat/`；保留每次 Exit Gate 的完整一份＋最近 3 次的其他 run。單 run 超過約 50MB 或 `docs/10-truth/uat/` 累計超過約 1GB 時，改上 Git LFS。證據裡不准有別人文章的全文（只放截圖與檔案路徑），原文留在 `EF_DATA`。
- 時間上限：`check` job 中位數 4 分鐘以內（`ci.yml` 註解的切分點）；pipeline job 10 分鐘以內；兩個 job 都沿用 `timeout-minutes: 15`。
- 偏離預設：
  1. **本機指令是 presubmit 的備援。** GitHub Actions 沒有跑成功的期間（含藍圖核准時，一次 run 都沒有），它是唯一的證據。每顆 PR 的 Testing 一節貼本機輸出：`uv run pytest && uv run mypy && uv run ruff check .`；碰到 pipeline 的再加 `uv run pytest -m pipeline`（需要用 nvm 裝好的 Node 與 Pi）。CI 跑起來之後這條不撤，因為 CI 掛掉時一樣要用。
  2. **presubmit 跑整合全套，不挑「受影響的」。** S0 的測試量小、沒有挑選工具，挑選本身出錯的風險比多跑幾秒大。`check` job 超過 4 分鐘時再修訂。
  3. **沒有 periodic。** 預設把昂貴的端到端放在排程跑；這個專案昂貴的就是真的模型呼叫，而 NFR-COST-06 規定自動化一律不打付費 API。真的呼叫只在 S0.14 手動跑。
  4. **沒有 merge queue。** 一個人的 repo，PR 量用不到；分支保護要求同一組 checks。
  5. **驗收時段叫 exit-gate，不叫發佈前。** S0 沒有對外發佈；階段的 Exit Gate 就是發佈前的那道門。
  6. **本機最多 2 條跑測試的線（主 session 算一條）。** repo 在慢的 `/mnt/d` 掛載上（S0 開發排程計畫 §3、§6）。測試資料一律放 pytest 的 `tmp_path`（WSL 原生的 `/tmp`），不准寫到 repo 底下的 `data/`；預設不開 `pytest-xdist`。

### 交給 S0.13（#30）

/test-blueprint 第 4 步的三樣結晶產物不跟藍圖一起寫，交給 S0.13（#30，CI 重播整條流程，T4）。理由：改 CI 是 T4，要創作者按合併；靜態檢查與產生器要有產品程式才有東西可檢查。S0.13 的票直接引用這一節。

| 產物 | 內容 | 依據 |
| --- | --- | --- |
| CI workflow | `ci.yml` 加 `pipeline` job（裝 Node＋鎖版 Pi、`uv run pytest -m pipeline`、只在 PR 上跑）；`check` job 的 pytest 加 `--junitxml` 並上傳報告；`check` job 排除 `-m "not pipeline"` | 層佈局、偏離 1～3 |
| 靜態檢查腳本 | ① 每個 `promise` 標記的編號都在本藍圖追溯表裡；② 測試碼不准把 `EF_GATEWAY_MODE` 設成重播以外的值；③ repo 追蹤的檔裡不准出現 `sk-ant-` 樣式；④ 單一寫入者：只有 `gateway/` 寫 `ledger.jsonl` 與 `ledger/intake.jsonl`、只有 `runner/` 寫 `job.md` 與 `events.jsonl`、只有 `ui/` 寫 `review/`；⑤ `.gitignore` 排除 `data/`。全部硬擋，沒有只提醒的模式 | NFR-COST-06、NFR-SEC-04、NFR-KEEP-02、NFR-KEEP-04、G-09 |
| 現況產生器 | merge 後讀 junit 報告的 `promise` 屬性、`uat-cases.md` 的最近判定、本藍圖的追溯表與落地票欄，產生 `docs/test-status.md`（格式照 `.claude/skills/test-blueprint/STATUS.md`），提交到 `dev`。「排程中」的判定要讀落地票有沒有合併 | 未實作豁免清單 1～3 |
