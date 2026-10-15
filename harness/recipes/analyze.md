---
name: analyze
description: 先分析：讀懂作者在說什麼、查證撐起論點的關鍵主張、給幾個可以寫的題材讓創作者挑，交一份分析報告
stages:
  - { id: brief, skill: brief, tier: local, escalate_to: [fast, strong], output: [brief.md, brief.json] }
  - { id: investigate, skill: investigate, tier: local, escalate_to: [fast, strong], output: [report.md, sources.json], tavily_credits: 24 }
on_feedback: investigate
default_goal: 讀懂這篇文章想說什麼，查證撐起論點的關鍵主張，給幾個可以寫的題材讓創作者挑
max_attempts: 2
auto_fix: 0
---

預設配方（PRD v3.0 §2、FR-AN-01、FR-IV-01、ADR-0020、ADR-0022）。創作者丟進來的文章先分析，不自動往下寫；
看完報告再叫重構（之後的 `rewrite` 配方）。

兩段都是本機優先（ADR-0022）：先用本機模型試滿 `max_attempts` 次，還不行就照升級鏈
`[fast, strong]`，Claude Haiku 5.5、Sonnet 5.5 各試一次；Sonnet 也不行這一段才算失敗。
本機不花錢，Haiku 很便宜，Sonnet 只在前面都失敗時才用到。兩段：

1. `brief`：用自己的話寫作者的論點與立場、事實與數據、觀點、文體（ADR-0026 的 7 種之一）、
   主題標籤與可以延伸的方向（`brief.md`），給程式讀的文體、主題標籤、關鍵主張放 `brief.json`。
   不附逐字引句。執行器驗 `brief.json` 的 schema、`brief.md` 是 Markdown 而且六節都在；
   沒過算這一次失敗，照上面的升級規則重試。
2. `investigate`（查證與找題材）：拿 brief 的關鍵主張上網查證（有佐證、有爭議、查不到、過時），
   找相關文章、給幾個有編號的可以寫的題材（每個附 7 種之一的建議文體），交分析報告 `report.md`（一句話、下一步、五節標題固定，PRD §5、ADR-0024）
   與來源清單 `sources.json`。執行器檢查一句話與五節依序都在、報告沒有 K、S 編號、題材有編號、
   查證與題材引用的編號都在清單上、清單上的網址都是這一段這一次真的抓過的
   （`runner/sources.py`），全部過了才把來源正文放進 `input/sources/S<n>.md`。
   `tavily_credits: 24` 是這一段在整張單裡的總額、含重試與升級：skill 一次最多搜 6 次 ×
   4 次嘗試（本機 `max_attempts: 2` 次、`fast` 一次、`strong` 一次）。閘道另外擋每一次最多
   24 ÷ 4 = 6 個，本機那兩次用不光整張單的額度（ADR-0022）。

報告貼回對話（P2）之前，就留在工單資料夾 `stages/investigate/<次>/report.md`。
`auto_fix: 0`：兩段的檢查沒過都算那一次失敗，走重試與升級，沒有「回去修」這一步。
