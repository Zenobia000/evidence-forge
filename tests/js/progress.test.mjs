// 工作進度列的字（static/js/progress.js）：第幾段、跑多久、在等什麼、花多少，全部來自真的狀態。

import assert from "node:assert/strict";
import { test } from "node:test";

import { describe, elapsedText, waitingText } from "../../src/evidence_forge/ui/static/js/progress.js";

const NOW = Date.parse("2026-10-13T14:31:12+08:00");
const job = (overrides = {}) => ({
  type: "ef_job",
  job_id: "j1",
  kind: "analysis",
  status: "running",
  stage: { id: "investigate", skill: "investigate", index: 2, total: 2, attempt: 1, started_at: "2026-10-13T14:28:00+08:00" },
  waiting: { kind: "tool", tool: "web_search", since: "2026-10-13T14:30:00+08:00" },
  spent_usd: 0.18,
  budget_usd: 1,
  credits_used: 4,
  budget_credits: 12,
  ...overrides,
});

test("分析中：第幾段、跑多久、搜尋中、花費、credits 一行講完", () => {
  const view = describe(job(), NOW);
  assert.equal(view.label, "分析中");
  assert.deepEqual(view.parts.map((p) => p.kind), ["stage", "elapsed", "waiting", "cost", "credits"]);
  assert.equal(view.parts[0].index, "2／2");
  assert.equal(view.parts[0].name, "查證與找題材");
  assert.equal(view.parts[1].text, "3 分 12 秒");
  assert.equal(view.parts[2].text, "搜尋中");
  assert.equal(view.parts[3].text, "US$0.18／1.00");
  assert.deepEqual([view.parts[4].used, view.parts[4].total], [4, 12]);
});

test("重構叫寫作中；排隊中只有花費，沒有假的段和時間", () => {
  assert.equal(describe(job({ kind: "rewrite" }), NOW).label, "寫作中");
  const queued = describe(job({ status: "queued", stage: null, waiting: null }), NOW);
  assert.equal(queued.label, "排隊中");
  assert.deepEqual(queued.parts.map((p) => p.kind), ["cost", "credits"]);
});

test("沒有搜尋額度的段不顯示 credits", () => {
  const view = describe(job({ budget_credits: 0, waiting: null }), NOW);
  assert.equal(view.parts.some((p) => p.kind === "credits"), false);
});

test("在等什麼：GPU、限流、模型、各種工具，認不得的工具照原名", () => {
  assert.equal(waitingText({ kind: "gpu" }).text, "等 GPU");
  assert.equal(waitingText({ kind: "rate_limit" }).text, "等限流");
  assert.equal(waitingText({ kind: "model" }).text, "等模型回應");
  assert.equal(waitingText({ kind: "tool", tool: "fetch_url" }).text, "抓取網頁中");
  assert.equal(waitingText({ kind: "tool", tool: "run_script" }).text, "跑腳本中");
  assert.equal(waitingText({ kind: "tool", tool: "mystery" }).text, "執行工具中：mystery");
  assert.equal(waitingText(null), null);
});

test("時鐘比開始時間早（時鐘差）算 0 秒，不會出現負的", () => {
  assert.equal(elapsedText("2026-10-13T14:35:00+08:00", NOW), "0 分 0 秒");
  assert.equal(elapsedText("不是時間", NOW), null);
});

test("不是在跑的工單不顯示在等什麼", () => {
  const view = describe(job({ status: "queued" }), NOW);
  assert.equal(view.parts.some((p) => p.kind === "waiting"), false);
});

test("同一段不是第一次試就註明", () => {
  const view = describe(job({ stage: { id: "brief", skill: "brief", index: 1, total: 2, attempt: 2, started_at: "2026-10-13T14:31:00+08:00" } }), NOW);
  assert.equal(view.sub, "這一段第 2 次");
});
