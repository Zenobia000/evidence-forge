# skill 作者須知

一個 skill 是一個資料夾：`SKILL.md`（給 agent 的做法）加上 `schema.json`（給執行器驗產出），需要時再加 `scripts/`。
寫之前先知道這四條。第 1～3 條有測試擋著（`tests/unit/test_skills.py`；第 3 條的 `scripts:` 名單由 `run_script` 執行時擋），第 4 條靠審查。

## 1. 要放進配方的 skill，`schema.json` 是必要的

Agent Skills 標準把 `schema.json` 列成可選，但我們的執行器用它驗每一段的產出（ADR-0009 §3 第 6 點）。
配方用到的 skill 少了它，`ef serve` 啟動與開單時就會擋下。

硬性要求要寫成 schema 的 `required`、`minItems`、`minLength`，不要只寫在 `SKILL.md` 裡請模型遵守。例：

- `digest`：每條主張的 `quote` 是 `required`、`minLength: 1`，主張陣列 `minItems: 1`（每條主張附逐字引句）。
- `compose`：每句事實句放在叫 `fact` 的欄位，`facts` 陣列 `minItems: 1`。

C1、C2 只認叫 `quote`、`fact` 的欄位（`src/evidence_forge/checks/stages.py`）。改了欄位名，C5 照樣過，C1、C2 卻會每次都過、沒人發現；所以有一條契約測試讀這兩份 schema，改名時先壞的是那條測試。

## 2. agent 只看得到 `SKILL.md` 和工單資料夾

`SKILL.md` 整份放進系統提示（ADR-0018），`read_file` 只能讀工單資料夾。所以 skill 資料夾裡的其他檔（`templates/`、範例）、
`harness/genres/`、`harness/conversions/`、`$EF_DATA/authors/` agent 都讀不到。

要用到的參考資料由執行器開段前放進工單的 `input/`（`src/evidence_forge/runner/stage_inputs.py`）：

| 路徑 | 內容 |
| :--- | :--- |
| `input/content.md` | 原文 |
| `input/genres.md` | 12 種文類的定義接成一份 |
| `input/target-genre.md` | 目標文類的定義 |
| `input/author/voice.md` | 作家聲音 |
| `input/author/genre-style.md` | 作家在目標文類的風格；沒有就是未校準 |
| `input/conversions/<來源>--<目標>.md` | 轉換說明 |

`SKILL.md` 只能提到上表的路徑與這一段的產出目錄（`stages/…`）。要多給 agent 一份東西，先改 `stage_inputs.py`，再寫進 `SKILL.md`。

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
