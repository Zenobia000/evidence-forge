# ADR-0006：多個 Run 並行 — Celery＋Redis 排工作、PostgreSQL 存狀態、依資源限流

> **狀態：** 已接受 | **日期：** 2026-10-10 | **決策者：** 專案擁有者
> **後續修正：** 由 ADR-0010 取代——工作單資料夾就是佇列，閘道的旗號就是資源鎖；不用 Celery、Redis、PostgreSQL。本文保留當時的理由。
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **修正 ADR-0003 的持久化部分**：checkpoint 與 metadata 由 SQLite 改為 PostgreSQL。ADR-0003 的「State 只存 ID、內容放檔案儲存區」不變。

## 1. 背景與問題

- **上下文：** v1.0 的設計是一次只跑一個 Run，理由是避免搶 GPU（PRD v1.1 §8）。專案擁有者要求系統能**同時處理多篇**，並要有 worker 與 queue 機制來管理機器資源與排程。
- **問題：**
  - 一篇 Run 大部分時間在等外部回應（Opus、Tavily、網頁）。一次一篇時，等待期間 GPU 和其他 API 額度都閒著。
  - 多個 worker 同時寫 SQLite 會互相鎖住；LangGraph 官方文件也把 `SqliteSaver` 定位為開發用，正式環境用 `PostgresSaver`。
  - 限制的單位應該是「資源」，不是「Run」：GPU 一次只能好好服務一個 Qwen 呼叫，Anthropic 與 Tavily 各有速率上限。
- **驅動因素：** 並行、資源可控、worker 掛掉不掉工作也不重複扣錢（ADR-0004）、素材不因佇列出事而遺失（ADR-0005）。
- **約束：** 專案規則是外部服務一律用 Docker，不裝在主機上。

## 2. 考量的選項

| 選項 | 做法 | 放棄的理由 |
| :--- | :--- | :--- |
| 維持 SQLite，開少量 worker | 多個 process 共用 SQLite（WAL） | 寫入互鎖，worker 越多越卡，並行的效果被打折 |
| PostgreSQL 一個就好 | 用資料表＋`SKIP LOCKED` 當佇列 | 可行且少一個服務，但擁有者偏好業界標準的佇列框架（重試、確認、路由、排程都現成） |
| **Celery＋Redis＋PostgreSQL** | Redis 當 broker，Celery 排工作，PostgreSQL 存狀態與 checkpoint | **採用** |
| Dramatiq／RQ | 較輕的佇列框架 | Dramatiq 社群與案例少很多；RQ 的速率限制與重送要自己補 |

## 3. 決策

### 3.1 誰負責什麼

| 元件 | 負責 | 不負責 |
| :--- | :--- | :--- |
| **PostgreSQL**（Docker） | 唯一的狀態來源：Run 狀態、LangGraph checkpoint（`PostgresSaver`）、操作紀錄（ADR-0004）、快取索引（ADR-0005）、審核決定 | — |
| **Redis**（Docker） | Celery 的 broker：只放「待辦工作」；資源限流的計數器 | **不存任何不能重建的東西** |
| **Celery worker** | 取工作、推進 Run | 不決定流程怎麼走（那是 LangGraph 圖的事，ADR-0001） |
| **檔案儲存區** | 原文、圖片、MDX（ADR-0003、0005） | — |

### 3.2 工作單位

- 一個 Celery 工作＝「把某個 Run 往前推進」：載入 checkpoint，跑到下一個 interrupt、結束、或預算停止點為止。
- 建立 Run 和定題 Gate 送出決定，都是「排一個推進工作」。
- **同一個 Run 同時只能有一個 worker 在推進**：開始前取 PostgreSQL 的 advisory lock（以 `run_id` 為鍵），取不到就放棄這個工作。這擋掉了重送造成的重複執行。

### 3.3 資源限流：限資源，不限 Run

| 資源 | 預設上限 | 機制 |
| :--- | :--- | :--- |
| 本機 GPU（Ollama／Qwen） | 同時 1 個呼叫 | Redis 全域 semaphore，在 `clients` 層取得與釋放 |
| Anthropic API | 照帳號的速率上限 | Redis token bucket |
| Tavily API | 照方案的速率上限 | Redis token bucket |
| 同時推進的 Run 數 | worker 併發數（設定值，起始 4） | Celery `concurrency` |

上限都是設定值。資源被占用時，工作在 `clients` 層等待，**不會改用別的模型或服務**（PRD NFR-13）。

### 3.4 不掉工作、不重扣錢

- Celery 工作選項 `acks_late=True`，加上全域設定 `task_reject_on_worker_lost=True`：worker 中途死掉，工作會被重送。
- 重送是安全的：ADR-0004 的操作紀錄保證做過的副作用不再做，advisory lock 保證不會兩個 worker 同時推進同一個 Run。
- Redis 開 AOF 持久化（`appendfsync everysec`），資料放 Docker volume。
- **Redis 的資料就算全部遺失也能恢復**：定期的對帳工作（Celery beat）掃 PostgreSQL，把「狀態是執行中、但心跳逾時」或「有待辦、但佇列裡沒有」的 Run 重新排進去。

### 3.5 誰寫 Run 狀態

- **只有 worker 寫 Run 的流程狀態。**
- CLI／API 只做三件事：建立 Run、寫入創作者的決定（定題、預算調整、發布核准）、排推進工作。
- 讀狀態一律讀 PostgreSQL，不讀 Redis。

## 4. 後果

- **正面：** 多篇可以同時跑：一篇在等 Opus，另一篇用 GPU。GPU 永遠不會被兩個呼叫同時搶。worker 掛掉、Redis 重啟都不掉工作、不重扣錢。
- **負面：**
  - 多兩個 Docker 服務（PostgreSQL、Redis）要維運。
  - worker 名額在等 GPU 時是占著的：所有 Run 剛好都卡在 Qwen 時，並行度會被 GPU 吃掉。規模小時可以接受。
  - 測試要多一層：CI 用 Celery 的 eager 模式或真的起服務，兩種都要有。
- **影響範圍：** 部署（Docker Compose）、`clients`（限流）、所有改 Run 狀態的程式、備份（改用 `pg_dump`）、狀態機設計、PRD §8。
- **重新評估觸發：**
  - 監控顯示 worker 名額大量耗在等 GPU 時，把 Qwen 呼叫拆成獨立佇列、由專屬的 GPU worker 處理。
  - 要跨機器擴充，或要多使用者時。

## 5. 參考

- LangGraph Persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>（2026-10-10 核實：`SqliteSaver` 為本機開發用，正式環境用 `PostgresSaver`）
- Celery 文件 — Tasks：<https://docs.celeryq.dev/en/stable/userguide/tasks.html>（2026-10-10 核實：`acks_late` 會讓 worker 中途崩潰的工作「may be executed multiple times」，官方要求工作必須冪等——由 ADR-0004 保證；`task_reject_on_worker_lost` 是全域設定）
- Redis 持久化：<https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/>（2026-10-10 核實：`appendfsync everysec` 是預設，災難時最多遺失 1 秒的寫入）
