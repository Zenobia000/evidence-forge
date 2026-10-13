---
name: analyze
description: 先分析：讀懂作者在說什麼、查證撐起論點的關鍵主張、找可以借題發揮的研究題材，交一份分析報告
stages:
  - { id: brief, skill: brief, tier: local, escalate_to: strong, output: [brief.md, brief.json] }
  - { id: investigate, skill: investigate, tier: strong, output: [report.md, sources.json], tavily_credits: 12 }
on_feedback: investigate
default_goal: 讀懂這篇文章想說什麼，查證撐起論點的關鍵主張，找出可以借題發揮的研究題材
max_attempts: 2
auto_fix: 0
---

預設配方（PRD v3.0 §2、FR-AN-01、FR-IV-01、ADR-0020）。創作者丟進來的文章先分析，不自動往下寫；
看完報告再叫重構（之後的 `rewrite` 配方）。兩段：

1. `brief`（本機模型，失敗升級 `strong`）：用自己的話寫作者的論點與立場、事實與數據、觀點、文類、
   主題標籤與可以延伸的方向（`brief.md`），給程式讀的文類、主題標籤、關鍵主張放 `brief.json`。
   不附逐字引句。執行器驗 `brief.json` 的 schema、`brief.md` 是 Markdown 而且六節都在；
   沒過算這一次失敗，本機試滿 `max_attempts` 次還不行就升到 `strong` 再試一次。
2. `investigate`（查證與找題材，`strong`）：拿 brief 的關鍵主張上網查證（有佐證、有爭議、查不到、過時），
   找可以借題發揮的相關文章，交分析報告 `report.md`（五節標題固定，PRD §5）與來源清單 `sources.json`。
   執行器檢查五節依序都在、查證與題材引用的編號都在清單上、清單上的網址都是這一段這一次真的抓過的
   （`runner/sources.py`），全部過了才把來源正文放進 `input/sources/S<n>.md`。
   `tavily_credits: 12` 是這一段在整張單裡的總額、含重試：skill 一次最多搜 6 次 × `max_attempts: 2`。

報告貼回對話（P2）之前，就留在工單資料夾 `stages/investigate/<次>/report.md`。
`auto_fix: 0`：兩段的檢查沒過都算那一次失敗，走重試與升級，沒有「回去修」這一步。
