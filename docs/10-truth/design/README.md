# 設計：token 與介面規則

這個資料夾是「介面長什麼樣、不能違反什麼」的家（`docs/README.md` 的分層表）。為什麼另外有這份說明：token 的值不寫在文件裡，寫在網頁直接載入的 CSS 檔。S0 沒有建置步驟，文件抄一份、CSS 再一份，兩份遲早對不上。

## token 的值：只有一份

| 檔 | 放什麼 |
| --- | --- |
| `src/evidence_forge/ui/static/css/tokens.css` | Loomy 設計系統的 token：顏色、間距、圓角、字級、版面、疊層。原樣從 Loomy 搬來，檔頭寫了版本 |
| `src/evidence_forge/ui/static/css/s0.css` | S0 簡易 UI 的樣式：ADR-0017 的狀態別名（`--job-*`、`--check-*`、`--claim-*`、`--genre-*`）、換成系統中文字的字型清單，以及頁面元件 |

載入順序是先 `tokens.css`、再 `s0.css`。`s0.css` 的字型清單要蓋過 `tokens.css` 裡以 Noto 開頭的那組（ADR-0017 §3.3）。

## 名稱與規則在哪

- token 怎麼命名、狀態怎麼上色：ADR-0007、ADR-0017。
- 外觀三選一（淺色、深色、跟隨系統）：ADR-0008，S4 才做；S0 只做淺色。
- 頁面長什麼樣：S0 設計稿 https://claude.ai/artifact/GqAyiKFvYZNd948RTmsAyT 。樣式以上面兩個 CSS 檔為準，設計稿只看版面。

文件裡要講到 token，只寫名稱（例：`--job-review`），不抄值。
