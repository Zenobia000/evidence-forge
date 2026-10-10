# 架構草案 v0.2 — Evidence-led Editorial Studio

> **版本：** v0.2 草案 | **日期：** 2026-10-10 | **狀態：** 規劃參考，**不是事實層**
> 依 `docs/templates/vibecoding/05_architecture_and_design_document.md`（v2.0）撰寫。前一版：`2026-10-10-architecture-draft.md`（v0.1，不再改）。
>
> **和 v0.1 的差異：**
> 1. 多個 Run 並行：Celery＋Redis 排工作、PostgreSQL 存狀態、依資源限流（ADR-0006）。v0.1 的「一次一個 Run、SQLite」作廢。
> 2. 不重做、跑得動、留得住：跨 Run 快取、只增不改的檔案儲存區、開跑前檢查（PRD §8、ADR-0005）。
> 3. 資料模型只存檔案參照，不存全文（ADR-0003）。
> 4. v0.1 §9 的七個問題已拍板，見 §9（編號照 `20-state/open-questions.md`）。
>
> **這份為什麼在 `30-snapshot/`：** `docs/README.md` 規定架構跟實作它的程式一起進來。哪一段做出來，就把那一段寫進 `10-truth/architecture.md`，章節標題標出它實作的需求編號。
> 需求 → `10-truth/requirements/prd.md`；決策理由 → `10-truth/adr/`；詞 → `CONTEXT.md`；Run 狀態 → `2026-10-10-run-state-machine-draft.md`；畫面 → `design/`。

---

## 第 1 部分：架構總覽

### 1.1 C4 模型

#### 1.1.0 命名防呆

| 術語 | 指什麼 | 勿混淆 |
| :--- | :--- | :--- |
| C4 System Context／Container／Component | 架構圖的縮放層級 | ≠ 路線圖的 R0～R5 |
| C4 Container | 可獨立執行的 runtime（process、資料庫、檔案儲存） | ≠ Python package、≠ LangGraph 節點、≠ Docker container（Docker 是部署方式，見 §5） |
| LangGraph 節點 | Graph 裡的一個步驟函式，在 Celery worker 裡執行 | ≠ C4 Component |
| Worker | Celery worker process | ≠ Ollama（模型服務是另一個 Container） |
| 推進工作（Job） | 佇列裡的一則訊息：把某個 Run 往前推到下一個停點 | ≠ Run（`CONTEXT.md`） |

#### 1.1.1 外部系統清單

| 類別 | 外部系統 | 何時 | 用途 |
| :--- | :--- | :---: | :--- |
| 資料源 | 種子文章與引用來源的網站 | R1 | HTTP 擷取原文 |
| 資料源 | Tavily Search／Extract API | R2 | 定向搜尋、擷取 |
| 模型 | Anthropic API（Opus 5.5、Sonnet 5.5） | R2 | 規劃、複核、寫作（ADR-0002） |
| 交易 | 無 | — | 不涉及交易 |
| 推送 | 無 | — | 不發通知；Run 停在 Gate 時創作者自己回來看（Q-05） |
| 發布 | 網站（形式未定） | R4 | 網站草稿預覽（FR-12）；網站還沒架，R3 結束前決定（Q-06） |
| 備份 | 第二顆本機硬碟（不是外部服務） | R0 | 每天備份資料庫與檔案儲存區（Q-03） |
| 雲端 IaaS | 無 | — | 全部跑在創作者的工作站 |

#### 1.1.2 Container 清單

| Container | 類型 | 技術 | 何時 | L3 |
| :--- | :--- | :--- | :---: | :---: |
| CLI | 命令列 | Python，同一個套件 | R1 | 略：薄殼，呼叫服務層 |
| Web UI | 使用者介面 | FastAPI＋Jinja2 伺服器端頁面＋HTMX，套用設計系統（§6.3） | R2 | 見 `design/2026-10-10-frontend-architecture-draft.md` |
| API Server | process | FastAPI | R2 | 表代圖（§3.3） |
| Graph Worker | process × N | Celery worker＋LangGraph | R0 | ✅ L3 |
| Scheduler | process × 1 | Celery beat（對帳工作、備份排程） | R0 | 略：只排兩個週期工作 |
| Model Server | process | Ollama＋Qwen3.5 9B（主力就是 9B，Q-02） | R1 | 略：第三方 |
| PostgreSQL | 資料庫 | PostgreSQL＋LangGraph `PostgresSaver` | R0 | 表代圖：§4.1 ER |
| Redis | 訊息 broker | Redis（AOF） | R0 | 略：只放待辦與限流計數器 |
| Artifact Store | 檔案儲存 | 本機檔案系統，hash 定址、只增不改，`raw/`、`derived/` 分開 | R0 | 略：沒有內部元件 |
| Object Storage | 檔案儲存 | 未定 | Later | 略 |

