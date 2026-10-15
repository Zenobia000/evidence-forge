---
id: tutorial
name: 教學文
group: 學會做事
default_formula: 步驟式
converts_to: [tutorial, explainer, tips]
---

# 教學文（`tutorial`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/tutorial.md`。

## 辨識訊號

- 編號步驟、「首先」「接著」
- 指令、程式碼、設定檔
- 列出環境或版本需求

## 抽取欄位（來源是這一類時）

- `goal`：做完能得到什麼
- `prerequisites`：前置知識與工具
- `environment`：作業系統、版本、硬體
- `steps`：每步做什麼、指令、預期結果
- `pitfalls`：踩到的坑與解法
- `verification`：怎麼確認成功

## 骨架（目標是這一類時）

骨架是思考的順序，不是文章的標題：標題寫那一節實際在講什麼（ADR-0024）。

預設結構公式：步驟式

1. 做完能得到什麼
2. 前置需求
3. 步驟（每步：做什麼、為什麼、預期結果）
4. 驗證
5. 常見問題

## 常見轉換

轉成：`tutorial`（教學文）、`explainer`（解說文）、`tips`（清單技巧）
