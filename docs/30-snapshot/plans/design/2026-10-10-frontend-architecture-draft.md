# 前端架構草案 — Evidence-led Editorial Studio

> **版本：** v0.1 | **日期：** 2026-10-10 | **狀態：** 草案，待創作者確認
> 依 `docs/templates/vibecoding/12_frontend_architecture_specification.md`（v2.0）撰寫，章節編號照範本。
> **上游：** `2026-10-10-ux-persona-journey.md`（五條設計原則）、`2026-10-10-frontend-ia-draft.md`（頁面、路由、跨頁資料）、`30-snapshot/plans/2026-10-10-architecture-draft-v0.2.md`（系統架構，Q-01 前端技術未定）
>
> **邊界**：這份只談技術視角（技術選型、分層、設計系統、效能、無障礙、檔案組織、安全）。頁面、旅程、導航、路由內容在範本 17 那份。
> 前端技術的選擇屬於架構 Q-01，正式拍板要寫成 ADR；這份是 UI/UX 這邊的建議與理由。

---

## 第 1 部分：架構目標

這是單人、本機的工具，可以同時跑幾個 Run（PRD v1.3 NFR-20、ADR-0006）。目標跟對外網站不一樣：不用擔心流量與 SEO，要擔心的是**長時間閱讀的舒適、證據核對的速度、執行中狀態的即時性，以及顯示外部不可信內容時的安全**。

| 維度 | 目標 | 衡量指標 |
| :--- | :--- | :--- |
| **效能** | 頁面切換感覺不到等待；證據矩陣大了也不卡 | LCP、INP、伺服器渲染時間（第 4 部分） |
| **技術可用性** | 桌機為主、平板可做決定；鍵盤可完成整條旅程 | 斷點覆蓋、WCAG 2.2 AA 自動檢測 |
| **可維護性** | 一個人能維護；不為前端多養一套工具鏈 | 語言與建置工具數量、頁面測試覆蓋 |
| **可靠性** | 程式重啟後畫面能接回同一個 Run；外部內容不會打破頁面 | 重啟後的旅程測試、XSS 測試 |
| **安全** | 外部網頁、LLM 輸出、抓回來的圖片都當不可信內容 | 第 8 部分的檢查清單 |

---

## 第 2 部分：系統化分層與技術選型

### 建議：FastAPI 伺服器渲染＋HTMX

```
用戶感知層    -- Jinja2 模板（巨集當元件）＋ 純 CSS（CSS 自訂屬性當設計 token）
互動邏輯層    -- HTMX（局部更新、輪詢、表單送出）＋ 少量原生 JavaScript（面板、鍵盤操作）
狀態管理層    -- 伺服器是唯一的狀態來源；URL 放選中項目與篩選；瀏覽器不存業務狀態
資料通訊層    -- 頁面路由直接呼叫服務層，回傳 HTML；JSON API 給 CLI 用，兩者共用服務層
基礎設施層    -- uv、pytest、Playwright（Python 版）、axe-core；沒有 Node 建置步驟
```

| 層級 | 職責 | 選型 |
| :--- | :--- | :--- |
| 感知層 | 渲染畫面、視覺一致 | Jinja2＋純 CSS（自訂屬性） |
| 互動層 | 輸入、表單驗證、局部更新 | HTMX；表單驗證以伺服器端 Pydantic 為準，瀏覽器只做提示 |
| 狀態層 | Run 狀態、篩選、選中項目 | 伺服器＋URL（範本 17 那份 §8） |
| 通訊層 | 頁面與服務層 | FastAPI 路由；HTMX 請求回傳 HTML 片段 |
| 基礎設施 | 建置與品質 | uv、ruff、mypy、pytest、Playwright for Python |

### 為什麼是它，而不是另外兩個

