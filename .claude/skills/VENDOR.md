# Vendored skills

這個目錄下的技能有幾種來源。**規則一句話：無前綴＝上游快照，不手改；例外寫在下表最後兩列。**

| 前綴 | 支數 | 來源 | 可否手改 |
| --- | ---: | --- | --- |
| （無前綴） | 42 | luca-skills，見下 | 否（已知分岔見下） |
| `community-*` | 8 | 社群技能，見下 | 否 |
| `archify`、Emil 那 12 支、`impeccable` | 見 `UPSTREAM.md` | 逐一記錄 SHA 與授權 | 否 |
| `human-layer`、`init-from-template` | 2 | **自製，沒有上游** | 可 |
| `adhd-dev-mode` | 1 | **使用者全域技能的副本** | 可，但見下 |

## 自製的那幾支，為什麼都沒有前綴

`human-layer`、`init-from-template`、`adhd-dev-mode` 都是自製或本地維護的，都可以手改，
但都沒有前綴——**所以上表第一列（無前綴＝上游快照＝不可改）對它們不成立，
不寫在這裡，下一個人會不敢動它們。** 新專案要加自己的技能時，建議定一個前綴
（例：專案縮寫加 `-`），之後就不必再往這張表加例外。

## `adhd-dev-mode` 為什麼也是例外

它沒有前綴，卻是可以改的。名字刻意不加前綴的理由：使用者的全域技能目錄
（`~/.claude/skills/adhd-dev-mode/`）有同名的一份，而 Claude Code 的規則是
**專案的同名技能蓋過使用者的**。同名才蓋得住；改名會變成兩支並存的技能，
而且 `/adhd-dev-mode` 這個叫法會失效。

luca-skills 的更新指令（`cp -rL` 那條）不會誤觸它，因為上游沒有這個名字。

**代價是它有兩份，會漂移。** 這份進版控、跟著範本走到新專案；全域那份管其他所有專案。
兩邊不會自動同步。改了任何一邊，要嘛手動同步另一邊，要嘛當場決定哪一份是準的並在這裡寫下來。

---

## 一、luca-skills（42 支，無前綴）

- **來源**：https://github.com/Luca0x5755/luca-skills
- **版本**：commit `d3d3a30`（2026-08-31）
- **授權**：MIT，Copyright (c) 2026 Luca (Luca0x5755)。完整條款見上游 repo 的 `LICENSE`。
- **內容**：上游 `skills/core/`（23 支，正式）與 `skills/draft/`（19 支，試用中，內容仍會變動）全部展平在本目錄下。

### 怎麼更新

```bash
cd <luca-skills 所在路徑> && git pull
cd <本 repo>
for b in core draft; do for s in <luca-skills 所在路徑>/skills/$b/*/; do cp -rL "$s" .claude/skills/; done; done
```

更新後改本檔的 commit SHA 與日期，連同技能變更一起 commit。**更新前先看下面的已知分岔**——`cp -rL` 會把它們蓋回上游版。

### 已知分岔：本地改過的上游技能

規則是「不手改」，但下面這幾支為了範本改過。更新上游時要把這些改動補回去，或者決定放棄它：

| 技能 | 改了什麼 | 為什麼 |
| --- | --- | --- |
| `code-review` | 加上「審查子代理只讀：不准 commit、push、開 PR」與相關規矩 | `Ai-coding-principle/LESSONS.md` L-016：派去只讀的 fork 會把工作做完、推上去、連 PR 都開好 |
| `setup-skills` | Section E 改成「核對 `.claude/hooks/` 已存在、跟範本版一致」，不再去外部 repo 複製守衛；預設 ADR 路徑改成 `docs/10-truth/adr/` | 範本自帶守衛；從外部複製會拿到舊版 |

### 已知分岔：`browser-evidence` 的種子檔

`browser-evidence/drv.py` 是**種子檔**：技能的設計是第一次執行時把它複製到專案的
`tests/e2e/lib/drv.py`，之後每一輪的 case script 都 import 專案那份。所以**種子檔從第一次執行起就不會再被讀到**，專案那份改了也不必回頭改種子檔。

技能假設了一套通用的路徑慣例：`docs/uat/`、`docs/uat-cases.md`、`docs/test-blueprint.md`、`tests/e2e/lib/drv.py`。
這個範本的文件分層（`docs/README.md`）把前三個放在別處——對照表在 `docs/README.md`〈上游技能寫死的路徑〉。
**那不是「它寫死了我們的路徑」，是「它寫的是一套通用慣例，而我們偏離了那套慣例」。**
收編等於 fork 一支通用技能去寫死我們的目錄，換來的是永久的維護責任，而上游改了什麼都拿不到。所以維持快照、不收編，偏離的地方記在對照表。

### 不適用：`git-pr` 的 PR 格式那一段

`git-pr/SKILL.md` 規定的中文五節（`摘要`／`問題`／`變更內容`／`設計決定`／`測試`）
**本專案不用**。PR 格式的唯一一份規定在 `.claude/skills/human-layer/SKILL.md`
的「PR」那一條，是英文五節（`.github/PULL_REQUEST_TEMPLATE.md` 是它的展示）。

寫在這裡的理由：那支技能不手改，所以沒辦法在它裡面註明。**下一個人讀到
`git-pr` 那五節時會以為那是本專案的規定**——這一段就是攔住那個誤會的地方。

`git-pr` 的其餘部分（開 PR 的流程、合併後清理、`git pull --ff-only` 那幾條排錯）照樣適用。

---

## 二、community-*（8 支）

使用者手動加入。以下是**從檔案本身讀得出來的**資訊：

| 技能 | metadata.author | 授權 |
| --- | --- | --- |
| `community-react-performance` | vercel | MIT |
| `community-ui-design-system` | （無標記） | （無標記） |
| `community-react-native` | vercel | MIT |
| `community-react-composition` | vercel | MIT |
| `community-frontend-design` | （無標記） | Apache-2.0（附 `LICENSE.txt`）|
| `community-a11y-audit` | （無標記） | （無標記） |
| `community-ux-foundations` | （無標記） | （無標記） |
| `community-web-guidelines` | vercel | （無標記，描述指向 Vercel 的 Web Interface Guidelines）|

### 還不知道的事

**來源 repo 與版本沒有紀錄。** 上面那批 luca-skills 有 commit SHA，這一批沒有——
沒有它，日後無法判斷「上游改了什麼」，也無法重現這個狀態。
三支完全沒有作者或授權標記（`community-a11y-audit`、`community-ui-design-system`、`community-ux-foundations`）。
**知道來源的話請補進這一節**：來源 URL、取用當下的 commit SHA、日期。對外散布範本之前，先確認這三支能不能帶。

---

## 為什麼是複製而不是 symlink

複製讓 repo 自帶技能——換一台機器 clone 下來就能用，不必先裝上游。
代價是**不會自動更新**：上游改了，這裡不會跟著改。

## 不要在這裡改技能

要改上游技能的行為，改上游 repo 再複製回來。直接改這裡的檔案，下次更新會被覆蓋而且不會有人發現——真的非改不可，就登記在上面的「已知分岔」。