#### L1 — System Context

```mermaid
flowchart TB
    creator["創作者<br/>(Person)"]

    subgraph boundary["Evidence-led Editorial Studio"]
        sys["Evidence-led Editorial Studio"]
    end

    web[("種子文章與引用來源的網站")]
    tavily[("Tavily API")]
    anthropic[("Anthropic API")]
    site[("網站（形式未定）")]

    creator -->|"終端機／HTTPS：建立研究、選題、定題、核准發布"| sys
    sys -->|"HTTPS GET：擷取原文"| web
    sys -->|"HTTPS：搜尋與擷取"| tavily
    sys -->|"HTTPS：規劃、複核、寫作"| anthropic
    sys -.->|"HTTPS：送網站草稿預覽（R4）"| site
```

#### L2 — Container（MVP：R0～R3）

CLI 從 R1 就有；Web UI 與 API Server 從 R2 開始（虛線）。

```mermaid
flowchart TB
    creator["創作者"]

    subgraph boundary["Evidence-led Editorial Studio（本機）"]
        cli["《container》<br/>CLI"]
        ui["《container》<br/>Web UI<br/>Jinja2 + HTMX<br/>R2+"]
        api["《container》<br/>API Server<br/>FastAPI<br/>R2+"]
        redis[("《broker》<br/>Redis")]
        worker["《container》<br/>Graph Worker × N<br/>Celery + LangGraph"]
        beat["《container》<br/>Scheduler<br/>Celery beat"]
        ollama["《container》<br/>Model Server<br/>Ollama + Qwen3.5 9B"]
        pg[("《database》<br/>PostgreSQL")]
        store[("《file store》<br/>Artifact Store")]
    end

    web[("來源網站")]
    tavily[("Tavily API")]
    anthropic[("Anthropic API")]

    creator -->|"終端機指令"| cli
    creator -.->|"HTTPS（R2+）"| ui
    ui -.->|"HTTP：HTMX 輪詢與表單"| api
    cli -->|"SQL：建立 Run、寫入決定、讀狀態"| pg
    cli -->|"Redis：排推進工作"| redis
    api -.->|"SQL：建立 Run、寫入決定、讀狀態"| pg
    api -.->|"Redis：排推進工作"| redis
    api -.->|"file I/O：讀草稿與清單檔"| store
    redis -->|"Redis：取工作"| worker
    worker -->|"SQL：checkpoint、Run 狀態、操作紀錄、快取索引"| pg
    worker -->|"Redis：GPU semaphore、API 限流"| redis
    worker -->|"HTTP：Ollama API"| ollama
    worker -->|"file I/O：寫原文、圖、MDX"| store
    worker -->|"HTTPS"| web
    worker -->|"HTTPS"| tavily
    worker -->|"HTTPS"| anthropic
    beat -->|"Redis：排對帳工作"| redis
```

規則（ADR-0006）：

- **PostgreSQL 是唯一的狀態來源。** Redis 只放待辦工作與限流計數器，資料遺失可從 PostgreSQL 重建。
- **只有 worker 寫 Run 的流程狀態。** CLI／API 只建立 Run、寫入創作者的決定、排推進工作。
- CLI／API 與 worker 不直接通話。

#### L2 — Container（Target：全部階段完成後）

