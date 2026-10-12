Feature: 聊天室：對話、追問、進度與恢復
  # 需求：FR-CH-01、FR-CH-02、FR-CH-03、FR-UI-11、NFR-OBS-02、NFR-SEC-02｜故事：US-21、US-23、US-26、US-10｜決策：ADR-0019

  @happy-path @smoke
  Scenario: 從丟網址到拿到報告
    Given 創作者開了一個新對話
    When 貼上一篇文章的網址
    Then 對話的 agent 開一次分析
    And 對話裡看得到「第 1／2 段 brief」與這段跑了多久
    And 分析結束後，報告出現在對話裡，右側面板可以打開完整報告

  Scenario: 看得到正在等什麼
    Given 一次分析正在查證與找題材段
    When 這一段正在呼叫 web_search
    Then 對話裡顯示「搜尋中」
    And 本機模型排隊時顯示「等 GPU」

  Scenario: 追問依據報告回答
    Given 對話裡已經有一份報告
    When 創作者問「作者對 sub-agent 的立場是什麼？」
    Then 對話的 agent 用 read_report 讀報告後回答
    And 不開新的工作

  Scenario: 分析完不自動寫，除非創作者說要
    Given 創作者丟素材時沒說要寫
    When 分析結束
    Then 不開重構，等創作者下一句
    But 創作者一開始就說「分析完直接用 me 寫觀點文」時，分析結束就接著開重構

  @security
  Scenario: 對話的 agent 不自己寫內容
    When 對話的 agent 想寫一份報告或文章
    Then 它沒有可以寫檔的工具，只能用 analyze 或 rewrite 開工

  @security
  Scenario: 對話只碰得到自己的資料夾
    When 對話的 agent 想讀另一個對話的報告
    Then 工具拒絕

  Scenario: 重啟後對話還在
    Given 一個對話有兩份報告與一篇文章
    When 程式重啟，創作者重新打開這個對話
    Then 看到完整的對話紀錄、每一版報告與文章
    And 可以接著追問

  Scenario: 對話清單
    When 創作者打開聊天室
    Then 左邊列出所有對話，最近的在上面，標題是第一份素材的標題

  Scenario: 對話本身的花費也入帳
    When 對話的 agent 回答一次追問
    Then 這次模型呼叫記在這個對話的帳本
    And 帳單頁的每月總表算得到它
