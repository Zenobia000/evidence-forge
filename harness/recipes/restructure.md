---
name: restructure
description: 把別人的文章當主題的種子：整理出主張與研究方向，上網找相關文章補料，再綜合多個來源、照目標、用作家的聲音寫成目標文類的新文章
stages:
  - { id: digest, skill: digest, tier: local, output: [claims.json] }
  - { id: research, skill: research, tier: strong, output: [sources.json], tavily_credits: 12 }
  - { id: compose, skill: compose, tier: strong, output: [draft.md, compose.json] }
on_feedback: compose
default_goal: 以這篇的主題為起點，綜合幾篇相關文章，寫一篇給台灣讀者看、有自己觀點的文章
max_attempts: 2
auto_fix: 1
---

預設配方（PRD §2、FR-RC-01）。創作者丟進來的文章是**主題的種子**，不是要改寫的底稿（#70）。三段：

1. `digest`（整理，本機模型）：判斷文類與信心，依文類的抽取欄位整理，每條主張附逐字原文引句，列出研究方向。
   執行器在這一段之後直接跑 C1（引句真實）、C5（格式）；沒過就重試這一段，用完 `max_attempts` 以失敗進待審。
2. `research`（補料，`strong`）：照研究方向寫具體的查詢、用 `web_search` 搜尋、挑 2～5 篇跟種子不同網站或作者的相關文章、
   用 `fetch_url` 抓正文，交一份來源清單（`sources.json`）。`tavily_credits: 12` 是這段的搜尋額度，
   **也決定這段有沒有 `web_search` 工具**（大於 0 才有）；工單的額度預設是各段加總。
   這個數字是這一段在整張單裡的總額，**重試也算在裡面**（閘道只看整張單用了多少）：skill 一次最多搜 6 次、
   `max_attempts: 2`，所以給 12。實測一次用 3～4（2026-10-12：重播 fixture 3、ihower 第 5 篇 4）。
   正文由執行器從這一段真的抓過的網頁放進 `input/sources/S<n>.md`（`runner/sources.py`）；清單上有沒抓過的網址算這段不合格。
3. `compose`（二創，`strong`）：種子只決定主題，綜合種子（`S1`）與補料找到的來源（`S2`…）寫成新文章，
   不沿用任何一篇的段落順序，每段都要有來源裡沒有的東西。
   執行器在這一段之後直接跑 C2～C5（C2 認所有來源的編號、C3 拿稿子比所有來源）；沒過就帶著結果回這一段修，
   最多 `auto_fix` 次，還沒過以未完成進待審。

C6、C7（`verify` 段，只提醒、不擋）拆到 #62，之後再接成第四段。
目標文類由工單指定；沒指定時開單用作家設定的預設文類，記在 `job.md`。
每張單的預設美元預算多半不夠兩段上雲端，S0.14（#31）實測後再調；現在用 `ef new --budget-usd` 指定。
