---
id: opinion
name: 觀點文
group: 被說服、想一想
default_formula: SCQA＋金字塔原理
converts_to: [opinion, review, explainer]
---

# 觀點文（`opinion`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/opinion.md`。

## 辨識訊號

- 主張句、「我認為」
- 有論證與反駁
- 全文圍繞一個立場

## 抽取欄位（來源是這一類時）

- `thesis`：核心主張（一句）
- `arguments`：2～3 個論據
- `evidence`：每個論據的證據與出處
- `counterarguments`：反方說法
- `conclusion`：結論或行動建議

## 骨架（目標是這一類時）

預設結構公式：SCQA＋金字塔原理

1. 結論先講
2. 論據 1～3（各附證據）
3. 反方說法與回應
4. 結論與行動

## 常見轉換

轉成：`opinion`（觀點文）、`review`（評測比較）、`explainer`（解說文）
