---
name: rewrite
description: 重構：從一份分析報告與它找到的來源，照指定的角色、文體與目標寫一篇新文章；再改是帶著一句意見與上一版開一張新的重構工單
stages:
  - { id: rewrite, skill: rewrite, tier: strong, output: [article.md, article.json] }
needs: [report.md]
on_feedback: rewrite
default_goal: 以分析報告的論點與研究題材為起點，寫一篇給台灣讀者看、有自己觀點的文章
max_attempts: 2
auto_fix: 1
---

重構配方（PRD v3.0 FR-RW-01～03、ADR-0019、ADR-0020、ADR-0015）。一段 `rewrite`，`strong`。

開單：`ef new --from <分析工單> --persona me --genre opinion --goal "..."`（帶 `--from` 時 `--recipe` 預設就是 `rewrite`）。
`needs: [report.md]`：一定要 `--from`，而且那張單要有過了程式檢查的分析報告，不然不開單。
執行器開工前把那張單的產出放進新工單的 `input/`（`runner/stage_inputs.py`）：

- `input/report.md`、`input/brief.md`、`input/brief.json`：分析報告與 brief；
- `input/sources/S<n>.md`：查證段抓過的來源正文；
- `input/author/`：角色的聲音與它在目標文類的風格（`$EF_DATA/authors/<角色>/`）；沒有校準過的風格就是未校準，結果上會標；
- `input/target-genre.md`、`input/conversions/`：文類骨架與轉換說明；
- `input/headline-punchline.md`：標題與金句規則（`$EF_DATA/style/headline-punchline-system.md`，不進 repo）。

**原文全文不放**（`input/content.md` 不在重構工單裡）：重構只讀分析報告與來源（FR-RW-01）。
原文出處（標題、作者、網址）由執行器寫進任務說明，文章結尾的參考來源照抄。

執行器的程式檢查（`checks/article.py`）：文章是標準 Markdown、最後一節「參考來源」列了原文出處、
`article.json` 合 schema；沒過就帶著結果回這一段修，最多 `auto_fix` 次，還沒過以未完成結束。
跟原文、跟每一份來源的逐字重疊率照樣算出來寫進檢查結果，只顯示、不擋。

再改（FR-RW-03）：`ef new --from <上一版的重構工單> --feedback "開頭太長，直接講結論"`。
每一版都是一張新工單（ADR-0019 §3.6）：上一版文章放在 `input/article.md`，報告與來源跟著帶過來，
意見寫進任務說明；角色、文體、目標沒給就沿用上一版。分析不重跑，上一版不動。
