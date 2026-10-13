# ADR-0017：狀態 token 改用新設計的實體命名；中文不載網路字型

> **狀態：** 已接受 | **日期：** 2026-10-11 | **決策者：** 專案擁有者（視覺設計、Product 兩個 session 對過名單，系統架構 session 撰寫）
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **後續補充：** 聊天室新增 `--chat-*`、`--verify-*`、`--diff-*`，並退役 `--job-review`、`--job-approved`、`--job-discarded`，見 ADR-0023。
> **修正 ADR-0007**：§3.1 第 2 點的狀態前綴清單；§3.4 表格最後一列的字型值。ADR-0007 的其餘規則不變，包括兩層命名、顏色的意思、三條硬規則。

## 1. 背景與問題

- **上下文：** ADR-0007 定了兩層命名：基礎 token 用 Loomy 的名字，狀態 token 用本專案的名字，再指向 Loomy 的顏色。但狀態前綴是照舊設計的實體取的：`--run-*`、`--gate-*`、`--verdict-*`。ADR-0009～0013 換掉了流程之後：
  - Run 改叫「工單」。
  - 定題 Gate 拿掉了（ADR-0013）。
  - 逐條驗證變成自動檢查 C1～C7。
  - 多了兩種新的提醒：文類判斷信心低、文類風格未校準（ADR-0015）。
  - 素材多了「引用」使用狀態（ADR-0016）。
- **為什麼現在要定：** 視覺設計 session 要開新畫布畫 S0 的 5 張設計稿。名字在動筆之前定下來最便宜；畫完再改，設計稿和之後的 `static/css/tokens.css` 都要跟著改。
- **字型：** ADR-0007 把中文字型定為 Noto Sans TC 和 Noto Serif TC，但這兩套每個字重都有好幾 MB。S0 的 UI 是 FastAPI 直接送出的靜態檔，沒有 Node 建置，不能把字型切成小塊。而且 Windows 沒有內建好看的中文襯線字。
- **為什麼要寫成 ADR：** 這是在修正一份已接受 ADR 的決定。

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 沿用舊前綴，只是意思換掉（例如 `--run-*` 拿來代表工單） | 不採用：模板裡讀不出意思，這正是 ADR-0007 當初要避開的 |
| 每種狀態各自配一個新顏色 | 不採用：ADR-0007 規定顏色只留給「要不要你出手」，畫面到處都是顏色，就看不出哪裡重要 |
| **換成新實體的前綴，顏色仍然只用 Loomy 現有的顏色** | **採用** |

## 3. 決策

### 3.1 前綴

- 刪掉：`--run-*`、`--gate-*`、`--verdict-*`。
- 換成：`--job-*`、`--check-*`、`--claim-*`、`--genre-*`。
- 保留：`--source-*`、`--license-*`、`--asset-*`。

值一律是 Loomy 顏色的別名，不寫死色碼。這一點照 ADR-0007。

### 3.2 名單

下面的表記的是決定：每種意思對到 Loomy 的哪個顏色。token 檔的正本仍在 `docs/10-truth/design/`，由視覺設計 session 維護（ADR-0007 開頭的規定）。

**`--job-*`：工單。** 對照 S0 IA 的看板五欄、結果標籤、「在等什麼」、過渡狀態。

| token | 意思 | 值 |
| :--- | :--- | :--- |
| `--job-review` | 待審，看板的待審欄 | `--color-bg-selection`（淡黃底），意思是「這一區要你看」 |
| `--job-incomplete` | 結果：未完成 | `--color-accent-action`（實黃）只當底色，字用 `--color-on-action` |
| `--job-failed` | 結果：失敗 | `--color-danger` |
| `--job-complete` | 結果：完成 | `--color-text-primary` |
| `--job-queued`、`--job-running`、`--job-approved`、`--job-discarded` | 排隊、執行中、通過、丟棄 | `--color-text-secondary`，不佔顏色，靠圖示＋文字區分 |
| `--job-waiting` | 在等什麼：等 GPU、等限流、等模型回應、跑工具 | `--color-text-secondary`，四種共用一個 token，靠圖示＋文字區分 |
| `--job-pending` | 已送出，等待套用；新增後的「準備中」卡片 | `--color-text-tertiary`，虛線 |
| `--job-superseded` | 這份決定已失效，因為另一份先被套用了 | `--color-text-tertiary`，虛線 |

- **待審欄和未完成標籤用兩個不同的黃。** 待審欄裡很多卡片的結果剛好是「未完成」。兩個如果用同一個黃，標籤就會跟欄的底色融在一起。
- IA §8 的「已有工單」是一則提示，不是狀態，用一般的提示樣式，不需要 token。

**`--check-*`：自動檢查 C1～C7。** 舊的 `--verdict-*` 併進這裡，因為逐條驗證現在就是 C6「引用撐得住」。

