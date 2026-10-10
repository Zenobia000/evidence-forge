Feature: 匯出與發布預覽
  # 需求：FR-09、FR-12｜故事：US-09、US-10｜關卡：G5
  # 「本機預覽」與「網站草稿預覽」的差別見 CONTEXT.md

  @happy-path @smoke
  Scenario: 匯出網站可讀的內容包
    Given 草稿已通過 G1 到 G5
    When 創作者匯出內容包
    Then 內容包含 article.mdx、sources.json、assets.json 與 run.json
    And 每個檔案都通過 schema 驗證

  @sad-path
  Scenario: 本機預覽有斷鏈時不產出可發表的內容包
    Given 草稿引用了一張不在素材清單裡的圖
    When 系統渲染本機預覽
    Then 本機預覽標出遺失的圖片
    And 不產出可發表的內容包

  @happy-path
  Scenario: 送到網站的是網站草稿預覽，不是公開文章
    Given 創作者已在發布審核核准
    When 系統送出到網站
    Then 網站上出現一份網站草稿預覽
    And 文章沒有公開

  @sad-path
  Scenario: 沒有人工核准就不送網站草稿預覽
    Given 創作者還沒在發布審核核准
    When 系統準備送出到網站
    Then 什麼都不會送出
