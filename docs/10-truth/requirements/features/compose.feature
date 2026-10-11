Feature: 二創
  # 需求：FR-CP-01～FR-CP-03、FR-VF-01｜故事：US-05、US-06｜檢查：C2、C3、C4、C6、C7

  @happy-path @smoke
  Scenario: 照目標、用我的聲音和該文類的風格寫新文章
    Given 一張工單的目標是「給台灣 PM 看的 1,500 字觀點文」，目標文類是 opinion
    And 整理與補料都已完成
    When 二創段完成
    Then 產出一份標準 Markdown 稿子，版本號是 v1
    And 二創段載入了作家聲音、作家的 opinion 文類風格與 opinion 的骨架
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
    Then 工單進待審，結果是「未完成」
    And 待審畫面列出沒過的檢查與位置

  Scenario: 軟檢查只提醒不擋
    Given C6 判斷某個引用的來源只部分支持那句話
    When 工單進待審
    Then 待審畫面把它列為提醒，附判斷理由
    And 工單結果仍是「完成」

  @sad-path
  Scenario: 不捏造來源
    Given 二創段想引用一份補料與整理裡都沒有的來源
    When 檢查段完成
    Then C2 不通過，因為引用編號對不到任何來源

  Scenario: 沒指定目標文類時用預設
    Given 工單與訂閱源都沒指定目標文類
    And 作家設定的預設文類是 explainer
    When 二創段開始
    Then 目標文類是 explainer，並記在 job.md

  Scenario: 沒有範本的文類標成未校準
    Given 目標文類是 review，作家設定裡沒有 review 的文類風格
    When 二創段完成
    Then 稿子只依作家聲音與 review 的骨架寫成
    And 待審畫面標示「未校準：review」

  Scenario: 有轉換補充說明時一起載入
    Given 來源文類是 roundup、目標文類是 explainer，且有 roundup--explainer 的補充說明
    When 二創段開始
    Then 補充說明一起載入，稿子只深挖其中一則，其餘列為延伸閱讀
