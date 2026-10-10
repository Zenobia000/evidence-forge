Feature: 證據與反證迴圈
  # 需求：FR-04、FR-05、FR-11｜故事：US-04、US-05｜關卡：G1、G2

  Background:
    Given 研究計畫已通過檢查
    And 這次研究執行的搜尋上限是每輪 5 個查詢、20 個 credits

  @happy-path @smoke
  Scenario: 每個核心主張都對到來源原文
    When 系統完成搜尋與驗證
    Then 證據矩陣裡每個核心主張都標出來源編號與原文段落

  @sad-path
  Scenario: 多篇文章轉述同一份公告，只算一組證據
    Given 三篇搜尋結果都引用同一份官方公告
    When 系統整理證據
    Then 那三篇與公告在證據矩陣裡算成一組原始證據

  @sad-path
  Scenario: 找不到來源的主張標成未驗證
    Given 有一個主張在所有來源裡都找不到支持
    When 系統整理證據
    Then 那個主張標成 UNVERIFIED

  @happy-path
  Scenario: 證據不足時自動補搜
    Given 審查結果是 RESEARCH_MORE，目前是第 1 輪
    When 系統依審查意見補搜
    Then 系統進行第 2 輪定向搜尋
    And 這一輪的查詢數與 credits 都記錄下來

  @edge-case
  Scenario: 補搜超過 2 輪就停下交給人
    Given 第 2 輪補搜後審查結果仍是 RESEARCH_MORE
    When 系統處理審查結果
    Then 系統不再自動搜尋
    And 研究執行停在等待人工決定

  @edge-case
  Scenario: 創作者授權的補搜最多再 2 輪
    Given 自動補搜 2 輪後研究執行停在等人工決定
    And 創作者已經授權過 2 輪補搜
    When 創作者想再授權一輪補搜
    Then 系統不接受這次授權
    And 創作者只能用現有證據進入定題 Gate 或取消這次研究執行

  @edge-case
  Scenario: 搜尋預算用完就停
    Given 這一輪已經用掉 20 個 credits
    When 系統想再發一個查詢
    Then 查詢不會送出
    And 創作者看到預算已達上限的通知
