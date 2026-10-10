Feature: 二創
  # 需求：FR-CP-01、FR-CP-02、FR-VF-01｜故事：US-05、US-06｜檢查：C2、C3、C4、C6、C7

  @happy-path @smoke
  Scenario: 照目標、用我的風格寫新文章
    Given 一張工作單的目標是「給台灣 PM 看的 1,500 字觀點文」
    And 整理與補料都已完成
    When 二創段完成
    Then 產出一份標準 Markdown 稿子，版本號是 v1
    And 二創段載入了 my-style
    And 每個事實都附引用編號，編號對得到來源

  @sad-path
  Scenario: 照抄原文會被擋下並自動修
    Given 稿子裡有一段跟原文的逐字重疊超過門檻
    When 檢查段完成
    Then C3 不通過
    And 執行器帶著檢查結果回二創段重寫一次
    And 新稿子是新版本，舊版保留

  @sad-path
  Scenario: 自動修到上限仍沒過就進待審
    Given 配方設定自動修最多 1 次
    And 修過一次後 C2 仍不通過
    When 執行器處理檢查結果
    Then 工作單進待審，結果是「未完成」
    And 待審畫面列出沒過的檢查與位置

  Scenario: 軟檢查只提醒不擋
    Given C6 判斷某個引用的來源只部分支持那句話
    When 工作單進待審
    Then 待審畫面把它列為提醒，附判斷理由
    And 工作單結果仍是「完成」

  @sad-path
  Scenario: 不捏造來源
    Given 二創段想引用一份補料與整理裡都沒有的來源
    When 檢查段完成
    Then C2 不通過，因為引用編號對不到任何來源
