Feature: 帳本、預算與帳單
  # 需求：FR-LG-01、FR-LG-02、NFR-COST-01～07、NFR-OBS-01、NFR-SEC-04｜故事：US-08｜決策：ADR-0011

  @happy-path @smoke
  Scenario: 每次模型呼叫都入帳
    When 某段透過閘道完成一次模型呼叫
    Then 該工單的 ledger.jsonl 多一筆
    And 這筆有段、第幾次、等級、實際模型、輸入／輸出／快取 token、金額、第一個字的時間、總延遲、排隊時間、狀態與請求／回應 hash

  Scenario: 本機呼叫也入帳
    When 某段呼叫 local 模型
    Then 帳本記下 token 數與 GPU 秒數，金額是 0

  Scenario: 搜尋、抓取與腳本也入帳
    When 某段呼叫 web_search、fetch_url 或 run_script
    Then 帳本各多一筆，類型分別是 search、fetch、script

  @sad-path
  Scenario: 預算先預留，不夠就拒絕
    Given 這張工單的美元預算剩 US$0.02
    When 二創段送出一個估計最高花費 US$0.05 的請求
    Then 閘道拒絕，回傳 budget_exceeded
    And 工單進待審，結果是「未完成：預算用完」
    And 沒有任何超出預算的實際花費

  Scenario: 結算時退回多預留的部分
    Given 一個請求預留了 US$0.05
    When 實際花費是 US$0.03
    Then 這張單的剩餘預算只扣 US$0.03

  @happy-path
  Scenario: 單張帳單
    When 一張工單進待審
    Then bill.md 列出總花費、每段花費與時間、用了哪些模型、升級幾次、退件重跑多花多少

  Scenario: 每月總表
    When 創作者打開帳單頁
    Then 看到本月依 skill、模型、訂閱源分的花費，本機與雲端的比例
    And 看到每篇通過文章的平均成本，退件重跑的花費也算進去

  @security
  Scenario: 雲端金鑰不外流
    When 執行器啟動 Pi
    Then 傳給 Pi 的只有代表工單的識別字串，不是 Anthropic 金鑰
    And 帳本與事件紀錄裡沒有任何金鑰
