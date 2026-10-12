Feature: 查證與找題材的搜尋與抓取
  # 需求：FR-IV-01、NFR-COST-01、NFR-COST-03、NFR-RUN-08｜故事：US-22、US-23｜決策：ADR-0020
  # 報告本身的情境在 analyze.feature；這裡只管搜尋、抓取與額度

  @happy-path
  Scenario: 照 brief 的關鍵主張與延伸方向找來源
    Given brief 標了三條關鍵主張與兩個延伸方向
    When 查證與找題材段完成
    Then 每條關鍵主張與每個延伸方向都有搜尋紀錄
    And 列進來源清單的每份來源都記錄網址與抓取時間

  Scenario: 二手轉述不算獨立來源
    Given 三篇文章都轉述同一份官方公告
    When 查證與找題材段整理來源
    Then 追到那份公告當原始來源
    And 這三篇只算一組證據

  @sad-path
  Scenario: 搜尋額度用完就停
    Given 這張工單的 Tavily 額度剩 1 credit
    When 查證與找題材段要做一次需要 2 credits 的搜尋
    Then 閘道拒絕這次搜尋
    And 這一段用已經找到的資料結束
    And 帳本記下 budget_exceeded

  Scenario: 搜尋與抓取結果跨工單共用
    Given 另一張工單在快取有效期內搜尋過同一個查詢
    When 這張工單送出同一個查詢
    Then 直接回傳快取結果，不扣 credits
    And 帳本記下 cache_hit

  @sad-path
  Scenario: 不繞過付費牆與 robots 限制
    Given 一個補充來源是付費牆或 robots 不允許
    When 這一段要抓它
    Then 標成 unavailable 並記錄原因
    And 不嘗試繞過
