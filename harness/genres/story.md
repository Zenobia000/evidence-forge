---
id: story
name: 故事心得
group: 被說服、想一想
default_formula: BAB（之前 → 之後 → 橋）
converts_to: [tips, case-study, opinion]
---

# 故事心得（`story`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/story.md`。

## 辨識訊號

- 第一人稱敘事、時間順序
- 轉折、「我學到」

## 抽取欄位（來源是這一類時）

- `situation`：情境
- `turning_point`：轉折
- `actions`：做了什麼
- `outcome`：結果
- `takeaway`：收穫

## 骨架（目標是這一類時）

骨架是思考的順序，不是文章的標題：標題寫那一節實際在講什麼（ADR-0024）。

預設結構公式：BAB（之前 → 之後 → 橋）

1. 情境
2. 轉折
3. 我怎麼做
4. 結果
5. 收穫

## 常見轉換

轉成：`tips`（清單技巧）、`case-study`（案例拆解）、`opinion`（觀點文）