| token | 意思 | 值 |
| :--- | :--- | :--- |
| `--check-pass` | 過 | `--color-text-secondary` |
| `--check-fail` | 沒過 | `--color-text-primary` |
| `--check-warn` | 提醒（C6、C7） | `--color-text-primary` |

**單一檢查不上色，只靠圖示＋文字。** 「要不要你出手」看的是整張工單的結果，這已經由 `--job-*` 上色了。同一頁如果每條檢查都紅黃交錯，反而看不出哪裡重要。保留成 token 的用意是：之後真的要上色，只改這裡一處。

**`--claim-*`：整理結果裡主張的種類。**

| token | 意思 |
| :--- | :--- |
| `--claim-fact` | 事實 |
| `--claim-data` | 數據 |
| `--claim-opinion` | 觀點 |

- 三種照 PRD §5 和 `features/digest.feature`。
- 這是「種類」，不是「狀態」，所以不佔顏色，全部用 `--color-text-secondary` 的標籤文字區分。
- 要加種類，先改需求，再加 token。

**`--genre-*`：文類。** ADR-0015。

| token | 意思 | 值 |
| :--- | :--- | :--- |
| `--genre-uncertain` | 文類判斷信心低 | `--color-accent-mark`；字用 `--color-accent-mark-text` |
| `--genre-uncalibrated` | 目標文類的作家風格還沒校準 | 同上 |

用靛藍，是照 ADR-0007 §3.2「AI 動過或還沒定案」的意思。

**`--source-*`、`--license-*`、`--asset-*`：S3 才用到，名字先定。**

| token | 意思 | 值 |
| :--- | :--- | :--- |
| `--source-ok` | 讀到原文 | `--color-text-primary` |
| `--source-unreadable` | 讀不到原文（403、付費牆） | `--color-danger` |
| `--license-ok` | 授權可用 | `--color-text-primary` |
| `--license-unknown` | 授權不明 | `--color-text-tertiary`，虛線 |
| `--license-denied` | 權利人拒絕 | `--color-danger` |
| `--asset-embed`、`--asset-quote-approved` | 直接使用；已核准的引用 | `--color-text-primary` |
| `--asset-quote-pending` | 引用待你核准（ADR-0016） | `--color-accent-action` 只當底色，字用 `--color-on-action` |
| `--asset-reference` | 只保存，不放進稿子 | `--color-text-tertiary` |

ADR-0007 §3.3 的三條硬規則照舊：
- 每個狀態都是顏色＋圖示＋文字三件一組。
- 黃與靛藍不當文字色。
- 淡灰只用在有文字標籤的狀態上。

### 3.3 字型：中文用系統字型，不載網路字型

| token | 值 |
| :--- | :--- |
| `--font-ui`、`--font-reading` | `"Inter", "Microsoft JhengHei", "PingFang TC", system-ui, sans-serif` |
| `--font-reading-serif` | `"Source Serif 4", "Microsoft JhengHei", "PingFang TC", sans-serif` |
| `--font-mono` | `"JetBrains Mono", ui-monospace, monospace` |

- Loomy 原本的 `--font-ui`、`--font-reading` 開頭是 Noto Sans TC。**要整組換掉**，不能只在最前面加 Inter，否則瀏覽器還是會去載 Noto。
- **襯線只用在英文**（Source Serif 4）。中文標題退回系統黑體，用字級和字重拉出層次。
- Inter、Source Serif 4、JetBrains Mono 自己放 woff2 在 `static/`，這幾個檔案小。中文不放任何網路字型。

## 4. 後果

- **正面：**
  - 模板裡讀得出意思，例如 `--job-incomplete`、`--genre-uncalibrated`。
  - 沒有新增任何顏色。Loomy 改色值時，這些別名全部跟著變。
  - 頁面不用載入幾 MB 的中文字型。
- **負面：**
  - 中文標題沒有襯線，跟 moodboard 的明體標題不一樣。
  - 各台電腦的系統字型不同，Windows 和 macOS 看到的中文字會有差異。
- **影響範圍：** S0 的設計稿、之後的 `static/css/tokens.css`、所有 Jinja2 元件；ADR-0007 頂部加一行修正說明。
- **重新評估觸發：**
  - 畫面上出現名單裡沒有的狀態。
  - 決定要讓單一檢查上色。
  - 改成有前端建置，可以切中文字型了。

## 5. 參考

- ADR-0007（兩層命名）、ADR-0008（外觀三選一）
- S0 UI 的 IA：`30-snapshot/plans/design/2026-10-11-s0-ui-ia.md`（PR #11）§3、§4、§7、§8
- PRD v2.0 §5；`features/digest.feature`
- Loomy 設計系統（Artifact）：`https://claude.ai/artifact/NxwdZTmM2cgFPgFVZVKP3z`。`--color-bg-selection` 由視覺設計 session 對照 `tokens.json` 確認。
