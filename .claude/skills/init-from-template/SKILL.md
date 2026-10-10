---
name: init-from-template
description: 新專案從這個範本開出來之後，第一次打開時跑一次——問專案名、GitHub repo、分支模型、本機檢查指令、高風險路徑、要不要接力日誌，替換佔位、寫好守衛設定、檢查 jq 與 gh、（問過才）建標籤，最後列出下一步。
disable-model-invocation: true
---

# Init from template

這個 repo 是從範本開出來的，裡面有幾個佔位（`your-project`、`<owner>/<repo>`）和一份只有範例的守衛設定。這支技能把它們換成這個專案的真東西。**只跑一次**；跑過之後要改，直接改檔案。

為什麼要問而不是猜：每一個答案都會寫進守衛或常駐指示，猜錯的代價是守衛擋錯東西、或該擋的沒擋——而守衛沒擋的時候不會出聲（`Ai-coding-principle/LESSONS.md` L-007）。

## 0. 先看狀態

讀，不要改：

- `git remote -v`、`git branch -a`——有沒有 GitHub remote？已經有 `dev` 分支嗎？
- `grep -n 'your-project\|<owner>/<repo>' pyproject.toml CLAUDE.md README.md .env.example docs/agents/issue-tracker.md`——第 4 步要換的那幾個檔還剩哪些佔位（只查這幾個：帳本與技能裡有合法的 `<owner>/<repo>` 範例，全 repo 搜永遠不會歸零）。一個都沒有的話，這支技能可能跑過了：說出來，問要不要繼續。
- `.claude/hooks/human-only-paths.conf`——除了 `#ex ` 範例之外有沒有生效的行。

## 1. 檢查工具（先擋，後問）

逐一跑，把結果原樣貼給使用者（缺 `jq` 的話，連這幾行都會被守衛擋下來——擋的訊息本身就是答案：請使用者在自己的終端機跑 `command -v jq` 確認）：

```bash
command -v jq && jq --version
command -v gh && gh --version | head -1
gh auth status
command -v uv && uv --version
cat .python-version && uv python find
```

- **缺 `jq`**：守衛 `guard-git.sh` 會擋下每一個 shell 指令（刻意的——沒有 jq 它讀不到指令，會靜靜全部放行）。請使用者自己裝（例：`sudo apt install jq`、`brew install jq`、`winget install jqlang.jq`），裝好再繼續。你不能替他裝：那一步被守衛擋住，而且裝系統套件是他的決定。
- **缺 `gh` 或 `gh auth status` 沒過**：請使用者裝好並 `gh auth login`。沒有它，議題追蹤、合併閘、標籤都動不了。
- 沒有 uv：請他照官方說明裝（`curl -LsSf https://astral.sh/uv/install.sh | sh`）。找不到 `.python-version` 指定的 Python 不用擋，`uv sync` 會自己下載。

工具沒齊不往下走。

## 2. 問（一次一題，每題先給建議答案）

照順序問，每題附**建議答案**讓他一個字就能接受。探查已經答得出來的題目，直接說「我看到的是 X，就用它」，不再問。

1. **專案名**：給 `pyproject.toml` 的 `[project] name`（小寫、連字號）與 `CLAUDE.md`／`README.md` 的標題。建議：repo 目錄名。
2. **GitHub owner/repo**：建議：從 `git remote get-url origin` 解出來的那一個。沒有 remote 就請他給，並提醒 `git remote add origin …` 由他做。
3. **分支模型**：
   - **單線**（只有 `main`；PR 直接進 `main`）——建議給一個人、一條線的專案。
   - **main＋dev**（PR 進 `dev`，定期用 merge commit 發 `main`）——多個 session 並行、要有一條整合線時用。見 `Ai-coding-principle/GIT-FLOW.md` 與 `.claude/skills/PIPELINE.md`〈審查與合併〉。
4. **本機檢查指令**：CI 停擺時合併閘在本機重跑的那一組。建議：`uv run pytest && uv run mypy && uv run ruff check .`（這個 repo 的預設；`guard-git.sh` 寫死的 pnpm 版在這裡不適用）。專案加了別的必跑檢查（例：格式、產生檔比對）就接上去。
5. **高風險路徑**：這個專案弄錯了會壞使用者的資料、或牽涉安全、金錢、權限的地基在哪？每一條會變成合併閘的 T3（無論檢查多綠都由人按）。給他看 `human-only-paths.conf` 裡的 `#ex ` 範例；路徑還沒建立也可以先寫位置無關的樣式（例：`*/migrations/*`）。**寧可多擋**：漏一條的代價遠高於多按幾次。他說「還不知道」也是合法答案——記下來，告訴他之後在哪裡加。
6. **要不要接力日誌**：會不會有兩台電腦、或同一台的幾個 session 輪流推這個 repo？
   - 要：保留 `docs/agents/relay.md` 與 `CLAUDE.md` 那一段，等一下建 `relay` 標籤與接力日誌票。
   - 不要：刪掉 `docs/agents/relay.md` 和 `CLAUDE.md` 裡「多台電腦／多 session 接力」那一段。

