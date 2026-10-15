Feature: 素材投入與訂閱源接入
  # 需求：FR-IN-01、FR-MT-02、FR-IN-02、FR-CH-01、NFR-SEC-01｜故事：US-21｜決策：ADR-0012、ADR-0019、ADR-0028

  Scenario Outline: 只丟素材，先收進素材清單，不開工單
    Given 創作者在一個對話裡「<kind>」，沒說要做什麼
    When 送出
    Then 素材清單多一個附件，原始內容寫一次不再改（ADR-0028 §3.5）
    And 沒有開任何工單，inbox 也沒有多素材項目
    And 對話的 agent 回一句收到了什麼，問要不要分析

    Examples:
      | kind     |
      | 貼網址   |
      | 貼文字   |
      | 上傳檔案 |

  @happy-path @smoke
  Scenario Outline: 丟素材並說要分析，就開分析工單
    Given 創作者在一個對話裡「<kind>」並說「分析這篇」
    When 送出
    Then inbox 的 manual 底下多一個素材項目，附出處、時間與內容 hash
    And 對話的 agent 用 analyze 工具開出一張分析工單
    And 對話裡顯示這次分析跑到哪

    Examples:
      | kind     |
      | 貼網址   |
      | 貼文字   |
      | 上傳檔案 |

  Scenario: 同一份素材在同一個對話不重複分析
    Given 這個對話已經分析過一篇文章
    When 創作者再丟一次內容 hash 相同的文章
    Then 不開新的分析，對話的 agent 指出之前那份報告
    And 創作者明說要重做時才開新的分析

  @sad-path
  Scenario: 讀不到的網址不繞過
    Given 創作者貼的網址回應 403 或是付費牆
    When 系統抓取
    Then 素材項目標成讀不到，並寫明原因
    And 不嘗試任何繞過方式
    And 對話裡說明讀不到的原因，提示改用貼文字或上傳檔案

  # 以下是訂閱源（FR-IN-02，Later）的情境；自動投入只跑到分析報告（ADR-0019）
  @happy-path
  Scenario: 訂閱源依排程自動抓取並開始分析
    Given feeds.yaml 裡有一個 rss 訂閱源，排程是每天 07:00，指定了配方與目標
    When 到了 07:00，且有一篇新文章
    Then 那篇文章進 inbox
    And 自動開出一張分析工單，報告放進這個訂閱源的對話

  Scenario: 同一篇不會開兩張單
    Given 一篇文章已經進過 inbox
    When 同一個訂閱源或另一個訂閱源再抓到內容 hash 相同的文章
    Then 不會新增素材項目，也不會再開工單

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
    Then 工具拒絕，因為路徑不在這張工單的資料夾內

  Scenario: 沒有接口的 app 透過 Drive 資料夾接入
    Given 外部工具把一篇 app 文章放進 Google Drive 的指定資料夾
    When drive connector 依排程掃描
    Then 那篇文章跟其他來源一樣進 inbox 並開始分析
