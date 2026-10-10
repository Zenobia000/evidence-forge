Feature: 定題 Gate
  # 需求：FR-06｜故事：US-06｜關卡：G3

  Background:
    Given 研究執行已完成證據審查，停在定題 Gate

  @happy-path @smoke
  Scenario: 核准論點後進入寫作
    When 創作者核准建議的論點，並寫下自己的觀點
    Then 寫作開始
    And 審核紀錄有核准的論點、創作者的觀點、決定與時間

  @happy-path
  Scenario: 修改論點後，用修改後的版本寫作
    When 創作者改寫論點後核准
    Then 寫作使用創作者改寫的論點

  @sad-path
  Scenario: 否決論點後不會產出稿子
    When 創作者否決論點
    Then 寫作不會開始
    And 這次研究執行沒有產出任何草稿

  @sad-path
  Scenario: 還沒決定時，流程停在 Gate
    When 創作者還沒有做出決定
    Then 研究執行停在定題 Gate，不會自己往下走

  @edge-case
  Scenario: 程式重啟後從 Gate 接著走
    Given 程式在等待定題時重新啟動
    When 創作者用同一個研究執行核准論點
    Then 研究執行從定題 Gate 接著往下走
    And 之前的搜尋沒有重新執行，也沒有重複扣費