| 選項 | 適合的情況 | 在這個專案的代價 | 結論 |
| :--- | :--- | :--- | :--- |
| **A. FastAPI＋Jinja2＋HTMX** | 狀態本來就在伺服器、頁面以閱讀與表單為主、一個人維護 | 證據矩陣的複雜互動要自己寫一點 JavaScript；元件生態比 React 小 | **建議** |
| B. React SPA（Vite）＋OpenAPI 產生的 client | 大量瀏覽器端互動、多人協作、要離線編輯 | 多一套 Node 工具鏈（這個 repo 剛從 TypeScript 換成 Python）；型別要從 OpenAPI 產生再同步；兩個開發伺服器 | 互動需求長大到 A 撐不住時再評估 |
| C. Streamlit／Gradio／NiceGUI | 資料探索、內部儀表板、一兩頁的工具 | 每次互動整頁重跑，不適合長時間執行的 Run；很難做出 URL 驅動的 13 頁資訊架構與原文側欄 | 不建議 |

A 成立的前提：

1. Run 的狀態本來就存在伺服器（LangGraph checkpoint、Metadata DB），瀏覽器不需要另外維護一份。
2. 主要互動是閱讀、篩選、送出決定，不是拖拉或即時協作。
3. 範本 17 那份的路由都是可書籤的 URL，伺服器渲染天生符合。

如果之後證據矩陣需要大量瀏覽器端互動（例：拖拉重排、即時多欄篩選），先把那一個元件做成獨立的小型前端元件，整站不必換掉。

### 跟架構草案 Q-01 的關係

架構草案 Q-01 建議「R1 先用 CLI，R2 再用 FastAPI 伺服器端模板做最小網頁」。這份的技術建議與它一致（A 就是 FastAPI 伺服器端模板）。差別只在**什麼時候開始做網頁**，列在第 10 部分 Q-15。

---

## 第 3 部分：設計系統

設計 token 與元件是「介面不能違反什麼」，依 `docs/README.md` 的「一件事只有一個家」，實作後的家是 `10-truth/design/`。這裡先定**有哪些 token、叫什麼名字**；實際的色值、字級在設計稿階段定，定了之後寫進 `10-truth/design/` 與 `static/css/tokens.css`，其他地方只引用名稱。

### 設計系統的來源：Loomy

視覺以創作者在 claude.ai/design 上的 **Loomy** 設計系統為準（視覺 session 已搬成 Artifact 版，wireframe 畫布見 `20-state/progress.md`）。**只借視覺，不借規則**：採用 Loomy 的 token 值、`lm-*` 元件、「三色各管一件事」、一個畫面最多一顆主要按鈕、按鈕用動詞開頭。下面五條 Loomy 規則不採用（2026-10-10 創作者同意）：

| Loomy 規則 | 這裡怎麼做 | 為什麼 |
| :--- | :--- | :--- |
| 沒有進度條、步驟數字、下一步 | 有階段導覽列、預算條、NFR-24 進度條 | Run 會跑好幾分鐘、停好幾次，看不到進度就無法離開去做別的事 |
| 紅色只給不可逆操作與系統錯誤 | 關卡未通過、授權 denied、來源 unavailable 也用紅 | 這些都會擋下發布，看漏的代價跟系統錯誤一樣高 |
| 寫作 app 專屬的規則（只能填 API key、不畫 Pro、寫作畫面沒有主要按鈕、側欄卡對齊段落、出處深淺滑桿） | 不適用 | 這是研究工作室，不是寫作 app |
| 內文只用黑體、不用明體 | 標題用 Source Serif 4＋Noto Serif TC（Loomy 的 `--font-reading-serif`）；介面用 Inter＋Noto Sans TC | 創作者的 moodboard |
| 跟著作業系統自動切深色 | 一律明確寫 `data-theme` | 執行紀錄頁固定深色，其他頁固定淺色，不隨系統變 |

