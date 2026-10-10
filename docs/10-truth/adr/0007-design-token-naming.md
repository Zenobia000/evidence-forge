# ADR-0007：設計 token 命名 — 基礎用 Loomy 的名字，狀態用自己的名字

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者（產品流程、視覺兩個 session 提案，架構 session 撰寫）
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **這份只記決定。** token 的值與元件規格的唯一的家是 `docs/10-truth/design/`（視覺 session 負責）；完整對照表的提案版本在 `30-snapshot/plans/design/2026-10-10-frontend-architecture-draft.md` 第 3 部分。

## 1. 背景與問題

- **上下文：** R2 起的 Web UI 要套用創作者的 Loomy 設計系統（Artifact 版，`https://claude.ai/artifact/NxwdZTmM2cgFPgFVZVKP3z`，2026-10-10 讀取的版本 `1791631747-b15c`；正本在另一個 repo 的 `system/tokens.css`）。
- **問題：** Loomy 是為一個寫作 app 設計的，只有基礎 token（底色、文字、三個強調色、字級、間距），沒有「狀態」的 token。本專案畫面上大部分資訊是狀態：主張驗證了沒、來源抓到沒、圖片授權如何、Run 在等誰。
- **為什麼要寫成 ADR：** token 名字一旦寫進模板與元件，改名要動到每一頁；而且 Loomy 有幾條產品規則跟本專案的需求衝突，以後的人會問「為什麼沒照 Loomy」。
- **約束：** 狀態不能只靠顏色區分（無障礙）；不引入 Node 建置鏈（架構草案 v0.2 §1.4）。

## 2. 考量的選項

| 選項 | 做法 | 放棄的理由 |
| :--- | :--- | :--- |
| 全部自己命名 | 自訂一套名字，值從 Loomy 抄 | Loomy 更新時要手動對照，兩邊名字對不起來 |
| 全部用 Loomy 的名字 | 狀態直接寫 `--color-accent-mark` 等 | 模板裡看不出「這是驗證狀態還是 AI 標記」；改一個狀態的顏色會連帶改到不相干的地方 |
| **基礎用 Loomy、狀態用自己的名字並指向 Loomy** | 兩層 | **採用** |

## 3. 決策

### 3.1 兩層命名

1. **基礎 token 用 Loomy 的名字，不改名**：`--color-bg-page`、`--color-bg-surface`、`--color-text-primary`／`secondary`／`tertiary`、`--color-accent-action`、`--color-accent-action-line`、`--color-on-action`、`--color-accent-mark`、`--color-accent-mark-text`、`--color-danger`、`--font-reading-serif`，以及字級、間距、圓角（2026-10-10 已逐一對過 Loomy 的 `tokens.json`，名字都存在）。
2. **狀態 token 用本專案的名字**，前綴固定為 `--verdict-*`、`--source-*`、`--license-*`、`--asset-*`、`--run-*`、`--gate-*`，**值一律是 Loomy 顏色的別名**，不寫死色碼。

### 3.2 顏色的意思

| 意思 | 指向的 Loomy 顏色 |
| :--- | :--- |
| 確定的 | 墨（`--color-text-primary`） |
| AI 動過或還沒定案 | 靛藍（`--color-accent-mark`；當字時用 `--color-accent-mark-text`） |
| 輪到你了 | 黃（`--color-accent-action`；**只當底色**，上面的字用 `--color-on-action`） |
| 擋下、失敗 | 紅（`--color-danger`） |
| 未知、還沒到 | 淡灰虛線（`--color-text-tertiary`） |

Run 的執行中、排隊中、部分完成、已否決、已取消不另外佔顏色，用圖示與文字區分；顏色只留給「要不要你出手」。

### 3.3 三條硬規則

1. **每個狀態都是顏色＋圖示＋文字三件一組**，元件不接受只傳顏色。
2. **黃與靛藍不能直接當文字色**：Loomy 的用法說明寫明黃色在白底上對比不到 3:1、靛藍的線色不當字。所以 `--run-waiting` 只能做成黃底墨字的標籤；靛藍狀態的文字要用 `--color-accent-mark-text` 的別名。
3. **淡灰只用在有文字標籤的狀態上**。Loomy 規定 `--color-text-tertiary` 不用在需要被讀懂的主要資訊；本專案的「未知／還沒到」都附文字與圖示，顏色只是輔助。

### 3.4 不採用的 Loomy 規則

Loomy 的產品規則是寫給寫作 app 的。本專案**只借視覺，不借規則**，下面四條明確不採用：

| Loomy 規則 | 本專案的做法 | 理由 |
| :--- | :--- | :--- |
| 沒有分數、進度條、步驟數字、「下一步」 | 有階段導覽列、預算條、進度條 | 一個 Run 會跑好幾分鐘、停好幾次，看不到進度就沒辦法放心離開（PRD NFR-24） |
| 紅色只給不可逆操作與系統錯誤 | 關卡沒過、授權被拒、來源抓不到也用紅 | 這些都會擋下發布，看漏的代價跟系統錯誤一樣高 |
| 寫作 app 專屬規則（只能填 API key、不畫 Pro、寫作畫面沒有主要按鈕、側欄卡對齊段落等） | 不適用 | 這是研究工作室，不是寫作 app |
| 內文與介面都用黑體，不用明體 | 標題與文章預覽用明體：沿用 `--font-reading-serif` 這個名字，但值改成 Source Serif 4 在前、Noto Serif TC 在後；介面用 Inter＋Noto Sans TC | 創作者的 moodboard。Loomy 原本的 `--font-reading-serif` 只有中文明體，沒有 Source Serif 4，所以**這是改值，不只是換用法** |

另外：提案時列的第五條「不跟作業系統自動切深色」**不算偏離**——Artifact 版的 Loomy 本來就規定要明確寫 `data-theme`，不自動跟系統。本專案照做：執行紀錄頁固定深色，其他頁固定淺色。

## 4. 後果

- **正面：** 模板裡讀得出意思（`--license-denied` 比 `--color-danger` 清楚）；Loomy 更新色值時，基礎層直接跟著變，狀態層因為是別名也跟著變。
- **負面：** 多一層別名要維護；Loomy 改名時，基礎層的名字要跟著改（機率低，但會動到所有別名）。`--font-reading-serif` 的值跟 Loomy 不同，同步 Loomy 時不能整份覆蓋字型設定。
- **影響範圍：** `docs/10-truth/design/`、之後的 `static/css/tokens.css`、所有 Jinja2 元件。
- **重新評估觸發：** Loomy 正本改了基礎 token 的名字；或 Loomy 自己長出狀態 token。

## 5. 參考

- Loomy 設計系統（Artifact）：`https://claude.ai/artifact/NxwdZTmM2cgFPgFVZVKP3z`，`project/tokens.json`、`project/README.md`（2026-10-10 讀取，版本 `1791631747-b15c`）
- 提案與完整對照表：`30-snapshot/plans/design/2026-10-10-frontend-architecture-draft.md` 第 3 部分
