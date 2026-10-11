Feature: 素材授權
  # 需求：FR-AS-01、FR-AS-02、FR-RV-01、NFR-KEEP-03｜檢查：C4｜決策：ADR-0016

  @happy-path
  Scenario: 授權可用的圖可以直接使用
    Given 一張圖片的授權狀態是 ok
    When 二創段在稿子裡以 embed 放入它
    Then C4 通過
    And 素材清單記錄它的網址、出處頁、作者與授權

  @sad-path
  Scenario Outline: 授權不是 ok 的圖不能直接使用
    Given 一張圖片的授權狀態是 "<license>"
    When 二創段在稿子裡以 embed 放入它
    Then C4 不通過，執行器回二創段改成引用或拿掉
    And 那張圖與它的授權資訊仍保存在工單資料夾

    Examples:
      | license |
      | unknown |
      | denied  |

  @happy-path
  Scenario: 授權不明的上游截圖以引用放入
    Given 一張簡報截圖的授權狀態是 unknown
    When 二創段以 quote 放入它，旁邊附作者、標題、連結，前後段落在討論它
    Then C4 通過
    And 待審畫面把它列為「引用，待核准」

  @sad-path
  Scenario: 引用沒附出處
    Given 稿子以 quote 放入一張圖，但旁邊沒有出處說明
    When 檢查段完成
    Then C4 不通過，執行器回二創段補上出處

  @sad-path
  Scenario: 權利人拒絕的素材不能引用
    Given 一張圖片的授權狀態是 denied
    When 二創段以 quote 放入它
    Then C4 不通過

  Scenario: 影片只嵌入或連結
    Given 上游素材是一支影片
    When 二創段要用它
    Then 只能用官方嵌入或「連結＋時間點」，不保存影片檔到稿子的素材裡

  Scenario: 引用素材要逐件核准才能通過
    Given 稿子用了兩件 quote 素材，創作者只核准了其中一件
    When 創作者按通過
    Then 不能通過，畫面指出還有一件引用未核准
    When 創作者拒絕那一件並退件
    Then 重跑的二創段把那一件換掉或拿掉

  Scenario: 原文與上游的素材一律保存，預設只當參考
    Given 電子報裡附了一張圖，沒有任何授權資訊
    When 膠水層或補料段下載它
    Then 它被保存，授權狀態是 unknown，使用方式是 reference
