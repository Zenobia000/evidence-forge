Feature: 素材授權
  # 需求：FR-08｜故事：US-08｜關卡：G4

  @happy-path @smoke
  Scenario: 授權清楚且已核准的圖片可以使用
    Given 一張圖片的授權允許重製，創作者也已核准
    When 系統打包內容
    Then 那張圖片出現在內容包裡
    And 素材清單記錄它的來源網址、出處頁、作者與授權

  @sad-path
  Scenario Outline: 授權不明或被拒絕的圖片不會打包
    Given 一張圖片的授權狀態是 "<license>"
    When 系統打包內容
    Then 那張圖片不會出現在公開的內容包裡
    And 素材清單把它標成已擋下
    And 那張圖片與它的授權資訊仍保存在本機，之後查得到為什麼擋下

    Examples:
      | license |
      | unknown |
      | denied  |

  @sad-path
  Scenario: 授權允許但創作者還沒核准，也不打包
    Given 一張圖片的授權允許重製，但創作者還沒核准
    When 系統打包內容
    Then 那張圖片不會出現在公開的內容包裡
