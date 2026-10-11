---
name: echo
description: 打通用的測試配方：一段，用本機模型讀素材、寫一個摘要檔
stages:
  - { id: echo, skill: echo, tier: local, output: [summary.json] }
on_feedback: echo
max_attempts: 1
auto_fix: 0
---

只用來證明整條路是通的（S0.3）：投入 → 開單 → 執行器開 Pi → 閘道轉送到本機模型 →
產出用 `harness/skills/echo/schema.json` 驗過 → 進待審。不是給創作者用的配方。
