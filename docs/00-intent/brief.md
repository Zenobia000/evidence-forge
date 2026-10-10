# Evidence-led Editorial Studio — 專案簡報

> 依 `docs/templates/vibecoding/02_project_brief_and_prd.md` 的簡報半部撰寫。需求本身在 `10-truth/requirements/prd.md`，階段在 `roadmap.md`，這份只講為什麼做、做給誰、什麼算成功。
> 原始版本：`30-snapshot/2026-10-10-brief-v1.0.md`、`30-snapshot/2026-10-10-prd-roadmap-v1.0.md` §1、§2、§10。

## 1. 專案總覽

| 項目 | 內容 |
| :--- | :--- |
| 專案名稱 | Evidence-led Editorial Studio（repo：`evidence-forge`） |
| 一句話 | 輸入一篇技術文章，核准研究切角之後，拿到可追溯的證據、圖文素材，和網站能直接預覽的 MDX 草稿。 |
| 使用者 | 一個人的內容工作室：技術作者、產品經理、資料科學與 AI 工程工作者。會自己審來源、也要對論點負責。 |
| 部署形態 | 雲地混合：本機 3090 跑 Qwen，雲端呼叫 Anthropic API 與 Tavily。 |

## 2. 為什麼做

**痛點。** 寫一篇有根據的技術觀點文章，最花時間的是追 References、找反證、整理圖和引用。直接叫 AI 改寫又有三個問題：照搬原作者的框架、編出不存在的來源、用了沒授權的圖。

**定位。** 不是「文章改寫器」，是個人研究工作室，順序固定：先追來源做研究 → 人決定立場 → 再依證據寫。產品要守住的是**人的立場決定權**：AI 負責找證據和提案，論點由創作者拍板。

**參考脈絡。** 流程設計參考了 VK 公開課綱（產業研究、學術研究、內容創作）的步驟名稱；課程內文與專屬提示詞沒有取得，只用公開大綱做概念對照。課程宣傳的「70% AI／30% 個人觀點」不是可驗證的比例，這裡改成「立場由人決定」這條可檢查的規則。

## 3. 核心工作（JTBD）

> 我丟進一篇種子文章，很快拿到研究提案和可引用的證據；選定自己的立場之後，拿到圖文整合的草稿。

## 4. 什麼算成功

以下都是**要驗證的門檻**，還沒有實測結果。驗證方式與時間點見 `roadmap.md` 的 R5。

| 指標 | 門檻 |
| :--- | :--- |
| 測試集 | 20 篇種子文章、5 類主題 |
| 核心主張可定位到來源原文 | 100% |
| 授權不明的素材被公開使用 | 0 件 |
| 中斷後可恢復的流程（故障注入測試） | 100% |
| 創作者能在定題 Gate 改選觀點 | 每篇都能 |
| 研究新穎性與反方覆蓋（技術作者盲評） | ≥ 4/5 |
| 每篇成本 | 可計量，且落在每次 Run 設定的上限內；US$0.64 是試算基準，不是保證（見 §5） |

任何一項沒過，都不能自動公開發布。

## 5. 要驗證的假設

### 5.1 成本假設

單價以 2026-10-10 官方頁面為準（已核實，來源見 §6）：Opus 5.5 每百萬 tokens 輸入 US$4／輸出 US$20；Sonnet 5.5 輸入 US$2／輸出 US$10；Tavily 每 credit US$0.008，免費方案每月 1,000 credits。

| 階段 | 每篇假設用量 | 成本 |
| :--- | :--- | :--- |
| Tavily 搜尋＋擷取 | 18 credits | US$0.144（未扣免費額度） |
| Opus Director＋Reviewer | 輸入 35,000＋輸出 7,000 tokens | US$0.280 |
| Sonnet Outline＋Writer | 輸入 50,000＋輸出 12,000 tokens | US$0.220 |
| 本機 Qwen3.5 9B | 無按量 API 費，電費另計 | US$0 |
| **合計** | 假設沒有重試、沒有額外工具費 | **US$0.644／篇** |

- 每月 30 篇：Tavily 540 credits，在免費額度內；雲端 LLM 約 US$15。
- 每月 100 篇：Tavily 超出免費額度 800 credits；總額約 US$56.40。
- 18 credits 是假設。Tavily 的實際計價是：基本搜尋 1 credit、進階搜尋 2 credits；擷取每成功 5 個 URL 算 1 credit（進階 2 credits），失敗的不收。實際用量在 R2 搜尋功能做好後實測（`roadmap.md` R2）。
- 沒算進去的：重試、Opus 的延伸思考 tokens、圖片擷取、人工編修時間。這份假設**不是成本上限**，上限由每次執行的預算設定擋（PRD NFR-04）。
- 沒算進去的省錢機制：跨 Run 快取（NFR-02）、prompt caching（Opus／Sonnet 5.5 快取命中只算基本輸入價的 0.05 倍）、Batch API 半價（NFR-05）。實際成本應該比試算低，R2 實測後再更新。

### 5.2 模型分工假設

Opus 只在「研究規劃」與「證據複核」兩個節點出場，假設這樣比全程用便宜模型品質明顯好、又比全程用 Opus 便宜。用三組比較，同一批 20 篇種子、同樣的搜尋預算與資料快照：

| 組 | 組合 |
| :--- | :--- |
| A | Qwen 規劃＋Sonnet 寫 |
| B | Qwen 產問題＋Opus 規劃與複核＋Sonnet 寫（目前的設計） |
| C | Opus 全程研究＋Sonnet 寫 |

量的是：引用支持率、研究問題跟原文的獨立程度、反證覆蓋、人工修改分鐘數、總費用。**只有 B 明顯勝過 A，才繼續用 Opus 的兩個節點**（決策見 ADR-0002）。

**實驗本身的成本**：20 篇 × 3 組各自搜尋，約 1,080 credits，會超過 Tavily 每月 1,000 的免費額度。所以三組共用同一份凍結的證據包，搜尋只做一次（PRD NFR-06）；LLM 呼叫走 Batch API 半價。

## 6. 參考來源

- Claude 定價：<https://platform.claude.com/docs/en/about-claude/pricing>
- Tavily 價格：<https://www.tavily.com/pricing>；計價規則：<https://docs.tavily.com/documentation/api-credits>
- VK 公開課綱：<https://vktechread.kaik.io/courses/nblm-industry>、<https://vktechread.kaik.io/courses/nblm-academic>、<https://vktechread.kaik.io/courses/nblm-content>