> token 命名的正式決定寫成 ADR（由架構 session 撰寫，見第 10 部分）；ADR 定案後，這一節以 ADR 為準。
>
> **已定案：ADR-0007**（`10-truth/adr/0007-design-token-naming.md`）。跟本節不同的四點以 ADR 為準：`--font-reading-serif` 沿用名字、改值（Loomy 原本只有中文明體）；黃色只當底色，「輪到你了」是黃底墨字（`--color-on-action`）；靛藍狀態的文字用 `--color-accent-mark-text`；「不跟系統自動切深色」本來就是 Loomy 的規定，所以不採用的 Loomy 規則是 4 條，不是 5 條。

### 設計 token

**基礎 token：用 Loomy 的名字**

| 用途 | Loomy token | 說明 |
| :--- | :--- | :--- |
| 頁面底色、面板底色 | `--color-bg-page`、`--color-bg-surface` | |
| 文字 | `--color-text-primary`、`--color-text-secondary`、`--color-text-tertiary` | 越後面越淡 |
| 主要動作與「現在在這裡」 | `--color-accent-action`（黃）、`--color-accent-action-line`（畫線、焦點框） | 全站只有這一個動作色 |
| AI 動過或還沒定案 | `--color-accent-mark`、`--color-accent-mark-text` | 靛藍 |
| 錯誤與擋下 | `--color-danger` | 範圍見上表第 2 條 |
| 字體 | `--font-reading-serif`（標題、文章預覽）；介面字體與等寬字體照 Loomy | 字型檔放本機，不從外部 CDN 載入 |
| 字級、間距、圓角 | 照 Loomy；字級拆成大小、行高、字重三個變數 | |

**語意 token（狀態）：自己的名字，指向 Loomy 顏色**

Loomy 沒有狀態 token，所以狀態保留這個專案自己的名字，定義成 Loomy 顏色的別名。每種狀態都是「顏色＋圖示＋文字」三件一組（原則：狀態不只用顏色）。

| Loomy 顏色 | 意思 | 狀態 token |
| :--- | :--- | :--- |
| 墨（`--color-text-primary`） | 確定的東西 | `--verdict-verified`、`--source-fetched`、`--license-allowed`、`--asset-approved`、`--run-done`、`--gate-pass` |
| 靛藍（`--color-accent-mark`） | AI 動過或還沒定案 | `--verdict-unverified`、`--verdict-inference`、`--verdict-contested`、`--source-grouped` |
| 黃（`--color-accent-action`） | 輪到你了 | `--run-waiting` |
| 紅（`--color-danger`） | 擋下、失敗 | `--gate-fail`、`--license-denied`、`--source-unavailable`、`--run-retryable` |
| 淡灰虛線（`--color-text-tertiary`） | 未知、還沒到 | `--verdict-unknown`、`--gate-pending`、`--asset-excluded`、`--source-unverifiable`、`--license-unknown` |

執行中、排隊中、部分完成、已否決、已取消這幾個 Run 狀態用文字與圖示區分，不另佔顏色（顏色留給「要不要你出手」）。

### 元件分層（Atoms → Templates）

元件做成 Jinja2 巨集，每個元件一個檔案，參數就是它的介面。

```
原子 (Atoms)      → Button（主要／次要／危險／安靜）、Badge、StatusIcon、IdChip（主張與來源的編號）、
                    Money、Timestamp、TextInput、Textarea、Select、Toggle、ProgressBar、Skeleton
分子 (Molecules)  → VerdictBadge、LicenseBadge、RunStatusPill、BudgetMeter、CitationMarker、
                    QuoteBlock（原文段落＋擷取時間）、SourceRow、PreflightItem、FormField
組織 (Organisms)  → GlobalNav、StageStepper、DecisionCard、RunList、QuestionCompare、
                    ThesisCompare（原作者立場／建議論點／競爭假說）、GateDecisionForm、
                    EvidenceMatrix、SourcePanel（右側原文面板）、ArticlePreviewFrame、
                    AssetTable、GateChecklist（G1～G5）、TraceTable
模板 (Templates)  → AppShell、RunWorkspaceLayout（階段導覽列＋內容＋可選右側面板）、
                    ReadingLayout（文章與原文）、FormLayout
```

