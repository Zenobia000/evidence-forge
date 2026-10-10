# your-project

## 這個 repo 的狀態

<!-- 佔位：/init-from-template 會問你，然後換掉這一段。寫兩三句：這個專案在做什麼、現在走到哪、有什麼不能動的東西。 -->
（還沒填。）背景與方向見 `README.md`。開發的順序與停點見 `.claude/skills/PIPELINE.md`。

## Agent skills

### Issue tracker

GitHub Issues（`<owner>/<repo>`，`gh` CLI）。見 `docs/agents/issue-tracker.md`。

<!-- 選用：多台電腦或多個 session 輪流推這個 repo 時才留下面這一段；只有一條線就刪掉，連同 docs/agents/relay.md。 -->
### 多台電腦／多 session 接力

**開工前先讀 `docs/agents/relay.md`**：讀接力日誌的最新換班留言、`gh pr list`、`gh issue list --label in-progress`、`git branch -r`，有人在做的票不碰；下班前推完所有東西、在接力日誌留言。開發班派票，審查班只審不派。

### Domain docs

領域語言在根目錄 `CONTEXT.md`，決策在 `docs/10-truth/adr/`。技能產出全部進版控。
新的實體長出來之後，先填 `CONTEXT.md` 再寫程式。single-context、shared，見 `docs/agents/domain.md`。
文件分層與「一件事只有一個家」見 `docs/README.md`。

### Comment language

程式註解一律使用繁體中文。

### 派子代理

子代理只准用三格：**格 1 便宜快、格 2 強、格 3 最強**，角色檔在 `.claude/agents/`（`builder-sonnet-high`、`builder-opus-high`、`builder-opus-xhigh`）。
從推得動那張票的最便宜一格開始，撞牆才升級，每張票記帳。
規則是家規第 43 條，起始格、升級訊號與帳本見 `Ai-coding-principle/MODEL-ROUTING.md`。
派會跑測試的子代理之前先量資源（`PIPELINE.md`〈派工前的資源盤點〉）。

### 人語層（常駐輸出規則）

寫給人看的東西（聊天、commit、PR、文件、交接、票）：先講為什麼，再講做了什麼；用平常說話的字，高中生看得懂；專有名詞用圈子裡真的有人在用的，查不到使用鏈就不要發明。各場合該答到的問題見 `.claude/skills/human-layer/SKILL.md`。**PR 的格式不是形式自由**：五節、英文、順序固定，規定的唯一一份也在那裡（`.github/PULL_REQUEST_TEMPLATE.md` 是它的展示）。其餘場合方向對了就自由發揮。

掃讀記號，每次都要：回報第一行**粗體結論**；踩到帳本（`Ai-coding-principle/LESSONS.md`）記過的坑用 `⚠ 重複踩點：` 開頭並記累犯；數字、檔名、指令用 code span。偶爾在結尾附一行 `💡` 小知識（跟當下工作有關才給、來源可查、一次最多一條）。

`💡` 之外還有 `🎴` 提取卡：`💡` 是**這次**碰到的知識，讀完就算；`🎴` 是**下次還會遇到**的訊號，進 `Ai-coding-principle/PLAYBOOK.md`（人的組塊庫，與 `LESSONS.md` 對稱——那本記 AI 漏接了什麼，這本記人該調用什麼）。撞到既有卡的訊號時**先問再給答案**，一次對話最多問一次、問在做完之後。規則見 `PLAYBOOK.md`，卡的樣子見 human-layer 技能。

使用者手動開 caveman 時聊天讓位；寫進檔案的東西照舊走人語層。
