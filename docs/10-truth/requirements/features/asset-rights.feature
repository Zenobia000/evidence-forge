Feature: 素材授權
  # 需求：FR-AS-01、NFR-KEEP-03｜檢查：C4

  @happy-path
  Scenario: 授權可用的圖可以嵌入稿子
    Given 一張圖片的授權狀態是 ok
    When 二創段在稿子裡嵌入它
    Then C4 通過
    And 素材清單記錄它的網址、出處頁、作者與授權

  @sad-path
  Scenario Outline: 授權不明或被拒絕的圖不能嵌入
    Given 一張圖片的授權狀態是 "<license>"
    When 二創段在稿子裡嵌入它
    Then C4 不通過，執行器回二創段改成「參考圖」列出
    And 那張圖與它的授權資訊仍保存在工作單資料夾，之後查得到為什麼沒用

    Examples:
      | license |
      | unknown |
      | denied  |

  Scenario: 原文的圖預設只當參考
    Given 電子報裡附了一張圖，沒有任何授權資訊
    When 膠水層或補料段下載它
    Then 它的授權狀態是 unknown
