Feature: 補料研究
  # 需求：FR-RS-01、NFR-COST-01、NFR-COST-03、NFR-RUN-08｜故事：US-04

  @happy-path
  Scenario: 依研究方向補充來源
    Given 整理段列出了兩個研究方向
    When 補料段完成
    Then 每個方向都有補充來源與摘要
    And 每份來源都記錄網址與抓取時間

  Scenario: 二手轉述不算獨立來源
    Given 三篇文章都轉述同一份官方公告
    When 補料段整理來源
    Then 追到那份公告當原始來源
    And 這三篇只算一組證據

  @sad-path
  Scenario: 搜尋額度用完就停
    Given 這張工作單的 Tavily 額度剩 1 credit
    When 補料段要做一次需要 2 credits 的搜尋
    Then 閘道拒絕這次搜尋
    And 補料段用已經找到的資料結束
    And 帳本記下 budget_exceeded

  Scenario: 搜尋與抓取結果跨工作單共用
    Given 另一張工作單在快取有效期內搜尋過同一個查詢
    When 這張工作單送出同一個查詢
    Then 直接回傳快取結果，不扣 credits
    And 帳本記下 cache_hit

  @sad-path
  Scenario: 不繞過付費牆與 robots 限制
    Given 一個補充來源是付費牆或 robots 不允許
    When 補料段要抓它
    Then 標成 unavailable 並記錄原因
    And 不嘗試繞過
