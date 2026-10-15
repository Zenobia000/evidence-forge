// 「照這張編輯卡寫稿」的請求與結果（static/js/interview.js，#110）：純函式，node 內建的 test runner，不連網路。
// 畫面帶著確認卡上的事件筆數送出；伺服器說卡在確認之後又改過（409）就重開確認卡，請創作者再看一次。

import assert from "node:assert/strict";
import { test } from "node:test";

import { writeOutcome, writeRequest } from "../../src/evidence_forge/ui/static/js/interview.js";

test("送出的是確認卡上看到的事件筆數", () => {
  const { url, body } = writeRequest("c-1", "card-20261015-000000-abc123", "15");
  assert.equal(url, "/api/conversations/c-1/cards/card-20261015-000000-abc123/write");
  assert.deepEqual(body, { confirmed_events: 15 });
});

test("卡 id 與對話 id 會跳脫", () => {
  assert.equal(writeRequest("c/1", "a b", 0).url, "/api/conversations/c%2F1/cards/a%20b/write");
});

test("筆數不是 0 以上的整數就不送", () => {
  for (const bad of [undefined, "", "abc", "-1", "1.5", null]) {
    assert.throws(() => writeRequest("c-1", "card-1", bad), /筆數/);
  }
});

test("開了單：說開始寫了，角色與文體照伺服器回的", () => {
  const outcome = writeOutcome(202, { job_id: "j1", persona: "nightowl", genre: "research-review" });
  assert.equal(outcome.ok, true);
  assert.equal(outcome.reopen, false);
  assert.match(outcome.text, /開始照編輯卡寫稿/);
  assert.match(outcome.text, /nightowl/);
});

test("409：卡在確認之後改過、或還不能寫：說原因、重開確認卡", () => {
  const outcome = writeOutcome(409, { error: "編輯卡在確認之後又改過（確認時 15 筆，現在 16 筆）：請重新確認" });
  assert.equal(outcome.ok, false);
  assert.equal(outcome.reopen, true);
  assert.match(outcome.text, /沒有開始寫稿/);
  assert.match(outcome.text, /確認時 15 筆/);
  assert.match(outcome.text, /確認卡/);
});

test("其他錯誤：說原因，不重開確認卡，也說沒花錢", () => {
  const outcome = writeOutcome(503, { error: "這個 ef serve 沒有接上寫稿的工作台" });
  assert.deepEqual([outcome.ok, outcome.reopen], [false, false]);
  assert.match(outcome.text, /沒有接上寫稿的工作台/);
  assert.match(outcome.text, /沒花錢/);
  // 伺服器沒給原因時也有一句話可以看
  assert.match(writeOutcome(500, null).text, /500/);
});
