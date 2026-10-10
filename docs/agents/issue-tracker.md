# 議題追蹤器

GitHub Issues，repo `Zenobia000/evidence-forge`，用 `gh` CLI。

## 開票

```bash
gh issue create --repo Zenobia000/evidence-forge \
  --title "<標題>" --body-file <內文檔> --label <spec|ticket> [--milestone "<里程碑名>"]
```

內文寫在暫存檔，用 `--body-file` 傳，不要把多行內文塞進 `--body`。
規格用 `spec` 標籤，切出來的票用 `ticket` 標籤。有人在做的票加 `in-progress`。

## 列出開著的票

```bash
gh issue list --repo Zenobia000/evidence-forge --state open --label ticket --limit 100 \
  --json number,title,labels,milestone
```

## 阻塞關係（被誰擋住）

用 GitHub 原生的 issue dependencies，另外在內文的「Blocked by」段寫 `#編號`，方便人讀。

加上阻塞關係：先查擋路那張票的內部 id，再掛到被擋的票上。

```bash
id=$(gh api repos/Zenobia000/evidence-forge/issues/<擋路的票號> --jq .id)
gh api -X POST repos/Zenobia000/evidence-forge/issues/<被擋的票號>/dependencies/blocked_by -F issue_id=$id
```

查一張票被誰擋住：

```bash
gh api repos/Zenobia000/evidence-forge/issues/<票號>/dependencies/blocked_by --jq '.[] | "\(.number) \(.state)"'
```

**可以動工**＝所有 blocked_by 的票都是 closed。

## 里程碑

驗收節點開成 GitHub milestone（例：「M-A 走通」「M-B 資料可信」），名稱與內容寫在排程計畫裡
（例：`docs/30-snapshot/plans/<日期>-<名稱>.md`〈里程碑與驗收節點〉）。

```bash
gh api -X POST repos/Zenobia000/evidence-forge/milestones -f title="<名稱>" -f description="<說明>"
```

## 內文放在哪

全部在 issue 本身，repo 裡不另存一份。
