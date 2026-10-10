---
name: wait-what
description: 停 — 剛才那段沒讓人聽懂，要求代理重新講一次。
disable-model-invocation: true
---

Wait — I don't understand where you've got to here. Re-pitch it in the user's own language: a little context first, then short sentences, one instruction per sentence, plain words, using the ubiquitous language from `CONTEXT.md`.

Then log the miss: this invocation means the human-layer rules failed once. Append one line to the L-002 event log in `Ai-coding-principle/LESSONS.md` — date, what was being explained, which part wasn't in plain language. Three same-type misses → fix the template in `.claude/skills/human-layer/SKILL.md`, not the individual case.
