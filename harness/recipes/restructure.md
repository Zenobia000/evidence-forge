---
name: restructure
description: 把一篇別人的文章整理成主張與研究方向，再照目標、用作家的聲音寫成目標文類的新文章
stages:
  - { id: digest, skill: digest, tier: local, output: [claims.json] }
  - { id: compose, skill: compose, tier: strong, output: [draft.md, compose.json] }
on_feedback: compose
default_goal: 用自己的話把這篇的重點與值得延伸的地方，寫成一篇給台灣讀者看的文章
max_attempts: 2
auto_fix: 1
---

預設配方（PRD §2、FR-RC-01）。S0 先兩段：

1. `digest`（整理，本機模型）：判斷文類與信心，依文類的抽取欄位整理，每條主張附逐字原文引句。
   執行器在這一段之後直接跑 C1（引句真實）、C5（格式）；沒過就重試這一段，用完 `max_attempts` 以失敗進待審。
2. `compose`（二創，`strong`）：讀整理結果、目標文類的骨架、作家聲音與文類風格，寫成新文章。
   執行器在這一段之後直接跑 C2～C5；沒過就帶著結果回這一段修，最多 `auto_fix` 次，還沒過以未完成進待審。

C6、C7（`verify` 段，只提醒、不擋）拆到 #62，之後再接成第三段。
目標文類由工單指定；沒指定時開單用作家設定的預設文類，記在 `job.md`。