```mermaid
flowchart TB
    creator["創作者"]

    subgraph boundary["Evidence-led Editorial Studio"]
        ui["《container》<br/>Web UI"]
        api["《container》<br/>API Server"]
        redis[("《broker》<br/>Redis")]
        worker["《container》<br/>Graph Worker × N"]
        gpu["《container》<br/>GPU Worker<br/>專屬佇列"]
        beat["《container》<br/>Scheduler"]
        ollama["《container》<br/>Model Server"]
        pg[("《database》<br/>PostgreSQL")]
        obj[("《object store》<br/>Object Storage")]
    end

    ext[("來源網站 / Tavily / Anthropic")]
    site[("網站")]

    creator -->|"HTTPS"| ui
    ui -->|"HTTP"| api
    api -->|"SQL"| pg
    api -->|"Redis"| redis
    redis -->|"Redis"| worker
    redis -->|"Redis：gpu 佇列"| gpu
    gpu -->|"HTTP"| ollama
    worker -->|"SQL"| pg
    worker -->|"HTTPS：S3 相容 API"| obj
    worker -->|"HTTPS"| ext
    worker -->|"HTTPS：建立草稿預覽"| site
    beat -->|"Redis"| redis
```

Target 和 MVP 的差別：Qwen 呼叫拆成專屬佇列與 GPU worker（ADR-0006 的重評觸發），內容改放物件儲存，接上網站。

#### L3 — Component（zoom：Graph Worker）

```mermaid
flowchart TD
    subgraph container["Container: Graph Worker"]
        subgraph app["Application"]
            tasks["tasks<br/>Celery 工作：advance_run、reconcile"]
            runlock["runlock<br/>PostgreSQL advisory lock"]
            graph_def["graph<br/>節點與轉移規則"]
            nodes["nodes<br/>scout / director / reviewer / writer / gates"]
            effects["effects<br/>副作用節點（ADR-0004）"]
        end
        subgraph domain["Domain"]
            models["models<br/>Pydantic 資料契約"]
            rules["rules<br/>G1～G5、授權、補搜上限、預算"]
        end
        subgraph infra["Infrastructure"]
            prompts["prompts<br/>Prompt registry"]
            limiter["limiter<br/>GPU semaphore、API token bucket"]
            clients["clients<br/>Tavily / Anthropic / Ollama / HTTP"]
            ledger["ledger<br/>操作紀錄（Run 內冪等）"]
            cache["cache<br/>跨 Run 快取（ADR-0005）"]
            artifacts["artifacts<br/>hash 定址、只增不改"]
        end
    end

    tasks -->|"call：先取鎖"| runlock
    tasks -->|"call"| graph_def
    graph_def -->|"call"| nodes
    graph_def -->|"call"| effects
    nodes -->|"import"| models
    nodes -->|"import"| rules
    nodes -->|"call"| prompts
    effects -->|"call：1 先查"| ledger
    effects -->|"call：2 再查"| cache
    effects -->|"call：3 取得資源"| limiter
    effects -->|"call：4 才呼叫"| clients
    effects -->|"call"| artifacts
    effects -->|"import"| models
```

副作用節點的順序固定：**這個 Run 做過沒（ledger）→ 別的 Run 做過沒（cache）→ 扣預算（rules）→ 取得資源（limiter）→ 真的呼叫（clients）→ 結果存檔（artifacts）並記錄**。Domain 層不指向 Infrastructure。

#### 1.1.3 C4 自檢

- [x] L1～L3 各一張以上，一圖一層級；L3 只對應 Graph Worker
- [x] 每個 Container 都有 L3 或跳過理由
- [x] 外部系統五類都列了，沒有的明寫
- [x] 有 Target 圖；有 Sequence（§3.4）與 Deployment（§5.1）

### 1.2 DDD 戰略設計

| 限界上下文 | 主要落在 | 需求 |
| :--- | :--- | :--- |
| 來源（Sourcing） | Graph Worker | FR-01、FR-04 |
| 研究（Research，核心） | Graph Worker | FR-02、FR-03、FR-05、FR-11 |
| 編輯（Editorial） | Graph Worker、Web UI | FR-06、FR-07 |
| 素材（Assets） | Graph Worker | FR-08 |
| 發布（Publishing） | Graph Worker、API Server | FR-09、FR-12 |
| 執行（Run） | Graph Worker、Scheduler、PostgreSQL | FR-10、NFR-20～23 |

