Feature: 不重做、跑得動、留得住
  # 需求：NFR-01～NFR-23（PRD §8）｜ADR-0004、ADR-0005、ADR-0006

  @happy-path @smoke
  Scenario: 開跑前檢查全過才開始
    Given 本機模型服務在跑、API key 有效、搜尋額度與硬碟空間足夠
    When 創作者建立一次研究執行
    Then 研究執行開始

  @sad-path
  Scenario: 本機模型服務沒在跑時，一開始就停下並說明原因
    Given 本機模型服務沒有回應
    When 創作者建立一次研究執行
    Then 研究執行不會開始
    And 創作者看到「本機模型服務沒有回應」
    And 沒有任何付費呼叫發生

  @sad-path
  Scenario: 本機模型服務中途斷掉時，不偷偷改用付費模型
    Given 研究執行正在產生候選問題
    When 本機模型服務中途停止回應
    Then 研究執行停在可重試的失敗
    And 沒有改用雲端模型產生候選問題

  @happy-path
  Scenario: 同一篇種子文章再跑一次，沿用之前的擷取與搜尋
    Given 同一篇種子文章昨天已經完整跑過一次
    When 創作者用沿用快取的設定再跑一次
    Then 這次沒有重新擷取來源，也沒有重新搜尋
    And 執行紀錄標明哪些結果取自快取、原本來自哪一次執行

  @happy-path
  Scenario: 只改寫作的 prompt 後重跑，只有寫作花錢
    Given 同一篇種子文章已經完整跑過一次
    And 寫作節點的 prompt 升了一個版本
    When 創作者再跑一次
    Then 只有寫作節點發出付費呼叫

  @edge-case
  Scenario: 預算不夠時，呼叫前就停下
    Given 這次研究執行的美元上限只剩下一次 Reviewer 呼叫所需的一半
    When 系統準備呼叫 Reviewer
    Then 呼叫不會送出
    And 研究執行停在等人調預算

  @sad-path
  Scenario: 節點輸出格式不合時，有限次重試後交給人
    Given 研究規劃的輸出連續 3 次不符合格式
    When 系統處理研究規劃
    Then 系統最多重試 2 次
    And 研究執行停下並等待人工處理

  @happy-path
  Scenario: 來源網頁改版後，舊的證據仍找得回來
    Given 一個來源在上次執行時被擷取並存檔
    And 那個網頁之後改版了
    When 創作者打開上次執行的證據矩陣
    Then 看到的原文段落是上次擷取當時的版本

  @happy-path
  Scenario: 兩篇同時跑，本機 GPU 一次只服務一篇
    Given 創作者同時建立了兩次研究執行
    When 兩次執行都到了產生候選問題的步驟
    Then 本機模型同一時間只處理其中一篇的請求
    And 另一篇等待之後接著處理，沒有改用付費模型

  @happy-path
  Scenario: 一篇在等雲端模型時，另一篇照常前進
    Given 第一篇研究執行正在等研究規劃的回應
    When 第二篇研究執行需要本機模型
    Then 第二篇不必等第一篇完成就能使用本機模型

  @edge-case
  Scenario: 處理中的 worker 被關掉，工作會被重新處理且不重扣錢
    Given 一篇研究執行已完成搜尋，正在等 Reviewer 回應
    When 處理它的 worker 被強制關掉
    Then 另一個 worker 接手這篇研究執行
    And 已完成的搜尋沒有重新執行，也沒有重複扣費

  @edge-case
  Scenario: 同一篇研究執行不會被兩個 worker 同時推進
    Given 同一篇研究執行的推進工作被送出了兩次
    When 兩個 worker 同時拿到這兩個工作
    Then 只有一個 worker 推進這篇研究執行
    And 另一個工作直接結束，不做任何外部呼叫

  @edge-case
  Scenario: 佇列資料遺失後，沒做完的研究執行會被重新排回
    Given 有兩篇研究執行還在進行中
    When 佇列服務重啟且待辦資料全部遺失
    Then 系統的對帳工作把這兩篇重新排回佇列
    And 它們從各自的停點接著跑
