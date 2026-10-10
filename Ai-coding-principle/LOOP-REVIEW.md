# 用迴圈驗收：審寫分離的排程審查方法論（業界對照）

> 2026-09-05 調研。配套：[家規第 28、29 條](README.md)。
> （原本還指向「三環一真相」，2026-09-11 的重置移除了它：`git show main:docs/10-truth/guides/three-loops.md`。）
> 引句凡標「二手轉錄」者，表示原始 X 貼文無法直接開啟，引文來自可交叉比對的快取，URL 已附供人工複核。

## 一句話

**「用 /loop 定期審 PR、給判決但不按合併鍵」不是初級技巧——它是 2026 年社群正在收斂的終點形態：寫的迴圈大家都會了，稀缺的是獨立的「驗的迴圈」。**

你覺得「網路上沒人把這件事講清楚」的直覺是對的：這套做法散落在十幾個來源裡，各講一角，沒有一篇合訂。這份文件就是那篇合訂。

---

## 為什麼審和寫必須分開（不是風格偏好，是有研究的）

三個獨立來源指向同一件事：**做事的 agent 不能當自己的驗收者**。

1. **自我偏好偏誤**：研究顯示 AI 對自己的程式碼做通過／不通過判定時，會進入「rubber-stamp regime」——接受率上升、實際正確率下降。結論：驗證必須是 exogenous（外生的），不能與 maker 耦合。（HN: When AI writes the software, who verifies it?）
2. **Anthropic 官方原文**（Claude Code Best Practices）：「*A fresh context improves code review since Claude won't be biased toward code it just wrote.*」官方 subagents 文章補充：全新 context 的審查者「*doesn't inherit the assumptions, context, or blind spots from the primary conversation*」。
3. **銀行業類比**（Augment Code）：maker-checker 制度。同一人不能既開票又核銷。

這正是你在 three-loops 裡寫的那句話的業界版：「它寫的測試會證明它寫的程式碼」。

---

## 業界地圖：五個流派

| 流派 | 代表 | 誰驗收 | 觸發方式 | 驗收後的權力 |
|---|---|---|---|---|
| 1. Ralph／loop engineering | Geoffrey Huntley、官方 ralph-wiggum plugin | **同一個 agent 自己驗**（測試＋型別當 backpressure） | while true 迴圈 | 自己宣告完成 |
| 2. Writer／Reviewer 分離 | Anthropic 官方 best practices | 另開 session 的 Claude | 同一工作流內串行 | 回饋給 writer 改 |
| 3. 事件驅動產品 reviewer | CodeRabbit、Greptile、Graphite、Copilot、OpenAI Codex | 獨立審查服務 | PR 開啟／新 push | 只留評論，不擋合併 |
| 4. **排程 reviewer（你的流派）** | Boris Cherny 的 /loop、Hermes 24/7 agent、ChatPRD 每日 routine | 獨立時間軸上的排程 agent | cron／輪詢 | **給判決，人按合併** |
| 5. CI gate | LLM-as-judge eval gates | CI 裡的 judge | 每次 push | **有阻擋權**（分數低直接擋 merge） |

流派 1→5 是一條光譜：驗收者從「就是自己」走到「有權擋你」。你的做法落在 4，是刻意停在「判決與執行分離」的位置——與 GitHub Copilot 官方設計相同（Copilot 只留 review comment，明確不是 merge gate；要擋要另配 branch protection）。

---

## Boris 本人就在做你做的事

你記得沒錯。Claude Code 共同創作者 Boris Cherny 公開過他的常駐迴圈清單（二手轉錄，彙整站逐條連回原貼文）：

```
/loop 5m  /babysit            # 自動回應 code review、rebase、看護 PR
/loop 30m /slack-feedback     # 每 30 分鐘把 Slack 回饋開成 PR
/loop     /post-merge-sweeper # 補漏掉的 review comments
/loop 1h  /pr-pruner          # 關掉過期與多餘的 PR
```

他的三段口述定調了這個方法論：

