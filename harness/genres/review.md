---
id: review
name: 評測比較
group: 做決定
default_formula: 評比標準 → 比較 → 情境 → 結論
converts_to: [tips, opinion, explainer]
---

# 評測比較（`review`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/review.md`。

## 辨識訊號

- 「vs」「比較」「評測」「benchmark」
- 比較表、評分

## 抽取欄位（來源是這一類時）

- `candidates`：被比較的對象與版本
- `criteria`：評比標準
- `results`：各標準下的結果
- `setup`：測試條件
- `verdict`：結論與適用情境

## 骨架（目標是這一類時）

預設結構公式：評比標準 → 比較 → 情境 → 結論

1. 要選什麼、給誰看
2. 評比標準
3. 逐項比較（表格）
4. 各自適合的情境
5. 結論

## 常見轉換

轉成：`tips`（清單技巧）、`opinion`（觀點文）、`explainer`（解說文）
