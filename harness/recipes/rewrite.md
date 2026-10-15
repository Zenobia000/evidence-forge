---
name: rewrite
description: 寫稿：照創作者在訪談裡拍板的編輯卡，用選好的角色、文體與主題寫一篇新文章；從報告的題材開始時也讀分析報告與它找到的來源；再改是帶著一句意見與上一版開一張新的寫稿工單
stages:
  - { id: rewrite, skill: rewrite, tier: local, escalate_to: [fast, strong], output: [article.md, article.json] }
needs: [report.md]
on_feedback: rewrite
default_goal: 從分析報告「可以寫的題材」挑最適合的一個，用角色自己的聲音寫一篇給台灣讀者看、有自己觀點的文章
max_attempts: 2
auto_fix: 1
---

寫稿配方（PRD v3.0 FR-RW-01～03、ADR-0025 §3.4、ADR-0026 §3.3、ADR-0020、ADR-0022、ADR-0024）。一段 `rewrite`。

本機優先（ADR-0022）：先用本機模型試滿 `max_attempts` 次，產出不合格就照升級鏈 `[fast, strong]`，
Claude Haiku 5.5、Sonnet 5.5 各試一次。輸入大到本機模型的上下文放不下時，閘道直接擋下這一次，
不在本機再試，直接升到 `fast`。程式檢查沒過的「回去修」（`auto_fix`）在當下那一級做；
修完還沒過，升到下一級重寫。

**開單：照編輯卡寫（#110）。** 創作者在畫面上按「照這張編輯卡寫稿」，對話主機交給工作台的 `write_from_card`
（`chat/desk.py`）；對話的 agent 叫不到。工作台再查一次卡能不能寫、事件筆數跟創作者確認時一樣，
拿快照開單（`runner/intake.py` 的 `open_job_from_card`）：

- `input/card/`：編輯卡快照（開單時放，之後再改編輯卡不影響這張單）：寫稿讀的 `card.md`（只有現在的決定、
  主張清單、圖位）、留底的 `card.json` 與 `decisions.jsonl`、作者素材 `materials/<sha256>`；
- 從報告的題材開始時接著那張分析工單做（`needs: [report.md]` 是對那張單說的）：執行器開工前把
  `input/report.md` 與那個題材在報告裡列的來源 `input/sources/S<n>.md` 帶過來（brief 不帶），原文出處寫進任務說明。
  從創作者自己的想法或作者素材開始時沒有上一張單，也沒有原文；
- 角色資料只送這一篇用得到的（ADR-0026 §3.3，`runner/stage_inputs.py`）：`input/author/voice.md`、
  這個文體的 `input/author/genre-style.md`（沒有就是未校準）、寫稿用的 `input/author/craft/`
  （開場、標題、金句與收尾，有配圖或表格時加圖文編排）、這個主題在角色 `themes.md` 裡的那一節 `input/author/theme.md`、
  最近十筆 lessons `input/author/lessons.md`；角色沒有自己的標題或金句手藝時才放共用的 `input/headline-punchline.md`；
- `input/target-genre.md`：文體的通用定義（ADR-0026 的 7 種之一）。

**原文全文不放**（`input/content.md` 不在寫稿工單裡）：只讀分析報告與來源（FR-RW-01）。
文體是 `essay` 而且從報告開始時，內文可以引用並回應原文；其他文體內文不提原文（ADR-0025 §3.4、ADR-0024）。

執行器的程式檢查（`checks/article.py`）：文章是標準 Markdown；從報告開始時參考來源第一條是原文出處、
內文不提原文標題與原作者（essay 例外）；標題不用文體骨架的字；照編輯卡寫的文章另外查 front matter 有
`title`、`description`、`genre`、`theme`（文體與主題各一個、合法、跟編輯卡一樣）、參考來源每一筆有網址而且是這篇真的有的、
角色 `voice.md` 列的句型次數（`sentence_patterns`）、圖文分鏡的每一張圖都有圖位；`article.json` 合 schema。
沒過就帶著結果回這一段修，每一級最多 `auto_fix` 次，還沒過就升到下一級重寫，最後一級也修不好以未完成結束。
跟原文、跟每一份來源的逐字重疊率照樣算出來寫進檢查結果，只顯示、不擋。

再改（FR-RW-03）：對話的 `rewrite` 從上一版的寫稿工單帶一句意見開新單（命令列是
`ef new --from <上一版的寫稿工單> --feedback "開頭太長，直接講結論"`）。每一版都是一張新工單（ADR-0019 §3.6）：
上一版文章放在 `input/article.md`，報告與來源跟著帶過來，意見寫進任務說明；角色、文體、目標沿用上一版。
照編輯卡寫的文章，改稿開單時拿一份新的編輯卡快照；卡在上一版寫好之後又改過、或改稿要換文體，就不開（409），
請創作者改卡、再按「照這張編輯卡寫稿」寫新的一篇。
分析不重跑，上一版不動。

命令列的 `ef new --from <分析工單>`（沒有編輯卡）照舊開得出來，給手動測試用；`default_goal` 只在這種單用。
聊天室裡「寫第 N 個」開始的是訪談，不是直接寫（ADR-0025 §3.5）。
