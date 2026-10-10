Feature: 評估
  # 需求：FR-EV-01、FR-EV-02、FR-EV-03、FR-UI-02｜故事：US-09

  @happy-path @smoke
  Scenario: 比較兩個組合
    Given 10 篇凍結的評估案例，其中有人工標註的關鍵主張
    And 組合 A 用 local 做整理，組合 B 用 strong
    When 對兩個組合跑評估，每個案例各跑 3 次
    Then 報告列出硬指標（捏造引句率、格式通過率、引用完整率、照抄率）
    And 列出整理段跟人工標註比的召回與誤報
    And 列出花費、延遲、升級率的差異

  Scenario: 只重跑某一段
    Given 要比較兩個寫稿模型
    When 跑評估
    Then 整理與補料的產出用凍結的版本，不重新呼叫
    And 只有二創段用兩個組合各跑一次

  @sad-path
  Scenario: 硬指標退步就不通過
    Given 新組合的捏造引句率不是 0
    When 評估報告產生
    Then 新組合標為「不通過」，不論軟指標多好

  Scenario: 評審兩兩比較並對調順序
    When 評審模型比較新舊兩篇稿子
    Then 每組比較判兩次，第二次對調順序
    And 報告寫出勝、和、敗的數量

  Scenario: 評審先校準才採信
    Given 創作者已人工判過 20 組比較
    When 評審模型判同樣的 20 組
    Then 報告列出評審與創作者的一致率
    And 一致率低於 80% 時，報告標示評審結果僅供參考

  Scenario: 真實退件變成評估案例
    Given 創作者退件了一張工作單並寫了意見
    When 下次建立評估集
    Then 這個案例與退件意見出現在候選案例裡
    And 一次通過率與平均退件次數依組合長期追蹤

  Scenario: 評估的重跑不花錢
    Given 某個組合之前跑過這批案例
    When 用重播模式重跑同一個組合
    Then 沒有付費呼叫，結果與上次相同
