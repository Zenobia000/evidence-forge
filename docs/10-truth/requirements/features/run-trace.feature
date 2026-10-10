Feature: 執行紀錄
  # 需求：FR-10、NFR-09、NFR-24｜故事：US-11

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

  @happy-path
  Scenario: 排隊中的研究執行顯示排在第幾位
    Given 已經有 4 篇研究執行在跑，worker 都在忙
    When 創作者建立第 5 篇研究執行
    Then 這篇的狀態是排隊中
    And 創作者看到它排在第 1 位

  @happy-path
  Scenario: 執行中的研究執行顯示真實進度
    Given 一篇研究執行正在搜尋與複核
    When 創作者打開這篇研究執行
    Then 創作者看到目前在第幾步、共幾步
    And 看到這一步已經跑了多久

  @edge-case
  Scenario: 在等資源時說明在等什麼
    Given 一篇研究執行要產生候選問題，但本機模型正在服務另一篇
    When 創作者打開這篇研究執行
    Then 創作者看到它正在等本機模型

  @edge-case
  Scenario: 輪到創作者時不顯示進度條
    Given 一篇研究執行停在等定題
    When 創作者打開這篇研究執行
    Then 創作者看不到進度條
    And 看到「輪到你了」與要做的事