- `/loop` 發布貼文的官方範例就是「*babysit all my PRs. Auto-fix build issues…*」。
- 「*Self-verification is a key ingredient that enables the model to run for much longer… so you can do more without having to constantly check in.*」
- 「*I don't prompt Claude anymore. I have loops that are running… My job is to write loops.*」——工作型態從下 prompt 變成寫迴圈。

官方文件同方向：scheduled-tasks 文件明文寫「*babysit a PR*」是 /loop 的用途；headless 文件寫「*You can wrap a non-interactive call in a script to use Claude as a project-specific linter or reviewer*」；Anthropic 內部用 GitHub Action 跑「智慧 lint」自動 commit 回 PR。

注意一個區別：Boris 的 `/babysit` 是 **writer 側**迴圈（吃審查意見去修自己的 PR），你的審核環是 **reviewer 側**迴圈（審別人做完的東西）。兩側合起來才是完整架構——社群裡的 babysit-pr 技能（每 3 分鐘輪詢 review comments 自動修，但明文「不准 resolve review threads，那是人類 reviewer 的事」）是前者；Hermes 的 24/7 審查 agent 是後者。

---

## 與你做法一比一的同行

最接近的公開實作是一個 24/7 自主 PR 審查 agent（dev.to 個人實錄，跨 22 個 repos）：

> cron 每 15 分鐘 → 一次批撈所有 open PRs → **按 head SHA 過濾已審過的** → 平行抓 diff → 審安全／品質／測試 → 發 review（approve／changes／comment）→ **不合併** → state 檔記錄已審。

ChatPRD 的每日 routine 版本：每天 10:15 找開超過 12 小時的 PR、評估可合併度、能推進的開 thread、需要人的發 Slack——不自己合併。

Hermes 的 cron 審查模式補了一條你的 three-loops 已經寫過的鐵律的同款表述：「**每次 run 是全新 session，prompt 必須自包含**」＝你的「無記憶。判斷只根據這一 tick 讀到的真相」。

---

## 對照結論：三個確認、五個可抄的強化

### 你的設計被業界確認的三點

1. **verdict-only（給判決不按鍵）是光譜上的合理保守端**——Copilot、Graphite、CodeRabbit 全都停在這裡；只有 CI-gate 流派往前一步，而且那一步的前提是「先建立信任校準」（Augment：先 advisory mode，信任夠了才升 blocking）。
2. **無記憶、冪等、寫回真相**——你 three-loops 的四條 tick 性質，與 Hermes 的設計原則逐條對得上。
3. **人保留合併鍵、用 hook 硬擋**——官方 best practices 同款：「*Unlike CLAUDE.md instructions which are advisory, hooks are deterministic.*」

### 可抄進你審核環的五個強化

1. **SHA 水位線去重**（CodeRabbit／Hermes）：記錄每個 PR 上次審過的 head SHA，沒有新 commit 就跳過。你現在的「對每一顆沒有我留言過的 PR」只防重複審 PR，不防「審過之後又 push 了」的增量情境。
2. **Gate first, judge second**（LLM-as-judge 實務共識）：確定性檢查（測試、lint、擋刪測試的 regex）先跑，AI 判斷放在綠燈之後——AI 的非確定性不該浪費在機器就能判的事上。這也是家規第 29 條「不聽陳述，看證據」的排程版。
3. **Reviewer 換模型家族**（Augment）：maker 和 checker 用同家族模型會有 correlated blind spots——同一個訓練資料教出來的盲點，兩個都看不見。
4. **噪音控制**（Codex／Sean Goedecke）：只報 P0/P1。官方自己也告誡：「*A reviewer prompted to find gaps will usually report some, even when the work is sound.*」——被要求挑毛病的審查者一定挑得出毛病，全追會過度工程。
5. **上線前手動跑一次看實際輸出**（Hermes）：「*verify the actual message… rather than trusting a green schedule row*」——排程顯示綠色不等於輸出是對的。

### 順帶：一個你已經比大多數人強的地方

