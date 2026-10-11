Feature: 執行器：照配方跑、跑得動、可恢復
  # 需求：FR-RC-01、FR-MD-01、NFR-RUN-01～08、NFR-PAR-01～03、NFR-SEC-02、NFR-KEEP-01～02｜故事：US-11｜決策：ADR-0009、ADR-0010、ADR-0011

  @happy-path @smoke
  Scenario: 新增配方不用改程式
    Given harness/recipes 底下新增一份配方，列出三段與各自的 skill、等級、產出
    When 創作者用這份配方開一張工單
    Then 執行器照這份配方逐段執行

  Scenario: 每段只載入這段的 skill，沒有內建工具
    When 執行器為某一段啟動 Pi
    Then Pi 只載入該段的 skill
    And Pi 的內建讀寫檔與 shell 工具都是關閉的

  @security
  Scenario: 工具只能碰這張工單的資料夾
    Given 某段的 agent 要讀 ../another-job/draft.v1.md
    When 工具解析路徑
    Then 拒絕，因為路徑不在這張工單的資料夾內

  Scenario: 寫檔只增不改
    Given 這段的輸出目錄已經有 digest.md
    When agent 再寫一次 digest.md
    Then 工具拒絕覆寫

  @smoke
  Scenario: 開跑前檢查擋下
    Given Ollama 沒有在跑
    When 執行器要開始一張工單
    Then 不開始，並說明是「Ollama 沒回應」這一項

  @sad-path
  Scenario: 基礎設施故障不升級
    Given 配方的整理段可以升級到 strong
    And Ollama 在這段途中停止回應
    When 執行器處理這次失敗
    Then 不改用 strong
    And 工單進待審，結果是「失敗：Ollama 沒回應」

  @happy-path
  Scenario: 中途當機，重跑不重付
    Given 二創段已經完成 3 次模型呼叫，第 4 次進行中時程序被殺掉
    When 程序重啟
    Then 這張工單從二創段重跑
    And 前 3 次呼叫直接回放存好的回應，帳本記 cache_hit，不收費
    And 從第 4 次開始才真的呼叫模型

  Scenario: 本機 GPU 一次只服務一個請求
    Given 兩張工單同時在跑，都要呼叫 local 模型
    When 兩個請求同時到閘道
    Then 一次只送一個到 Ollama，另一個排隊
    And 排隊的時間記在帳本的 queue_ms

  Scenario: 同時跑的張數有上限
    Given 同時執行的上限是 2，已經有 2 張在跑
    When 又有一張進入排隊
    Then 它等到有一張結束才開始

  Scenario: 版本鎖在產出上
    When 任何一段完成
    Then 產出記錄當時的模型對照、skill 的 git sha 與配方版本

  @smoke
  Scenario: CI 用重播跑完整流程
    Given 閘道在重播模式，固定的小案例有錄好的回應
    When CI 從投入跑到待審
    Then 全程沒有任何付費呼叫
    And 工單進待審，結果是「完成」

  Scenario: 重播模式沒錄過的請求直接報錯
    Given 閘道在重播模式
    When 收到一個沒錄過的請求
    Then 回報錯誤，不轉送到任何真的模型
