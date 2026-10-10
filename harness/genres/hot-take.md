---
id: hot-take
name: 熱點評論
group: 知道發生什麼
default_formula: SCQA
converts_to: [opinion, explainer, news]
---

# 熱點評論（`hot-take`）

> 文類定義（ADR-0015）。整理段用「辨識訊號」判斷文類、用「抽取欄位」整理來源；二創段在目標是這一類時用「骨架」組稿。作家在這一類的寫法不寫在這裡，寫在作家設定的 `genres/hot-take.md`。

## 辨識訊號

- 一件新事件＋「我認為」「我的看法」
- 篇幅短、立場明確

## 抽取欄位（來源是這一類時）

- `event`：事件與出處
- `take`：作者的判斷（一句）
- `reasons`：支持判斷的理由
- `implication`：對讀者的意義

## 骨架（目標是這一類時）

預設結構公式：SCQA

1. 情境：發生了什麼
2. 衝突：為什麼值得注意、哪裡矛盾
3. 問題
4. 答案：我的判斷與理由
5. 反方說法
6. 結論

## 常見轉換

轉成：`opinion`（觀點文）、`explainer`（解說文）、`news`（新聞快訊）
