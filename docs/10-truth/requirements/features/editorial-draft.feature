Feature: 依證據寫作
  # 需求：FR-07｜故事：US-07｜關卡：G2、G5

  Background:
    Given 創作者已核准論點
    And 證據包裡的主張都有來源編號

  @happy-path @smoke
  Scenario: 草稿的核心事實都附來源編號
    When 系統產生大綱與草稿
    Then 草稿長度在 2,000 到 3,000 字之間
    And 每一個非個人推測的核心事實都附主張編號與來源編號

  @sad-path
  Scenario: 來源沒支持的敘述標成推論
    Given 草稿需要一段證據包沒有涵蓋的推論
    When 系統產生草稿
    Then 那段敘述標成推論
    And 沒有附上任何虛構的來源

  @sad-path
  Scenario: 不照原文的敘事順序寫
    When 系統產生大綱
    Then 大綱的段落順序依核准的論點安排，而不是照種子文章的段落順序

  @sad-path
  Scenario: 文章觀點和核准論點不一致時擋下
    Given 草稿的結論和核准的論點相反
    When 系統做發表前檢查
    Then 這份草稿不會成為可發表的內容包
