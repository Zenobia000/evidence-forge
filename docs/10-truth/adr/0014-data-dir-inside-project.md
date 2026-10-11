# ADR-0014：資料目錄放在專案資料夾裡（不進版控），帳號授權移到使用者家目錄

> **狀態：** 已接受 | **日期：** 2026-10-11 | **決策者：** 專案擁有者
> 依 `docs/templates/vibecoding/04_architecture_decision_record_template.md` 撰寫。
> **修正 ADR-0010 §3.7**（資料目錄的建議位置）與 **ADR-0012 §3.4**（帳號授權的位置）。其餘不變。

## 1. 背景與問題

- **上下文：** ADR-0010 建議把資料目錄（`EF_DATA`）放在 WSL 的 Linux 檔案系統（例：`~/evidence-forge-data`）。創作者希望資料跟專案放在一起，因為開發都是以這個專案為中心，要丟範本、看產出都從專案資料夾進去。
- **限制：**
  - 這個 repo 是公開的。資料目錄裡有別人的文章和圖，**絕對不能進版控**。
  - 專案在 `/mnt/d`（Windows 磁碟掛進 WSL），這種掛載預設不支援 Unix 權限：`chmod 700` 不會生效，所有檔案都是 777。ADR-0012 要求帳號授權權限 600，在這裡做不到。
- **為什麼要寫成 ADR：** 修改兩份已接受 ADR 的細節，而且牽涉安全（授權檔權限）與著作權（別人的文章不能進公開 repo）。

## 2. 考量的選項

| 選項 | 結果 |
| :--- | :--- |
| 維持在 `~/evidence-forge-data` | 不採用：創作者要以專案為中心 |
| 放在專案裡並進版控 | 不採用：公開 repo，別人的文章不能上傳 |
| **放在專案的 `data/`，`.gitignore` 排除；授權另放** | **採用** |

## 3. 決策

1. **資料目錄是專案根目錄的 `data/`**，`.gitignore` 排除整個資料夾；`EF_DATA` 指向它的絕對路徑。各個 worktree 都指向主目錄的同一份 `data/`，不各自一份。
2. **帳號授權不放在 `data/`**，改放 `~/.config/evidence-forge/secrets/`（Linux 檔案系統，權限 700），路徑由 `EF_SECRETS` 指定。只有膠水層讀，這點不變。
3. `data/` 的格式說明放在 `data/README.md`（給人看），格式範本的正本仍在 repo 的 `harness/templates/`。

## 4. 後果

- **正面：** 範本、評估案例、產出都在專案資料夾裡，Windows 檔案總管直接打開；授權仍然有真的檔案權限保護。
- **負面：**
  - `/mnt/d` 的檔案操作比 Linux 檔案系統慢。執行器掃描工單會慢一些，量大時再考慮移回 Linux 檔案系統（只改 `EF_DATA`）。
  - 有人誤把 `data/` 從 `.gitignore` 拿掉，就會把別人的文章推上公開 repo。所以 `.gitignore` 這一行旁邊要寫明原因。
- **影響範圍：** `.gitignore`、`.env.example`、`data/README.md`。
- **重新評估觸發：** 執行器掃描明顯變慢；或 repo 改成私有。

## 5. 參考

- WSL 掛載 Windows 磁碟的權限行為：<https://learn.microsoft.com/en-us/windows/wsl/file-permissions>
