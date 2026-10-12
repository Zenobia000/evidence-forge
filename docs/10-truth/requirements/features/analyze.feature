Feature: 分析：brief、查證與找題材，交出分析報告
  # 需求：FR-AN-01、FR-IV-01、NFR-COST-07、NFR-RUN-03｜故事：US-22、US-23｜決策：ADR-0020
  # 搜尋、抓取與額度的情境在 research.feature

  @happy-path @smoke
  Scenario: 一篇文章跑完分析，報告五節齊全
    Given 一張 analyze 配方的工單，素材是一篇網址抓得到的文章
    When 工單結束，結果是「完成」
    Then 有 brief.md、report.md 與 sources.json
    And report.md 依序有「作者在說什麼」「事實與觀點」「查證」「研究題材」「來源清單」五節
    And 報告貼回開出這張工單的對話

  Scenario: brief 用自己的話寫，標出關鍵主張
    When brief 段完成
    Then brief.md 寫出作者的論點、立場、事實與數據、觀點、文類與主題標籤
    And 撐起論點的事實主張標成關鍵
    And 不要求逐字引句

  Scenario: 有疑慮的主張放在查證最前面
    Given brief 有一條關鍵主張，查到的來源互相矛盾
    And 另一條關鍵主張有官方文件佐證
    When 查證與找題材段完成
    Then 查證一節先列有爭議的那一條，附兩邊的來源
    And 有佐證的那一條排在後面，附官方文件

  Scenario: 查不到不等於錯
    Given brief 有一條關鍵主張，搜尋找不到任何相關來源
    When 查證與找題材段完成
    Then 那一條標成「查不到」，不標成「錯誤」
    And 不用記憶或常識替它下結論

  Scenario: 研究題材附相關文章
    When 查證與找題材段完成
    Then 研究題材一節每個題材附至少一篇找到的相關文章，說明它補了原文沒有的什麼

  @sad-path
  Scenario: 報告引用沒抓過的網頁就重來
    Given 查證與找題材段在 sources.json 列了一個這一段沒有成功抓過的網址
    When 執行器跑程式檢查
    Then 這一次算失敗，依配方重試
    And 重試用完仍失敗，工單結束，結果是「失敗」並寫明原因

  @sad-path
  Scenario: 本機 brief 失敗就升級
    Given 配方的 brief 段是 local，失敗升級到 strong
    And brief 段在 local 上兩次都沒通過程式檢查
    When 執行器處理第二次失敗
    Then 改用 strong 再試，帳本記這次升級

  Scenario: 補查產生新版報告
    Given 對話裡已經有一份報告
    When 創作者要求針對某一條主張再查
    Then 開一次只跑查證與找題材段的工作，brief 沿用
    And 產生新版報告，舊版保留