```mermaid
graph LR
    subgraph ext["外部"]
        T[Tavily / 網站 / Anthropic / Ollama]
    end
    subgraph sourcing["Sourcing"]
        S[來源與引用鏈]
    end
    subgraph research["Research（核心）"]
        R[研究問題、論點、證據矩陣]
    end
    subgraph editorial["Editorial"]
        E[選題、定題、草稿]
    end
    subgraph assets["Assets"]
        A[素材與授權]
    end
    subgraph publishing["Publishing"]
        P[內容包、預覽]
    end
    subgraph run["Run"]
        X[狀態、預算、排程]
    end

    T -.->|"ACL：隔離外部回應格式"| S
    S ==>|"PL：Source 模型"| R
    R ==>|"CS：Research 供應證據包"| E
    A ==>|"PL：Asset 模型"| E
    E ==>|"CS：Editorial 供應草稿"| P
    A ==>|"CS：只供應已核准素材"| P
    X ==>|"OHS：所有上下文透過 Run 取得預算與狀態"| R
```

| DDD 元素 | 預計位置 | 說明 |
| :--- | :--- | :--- |
| Aggregate Root | `ResearchRun` | 一致性邊界：狀態轉移、預算、Gate 決定都經過它；轉移規格見狀態機草案 |
| Entity | `Source`、`Asset`、`ResearchQuestion` | 有身分，狀態會變 |
| Value Object | `ClaimEvidence`、`QuoteSpan`、`Budget` | 不可變 |
| Domain Event | `ThesisApproved`、`ThesisRejected`、`BudgetExhausted` | 記在審核紀錄與執行紀錄 |
| Domain Service | `rules` | G1～G5、授權、預算判斷，純函式 |
| Repository | `artifacts`、PostgreSQL 存取 | 持久化抽象 |
| Anti-Corruption Layer | `clients` | 外部回應轉成本專案的模型 |
| Specification | `rules` 裡的授權規則 | `license in {unknown, denied}` → 擋下 |

### 1.3 分層

| 層 | 預計位置 | 職責 |
| :--- | :--- | :--- |
| Domain | `src/evidence_forge/domain/` | 資料契約、品質關卡、授權與預算規則 |
| Application | `src/evidence_forge/graph/`、`src/evidence_forge/services/` | LangGraph 圖、節點、副作用節點；CLI／API／Web 共用的服務層（建立 Run、寫入決定、排工作） |
| Infrastructure | `src/evidence_forge/infra/` | client、限流、Prompt registry、操作紀錄、快取、檔案儲存、Celery 設定 |
| Interface | `src/evidence_forge/cli/`、`api/`、`web/` | 三個入口都只呼叫服務層（Q-16） |

### 1.4 技術選型

| 分類 | 選用 | 理由 | 備選 | ADR |
| :--- | :--- | :--- | :--- | :--- |
| 語言與套件管理 | Python 3.12、uv | repo 已有骨架 | — | — |
| 工作流引擎 | LangGraph StateGraph | interrupt＋checkpoint 內建 | Prefect 3、Pydantic Graph | 0001 |
| 任務佇列 | Celery＋Redis | 業界標準；重試、確認、路由、排程都現成 | PostgreSQL `SKIP LOCKED`、Dramatiq、RQ | 0006 |
| 狀態與 checkpoint | PostgreSQL＋`PostgresSaver` | 多 worker 同時寫；官方正式環境方案 | SQLite | 0006 |
| 內容 | 本機檔案，hash 定址、只增不改 | 可回溯、自動去重 | 物件儲存（Later） | 0003、0005 |
| 資料契約 | Pydantic | schema 驗證 | — | — |
| API／Web | FastAPI＋Jinja2＋HTMX | 不引入 Node 建置鏈；與 Pydantic 同一套模型 | SPA | — |
| 本機模型 | Ollama＋Qwen3.5 9B | 3090 可跑 | — | 0002 |
| 雲端模型 | Opus 5.5、Sonnet 5.5 | 判斷力／長文 | — | 0002 |
| 搜尋 | Tavily | 單一搜尋商 | — | — |
| 外部服務部署 | Docker Compose（PostgreSQL、Redis） | 專案規則：外部服務一律 Docker | — | 0006 |

---

## 第 2 部分：需求摘要

功能需求見 PRD §4，非功能需求見 PRD §8。架構上最吃重的：

