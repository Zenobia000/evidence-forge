# 顆粒度：票怎麼切、審查分幾級、教訓怎麼留（業界對照）

> 2026-09-06 調研。配套：[家規第 38–41 條](README.md#第七章-流程法敏捷票務平行化)、[LOOP-REVIEW.md](LOOP-REVIEW.md)（審核環的業界對照）、[LESSONS.md](LESSONS.md)（傷疤帳本）。
> 數字凡標「二手」者，表示我讀到的是引用它的文章，不是原始報告；URL 已附供人工複核。這份文件自己就受 [L-003](LESSONS.md) 管。

## 一句話

**做不完的不是開發，是驗收。而驗收做不完的原因不是流程太嚴，是三件事沒有分級：票沒分粗細、審查沒分深淺、教訓沒分現役與退役。**

沒有分級的系統只能用最嚴的標準對待所有東西——於是每一張票都要人審批相依性、每一顆 PR 都要確認二十件事、每一次開工都要載入整本帳本。這不是紀律，這是**把確定性的工作交給了非確定性的東西**。

---

## 為什麼會走到這裡（有數字，不是感覺）

Faros AI 追 10,000 名開發者、1,255 個團隊的 telemetry（2025）：

| 指標 | 變化 |
| --- | --- |
| 完成任務數 | **+21%** |
| 合併 PR 數 | **+98%** |
| PR 審查時間 | **+91%** |
| 平均 PR 大小 | **+154%** |
| bug 數 | **+9%** |
| 組織層級 DORA 指標 | **沒有改善** |

（轉引自 [arXiv:2605.01160](https://arxiv.org/html/2605.01160v1)〈The Productivity-Reliability Paradox〉；原始為 Faros AI 2025 telemetry study。二手。）

2026 年的續作數字更難看：審查時間中位數 **+441%**、**31% 的 PR 零人類審查就合併**、AI 產出的 PR 在被人撿起來之前要等 **4.6 倍**久（但一旦開審，速度是人寫的碼的 2 倍——**瓶頸在排隊，不在審查本身**）。這些數字散在多篇部落格裡轉述 Google Cloud 2026 DORA AI ROI 報告，我沒讀到一手 PDF，**引用前要自己去核**。

一句話收攏（O'Reilly 2026 *Agentic Code Review*，二手）：

> *「瓶頸不再是你寫程式有多快。是一個被信任的人要多久才能對一次審查有把握。」*

機制上的原因是**生成可以平行，信任不能分片**——十個 agent 可以同時開十顆 PR，但「有把握」這件事沒辦法拆給十個人各做十分之一。

---

## 業界地圖：三個分級

### 分級一：票的形狀（垂直切片 vs 水平切片）

| 切法 | 一張票的內容 | 相依性 | 做完之後 |
| --- | --- | --- | --- |
| **水平（功能票）** | 一層的全部（所有 schema／所有 API／所有 UI） | 天然串行，層層卡 | 每一層都要等下一層才看得出對不對 |
| **垂直（tracer bullet）** | 一條穿過所有層的最窄的路 | 只剩真相依 | **當場可 demo，可獨立驗收** |

「tracer bullet」（曳光彈）出自《The Pragmatic Programmer》，「walking skeleton」（能走路的骨架）出自 Alistair Cockburn——都不是新詞，但 2026 年被重新拿來當**餵給 agent 的工作單位**：

- [InfoQ〈Skeleton Architecture〉](https://www.infoq.com/articles/skeleton-architecture/) 給了最有結構的版本：**人定「骨架」（抽象類別、介面、安全上下文、跨切面），AI 填「組織」（垂直切片裡的具體實作）**。理由是 context——分層架構下 AI 要理解一條流程得跨五個目錄撈檔案，垂直切片讓「一起改的東西住在一起」，一次讀得完。
- Matt Pocock 的 [`to-tickets` 技能](https://github.com/mattpocock/skills/blob/main/docs/engineering/to-tickets.md)是最接近可抄的實作，而且多了一個關鍵維度：每張票標 **HITL / AFK**——需要人類決策的 vs 可以無人值守做完的，並明文「**盡量做成 AFK**」。所有 blocker 都完成的票叫 **frontier**，可以被搶走平行做。
- 反例參照：把「相依性審批」交給人，等於**人親自當排程器**。票切得對的話，相依性大部分會自己消失，剩下的用 `blocked by` 讓機器排。

### 分級二：審查深度（風險分級路由）

業界收斂出的五層，人的注意力放在最後而不是最前：

| 層 | 誰做 | 內容 |
| --- | --- | --- |
| 1 | 機器（確定性） | lint、type、測試、SAST、secrets、**PR 大小上限**、擋刪測試 |
| 2 | 腳本／人，2 分鐘 | PR 描述有沒有對上票的 Done when（**先讀意圖，再讀 diff**） |
| 3 | 路由 | 按風險決定下面審多深 |
| 4 | AI 審查者 | **只報 P0/P1**，附證據 |
| 5 | 人 | 只看架構契合、業務邏輯、跨模組影響 |

風險分級的四級版本（[zylos.ai 治理整理](https://zylos.ai/research/2026-06-28-ai-agent-code-governance-branch-protection-review-gates/)，與 AWS Well-Architected agentic lens 的 autonomous／notify／approve 三級同構）：

- **T1 例行**：文件、格式、依賴升版、產生的碼 → CI 綠即自動合，設 hold 期讓人喊停
- **T2 標準**：既有模式內的功能、附回歸測試的修 bug → 人審一次
- **T3 敏感**：authn/authz、schema migration、API 介面、**hook 與 agent 設定本身** → 人審＋專門審
- **T4 例外**：核心架構、CI/CD 本身、分支保護規則 → 人自己動手

兩條配套鐵律：

1. **分級寫進 `CODEOWNERS`，不要寫進流程文件**——文件是勸告，`CODEOWNERS` 是閘門（家規第 29 條）。
2. **agent 的核可不計入 required approvals，只當資訊。**這條與本 repo 審核環「給判決不按合併鍵」的設計一致（[LOOP-REVIEW.md](LOOP-REVIEW.md)），不用改。

### 分級三：教訓的現役與退役（選擇性檢索）

- **好消息**：[arXiv:2607.13091](https://arxiv.org/html/2607.13091v1)〈Self-Improving AI Coding Agents Through Accumulated Behavioral Rules〉做的幾乎就是傷疤帳本——把每一條**被接受的審查意見**固化成版控裡的行為規則、提交前跑自我檢查清單、規則集自動驗證完整性。量到的結果：**被寫成規則的錯誤類別復發率 0%**，而且審查重心從低階正確性移向設計層。它也明確跟 Reflexion 劃界：Reflexion 的記憶是 episodic、跨不了 session；規則檔跨得了。
- **壞消息一，全量注入會失效**：[ERL（arXiv:2603.24639）](https://arxiv.org/html/2603.24639v2)在 Gaia2 的消融實驗結論是「**選擇性檢索是必要的**」，而且「heuristics 比 few-shot 軌跡更能遷移」。教訓要抽象成啟發式，並**按當下任務撈相關的那幾條**，不是每次全灌。
- **壞消息二，記憶要能淘汰**：[DeltaMem（arXiv:2606.03083）](https://arxiv.org/html/2606.03083v1)點名 ExpeL／AWM 這類「扁平的每任務規則檔」的問題就是線性增長。對應到帳本，就是需要一條退休規則：**已經被 hook 或測試硬擋住的教訓，該從人類提醒層下架**。
- 背景：[context rot](https://stackoverflow.blog/2026/08/21/dispatches-from-o-reilly-the-right-amount-of-spec-for-agentic-development/)——輸入變長，模型在簡單任務上都會變不可靠；更糟的是**規格、計畫、票、舊驗收條件混在一起之後，模型分不清哪一段還生效**，於是在互相矛盾的真相之間取平均。

---

## 規格什麼時候寫（不是「要不要」，是「什麼時候」）

「先 one-shot 出雛形、之後再正規化」這個做法成立，但成立的前提是**那段碼還丟得掉**。四個訊號任一亮起，它就丟不掉了，必須當場沉澱規格（[Augment Code 整理](https://www.augmentcode.com/guides/vibe-coding-vs-spec-driven-development)）：

1. **Context drift**：修一個 bug，弄壞三個它沒看到的檔案
2. **回歸模式**：新功能不再遵守既有設計模式
3. **人數 > 1**：不只一個人（或 agent）需要理解這段程式
4. **生產意圖**：有真的使用者或流程要依賴它

同一份整理引 Codebridge 給了一道**三個月牆**（二手，我沒去核原始資料）：1–3 月狂奔、4–9 月整合開始卡、10–15 月新功能要靠 debug 舊 AI 碼、16–18 月團隊看不懂自己的系統。

反方向的證據一樣要記住，**過度規格化會反噬**：

- Scott Logic 拿真專案跑完整 SDD 流程，結論是「約 10 倍慢、儀式更多、bug 數一樣」（[DZone 轉述](https://dzone.com/articles/spec-driven-development)，二手）。
- Kent Beck 的反對更根本：寫完整規格再實作，等於假設**你在實作過程中不會學到任何該改規格的東西**。
- Martin Fowler 2025-08 的實驗：即使給了詳細規格，agent 仍然「加了沒要求的功能、中途改了假設、在 build 失敗時回報成功」（[SDD 三工具評比](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)）。**規格解決「它不知道要做什麼」，不解決「它會亂講」**——後者只有確定性檢查解得掉。
- [arXiv:2602.00180](https://arxiv.org/html/2602.00180v1) 把過度規格化定義得很乾淨：**規格寫到像虛擬碼就過頭了**——那時候你已經在規定 how，而不是 what。

而 SDD 陣營自己承認解不掉的是 **spec drift（規格漂移）**：多個在生產環境跑這套的人描述規格在**幾天內**就漂掉，而主流框架的答案都是「自律」——自律正是每一次都失敗的那個東西。唯一撐得住的做法是**把規格變成可執行的檢查**：驗收條件寫成二元的，規格漂掉的時候是測試變紅，不是靠人發現。

---

## 對照結論：五個可以下一輪就導入的動作（按投報率排）

1. **票的形狀改成垂直切片，並標 HITL／AFK。**一張票＝一條穿過所有層、做完當場可 demo 的窄路，票裡宣告 `blocked by`。同時解掉「相依性要人審批」與「PR 太大人審不動」——垂直切片天然小。
2. **審查分四級，寫進 `CODEOWNERS`，不寫進流程文件。**審查者之所以「要確認很多事」，是因為它不知道**哪些可以不確認**。
3. **驗收條件一律二元。**「正確運作」→「`POST /x` 在 `state=failed` 回 202、`succeeded` 回 409、不存在回 404」。能寫成測試的寫成測試，寫不成的才留給人，並在票裡明標哪幾條留給人。
4. **確定性閘門先跑，AI 判斷後跑。**確定性檢查全綠才允許 AI 審查者開工（本 repo 現在是 `pnpm typecheck` 與 `.claude/hooks/test-guard-git.sh`）；紅的直接退回，**AI 一個 token 都不花**。
5. **帳本改成可檢索＋可退休。**訊號當檢索鍵、開工前只撈相關的兩三條、被 hook 或測試接管的條目下架。

---

## 失效模式帳（引用這份文件時要一起講的）

| 失效 | 描述 | 對策 |
| --- | --- | --- |
| 水平切片假裝成票 | 「做完 API 層」——沒有任何一刻可以驗收 | 票的驗收條件必須是「跑起來看得到」 |
| 分級變成寬鬆的藉口 | T1 一路擴張，最後什麼都是 T1 | 分不清時算高的那級；擴充授權要回去拿新的授權紀錄（第 28 條） |
| 二元條件寫成虛擬碼 | 規格開始規定 how，失去抽象價值 | 寫「什麼狀態下回什麼」，不寫「怎麼實作」 |
| 規格漂移 | 實作中學到的事沒回寫，規格幾天內就過期 | 驗收條件可執行——漂了會紅，不靠人發現 |
| 帳本膨脹成 context rot | 教訓越記越多，全量注入反而讓判斷變糊 | 選擇性檢索＋退休欄（ERL、DeltaMem） |
| 教訓寫下就當處理完 | 規則寫下的那一刻最不會被套用到手上這一句 | L-011：寫完規則的下一個動作是拿它掃這次的 diff |

---

## 來源

**數據與報告**
- Productivity-Reliability Paradox（含 Faros 2025 telemetry 轉引）：https://arxiv.org/html/2605.01160v1
- Faros AI DORA 對照（+98% PR／零改善）：https://agentmarketcap.ai/blog/2026/04/10/faros-ai-dora-metrics-coding-agents-2026
- 2026 DORA AI ROI 續作數字（+441%、31% 零審查、4.6× 排隊）：https://coommit.com/blog/ai-code-review-bottleneck-2026 ；https://codex.danielvaughan.com/2026/05/24/human-review-bottleneck-code-review-strategies-agent-output/ （**均為二手轉述，未取得一手報告**）
- 五層審查流水線的共識版本：https://blog.codacy.com/ai-breaking-code-review-how-engineering-teams-survive-pr-bottleneck ；https://screencli.sh/blog/ai-code-review-bottleneck
- 風險路由：https://moderne.ai/blog/ai-didnt-break-coding-it-broke-code-review

**票的形狀**
- Skeleton Architecture（骨架／組織）：https://www.infoq.com/articles/skeleton-architecture/
- tracer bullet 餵給 agent：https://www.aihero.dev/tracer-bullets
- `to-tickets` 技能（垂直切片＋HITL/AFK＋frontier）：https://github.com/mattpocock/skills/blob/main/docs/engineering/to-tickets.md
- 垂直切片作為 agent 的工作單位：https://www.codemag.com/Blog/AIPractitioner/AIAGSD6

**規格的量**
- 剛剛好的規格量／context rot：https://stackoverflow.blog/2026/08/21/dispatches-from-o-reilly-the-right-amount-of-spec-for-agentic-development/
- SDD 沒解決的問題（drift、Beck 的反對、Scott Logic 10 倍慢）：https://dzone.com/articles/spec-driven-development
- Fowler 的 SDD 三工具評比：https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- 過度規格化的定義：https://arxiv.org/html/2602.00180v1
- 流程框架分類與比較：https://arxiv.org/html/2606.04967v1
- 何時 vibe、何時 spec（四個轉換訊號、三個月牆）：https://www.augmentcode.com/guides/vibe-coding-vs-spec-driven-development
- 二元驗收條件的寫法：https://jakubkontra.com/en/blog/spec-driven-development-writing-specs-ai-agents-ship

**教訓的累積**
- 累積行為規則的閉環（復發率 0%）：https://arxiv.org/html/2607.13091v1
- ERL（選擇性檢索是必要的）：https://arxiv.org/html/2603.24639v2
- ExpeL：https://arxiv.org/html/2308.10144v3
- DeltaMem（扁平規則檔線性增長）：https://arxiv.org/html/2606.03083v1
- Agent 記憶綜述：https://arxiv.org/abs/2603.07670 ；https://arxiv.org/html/2605.06716v1

**授權與閘門**
- AI 產出碼的分支保護與四級風險：https://zylos.ai/research/2026-06-28-ai-agent-code-governance-branch-protection-review-gates/
- AWS Well-Architected agentic lens（autonomous／notify／approve）：https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel02-bp05.html
- 閘門要按 blast radius 與可逆性分，不按模型信心：https://www.conceptualise.de/en/blog/human-in-the-loop-agent-approval-gates

**考據聲明**：本文所有 Faros／DORA 數字均為二手轉述，一手報告未取得；Codebridge 的三個月牆、Scott Logic 的 10 倍慢同樣是轉述。arXiv 論文的結論句取自各文的 abstract 與結果段，未逐節複核實驗設計。