## 3. 給他看草稿，確認了才寫

把每一個要改的檔、改成什麼，列成一張表給他看（檔名、改前、改後）。他可以逐項改。**確認之前一個字都不寫。**

## 4. 寫

逐項替換，替換完重跑第 0 步那條 grep，確認佔位一個都不剩（剩下的說出來是哪幾個、為什麼留）：

| 檔 | 換什麼 |
| --- | --- |
| `pyproject.toml` | `[project] name` ← 專案名 |
| `CLAUDE.md` | 標題 ← 專案名；〈這個 repo 的狀態〉那段佔位 ← 請他講兩三句（在做什麼、現在走到哪、有什麼不能動），講不出來就留「還沒填」；`<owner>/<repo>`；接力那一段照第 6 題留或刪 |
| `README.md` | 標題與「這個範本是什麼」那節 ← 這個專案是什麼（一兩句，請他講）；〈快速開始〉第 2 步 `/init-from-template` 那句改成「已初始化」。最上面指向 `docs/history/` 的那一行留著——那是這套工作方式的來歷 |
| `.env.example` | 開頭的 `your-project` ← 專案名 |
| `docs/agents/issue-tracker.md` | 所有 `<owner>/<repo>` |
| `.claude/hooks/human-only-paths.conf` | 在範例下面加上第 5 題的每一條（一行一個）；`check:` 行 ← 第 4 題。範例的 `#ex ` 行**留著**——`test-guard-git.sh` 拿它們驗 conf 的格式 |
| `.github/workflows/ci.yml` | 單線的話，`branches: [main, dev]` 改成 `[main]`，並刪掉那行「單線就刪 dev」的註解 |

寫完跑一次，貼實際輸出：

```bash
uv sync
uv run mypy && uv run ruff check . && uv run pytest
bash .claude/hooks/test-guard-git.sh | tail -3
```

`test-guard-git.sh` 在沒給 `SAMPLE_PR`、或 CI 上 `gh` 沒登入時會有幾發 `skip`，那是預期的；`fail` 必須是 0。

## 5. 標籤與接力日誌（問過才建）

先列出要建的，問他「要建嗎」，**他說要才跑**：

```bash
gh label create in-progress --description "有人在做，別碰" --color FBCA04 --repo <owner>/<repo>
gh label create ticket      --description "切出來的票"     --color 0E8A16 --repo <owner>/<repo>
gh label create spec        --description "規格"           --color 1D76DB --repo <owner>/<repo>
gh label create relay       --description "接力日誌"       --color 5319E7 --repo <owner>/<repo>   # 只有第 6 題答要
```

標籤已經存在時 `gh label create` 會失敗——那不是錯，說「已經有了」就好。

要接力日誌的話，問過之後開一張 pinned issue：

```bash
gh issue create --repo <owner>/<repo> --title "接力日誌" --label relay --body-file <暫存檔>
gh issue pin <號碼> --repo <owner>/<repo>
```

內文寫 `docs/agents/relay.md` 的「換班留言三段」。開好之後把號碼填進 `docs/agents/relay.md` 的 `#<接力日誌票號>`。

## 6. 不做的事

- **不 commit、不 push、不開 PR。** 這次改的檔裡有 `CLAUDE.md`、`.claude/hooks/*` 這些 T3 路徑，要不要提交、怎麼提交是他的按鈕。告訴他改了哪些檔，建議一個 commit 訊息（例：`chore: initialize from template`）。
- **不改守衛的程式**（`guard-git.sh`、`guard-secrets.sh`、`test-guard-git.sh`）。專案自己的東西只進 `human-only-paths.conf`。
- **不建 GitHub repo、不設 branch protection、不設 secrets。** 那些是他在 GitHub 上的決定；要的話建議他用 `/wizard`。
- 不刪 `docs/history/`、`Ai-coding-principle/` 的任何一份。

## 7. 列出下一步

最後一則訊息，第一行粗體結論（「初始化完成」或「卡在哪一步」），然後：

1. 這次改了哪些檔（一行一個）、還剩哪些佔位。
2. 他要按的：提交這次的改動（建議的 commit 訊息）；推上去之後確認 Actions 跑綠。
3. 接下來：
   - 跑 `/setup-skills` 核對議題追蹤器與守衛（它會比對 `.claude/hooks/` 跟範本版是否一致）。
   - 把第一件想做的事講出來，`/to-spec` 收成規格，`/to-tickets` 切票；順序與停點見 `.claude/skills/PIPELINE.md`。
   - 第一個實體出現時先填 `CONTEXT.md`；寫出第一個真的模組時刪掉 `src/evidence_forge/__init__.py` 的 `greet` 與 `tests/test_greet.py`。
   - 第 5 題答「還不知道」的話：高風險路徑之後加在 `.claude/hooks/human-only-paths.conf`。