| 需求 | 架構上怎麼接 |
| :--- | :--- |
| FR-06 中斷恢復 | `interrupt()`＋`PostgresSaver`，`thread_id`＝`run_id`；送出決定＝排一個推進工作 |
| NFR-01 不重複收費 | 副作用節點＋操作紀錄（ADR-0004） |
| NFR-02、03 不重做 | 跨 Run 快取（ADR-0005）；改論點只從寫作節點恢復 |
| NFR-04 預算 | 呼叫前從 `ResearchRun.budget` 預留，不足就停在等人調預算 |
| NFR-08 跑得動 | 建立 Run 前檢查一次（不過就不建立）；worker 推進前再查一次 |
| NFR-20 多篇同時 | Celery worker × N；advisory lock 保證同一 Run 只有一個 worker 在推進 |
| NFR-21 限資源 | `limiter`：GPU semaphore＝1，API token bucket |
| NFR-22、23 不掉工作 | `acks_late`＋`task_reject_on_worker_lost`；beat 對帳工作從 PostgreSQL 重排 |

---

## 第 3 部分：系統設計

### 3.1 架構模式

模組化單體＋任務佇列＋獨立的模型服務。CLI、API、Web、worker 是同一個程式碼庫的不同入口。

**工作單位（ADR-0006 §3.2）：** 一個推進工作＝載入某個 Run 的 checkpoint，跑到下一個 interrupt、結束或預算停止點。

### 3.3 元件職責

| 元件 | 職責 | 依賴 |
| :--- | :--- | :--- |
| CLI | 建立 Run、看狀態、在終端機選題與定題（R1） | 服務層 |
| API Server | 給 Web UI：建立 Run、寫入決定、讀狀態與草稿（R2 起） | 服務層、Artifact Store |
| 服務層 | 建立 Run（含開跑前檢查）、寫入決定、排推進工作。三個入口共用 | PostgreSQL、Redis |
| Graph Worker | 取工作、取鎖、推進 Run、寫 Run 狀態 | 全部外部系統、Model Server、PostgreSQL、Redis、Artifact Store |
| Scheduler | 每分鐘對帳（心跳逾時或佇列遺失的 Run 重排）；每天觸發備份 | Redis、PostgreSQL |

### 3.4 關鍵流程

#### 建立 Run 到定題 Gate

```mermaid
sequenceDiagram
    actor C as 創作者
    participant S as CLI / Web（服務層）
    participant PG as PostgreSQL
    participant RQ as Redis
    participant W as Graph Worker
    participant O as Model Server
    participant A as Anthropic / Tavily

    C->>S: 建立 Run（種子 URL、預算）
    S->>S: 開跑前檢查（服務、金鑰、額度、硬碟）
    alt 檢查不過
        S-->>C: 不建立 Run，說明哪一項沒過
    else 通過
        S->>PG: INSERT Run
        S->>RQ: 排 advance_run(run_id)
        RQ->>W: 交付工作
        W->>PG: 取 advisory lock(run_id)
        W->>PG: 查操作紀錄、快取
        W->>W: 擷取來源（沒抓過才抓），原文存 raw/
        W->>RQ: 取得 GPU semaphore
        W->>O: Scout（HTTP）
        O-->>W: 3–5 題
        W->>RQ: 釋放 GPU semaphore
        W->>PG: checkpoint；狀態＝等選題
        W-->>RQ: ack
        C->>S: 送出選定或改寫的題目
        S->>PG: 寫入決定
        S->>RQ: 排 advance_run(run_id)
        RQ->>W: 交付工作（可能是另一個 worker）
        W->>A: Director、搜尋、Reviewer（各自限流、先預留預算）
        W->>PG: checkpoint；狀態＝等定題（或等人工決定）
    end
```

#### worker 中途死掉

```mermaid
sequenceDiagram
    participant RQ as Redis
    participant W1 as Worker 1
    participant W2 as Worker 2
    participant PG as PostgreSQL
    participant A as Anthropic

    RQ->>W1: advance_run(run_id)
    W1->>PG: 取 lock、搜尋已記錄完成
    W1->>A: Reviewer 呼叫中
    Note over W1: process 被殺掉，工作未 ack
    RQ->>W2: 重送 advance_run(run_id)
    W2->>PG: 取 lock（W1 的連線已斷，鎖已釋放）
    W2->>PG: 讀 checkpoint、查操作紀錄：搜尋已做過
    W2->>A: 只重做 Reviewer
    W2->>PG: checkpoint、狀態
    W2-->>RQ: ack
```

Reviewer 呼叫在 W1 死掉前若已回應但沒記錄，會被重做一次——這是唯一可能多付的錢，上限是一次呼叫。

### 3.5 進度回報（NFR-24）

