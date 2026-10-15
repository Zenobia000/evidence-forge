"""配圖套件：封面、概念圖、數據圖、證據帳本、圖文節奏（創作流程 7a）。

這是創作者自己的五支配圖腳本收進 repo 後的樣子：機制（版面、驗證規則、封面三層組合）
在這裡、被測試；品牌內容（色票、字型、封面用語）不在 repo，放在角色的 `brand.yaml`。

模組
----
- `brand`：`brand.yaml` 的格式（`Brand`）、讀取（`load_brand`）、中性預設值、字型解析。
- `chart_style`：數據圖的固定版面（編號、結論標題、副標、圖、頁尾）與證據帳本的讀取。
- `concept_figure`：不含數字的概念圖、流程圖，用程式畫在 16 x 9 的網格上。
- `chart_brief`：證據帳本與每張圖的 brief 的結構、引用檢查。
- `layout`：圖文節奏檢查（L1～L6）。
- `cover`：封面的「形式 x 媒材 x 風格」三層組合與 prompt；生圖只留 `CoverRenderer` 介面。

這個 `__init__` 刻意不 import 任何模組：matplotlib 與 pandas 載入很慢，只用 `layout`
或 `chart_brief` 的人不該為此付代價。

brand.yaml
----------
位置：`$EF_DATA/authors/<角色>/brand.yaml`。找不到檔案就用中性預設值（灰階加一個通用重點色），
套件在沒有任何品牌設定的機器上也能跑。檔案只寫要蓋掉的欄位；多出不認得的欄位直接拒絕，
打錯字不會被默默忽略。格式由 `brand.Brand` 定義，全部欄位如下::

    name: "我的網站"            # 圖頁尾右側的品牌名；空字串就不印。預設空
    palette:                    # 七個色，#RRGGBB。重點色 accent 一張圖只標一個重點；
      paper: "#F5F5F5"          #   warn 只標失敗、衰退、風險
      ink: "#1F1F1F"
      graphite: "#4A4A4A"
      accent: "#2F6DB5"
      warn: "#B5483A"
      mist: "#C8C8C8"
      grid: "#E3E3E3"
    font:
      family: "Noto Sans TC"    # 錯誤訊息裡用的名稱
      regular: "NotoSansTC-Regular"   # 檔名（不含副檔名，找 .otf 再找 .ttf）或路徑
      bold: "NotoSansTC-Bold"
      dirs: ["fonts"]           # 另外找字型的資料夾；相對路徑以 brand.yaml 所在資料夾為準
      require_cjk: true         # 建立樣式時檢查字型有常用繁體中文字的字形；只用拉丁字母的字型才設 false
    cover:                      # 封面三層詞彙：形式 form、媒材 medium、風格 style
      forms: {名稱: 「這種作品怎麼存在」的英文描述}
      media: {名稱: {forms: [可搭配的形式], description: 英文描述}}
      styles: {名稱: 英文描述}
      presets: {名稱: {form, medium, style, preamble}}   # 選填：固定組合，舊文章的 cover_medium 靠它還原
      shared_rules: "每張封面都要遵守的限制（英文）"
      market_preamble: "市場觀察封面的開頭（英文）"
      market_rules: "市場觀察封面用的限制；不寫就沿用 shared_rules"

字型找法：`dirs` 裡的資料夾、角色資料夾本身與其中的 `fonts/`、`~/.fonts`，依序找。
找不到會丟 `FontNotFoundError`，訊息寫明缺哪個字型、找過哪些地方、該放到哪裡。
沒有 brand.yaml 時一樣會找角色資料夾的 `fonts/`。找到的檔案還要打得開、有繁中字形，
否則丟 `FontUnusableError`（缺字的字型不會報錯，只會畫出豆腐方塊，所以要在這裡擋）。
讀取的範圍就是角色自己的資料夾：角色資料夾本身若是符號連結、brand.yaml 連到資料夾之外，
一律拒絕（`BrandError`）；角色資料夾底下的 `fonts/`、字型檔、相對的 `font.dirs` 若連到資料夾之外
（例如別的角色的字型）就略過，當作沒有。brand.yaml 裡明確寫的絕對路徑（`regular`／`bold`／`dirs`）
和 `~/.fonts` 不受這個限制，因為那是創作者自己指定的位置。
`palette`、`font`、`cover` 內各欄位獨立：只寫 `palette.accent` 就只換重點色，其餘照預設。
`cover` 內的 `forms`、`media`、`styles`、`presets` 是整張字典一起換，不逐項合併。

生圖
----
真的呼叫圖像模型由創作流程 7b 經閘道接上；這裡只有 `cover.CoverRenderer` 介面與
測試用的 `cover.FakeCoverRenderer`。
"""
