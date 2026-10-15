Feature: 重構：用角色與文體，從分析報告寫一篇新文章
  # 需求：FR-RW-01、FR-RW-02、FR-RW-03、NFR-COST-04、NFR-COST-07｜故事：US-24、US-25｜決策：ADR-0015、ADR-0020、ADR-0022

  @happy-path @smoke
  Scenario: 用指定的角色與文體寫一篇文章
    Given 對話裡有一份分析報告
    And 角色 me 有聲音與觀點文的文類風格
    When 創作者說「用 me 寫一篇觀點文，給台灣 PM 看」
    Then 開一張 rewrite 配方的工單，帶著報告、角色、文體與目標
    And 工單結束時有 article.md，是標準 Markdown
    And 文章貼回對話

  Scenario: 重構不讀原文全文
    When 重構段開跑
    Then 它的輸入有分析報告與補充來源的正文
    And 沒有原文全文

  Scenario: 結尾一定列原文出處
    When 重構段完成
    Then 文章結尾的參考來源列出原文的作者、標題與連結
    And 文章內文不掛逐句引用編號，也不標哪一句有疑慮

  Scenario: 重疊率只顯示不擋
    Given 文章跟某一份來源的逐字重疊率是 8%
    When 重構段完成
    Then 工單結果仍是「完成」
    And 重疊率顯示在文章旁邊

  Scenario: 沒有該文類風格時標未校準
    Given 角色 me 沒有教學文的文類風格
    When 創作者要它寫教學文
    Then 照教學文的骨架與 me 的聲音寫
    And 文章標「未校準」

  Scenario: 沒給目標時用配方的預設目標
    Given 創作者只說「用 me 寫」
    When 工單開出來
    Then job.md 的目標是 rewrite 配方的預設目標

  @happy-path
  Scenario: 一句意見再改
    Given 對話裡已經有文章 v1
    When 創作者說「開頭太長，直接講結論」
    Then 開一張只跑重構段的工單，帶著這句意見與 v1
    And 產生 v2，v1 保留
    And 分析不重跑

  @sad-path
  Scenario: 角色不存在
    When 創作者要它用一個沒有資料夾的角色寫
    Then 不開工，對話的 agent 列出現有的角色

  @sad-path
  Scenario: 重構先用本機模型，不合格才一級一級往上
    Given 重構段是 local，升級鏈是 fast、strong
    And 本機模型兩次都交不出合格的文章
    When 執行器處理第二次失敗
    Then 改用 fast 再試一次，還不行再用 strong 試一次

  @sad-path
  Scenario: 輸入大到本機模型的上下文放不下，直接升級
    Given 重構段是 local，升級鏈是 fast、strong
    And 這一次的提示估起來超過本機模型的上下文
    When 閘道擋下這一次
    Then 不在本機再試，直接改用 fast

  @sad-path
  Scenario: 每一級修完還沒過，才升到下一級
    Given 重構段是 local，升級鏈是 fast、strong，auto_fix 是 1
    And 本機寫的文章沒列原文出處，修一次還是沒列
    When 執行器處理修完的那一次
    Then 改用 fast 重寫，重試與自動修的次數重新算
    And strong 也修完還沒過，才以未完成結束
