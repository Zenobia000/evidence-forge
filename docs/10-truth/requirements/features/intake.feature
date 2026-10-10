Feature: 素材投入與訂閱源接入
  # 需求：FR-IN-01、FR-IN-02、FR-IN-03、NFR-SEC-01｜故事：US-01、US-02｜決策：ADR-0012

  @happy-path @smoke
  Scenario Outline: 手動投入就開一張工作單
    Given 創作者在新增工作單頁選擇「<kind>」並填了目標
    When 送出
    Then inbox 的 manual 底下多一個素材項目，附出處、時間與內容 hash
    And 依選定的配方開出一張排隊中的工作單，目標寫在 job.md

    Examples:
      | kind     |
      | 貼網址   |
      | 貼文字   |
      | 上傳檔案 |

  Scenario: 沒填目標時用配方的預設目標
    Given 創作者投入一篇文章但沒填目標
    When 工作單開出來
    Then job.md 的目標是該配方的預設目標

  @sad-path
  Scenario: 讀不到的網址不繞過
    Given 創作者貼的網址回應 403 或是付費牆
    When 系統抓取
    Then 素材項目標成讀不到，並寫明原因
    And 不嘗試任何繞過方式
    And 提示創作者改用貼文字或上傳檔案

  @happy-path
  Scenario: 訂閱源依排程自動抓取並開單
    Given feeds.yaml 裡有一個 rss 訂閱源，排程是每天 07:00，指定了配方與目標
    When 到了 07:00，且有一篇新文章
    Then 那篇文章進 inbox
    And 依訂閱源設定自動開出一張工作單

  Scenario: 同一篇不會開兩張單
    Given 一篇文章已經進過 inbox
    When 同一個訂閱源或另一個訂閱源再抓到內容 hash 相同的文章
    Then 不會新增素材項目，也不會再開工作單

  @sad-path
  Scenario: 一個訂閱源壞掉不影響其他訂閱源
    Given 有兩個訂閱源，其中一個的授權過期
    When 排程時間到
    Then 正常的那個照常抓取
    And 壞掉的那個在訂閱源頁顯示錯誤與最後一次成功的時間

  @security
  Scenario: agent 讀不到帳號授權
    Given gmail connector 的授權放在資料目錄的 secrets 底下
    When 任何一段的 agent 想讀 secrets 底下的檔案
    Then 工具拒絕，因為路徑不在這張工作單的資料夾內

  Scenario: 沒有接口的 app 透過 Drive 資料夾接入
    Given 外部工具把一篇 app 文章放進 Google Drive 的指定資料夾
    When drive connector 依排程掃描
    Then 那篇文章跟其他來源一樣進 inbox 並開單
