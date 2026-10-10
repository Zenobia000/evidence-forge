---
name: community-web-guidelines
description: 靜態審查介面程式碼是否合乎 Web Interface Guidelines（Vercel 的百餘條規則）。當使用者說「審一下我的 UI」「這頁的 UX 對不對」「照最佳實踐檢查一遍」時使用。要對實際 DOM 跑 WCAG 稽核見 `community-a11y-audit`。
argument-hint: <file-or-pattern>
metadata:
  author: vercel
  version: "1.0.0"
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Fetch the latest guidelines from the source URL below
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in the fetched guidelines
4. Output findings in the terse `file:line` format

## Guidelines Source

Fetch fresh guidelines before each review:

```
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
```

Use WebFetch to retrieve the latest rules. The fetched content contains all the rules and output format instructions.

## Usage

When a user provides a file or pattern argument:
1. Fetch guidelines from the source URL above
2. Read the specified files
3. Apply all rules from the fetched guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