**關鍵元件的約束**

| 元件 | 約束 | 為什麼 |
| :--- | :--- | :--- |
| VerdictBadge、LicenseBadge、RunStatusPill | 一定同時有圖示與文字；不能只傳顏色 | 「狀態不只用顏色」 |
| CitationMarker | 一定帶 claim_id 與 source_id；點擊打開 SourcePanel 並更新 URL | 「出處一鍵可達」、範本 17 那份 §8 |
| QuoteBlock | 一定顯示擷取時間；內容來自存檔，不即時抓網頁 | NFR-14 |
| GateDecisionForm | 核准與否決按鈕同等視覺份量；沒有預設選項；送出前不自動儲存成決定（只存草稿） | 「決定不催促」 |
| BudgetMeter | 顯示三種上限中最接近用完的那一種，展開看全部 | 「錢先講清楚」、NFR-04 |
| ArticlePreviewFrame | 文章在隔離框架內渲染（第 8 部分） | LLM 輸出是不可信內容 |

> 頁面（Pages）不在這份。哪些頁面存在、每頁放什麼，在範本 17 那份 §3、§6。

---

## 第 4 部分：效能策略

服務跑在本機，網路延遲可以忽略，瓶頸在伺服器渲染與資料量。

| 指標 | 目標 | 做法 |
| :--- | :--- | :--- |
| LCP | < 1.0 秒 | 伺服器渲染；字型本機子集化；首屏不等圖片 |
| INP | < 200 毫秒 | HTMX 局部更新；篩選與面板開合不整頁重載 |
| CLS | < 0.1 | 縮圖與圖片預留尺寸；狀態更新只換固定大小的區塊 |
| 伺服器渲染 | 一般頁 < 200 毫秒；證據矩陣（50 主張 × 30 來源）< 1 秒 | 矩陣分段載入；查詢只取畫面需要的欄位 |
| 狀態輪詢 | 每 3～5 秒一次，只在畫面上有執行中的 Run 時 | 只回傳狀態區塊的 HTML；讀 PostgreSQL 的 Run 狀態，不讀佇列（ADR-0006 §3.5）；Run 總覽一次查所有執行中的 Run，不逐筆輪詢 |

### 載入與執行時優化

- 頁面 HTML 控制在 200 KB 以內；證據矩陣超過時分段載入。
- 縮圖延遲載入；原圖只在使用者點開時載入。
- JavaScript 只有 HTMX 與少量自寫腳本，不做打包；HTMX 版本固定並放在 repo 內（不從 CDN 載入）。

---

## 第 5 部分：技術可用性

### 響應式斷點

桌機優先：核對證據需要「矩陣＋原文」兩欄並排。

| 名稱 | 寬度 | 版面 | 支援程度 |
| :--- | :--- | :--- | :--- |
| xl | ≥ 1200px | 內容＋右側原文面板並排 | 主要設計目標 |
| lg | ≥ 992px | 原文面板改成覆蓋式抽屜 | 完整支援 |
| md | ≥ 768px | 單欄；矩陣改成以主張為單位的清單 | 可做所有決定（選題、定題、核准素材） |
| sm、xs | < 768px | 單欄 | 看狀態、讀稿；證據矩陣顯示「請用較寬的螢幕核對」 |

### 無障礙

- WCAG 2.2 AA。
- 狀態不只用顏色（原則：狀態不只用顏色）；色彩對比一般文字 ≥ 4.5:1、大字與圖示 ≥ 3:1。
- 鍵盤可完成整條旅程；證據矩陣用方向鍵移動、Enter 打開原文面板、Esc 關閉並把焦點還給原本的格子。
- 執行中的狀態更新用 `aria-live="polite"` 播報，不搶焦點。
- 尊重 `prefers-reduced-motion`；不用動畫表達狀態。
- 側邊面板與決策卡打開時，焦點移入；關閉時回到觸發它的元素。

