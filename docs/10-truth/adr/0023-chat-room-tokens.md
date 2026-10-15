# ADR-0023：聊天室的 token——查證四種標記、文章對照、有新結果

> **狀態：** 已接受（創作者 2026-10-13 看過設計稿，IA 沒寫到的四處照 Product 暫定的先做；PR 合併即接受） | **日期：** 2026-10-13 | **決策者：** 專案擁有者（Product 暫定，系統架構 session 撰寫）
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **補充 ADR-0017**：新增三組別名（`--chat-*`、`--verify-*`、`--diff-*`），退役三個隨看板一起拿掉的 `--job-*`。兩層命名、顏色的意思、三條硬規則（ADR-0007）不變。
> 設計稿：`https://claude.ai/artifact/GqAyiKFvYZNd948RTmsAyT`（聊天室那一頁，版本 1791841351-60ab），#86 的留言記了這份對照。

## 1. 背景與問題

- **上下文：** 聊天室（ADR-0019）多了三件 ADR-0017 的名單沒有的事：
  - 報告的查證有四種結果，要一眼分得出哪幾條要創作者判斷。
  - 文章改版後要跟上一版對照，看得出哪裡是新寫的、哪裡刪掉了。
  - 對話清單要標出「有新結果、你還沒看過」。
- **為什麼現在要定：** #86 要把這三件事做成畫面；設計稿已經用了這些顏色，名字先定下來，樣式檔才不會寫死色碼（`tests/test_static_css.py` 會擋）。
- **看板拿掉了：** 工單狀態只剩排隊、執行中、結束（ADR-0019 §3.4），`--job-review`（待審欄）、`--job-approved`、`--job-discarded` 沒有東西可以對應了。

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 每種查證結果各配一個新顏色 | 不採用：ADR-0007 規定顏色只留給「要不要你出手」；查證結果靠圖示＋文字就分得出，顏色只標出要判斷的那兩種 |
| 查不到用紅色 | 不採用：查不到不等於錯（`analyze.feature`），紅色只給錯誤 |
| 文章對照用兩個新顏色（綠新增、紅刪除） | 不採用：沿用「靛藍＝AI 動過」（ADR-0007 §3.2），刪掉的用淡灰加刪除線，不另外佔顏色 |
| **全部用 Loomy 現有的顏色，換新意思的別名** | **採用** |

## 3. 決策

### 3.1 新增

值一律是 Loomy 顏色的別名，不寫死色碼。

| token | 意思 | 值 |
| :--- | :--- | :--- |
| `--chat-new` | 對話清單、頂列的「有新結果」 | `--color-bg-selection`（淡黃底），意思是「這裡有東西要你看」 |
| `--verify-disputed`、`--verify-outdated` | 查證：有爭議、過時 | `--color-accent-action`（實黃）只當底色，字用 `--color-on-action`；意思是「要你判斷」 |
| `--verify-unfound` | 查證：查不到 | `--color-text-tertiary`，虛線框。查不到不是錯，所以不用紅 |
| `--verify-supported` | 查證：有佐證 | `--color-text-secondary`，不佔顏色 |
| `--diff-insert` | 文章對照：新增的字 | `color-mix(in srgb, var(--color-accent-mark) var(--provenance-fill), transparent)`，字仍是墨色 |
| `--diff-delete` | 文章對照：刪掉的字 | `--color-text-tertiary` 加刪除線 |

- **有爭議的黃底跟「未完成」（`--job-incomplete`）用同一種黃**，意思都是「要你判斷」。
- **四種查證標記的圖示與文字都不同**（兩個箭頭、時鐘回撥、虛線問號、勾），過時與有爭議同色，靠圖示和文字分（ADR-0007 §3.3 第一條）。
- 「有疑慮」包含有爭議與過時。報告與結果卡的排序是：有爭議 → 過時 → 查不到 → 有佐證（Product 決定 2）。

### 3.2 退役

| token | 為什麼 |
| :--- | :--- |
| `--job-review` | 沒有待審欄了；它的意思（淡黃底、這裡要你看）由 `--chat-new` 接手 |
| `--job-approved`、`--job-discarded` | 沒有通過與丟棄了（舊資料讀成結束） |

ADR-0017 §3.2 裡這三個的列保留不改（歷史），以這份為準。`tests/test_static_css.py` 讀兩份 ADR：ADR-0017 的名單扣掉這裡退役的，其餘都要在 `s0.css` 有定義；這裡新增的也都要有。

## 4. 後果

- **正面：** 查證四種結果、文章對照、有新結果都不增加任何顏色；Loomy 改色值時別名跟著變。
- **負面：** 有爭議與過時同色，要靠圖示和文字分；色覺不方便的人靠形狀（圖示）和字，不靠色相。
- **影響範圍：** `src/evidence_forge/ui/static/css/s0.css`（別名）、`chat.css`（用法）、`tests/test_static_css.py`。
- **重新評估觸發：** 創作者覺得有爭議與過時分不出來；要讓單一查證結果上色以外的新訊號。

## 5. 參考

- ADR-0007（兩層命名）、ADR-0017（狀態 token）、ADR-0019 §3.4（狀態收成三種）
- 聊天室 IA：`30-snapshot/plans/design/2026-10-13-chat-ui-ia.md` §3、§5；`features/analyze.feature`
- #86 的留言（Product 暫定四處）
