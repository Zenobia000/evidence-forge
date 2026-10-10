Feature: 研究提案
  # 需求：FR-02、FR-03｜故事：US-02、US-03

  Background:
    Given 種子文章與它引用的來源已擷取完成

  @happy-path @smoke
  Scenario: 拿到附原文前提的候選研究問題
    When 系統產生候選研究問題
    Then 創作者看到 3 到 5 個研究問題
    And 每個問題都附原文前提、為什麼現在值得問、需要什麼證據、風險與來源編號

  @sad-path
  Scenario: 缺少證據的地方標成未知，而不是自己下結論
    Given 某個研究問題需要的資料在來源裡找不到
    When 系統產生候選研究問題
    Then 那個問題的證據欄標成 UNKNOWN
    And 系統沒有宣稱那個問題的結論成立

  @happy-path
  Scenario: 創作者可以改寫候選問題
    Given 創作者看到候選研究問題
    When 創作者改寫其中一題並送出
    Then 研究規劃使用改寫後的題目

  @happy-path
  Scenario: 研究計畫包含競爭假說
    When 系統依選定的問題做研究規劃
    Then 研究計畫包含建議論點、至少一個競爭假說、查詢清單與停止條件

  @sad-path
  Scenario: 研究計畫格式不合時，不開始搜尋
    Given 研究規劃的輸出缺少停止條件
    When 系統檢查研究計畫
    Then 搜尋不會開始
    And 創作者看到研究計畫不完整的原因