進度一律由 worker 寫進 PostgreSQL，畫面只讀（Q-12：HTMX 每幾秒輪詢）。沒有假動畫。

| 畫面要顯示的 | 資料從哪來 | 誰寫 |
| :--- | :--- | :--- |
| 排在第幾位 | `status`＝排隊中的 Run，依 `queued_at` 排序後的名次 | 服務層建立 Run 時寫 `queued_at`；名次在讀取時算 |
| 第幾步、共幾步 | `step_index`／`step_total` | worker 每進一個節點時寫 |
| 這一步跑了多久 | 現在時間 − `step_started_at` | worker 每進一個節點時寫 |
| 在等什麼 | `waiting_on`：`gpu`／`anthropic_rate`／`tavily_rate`／`model_response`／空 | `limiter` 開始等待與取得資源時寫；呼叫雲端模型前後寫 |

- `step_total` 依這次 Run 實際會走的路徑估算（例：補搜多一輪就加一步），所以會變動；畫面顯示「第 5 步／約 9 步」。
- 等人的狀態（等選題、等人工決定、等定題、等人調預算、等發布審核）不顯示進度條，改顯示「輪到你了」與要做的事。
- 寫進度和寫 checkpoint 分開：進度是頻繁的小更新，不放進 checkpoint 的交易裡，免得拖慢流程。

---

## 第 4 部分：資料架構

### 4.1 資料模型（PostgreSQL）

```mermaid
erDiagram
    ResearchRun ||--o{ ResearchQuestion : "產生"
    ResearchRun ||--o{ Source : "擷取"
    ResearchRun ||--o{ Asset : "收集"
    ResearchRun ||--o{ EditorialDecision : "記錄"
    ResearchRun ||--o{ ContentPackage : "交付（改論點會產生新版）"
    ResearchRun ||--o{ Operation : "Run 內冪等"
    Source ||--o{ Source : "parent_refs 引用"
    Source ||--o{ ClaimEvidence : "支持或反對"
    CacheEntry ||--o{ Operation : "命中時記錄來源"

    ResearchRun {
        string run_id PK
        string seed_url
        string status
        int research_round
        string prompt_version
        json model_versions
        json token_usage
        json budget_cap
        json budget_reserved
        datetime heartbeat_at
        string current_step
        int step_index
        int step_total
        string waiting_on
        datetime step_started_at
        datetime queued_at
    }
    ResearchQuestion {
        string question_id PK
        string question
        string premise
        string novelty
        json search_queries
        json evidence_needed
        json risks
    }
    Source {
        string source_id PK
        string canonical_url
        string publisher
        datetime published_at
        datetime fetched_at
        string type
        string content_hash
        json parent_refs
    }
    ClaimEvidence {
        string claim_id PK
        string claim
        string source_id FK
        string quote_span
        string locator
        string stance
        string verification_status
    }
    Asset {
        string asset_id PK
        string source_url
        string origin_page
        string creator
        string license
        string permission
        string usage_status
        string sha256
    }
    EditorialDecision {
        string gate_id PK
        string gate_kind
        string approved_thesis
        string author_opinion
        string decision
        datetime timestamp
    }
    ContentPackage {
        string package_id PK
        string article_mdx_hash
        string outline_hash
        string citation_manifest_hash
        string asset_manifest_hash
        string preview_status
    }
    Operation {
        string run_id FK
        string operation_key
        string result_hash
        string cache_key
        datetime done_at
    }
    CacheEntry {
        string cache_key PK
        string kind
        string result_hash
        string origin_run_id
        datetime created_at
        datetime expires_at
    }
```

和 v1.0 資料契約的差異：

- `ContentPackage` 只存檔案 hash 參照，不存全文（ADR-0003）；一個 Run 可以有多版（回到 Gate 改論點時舊版保留）。
- 新增 `Operation`（ADR-0004）、`CacheEntry`（ADR-0005）。
- `ResearchRun` 加 `research_round`（補搜輪數）、`budget_reserved`（NFR-04）、`heartbeat_at`（對帳用），以及進度欄位（NFR-24，見 §3.5）。
- `EditorialDecision` 加 `gate_kind`：選題、補搜超限、定題、發布審核共用一張表。
- `status` 的列舉照 PRD NFR-09（Q-17、Q-22 已拍板），轉換規格照狀態機草案。LangGraph checkpoint 由 `PostgresSaver` 自己管理的資料表存放，不畫在這裡。

