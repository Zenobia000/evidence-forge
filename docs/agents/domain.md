# 領域文件

- **模式：single-context。** 領域語言在根目錄 `CONTEXT.md`；決策在 `docs/10-truth/adr/`
  （不是技能預設的 `docs/adr/`），編號從 `0001` 起，編號大的為準（`docs/README.md`〈ADR〉）。
- **協作模式：shared。** 技能寫出來的東西全部進版控，沒有排除任何路徑。團隊裡有人不用這套技能時，
  重跑 `/setup-skills` 改成 private。
- 技能預設路徑與本 repo 的對照在 `docs/README.md`〈上游技能寫死的路徑〉，跑完照右欄放。
- **註解語言：繁體中文。** 這是團隊事實，寫在 `CLAUDE.md`，跟程式碼一起進版控。
- 新的實體先寫進 `CONTEXT.md`，再寫程式。

這份只有工具會讀，沒有人會定期檢查它有沒有過期。應該讓人讀到的事實要升到 `CLAUDE.md` 或 ADR，
不要只改這份。