### 語言與時間

- 介面只有繁體中文；文章語言依每個 Run 的設定。
- 時間一律顯示絕對時間（`2026-10-10 14:32`，Asia/Taipei）並附相對時間；擷取時間、發布日期不能只寫「3 天前」，因為核對版本要看確切日期。
- 金額顯示到美分（`US$0.64`），token 與 credits 用千分位。

---

## 第 6 部分：工程化實踐

### 檔案組織

依架構草案 §1.3 的分層，Web UI 屬於 Interface 層，跟 `api/` 並列：

```
src/evidence_forge/
├── api/                    # JSON API（CLI 與外部用）— 架構那邊負責
└── web/                    # Web UI（這份負責）
    ├── routes/             # 頁面路由：一個階段一個檔案，只呼叫服務層
    ├── templates/
    │   ├── layouts/        # Templates：AppShell、RunWorkspaceLayout、ReadingLayout、FormLayout
    │   ├── components/     # Jinja2 巨集：atoms/、molecules/、organisms/
    │   └── pages/          # 每頁一個模板（頁面清單 → 範本 17 那份 §3）
    └── static/
        ├── css/            # tokens.css（設計 token）、base.css、components.css
        ├── js/             # htmx（固定版本）、少量自寫腳本
        ├── icons/          # SVG sprite（狀態圖示）
        └── fonts/          # 本機字型子集
tests/
└── web/                    # 頁面渲染測試、Playwright 旅程測試
```

### 程式碼品質

沿用 repo 現有的 ruff、mypy、pytest（`pyproject.toml`），不另加前端 linter。模板裡禁止對外部內容使用 `|safe`，由測試掃描模板確保（第 8 部分）。

### 測試策略

| 類型 | 工具 | 覆蓋目標 | 測什麼 |
| :--- | :--- | :--- | :--- |
| 頁面渲染 | pytest＋FastAPI TestClient | 13 頁 × 四種狀態 | 每種狀態渲染得出來，關鍵元素存在（例：Gate 頁沒有預設選項） |
| 元件 | pytest（直接渲染巨集） | 狀態類元件全部 | 每種狀態都有圖示與文字；外部內容被跳脫 |
| 旅程（E2E） | Playwright for Python | 主要旅程＋每個例外路徑 | 用錄好的回應重播（NFR-07、NFR-11），不打付費 API |
| 無障礙 | axe-core（在 Playwright 裡跑） | 13 頁 | WCAG 2.2 AA 自動檢測 |
| 安全 | pytest | 所有顯示外部內容的元件 | 注入 `<script>`、`javascript:` 連結、含腳本的 SVG，確認都被擋下 |

---

## 第 7 部分：前後端協作

### 通訊方式

- 頁面路由與 JSON API 都只呼叫服務層，不互相呼叫；同一個動作（例：送出 Gate 決定）只有一份業務邏輯。
- HTMX 請求回傳 HTML 片段；錯誤時回傳錯誤區塊的 HTML 與正確的 HTTP 狀態碼，不回傳 200 包錯誤。
- Run 狀態查詢要便宜（只讀 Metadata DB，不碰 checkpoint 全文），因為執行中會被輪詢。

### 沒有登入，但要防跨站請求

服務綁 `127.0.0.1`，只有本機能連，所以不做登入。但**創作者用同一個瀏覽器開的任何網站**，都可能對 `http://127.0.0.1:<port>` 送出表單——例如替他按下「核准論點」或「送網站草稿預覽」。所以：

- 所有會改變狀態的請求（POST）都檢查 CSRF token 與 `Origin` 標頭。
- 檢查 `Host` 標頭必須是 `127.0.0.1` 或 `localhost`，擋 DNS rebinding。
- Cookie 設 `SameSite=Strict`。

