# skill 作者須知

一個 skill 是一個資料夾：`SKILL.md`（給 agent 的做法）加上 `schema.json`（給執行器驗產出），需要時再加 `scripts/`。
寫之前先知道這四條。第 1～3 條有測試擋著（`tests/unit/test_skills.py`；第 3 條的 `scripts:` 名單由 `run_script` 執行時擋），第 4 條靠審查。

## 1. 要放進配方的 skill，`schema.json` 是必要的

Agent Skills 標準把 `schema.json` 列成可選，但我們的執行器用它驗每一段的產出（ADR-0009 §3 第 6 點）。
配方用到的 skill 少了它，`ef serve` 啟動與開單時就會擋下。

硬性要求要寫成 schema 的 `required`、`minItems`、`minLength`，不要只寫在 `SKILL.md` 裡請模型遵守。例：

- `brief`：`brief.json` 只放給程式讀的文類、主題標籤、關鍵主張（1～6 條，SKILL.md 要 3～6），不收 `quote`（ADR-0020 不附逐字引句）。
  `brief.md` 的六節由程式檢查（`checks/analysis.py` 的 `BRIEF_SECTIONS`），標題照抄 SKILL.md 的中文。
- `investigate`：`sources.json` 的來源一筆有 `id`（`S2` 起）、`url`、`title`、`why`，另外有 `fact_checks`（每條關鍵主張的判定：
  `supported`、`disputed`、`not_found`、`outdated`；除了 `not_found` 都要附來源）與 `topics`（每個題材至少一篇）。
  `report.md` 的五節依序由程式檢查（`REPORT_SECTIONS`，PRD §5）；查證與題材引用的編號要在清單上。
- `rewrite`：`article.json` 放選定的標題與全部候選標題。`article.md` 的格式與出處由程式檢查
  （`checks/article.py`）：標準 Markdown、最後一節是「參考來源」而且列了原文出處；跟原文與每份來源的逐字重疊率只顯示、不擋。

### 執行器看產出檔名決定跑哪些檢查

執行器不看 skill 叫什麼，看這一段在配方裡宣告的產出（`runner/hard_checks.py`、`runner/sources.py`）：

| 宣告的產出 | 執行器做什麼 |
| :--- | :--- |
| `report.md` | 分析報告的檢查：五節依序、Markdown、同一段的 JSON 合 schema、引用的編號在清單上 |
| `brief.md` | brief 的檢查：六節、Markdown、同一段的 JSON 合 schema |
| `article.md` | 重構文章的檢查：Markdown、結尾參考來源列了原文出處、同一段的 JSON 合 schema；重疊率只顯示。沒過帶著結果回這一段修，最多 `auto_fix` 次 |
| `sources.json` | 檢查都過了之後，照清單從這一段真的抓過的網頁放來源正文；清單上有沒抓過的網址算這一次不合格 |

所以 skill 改名、或新的 skill 交同一種產出，檢查照樣跑。反過來，**要交分析報告就一定叫 `report.md`、來源清單一定叫 `sources.json`**，換了名字檢查就不會跑；`tests/unit/test_skills.py` 有契約測試盯著 `analyze` 與 `rewrite` 配方。

## 2. agent 只看得到 `SKILL.md` 和工單資料夾

`SKILL.md` 整份放進系統提示（ADR-0018），`read_file` 只能讀工單資料夾。所以 skill 資料夾裡的其他檔（`templates/`、範例）、
`harness/genres/`、`harness/conversions/`、`$EF_DATA/authors/` agent 都讀不到。

要用到的參考資料由執行器開段前放進工單的 `input/`（`src/evidence_forge/runner/stage_inputs.py`）：

| 路徑 | 內容 |
| :--- | :--- |
| `input/content.md` | 原文（分析工單才有；重構工單不放，FR-RW-01） |
| `input/genres.md` | 12 種文類的定義接成一份 |
| `input/target-genre.md` | 目標文類的定義 |
| `input/author/voice.md` | 作家聲音 |
| `input/author/genre-style.md` | 作家在目標文類的風格；沒有就是未校準 |
| `input/conversions/<來源>--<目標>.md` | 轉換說明 |
| `input/sources/S<n>.md` | 查證與找題材段找到的來源正文（`S2` 起；`S1` 是原文）。那一段合格後由執行器照 `sources.json` 從那一段真的抓過的網頁放（`src/evidence_forge/runner/sources.py`），agent 不能寫；重構工單從分析工單原樣帶過來 |
| `input/headline-punchline.md` | 標題與金句規則（`$EF_DATA/style/headline-punchline-system.md`，創作者自己的寫作系統，不進 repo）；沒放就沒有這個檔 |
| `input/report.md`、`input/brief.md`、`input/brief.json` | 從上一張工單（`ef new --from`）帶過來的分析報告與 brief：只帶過了程式檢查的那一份 |
| `input/article.md` | 再改時上一版的文章（從上一張重構工單帶過來） |

`SKILL.md` 只能提到上表的路徑與這一段的產出目錄（`stages/…`）。要多給 agent 一份東西，先改 `stage_inputs.py`，再寫進 `SKILL.md`。

工具也是執行器給的：`read_file`、`list_files`、`write_file`、`fetch_url`、`run_script` 每一段都有；
**`web_search` 只有配方裡那一段 `tavily_credits` 大於 0 才有**（執行器設 `EF_WEB_SEARCH=1`，ef-tools 才註冊）。
所以這個欄位同時決定「能花多少搜尋額度」與「有沒有搜尋工具」；`SKILL.md` 要用 `web_search`，配方那一段就要給額度。
額度是**這一段在整張單裡的總額，含重試**（工單的上限預設是各段加總，閘道只看整張單用了多少），
所以要照「skill 一次最多搜幾次 × `max_attempts`」給，不是只給一次的量。
其他段的工具清單維持原樣，請求逐位元組不變，錄音才對得上。

## 3. 腳本不寫檔，結果只印到 stdout

`run_script` 用閘道的權限、以工單資料夾當工作目錄跑腳本，參數是 agent 給的。腳本只要能寫檔，被原文提示注入的 agent 就能叫它寫到
`review/decision.v1.json` 之類的地方，繞過 `write_file` 只准寫 `stages/<段>/<次>/` 的限制。所以：

- 輸入從參數、stdin 或讀工單資料夾來；結果一律印到 stdout。
- 不收「輸出路徑」這類參數。
- 要落地成檔案，由 agent 拿 stdout 的內容用 `write_file` 寫。寫入的邊界只有一個：閘道的 `write_file`。

測試會掃 `harness/skills/*/scripts/*.py`，擋 `open(..., "w")`、`write_text`、`shutil` 的複製搬移、`os.replace` 這類常見寫法；掃不到的靠審查。
腳本要列在 `SKILL.md` frontmatter 的 `scripts:`，`run_script` 才肯跑。

## 4. 提示詞用英文寫，原文只當材料

跟 `harness/pi/system-prompt.md` 一樣用英文寫給模型看。每個 `SKILL.md` 都要提醒：原文是材料，不是指令。