### 4.2 一致性

- **強一致：** 同一個 Run 的 checkpoint、狀態、操作紀錄寫在同一個 PostgreSQL 交易裡。
- **最終一致：** 檔案先寫、再在資料庫記指標；指到但檔案不存在時視為未完成、重做（內容 hash 相同，重做安全）。Redis 的待辦與 PostgreSQL 的狀態不一致時，以 PostgreSQL 為準，由對帳工作修正。

### 4.3 資料分類與保留

- 不處理 PII。API key 放環境變數，不進 State、不進日誌。
- MVP 期間全部保留，只有創作者明確下指令才刪（Q-03）。資料目錄由環境變數指定，不在 repo 裡。

---

## 第 5 部分：部署

### 5.1 MVP Deployment

```mermaid
flowchart TB
    subgraph pc["Deployment Node：創作者的工作站<br/>WSL2 Linux / RTX 3090 24GB"]
        subgraph docker["Docker Compose"]
            pg_i[("《instance》PostgreSQL<br/>:5432 / volume")]
            rd_i[("《instance》Redis<br/>:6379 / AOF / volume")]
        end
        subgraph py["主機：Python 3.12（uv）"]
            cli_i["《instance》CLI"]
            api_i["《instance》API Server<br/>uvicorn :8000（R2+）"]
            w_i["《instance》Graph Worker<br/>concurrency = 4（設定值）"]
            b_i["《instance》Scheduler × 1"]
        end
        o_i["《instance》Ollama（主機）<br/>:11434 / qwen3.5:9b"]
        fs_i[("資料目錄<br/>raw/ derived/")]
    end

    subgraph bk["Deployment Node：第二顆本機硬碟"]
        bk_i[("每日備份")]
    end

    ext[("Tavily / Anthropic / 來源網站")]

    cli_i -->|"SQL :5432"| pg_i
    cli_i -->|"Redis :6379"| rd_i
    api_i -->|"SQL :5432"| pg_i
    api_i -->|"Redis :6379"| rd_i
    w_i -->|"Redis :6379"| rd_i
    w_i -->|"SQL :5432"| pg_i
    w_i -->|"HTTP :11434"| o_i
    w_i -->|"file I/O"| fs_i
    w_i -->|"HTTPS :443"| ext
    b_i -->|"Redis :6379"| rd_i
    pg_i -->|"pg_dump"| bk_i
    fs_i -->|"rsync"| bk_i
```

| 屬性 | 值 |
| :--- | :--- |
| 模式 | 外部服務（PostgreSQL、Redis）用 Docker；Ollama 在主機上直接用 GPU；應用程式在主機用 uv 跑 |
| 並行 | worker 併發數起始 4；GPU 同時 1 個呼叫；API 依速率上限（ADR-0006 §3.3） |
| 高可用 | 不做；靠 checkpoint、操作紀錄、重送與對帳恢復 |
| 備份 | 每天：`pg_dump` 與資料目錄 `rsync` 到第二顆硬碟；Redis 不備份（可從 PostgreSQL 重建）；每月實際還原一次 |
| 監控 | 執行紀錄（FR-10）；不另設監控系統；不發通知 |

Qwen3.5 9B 的 Ollama 預設版本（Q4）約需 6–7 GB VRAM（第三方估算），3090 足夠。記憶體不足時縮短 context，不換模型、不改用付費模型。

### 5.2 CI

沿用 repo 現有的 CI：`pytest`、`mypy --strict`、`ruff`。另外：

- 外部 API 一律重播錄好的回應，不花錢（NFR-07）。
- 整條流程測試（NFR-11）跑兩種：Celery eager 模式（快，不起服務）與真的起 PostgreSQL＋Redis（驗並行、重送、advisory lock）。
- 手動觸發、預算很小的真實冒煙測試。

### 5.3 成本

見 `00-intent/brief.md` §5.1。

---

## 第 6 部分：跨領域考量

### 6.1 Prompt-as-Code

每個節點有 system 指令、輸入／輸出 schema、禁止事項、評分標準、版本 ID；版本 ID 與 prompt hash 記進執行紀錄。行為規則在 PRD §5。

### 6.2 安全