---

## 第 8 部分：監控與安全

### 不可信內容

這個工具會在畫面上顯示四種外部來源的內容：抓回來的網頁原文、LLM 的輸出（論點、主張、MDX 草稿）、搜尋結果、下載的圖片。它們全部當成不可信（PRD §5「從網頁抓回來的內容只當資料」）。

| 內容 | 風險 | 做法 |
| :--- | :--- | :--- |
| 網頁原文、搜尋結果、LLM 產生的文字 | XSS | Jinja2 自動跳脫；外部內容禁止 `|safe`；只顯示純文字與我們自己標的段落 |
| MDX 草稿的本機預覽 | 草稿裡可能夾帶 HTML 或 JSX | 在沒有 `allow-scripts` 的 `sandbox` iframe 裡渲染；只允許固定的幾個元件（Q-14） |
| 下載的圖片 | SVG 內含腳本；偽裝副檔名 | 一律用 `<img>` 顯示（不內嵌 SVG）；回應加 `X-Content-Type-Options: nosniff`；從本機存檔提供，不熱連結原網址 |
| 外部連結 | 反向控制原分頁 | `rel="noopener noreferrer"`、新分頁開啟 |

全站 Content Security Policy：`default-src 'self'`，不載入任何外部資源。

### 監控

- 頁面錯誤記在伺服器日誌，附 run_id。
- 範本 17 那份 §4 定義的業務動作（例：`gate.approved`、`claim.source_opened`）記錄在 Metadata DB，存在哪張表由架構那邊決定。
- 不送任何資料到外部分析服務。

---

## 第 9 部分：技術上線檢查清單

- [ ] mypy、ruff 無錯誤
- [ ] 13 頁 × 四種狀態的渲染測試通過
- [ ] 主要旅程與例外路徑的 Playwright 測試通過（重播錄好的回應）
- [ ] 斷點覆蓋第 5 部分四種版面
- [ ] axe-core 檢測 WCAG 2.2 AA 通過
- [ ] 第 4 部分的效能目標達標
- [ ] 第 7、8 部分的安全項目都有測試
- [ ] 沒有任何外部 CDN 或分析服務的請求
- [ ] Code Review 通過

---

## 第 10 部分：待決事項

延續 `2026-10-10-ux-persona-journey.md` 的 Q-08～Q-11 與 `2026-10-10-frontend-ia-draft.md` 的 Q-12～Q-13。

| ID | 問題 | 建議 | 要跟誰對齊 |
| :--- | :--- | :--- | :--- |
| Q-14 | 本機預覽怎麼渲染 MDX？MDX 原生要 JavaScript 執行環境，跟「不引入 Node」衝突 | 讓 Writer 只輸出受限的 MDX：Markdown 加上固定幾個元件（例：引用、圖片、推論標記）。本機預覽用 Python 的 Markdown 解析器處理這幾個元件；完全照網站樣式的預覽留給 R4 的網站草稿預覽 | 架構（Writer 輸出 schema）＋PRD FR-07、FR-09 |
| Q-15 | 網頁從哪一階段開始做？ | 兩案：(a) 照架構 Q-01，R1 用 CLI，R2 起做網頁（R1 的頁面一起補上）；(b) R1 就做最小網頁（Run 總覽、新研究、Run 工作區、研究提案），因為選題要並排比較 3～5 題，CLI 很難比。傾向 (b)，伺服器渲染的成本不高 | 創作者＋架構 Q-01 |
| Q-16 | Web UI 的程式放 `web/` 還是併進 `api/`？ | 放 `web/`，與 `api/` 並列，共用服務層 | 架構 §1.3 分層 |

> 設計 token 命名（第 3 部分的 Loomy 對照）創作者已於 2026-10-10 同意，由架構 session 寫成 ADR；ADR 編號定了之後在 `20-state/progress.md` 記一筆。
