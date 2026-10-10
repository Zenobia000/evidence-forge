Feature: 來源擷取
  # 需求：FR-01｜故事：US-01｜關卡：G1

  Background:
    Given 創作者已開好一個新的研究執行

  @happy-path @smoke
  Scenario: 貼上可讀取的種子文章，取得它引用的來源
    Given 種子文章可以讀取，並引用了 3 個外部來源
    When 創作者貼上種子文章的 URL
    Then 來源清單列出種子文章和它引用的 3 個來源
    And 每個來源都有 URL、擷取時間與狀態

  @sad-path
  Scenario: 種子文章讀不到時，改用手動貼內容
    Given 種子文章回應 403
    When 創作者貼上種子文章的 URL
    Then 研究不會開始
    And 創作者看到「來源無法讀取」與手動貼上內容的選項

  @sad-path
  Scenario: 引用的來源在付費牆後面，標成不可驗證而不繞過
    Given 種子文章引用了一個需要付費才能閱讀的來源
    When 系統擷取引用的來源
    Then 那個來源的狀態是「不可驗證」
    And 系統沒有嘗試繞過付費牆

  @edge-case
  Scenario: 同一個來源用不同網址出現，只算一次
    Given 種子文章用兩個不同網址引用了同一篇文章，兩者的 canonical URL 相同
    When 系統擷取引用的來源
    Then 來源清單裡那篇文章只出現一次