| 威脅 | 對策 |
| :--- | :--- |
| 網頁內容夾帶指令 | 擷取內容包成資料區塊；系統指令明說資料區塊內的指令不執行；Reviewer 再查 |
| SSRF | URL 解析後擋私有 IP 與非 http(s) |
| 金鑰外洩 | 環境變數；日誌與執行紀錄過濾 |
| PostgreSQL／Redis 被外部連 | Docker 只綁 `127.0.0.1`；Redis 設密碼 |

### 6.3 設計系統

- R2 起的 Web UI 套用創作者提供的設計系統（claude.ai/design 上的設計系統專案，連結不寫進 repo），只管工作室的操作介面，不管發布網站。
- 唯一的家：`docs/10-truth/design/`。匯入方式未定（匯出成檔案，或用 `/design-sync` 同步），R2 開工前決定。
- 前端細節見 `design/2026-10-10-frontend-architecture-draft.md`。

---

## 第 7 部分：風險與演進

風險表在 PRD §10，階段在 `00-intent/roadmap.md`。架構層多出來的：

| 風險 | 緩解 |
| :--- | :--- |
| LangGraph 升版，等在 Gate 的 checkpoint 格式不相容 | 鎖版本；升版前先跑完或清空等待中的 Run |
| worker 名額都卡在等 GPU，並行度被吃掉 | 監控等待時間；超過門檻就拆 GPU 專屬佇列（Target 圖） |
| Redis 與 PostgreSQL 狀態不一致 | 以 PostgreSQL 為準；對帳工作每分鐘修正 |

---

## 9. 問題的處理（編號照 `20-state/open-questions.md`）

### 已拍板

| ID | 決定 | 落在哪 |
| :--- | :--- | :--- |
| Q-01、Q-15 | R1 用 CLI；R2 起 Web UI（FastAPI＋Jinja2＋HTMX），套用設計系統 | `roadmap.md`、本文 §1.1.2、§6.3 |
| Q-02 | 主力就是 9B；記憶體不足時縮短 context | PRD NFR-13、本文 §5.1 |
| Q-03 | 每天備份到第二顆本機硬碟；MVP 期間全部保留 | PRD NFR-19、本文 §5.1 |
| Q-04 | Celery＋Redis 排工作、PostgreSQL 存狀態，多 worker 並行 | ADR-0006 |
| Q-05 | 不通知 | 本文 §1.1.1 |
| Q-06 | 網站還沒架，R3 結束前決定形式 | `roadmap.md`、PRD US-10 |
| Q-07 | 不需拍板，R0 實測 | `brief.md` §5.1 |
| Q-20 | 只有 worker 寫 Run 的流程狀態 | ADR-0006 §3.5 |

### 本輪也拍板了（2026-10-10，創作者同意全部照建議）

| ID | 決定 | 落在哪 |
| :--- | :--- | :--- |
| Q-08 | 產完候選問題後停下等選題 | PRD v1.4 FR-02、`research-proposal.feature` |
| Q-11 | Director 輸出原作者立場，且論點不得與它實質相同 | PRD v1.4 FR-03 |
| Q-12 | 畫面用 HTMX 輪詢 PostgreSQL | 本文 §3.5 |
| Q-13 | 匯出後可回 Gate 改論點，產生新版，舊版保留 | PRD v1.4 FR-06 |
| Q-14 | Writer 只輸出受限 MDX，本機預覽用 Python 渲染 | PRD v1.4 FR-07 |
| Q-16 | Web UI 放 `web/`，與 `api/`、`cli/` 共用服務層 | 本文 §1.3 |
| Q-17、Q-22 | Run 狀態補上排隊中、等選題、等人工決定、等發布審核、已取消 | PRD v1.4 NFR-09 |
| Q-18 | 人工授權的補搜最多再 2 輪 | PRD v1.4 FR-11 |
| Q-19 | 發表前檢查沒過不自動重寫，進發布審核列出原因 | PRD v1.4 §7 |
| （新） | 排隊中與執行中顯示真實進度；等人的狀態顯示「輪到你了」 | PRD v1.4 NFR-24、本文 §3.5 |

### 還要創作者決定的

Q-09（補搜超限時還有哪些選項；目前已定：用現有證據進 Gate、加預算再補、取消）、Q-10（沒有通知時怎麼提醒有 Run 在等）、Q-21（R0 的 Tavily 實測與 R2 的順序）。出處見 `20-state/open-questions.md`。
