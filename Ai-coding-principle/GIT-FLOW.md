# Git 協作規則：一條變更從票走到 main

> 2026-09-07 定案。法在[家規第 27 條](README.md#第七章-流程法敏捷票務平行化)，這裡是它的展開與理由。
> 互動圖：[`GIT-FLOW.html`](GIT-FLOW.html)（自足頁，瀏覽器直接開；來源 `GIT-FLOW.workflow.json`，
> 改完用 `node .claude/skills/archify/bin/archify.mjs deliver workflow Ai-coding-principle/GIT-FLOW.workflow.json Ai-coding-principle/GIT-FLOW.html --quality showcase` 重產）。
> 分級表（誰按合併鍵）在[第 39 條](README.md#第七章-流程法敏捷票務平行化)，這裡不重述——一件事只有一個家。

## 一句話

**號碼池只有一個，其餘全部用引用串起來；分支盡量短，進 main 盡量平。**

---

## 一、三個號碼

GitHub 一個 repo 裡，**issue、PR、discussion 共用同一個遞增計數器**。所以 `#17` 是票、`#23` 是 PR（本文的號碼與 sha 都是例子），兩者不會撞號，但**光看號碼分不出型別**。

底層原因是資料模型：PR 在 GitHub 裡本質上是「一個夾帶了分支與變更紀錄的 issue」。API 的留言端點因此叫 `issue_number`，對 PR 一樣用——`gh issue view <PR號>` 不會報錯，它會回一個看起來正常的東西。

| 你看到的 | 那是什麼 | 誰放上去的 |
| --- | --- | --- |
| `gh issue list` 的 `#N` | 票號 | 開票的人 |
| `gh pr list` 的 `#N` | PR 號 | GitHub 自動配 |
| 分支名 `fix/t17` 的 `17` | 票號 | 開分支的人 |
| commit 標題結尾 `(#23)` | **PR 號** | **GitHub 在 squash 時自動補的** |
| commit 內文 `Closes #17` | 票號 | 寫 PR 描述的人 |

一條走完的鏈長這樣：

```
票 #17 「擋 --auto 延遲合併繞過」
  ↓  開分支，名字帶票號
fix/t17
  ↓  開 PR，base 一律 main
PR #23
  ↓  squash
commit a1b2c3d
    標題：fix(hooks): --auto 一律擋…  (#23)   ← PR 號
    內文：Closes #17                          ← 票號
```

### 為什麼不加型別標記

2026-09-07 想過三條路，選了維持現狀：

- **不學 GitLab 用 `!N` 分開 PR。** 那是 GitLab 的**渲染規則**，不是寫作慣例。在 GitHub 上打 `!23` 不會變成連結，只是一串死文字——三個月後回頭看，你還得自己猜 23 是什麼。GitLab 換來「一眼看得出型別」，GitHub 換來「引用時不必先知道型別」，兩邊都是有意識的取捨。
- **不把票號寫進 commit 標題。** squash 會自動補 `(#PR號)`，再加一個票號就是**一顆 commit 兩個數字，而且它們長得一模一樣**。更貴的是那個票號是手打的，沒有任何東西在驗它——打成 `[#16]` 不會有錯誤訊息，票不會關、圖上對不到，而 commit 看起來完全正常（帳本 L-012 的形狀）。
- **要查就查。** 型別問 API 有沒有 `pull_request` 欄位；票號看分支名或 `Closes` trailer。

```bash
# 這個號碼是票還是 PR
gh api repos/{owner}/{repo}/issues/23 --jq 'if .pull_request then "PR" else "issue" end'

# 這顆 commit 是哪張票做的
git show a1b2c3d -s --format=%B | grep Closes

# 掃 log 時把票號一起帶出來
git log --oneline -20 --format='%h %s%n     %(trailers:key=Closes,valueonly)'
```

**`Closes #N` 之所以能自動關票，是因為它落在 git trailer 的位置**（最後一段、`Key: value` 形狀）。寫在正文中間、或前面多一個 `-`，GitHub 就解析不到——票不會關，而且不會有任何錯誤訊息。

---

## 二、分支怎麼跟上 main

| 分支類型 | 做法 |
| --- | --- |
| `feat/*` `fix/*` `docs/*`（單一任務、短命） | `git rebase origin/main` |
| 多人／多 session 共同在上面工作的長分支 | `git merge origin/main` |
| `tmp*` | 不該存在超過一次解衝突 |

**判準是「有沒有第二個人在這條分支上工作」，不是分支活了多久。** rebase 會改寫 commit sha，把別人的基底抽掉；merge 不會。

反覆把 main 灌進工作分支，會在分支之間長出一堆菱形。菱形讓「這顆 PR 到底改了什麼」變難讀——審查的人得先分辨哪些行是作者寫的、哪些是併進來的。

### 一個容易讀錯的事實

**squash 合併之後，工作分支上那些 merge 不會進 main。** 本專案的 `main` 目前是直的，最近十幾顆全是 squash。所以「工作分支上有 merge」本身不會弄髒 main——真正會弄髒的是**用合併鍵合**（見下一節）。

判斷一條分支「已經合了沒」，**不能用 `git merge-base --is-ancestor`**：squash 產生的是新 commit，原本那幾顆不會是 `main` 的祖先，用它判會把「已經合了」讀成「還沒合」。可靠的是：

```bash
gh pr view <n> --json state --jq .state          # 問 PR 狀態
git cherry origin/main <branch>                   # 用 patch-id 比對
```

---

## 三、怎麼進 main

| 方式 | 什麼時候用 | 條件 |
| --- | --- | --- |
| `--squash` | **預設** | 無 |
| `--rebase` | 這顆 PR 的每一顆 commit **各自都通過檢查** | 條件由選的人宣稱，閘門驗不了 |
| 合併鍵 | 不用 | —— |

`guard-git.sh` 擋掉合併鍵那一種，所以走 hook 的路徑只出得了前兩種。**它擋不住 GitHub 網頁上的按鈕**——要全面成立，得去 repo 設定關掉 merge commit。

### squash 為什麼是預設

**一、`main` 上每一顆都是「通過檢查的完整狀態」。** 不 squash 的話，分支中途那些 commit 會進來——測試紅的、寫到一半的。`git bisect` 因此失效：它假設每一顆都跑得起來，而你會落在一顆本來就是壞的 commit 上，然後得自己判斷那是不是你要找的那顆。

**二、回退是一個指令。** `git revert <sha>` 把一整個功能原樣退掉。不 squash 的話那個功能散在五顆裡，你得先找出是哪五顆、順序反過來、一顆一顆退——而中間漏一顆不會有錯誤訊息。

**三、把 main 灌回分支那些噪音進不了 main。**

**四、Conventional Commits 從第一版就假設你用 squash。** 規格 `v1.0.0-beta.1` 的開場白：*As an open-source maintainer, squash feature branches onto `master` and write a standardized commit message while doing so.* 那條規格的形狀是為 squash 設計的——一顆 commit 對一件事，才推得出 CHANGELOG 與版號。

### squash 的代價

| 失去 | 什麼時候會痛 |
| --- | --- |
| PR 內部的 commit 順序 | 「為什麼先做 A 再做 B」的推理過程沒了 |
| `git blame` 的粒度 | 指到 squash 那一顆，不是原本那一行的 commit |
| 大 PR 壓成一顆巨大的 commit | PR 一大，squash 等於把一團東西壓成一塊 |

第三條有個好處：**它逼你把 PR 切小**，而切小正是[第 38 條](README.md#第七章-流程法敏捷票務平行化)垂直切片要的東西——代價與目標同向。

### 什麼時候用 rebase merge

**分支上每一顆 commit 各自都通過檢查、而且順序本身是資訊。** 例如一次分三步的重構：先加新路徑、再搬呼叫端、最後刪舊路徑。那三顆各自可回退，壓成一顆反而讓回退變粗。

**那個條件閘門驗不了**——它沒有辦法對每一顆 commit 各跑一次檢查。所以選 `--rebase` 的人是在**宣稱**那件事成立。寫出來是刻意的：一個閘門說不出自己在保證什麼的時候，讀的人會以為它保證了全部。

### 為什麼不用合併鍵

它會把工作分支上那些「把 main 灌回來」的 merge 原樣帶進 main。前一個專案的 `main` 上留著兩個菱形，就是這樣來的——那兩顆用了合併鍵，其餘全是 squash。

---

## 四、誰按合併鍵

按**路徑**判，不按任何人對 diff 的判斷——判斷正是分級要拿掉的東西。清單在 `guard-git.sh` 的 `human_only_path`，分級定義在[第 39 條](README.md#第七章-流程法敏捷票務平行化)。

| 級 | 長相 | 誰按 |
| --- | --- | --- |
| T1 例行 | 文件、格式、依賴升版、產生的產物 | 閘門放行 |
| T2 標準 | 既有模式內的功能、附回歸測試的修 bug | 閘門放行 |
| T3 敏感 | schema、API 介面、閘門與代理指示文件本身 | 人 |
| T4 例外 | 核心架構、CI/CD、分支保護、**任何刪除** | 人 |

**分不清的時候算高的那一級。**

---

## 五、合完就刪分支

本機與遠端都刪。分支留著不會有任何錯誤訊息，但下一個人得逐條去問「這條還有用嗎」——而**判斷一條分支還有沒有用，比刪掉它貴得多**。

```bash
gh pr merge <n> --squash
git push origin --delete <branch>
```

**不要加 `--delete-branch`**：那條分支若被 worktree 佔著，`gh` 會在**合併之後**才失敗（它想切回 `main`），而錯誤訊息會讓你以為合併沒成功。看到那個錯誤先 `gh pr view <n> --json state` 確認，不要重跑。

---

## 出處

- GitHub 共用號碼池：<https://github.com/orgs/community/discussions/69759>
- GitLab 的 `#` 與 `!` 對照：<https://thoughtbot.com/blog/this-week-in-dev-mar-24-2023>
- Conventional Commits v1.0.0：<https://www.conventionalcommits.org/en/v1.0.0/>
- 那句「squash feature branches」出自 beta.1：<https://www.conventionalcommits.org/en/v1.0.0-beta.1/>
- 規格源自 Angular 的 commit 格式：<https://en.wikipedia.org/wiki/Conventional_Commits>

**考據聲明**：Conventional Commits v1.0.0 的正式發布日我沒查到一手來源；只能確認 2019-10-01 的 Hacker News 討論串在談 v1.0.0，所以那個版本在 2019 年已經流傳。不要拿本文當那個日期的出處。
