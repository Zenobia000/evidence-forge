> 這套工作方式是怎麼長出來的、每一條規則背後出過什麼事：[`docs/history/how-this-workflow-grew.md`](docs/history/how-this-workflow-grew.md)

# AI coding 工作流範本（Claude Code ＋ GitHub ＋ Python/uv）

## 這個範本是什麼

讓 Claude Code 在一個 repo 裡**能放手做、又不會把東西弄壞**的一整套工作方式：家規、守衛（hook）、技能、文件分層、帳本，加上一副能跑的 Python 骨架。

它不是事先設計的。前一個專案用 Claude Code 開發了五週，每出一次事就補一條規則；這裡把「工作方式」從產品裡拆出來，產品程式全部拿掉。所以每一條規則都有出處——查不到為什麼的時候，看最上面那份長文，或 `Ai-coding-principle/LESSONS.md` 對應的條目。

範本原本服務的組合是 **Claude Code ＋ GitHub（Issues、PR、Actions）＋ pnpm／TypeScript**；這個 repo 把技術棧換成 **Python／uv**（pytest、mypy、ruff），CI 與合併閘的本機檢查（`human-only-paths.conf` 的 `check:`）已跟著改。`guard-git.sh` 寫死的預設檢查仍是 pnpm 版，所以 `check:` 那一行不能刪。

## 快速開始

需要：uv（Python 3.12 由 `.python-version` 釘住，`uv sync` 找不到時會自己下載）、`git`、`jq`、`gh`（並已 `gh auth login`）。**沒有 `jq` 的話守衛會擋下所有 shell 指令**——這是刻意的，見 `.claude/hooks/guard-git.sh`。

1. **clone**：用這個範本開一個新 repo，clone 下來。
2. **跑初始化技能**：在 repo 裡開 Claude Code，打 `/init-from-template`。它會問專案名、GitHub repo、分支模型、本機檢查指令、高風險路徑、要不要接力日誌，然後替換佔位、寫好守衛的設定、（問過之後）建標籤，最後列出下一步。
3. **確認綠燈**：

   ```bash
   uv sync
   uv run mypy && uv run ruff check . && uv run pytest
   bash .claude/hooks/test-guard-git.sh   # 合併閘自己的測試；需要真 PR 的幾發會 skip 並說明
   ```

4. **開第一張票**：把想做的事講給 Claude，打 `/to-spec` 收成規格，再 `/to-tickets` 切成票。之後的順序與停點見 `.claude/skills/PIPELINE.md`。

## 目錄地圖

| 路徑 | 是什麼 |
| --- | --- |
| `CLAUDE.md` | 給 Claude 的常駐指示：專案狀態、技能組態、派子代理、人語層 |
| `CONTEXT.md` | 領域語言（空的，實體長出來一個填一個） |
| `.claude/hooks/` | 守衛。`guard-git.sh`（擋大量 add、force push、跳過 hook、按風險分級的合併閘）、`guard-secrets.sh`（擋把金鑰 commit 進去）、`human-only-paths.conf`（這個專案自己的高風險路徑與本機檢查指令）、`test-guard-git.sh`（守衛的測試，CI 會跑） |
| `.claude/skills/` | 技能。大部分是上游快照，來源與哪些可以改見 `VENDOR.md`、`UPSTREAM.md`；開發順序見 `PIPELINE.md` |
| `.claude/agents/` | 三格子代理角色（便宜快／強／最強），見 `Ai-coding-principle/MODEL-ROUTING.md` |
| `Ai-coding-principle/` | 家規（`README.md`）、傷疤帳本（`LESSONS.md`）、人的組塊庫（`PLAYBOOK.md`）、模型路由、分級、Git 協作、語言法 |
| `docs/` | 文件四層（意圖／事實／狀態／快照），地圖在 [`docs/README.md`](docs/README.md)；`docs/agents/` 是給技能讀的組態 |
| `.github/` | CI（型別、lint、單元、守衛測試）與 PR 模板 |
| `src/` | 程式。現在只有一支示範函式和它的測試，寫出第一個真的模組就刪掉 |

## 更新方式

**上游（範本 → 你的專案）**：範本之後改了守衛、技能或家規，想拿回來：

```bash
git remote add template <這個範本的 repo 網址>   # 只要做一次
git fetch template
git diff HEAD template/main -- .claude/hooks/ .claude/skills/ Ai-coding-principle/
git checkout template/main -- <要拿的路徑>         # 一個路徑一個路徑拿，不要整包蓋
```

加了 `template` remote 之後，`/setup-skills` 會核對 `.claude/hooks/` 跟範本版是否一致（沒加就只能看本機的修改紀錄）。**`human-only-paths.conf`、`CLAUDE.md`、`CONTEXT.md`、`LESSONS.md` 是你專案自己的**，不要從範本蓋回來。守衛、技能、`CLAUDE.md`、家規與帳本都是 T3 路徑，拿回來的那顆 PR 由人按。

**下游（你的專案 → 範本）**：專案裡踩出新的坑、長出新的規則，覺得別的專案也會用到：

1. 帳本條目（`LESSONS.md` 的 L-xxx、`PLAYBOOK.md` 的 P-xxx）去掉專案名、票號、檔名，例子改泛稱，首犯日期與累犯數留著當證據。
2. 守衛或技能的改動，連同測試一起搬；專案專屬的路徑留在 `human-only-paths.conf`，不進範本的 `guard-git.sh`。
3. 對範本開 PR。PR 說明寫「在哪個專案、出了什麼事、所以加了什麼」——規則沒有出處，下一個人就不敢動它，也不知道什麼時候可以拿掉。
