Feature: 執行紀錄
  # 需求：FR-10｜故事：US-11

  @happy-path @smoke
  Scenario: 每個節點都留下可比較的紀錄
    When 一次研究執行跑完
    Then 每個節點都記錄耗時、模型版本、prompt 版本、輸入與輸出的 hash、費用與錯誤碼

  @sad-path
  Scenario: 部分成功不算完成
    Given 有一個引用來源擷取失敗
    When 研究執行結束
    Then 執行紀錄的狀態是部分成功，而不是完成
    And 失敗的來源標成 unavailable
