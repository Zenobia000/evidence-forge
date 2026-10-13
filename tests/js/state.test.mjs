// 對話串流的狀態（static/js/state.js）：丟掉比目前狀態更舊的事件、結束的工單會叫畫面重抓。
// 事件的形狀照 chat/host.py 的說明；用 node 內建的 test runner，不連網路。

import assert from "node:assert/strict";
import { test } from "node:test";

import { createState, reconnect, reduce, seedFinished } from "../../src/evidence_forge/ui/static/js/state.js";

const job = (overrides = {}) => ({
  type: "ef_job",
  job_id: "j1",
  kind: "analysis",
  status: "running",
  finished: false,
  outcome: null,
  seq: 1,
  ...overrides,
});

const apply = (state, ...events) => {
  const all = [];
  for (const event of events) {
    const result = reduce(state, event);
    state = result.state;
    all.push(...result.effects);
  }
  return { state, effects: all };
};

test("同一張工單序號比較小的事件是舊的，丟掉", () => {
  const { state, effects } = apply(createState(), job({ seq: 5, status: "running" }), job({ seq: 3, status: "queued" }));
  assert.equal(state.jobs.j1.status, "running");
  assert.equal(state.jobs.j1.seq, 5);
  assert.equal(effects.filter((e) => e.type === "progress").length, 1);
});

test("同一個序號重送也算舊的", () => {
  const { effects } = apply(createState(), job({ seq: 5 }), job({ seq: 5 }));
  assert.equal(effects.filter((e) => e.type === "progress").length, 1);
});

test("不同工單的序號各算各的", () => {
  const { state } = apply(createState(), job({ job_id: "a", seq: 9 }), job({ job_id: "b", seq: 2 }));
  assert.deepEqual(Object.keys(state.jobs).sort(), ["a", "b"]);
});

test("Pi 的狀態一樣只收比較新的", () => {
  const { state } = apply(
    createState(),
    { type: "ef_pi", state: "running", seq: 7, busy: false },
    { type: "ef_pi", state: "starting", seq: 4, busy: false },
  );
  assert.equal(state.pi.state, "running");
});

test("工單從跑著變成結束：重抓串流、清單、面板，並回報創作者看到了", () => {
  const { effects } = apply(createState(), job({ seq: 1 }), job({ seq: 2, status: "done", finished: true, outcome: "complete" }));
  const refreshed = effects.filter((e) => e.type === "refresh").map((e) => e.what).sort();
  assert.deepEqual(refreshed, ["list", "panel", "thread"]);
  assert.deepEqual(effects.find((e) => e.type === "seen"), { type: "seen", job_id: "j1" });
});

test("失敗的工單不回報看過，清單才會標失敗而不是有新結果", () => {
  const { effects } = apply(createState(), job({ seq: 1 }), job({ seq: 2, status: "done", finished: true, outcome: "failed" }));
  assert.equal(effects.some((e) => e.type === "seen"), false);
});

test("已經知道結束的工單，現況事件再來一次不重抓", () => {
  const done = job({ seq: 2, status: "done", finished: true, outcome: "complete" });
  const first = apply(createState(), done);
  const again = apply(reconnect(first.state), { ...done, seq: 1 });
  assert.equal(again.effects.some((e) => e.type === "refresh"), false);
});

test("連線重開：序號重算，新連線的小序號不會被舊的大序號擋掉", () => {
  const before = apply(createState(), job({ seq: 900, status: "running" })).state;
  const { state } = apply(reconnect(before), job({ seq: 1, status: "done", finished: true, outcome: "complete" }));
  assert.equal(state.jobs.j1.status, "done");
});

test("agent 的回答一路串流，結束時清掉並重抓串流", () => {
  const partial = { role: "assistant", content: [{ type: "text", text: "收到。" }] };
  const { state, effects } = apply(
    createState(),
    { type: "agent_start" },
    { type: "message_update", message: partial },
    { type: "message_end", message: { ...partial, content: [{ type: "text", text: "收到。好" }] } },
  );
  assert.deepEqual(effects.filter((e) => e.type === "live").map((e) => e.text), ["收到。", ""]);
  assert.equal(state.live, "");
  assert.ok(effects.some((e) => e.type === "refresh" && e.what === "thread"));
});

test("你自己的訊息進了紀錄也重抓串流，但不碰串流中的文字", () => {
  const { effects } = apply(createState(), { type: "message_end", message: { role: "user", content: "嗨" } });
  assert.ok(effects.some((e) => e.type === "refresh" && e.what === "thread"));
  assert.equal(effects.some((e) => e.type === "live"), false);
});

test("Pi 在接回：顯示接回對話中，跑起來之後收掉", () => {
  const { effects } = apply(
    createState(),
    { type: "ef_pi", state: "starting", seq: 1 },
    { type: "ef_pi", state: "running", seq: 2 },
  );
  assert.deepEqual(effects[0], { type: "note", kind: "resume", text: "接回對話中" });
  assert.equal(effects[1].kind, "clear");
});

test("錯誤事件變成一行說明；標題變了換標題並重抓清單", () => {
  const { effects } = apply(createState(), { type: "ef_error", message: "Pi 開不起來" }, { type: "ef_conversation", title: "新標題", seq: 3 });
  assert.deepEqual(effects[0], { type: "note", kind: "error", text: "Pi 開不起來" });
  assert.deepEqual(effects[1], { type: "title", title: "新標題" });
});

test("看不懂的事件原樣放過", () => {
  const state = createState();
  assert.equal(reduce(state, { type: "tool_execution_start" }).state, state);
  assert.equal(reduce(state, null).state, state);
});

test("載入時串流裡就有結果卡的工單，現況事件來了不重抓", () => {
  const seeded = seedFinished(createState(), ["j1"]);
  const { effects } = apply(seeded, job({ seq: 1, status: "done", finished: true, outcome: "complete" }));
  assert.equal(effects.some((e) => e.type === "refresh" || e.type === "seen"), false);
});

test("seedFinished 不洗掉已經收到的序號：晚到的舊事件不能把結束的工單改回跑著", () => {
  const known = apply(createState(), job({ seq: 9, status: "done", finished: true, outcome: "complete" })).state;
  const seeded = seedFinished(known, ["j1"]);
  const { state } = apply(seeded, job({ seq: 4, status: "running" }));
  assert.equal(state.jobs.j1.status, "done");
});
