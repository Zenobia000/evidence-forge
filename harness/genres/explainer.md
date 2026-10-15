---
id: explainer
name: 解說文
group: 理解一件事
default_formula: 問題 → 直覺 → 原理 → 例子 → 誤解
converts_to: [deep-dive, notes, tips, tutorial]
---

# 解說文（`explainer`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/explainer.md`。

## 辨識訊號

- 「為什麼」「怎麼運作」「是什麼」
- 類比、推導、示意圖
- 以理解為目的，不要求讀者動手

## 抽取欄位（來源是這一類時）

- `concept`：要解釋的概念
- `prerequisites`：讀懂需要的前置知識
- `intuition`：直覺說法與類比
- `mechanism`：原理或推導步驟
- `examples`：例子
- `misconceptions`：常見誤解

## 骨架（目標是這一類時）

骨架是思考的順序，不是文章的標題：標題寫那一節實際在講什麼（ADR-0024）。

預設結構公式：問題 → 直覺 → 原理 → 例子 → 誤解

1. 一個讓人好奇的問題
2. 直覺說法
3. 原理（逐步推導）
4. 例子
5. 常見誤解
6. 小結

## 常見轉換

轉成：`deep-dive`（深度長文）、`notes`（筆記摘要）、`tips`（清單技巧）、`tutorial`（教學文）
