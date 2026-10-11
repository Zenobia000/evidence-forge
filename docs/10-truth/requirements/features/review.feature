Feature: 待審、退件與看板
  # 需求：FR-RV-01、FR-RV-02、FR-UI-01、NFR-OBS-02｜故事：US-06、US-07、US-10｜決策：ADR-0013

  @happy-path @smoke
  Scenario: 通過
    Given 一張工單在待審，結果是「完成」
    When 創作者按通過
    Then UI 寫下一份決定檔
    And 執行器把狀態改成通過

  @happy-path
  Scenario: 退件附一句意見，從二創段重跑
    Given 一張工單在待審，最新稿子是 v1
    When 創作者退件，意見是「開頭太長，改成先講結論」，重跑段用預設
    Then 工單回到排隊中，記下從二創段重跑
    And 整理與補料的產出沿用，不重新呼叫
    And 新稿子是 v2，v1 仍在

  Scenario: 退件時改選從整理段重跑
    Given 一張工單在待審
    When 創作者退件並選擇從整理段重跑
    Then 工單從整理段開始重跑，之後各段照配方往下走

  Scenario: 通過之後還能再改
    Given 一張工單已通過，最新稿子是 v2
    When 創作者在詳情頁再退件一次
    Then 工單回到排隊中，之後產出 v3
    And v2 仍在

  Scenario: 丟棄
    Given 一張工單在待審
    When 創作者按丟棄
    Then 狀態變成丟棄，資料夾與所有產出保留

  @happy-path
  Scenario: 看板顯示真實進度
    Given 一張工單正在跑二創段，是配方 4 段裡的第 3 段
    When 創作者打開看板
    Then 卡片顯示「3／4 二創」、這段已經跑了多久與目前花費
    And 數字每幾秒自動更新

  Scenario Outline: 看板說明正在等什麼
    Given 一張執行中的工單正在等 "<waiting>"
    When 創作者打開看板
    Then 卡片顯示「<label>」

    Examples:
      | waiting       | label      |
      | gpu           | 等 GPU     |
      | rate_limit    | 等限流     |
      | model         | 等模型回應 |

  Scenario: 所有結果都進同一個待審清單
    Given 三張工單分別是完成、預算用完、Ollama 沒開而失敗
    When 創作者打開待審
    Then 三張都在待審，各自標示「完成」「未完成：預算用完」「失敗：Ollama 沒回應」

  @security
  Scenario: 不會自動公開
    Given 一張工單已通過
    Then 系統沒有任何對外發布的動作