Greptile 的賣點是「先把 repo 索引成圖，審 diff 時能抓到 PR 沒碰到的檔案裡被打破的假設」。你的審核環讀「票的 Done when」再驗證，已經有一半這個能力；缺的是跨檔案影響面——若要補，讓 reviewer 在 worktree 裡跑測試（你已經在做）比建圖便宜得多。

---

## 失效模式帳（引用時要一起講的）

| 失效 | 描述 | 對策 |
|---|---|---|
| Reviewer rubber-stamp | AI 審自己或同家族的碼會過度寬容 | exogenous verifier＋換模型家族 |
| 人退化成橡皮圖章 | 「Copilot 寫→CodeRabbit 審→人掃摘要→LGTM」鏈；研究指 40% AI 程式含漏洞 | 人審抽查不可廢；家規第 16 條的理解底線 |
| 噪音淹沒訊號 | 一堆命名意見埋掉真正的功能性問題 | 只報 P0/P1、限制評論量 |
| Doom loop | 壞 code 觸發無限「修壞」循環 | max-iterations 上限＋斷路器（家規第 29 條） |
| Gaming weak checks | 迴圈分不清「解掉問題」和「玩弄弱檢查」 | 檢查本身要被審——mutation testing 的存在理由（家規第 23 條） |
| 成本爆炸 | 有企業四個月燒完年度 AI 預算 | 記帳＋noop 原則：沒事不動（three-loops 已有） |

paddo.dev 的結語值得整段留下：「*The loop got a respectable name this month. It did not get a conscience. That part is still your job.*」——迴圈這個月得到了體面的名字（loop engineering），但它沒有得到良知。那部分仍然是你的工作。

---

## 來源

**官方一線**
- Claude Code Best Practices（原版 2025-04，現行改寫版）：https://code.claude.com/docs/en/best-practices ；原版 PDF 鏡像：https://www.rivista.ai/wp-content/uploads/2025/05/1746131911005.pdf
- scheduled tasks／headless／GitHub Actions／Code Review／hooks：https://code.claude.com/docs/en/scheduled-tasks 等同站文件
- Building agents with the Claude Agent SDK（gather→act→verify→repeat；LLM-as-judge 列次選）：https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- Effective harnesses for long-running agents：https://anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Boris Cherny：/loop 發布 https://x.com/bcherny/status/2030193932404150413 ；self-verification https://x.com/bcherny/status/2064426115255730578 ；automate entire classes of work https://x.com/bcherny/status/2077929390806073807 （均二手轉錄）；Latent Space 訪談 https://www.latent.space/p/claude-code ；迴圈清單彙整 https://howborisusesclaudecode.com/

**社群與研究**
- Ralph：https://ghuntley.com/ralph/ ；官方 plugin https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/README.md
- loop engineering 正名與批評：https://paddo.dev/blog/loop-engineering-rename-and-invoice
- 驗證成本分級（supervisor pattern）：https://blakecrosley.com/blog/loops-win-where-verification-is-cheap
- Adversarial review 指南：https://www.augmentcode.com/guides/adversarial-code-review ；學術版 https://arxiv.org/abs/2608.18167
- 24/7 排程審查實作：https://dev.to/hongphuc5497/i-built-an-autonomous-pr-review-agent-that-runs-247-on-my-github-38l9 ；https://www.chatprd.ai/how-i-ai/workflows/automate-daily-pull-request-reviews-with-a-claude-code-agent
- 產品文件：CodeRabbit https://docs.coderabbit.ai/configuration/auto-review ；Greptile https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context ；Copilot https://docs.github.com/en/copilot/concepts/agents/code-review ；Codex https://openai.com/index/introducing-upgrades-to-codex/
- 失效模式：rubber-stamp 研究討論 https://news.ycombinator.com/item?id=47234917 ；噪音 https://www.seangoedecke.com/ai-agents-and-code-review/ ；泡沫 https://news.ycombinator.com/item?id=46766961

**考據聲明**：Boris 的 X 引句為搜尋快取二手轉錄；原版 best-practices 的作者掛名未能從一手來源確認（原網址已轉址改寫），引文經 PDF 鏡像＋掃描版雙重比對。
