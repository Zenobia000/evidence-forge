// lesson 提議的按鈕（static/js/lessons.js，#114）與事件（ef_lesson，static/js/state.js）：純函式，node 內建的 test runner。

import assert from "node:assert/strict";
import { test } from "node:test";

import { lessonOutcome, lessonRequest } from "../../src/evidence_forge/ui/static/js/lessons.js";
import { createState, reconnect, reduce } from "../../src/evidence_forge/ui/static/js/state.js";

test("同意與不要各打自己的路徑，body 是空的", () => {
  assert.deepEqual(lessonRequest("c-1", "L3", "approve"), { url: "/api/conversations/c-1/lessons/L3/approve", body: {} });
  assert.equal(lessonRequest("c-1", "L3", "reject").url, "/api/conversations/c-1/lessons/L3/reject");
});

test("對話 id 會跳脫；編號與動作不對就不送", () => {
  assert.equal(lessonRequest("c/1", "L3", "approve").url, "/api/conversations/c%2F1/lessons/L3/approve");
  assert.throws(() => lessonRequest("c-1", "../x", "approve"), /編號/);
  assert.throws(() => lessonRequest("c-1", "L3", "delete"), /動作/);
});

test("成功什麼都不說；失敗說原因，而且講明沒寫東西", () => {
  assert.deepEqual(lessonOutcome("approve", 200, {}), { ok: true, text: "" });
  const failed = lessonOutcome("approve", 403, { error: "這個對話沒有這筆 lesson 提議" });
  assert.equal(failed.ok, false);
  assert.match(failed.text, /沒有寫進 lessons/);
  assert.match(failed.text, /沒有這筆/);
  assert.match(lessonOutcome("reject", 500, null).text, /沒有關掉這筆提議：伺服器回 500/);
});

const lessonEvent = (overrides = {}) => ({
  type: "ef_lesson",
  seq: 1,
  events: [{ n: 1, type: "proposed" }],
  proposals: [{ id: "L1", status: "pending" }],
  ...overrides,
});

test("有新事件：叫畫面重抓串流；序號比較小的是舊的，丟掉", () => {
  const first = reduce(createState(), lessonEvent({ seq: 5 }));
  assert.deepEqual(first.effects.filter((e) => e.type === "refresh").map((e) => e.what), ["thread"]);
  const stale = reduce(first.state, lessonEvent({ seq: 3 }));
  assert.equal(stale.effects.length, 0);
});

test("訂閱時的現況（events 是空的）不重抓；重連之後序號重算", () => {
  const snapshot = reduce(createState(), lessonEvent({ events: [] }));
  assert.equal(snapshot.effects.filter((e) => e.type === "refresh").length, 0);
  const after = reconnect(reduce(createState(), lessonEvent({ seq: 9 })).state);
  assert.equal(after.lessons.seq, 0);
  assert.equal(reduce(after, lessonEvent({ seq: 1 })).effects.filter((e) => e.type === "refresh").length, 1);
});
