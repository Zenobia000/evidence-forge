// 編輯卡的事件（ef_card，static/js/state.js）：同一張卡序號比較小、決策紀錄筆數比較少的都是舊的。
// 事件的形狀照 chat/host.py：{type, seq, card_id, events: [這次寫的事件], card: 編輯卡（card.events 是筆數）}。

import assert from "node:assert/strict";
import { test } from "node:test";

import { createState, reconnect, reduce } from "../../src/evidence_forge/ui/static/js/state.js";

const cardEvent = (overrides = {}) => ({
  type: "ef_card",
  seq: 1,
  card_id: "card-1",
  events: [{ n: 1, type: "questions_proposed" }],
  card: { card_id: "card-1", events: 1 },
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

const refreshes = (effects) => effects.filter((e) => e.type === "refresh").map((e) => e.what);

test("新的事件換掉整張卡，並叫畫面重抓串流、清單、面板", () => {
  const { state, effects } = apply(createState(), cardEvent());
  assert.equal(state.cards["card-1"].events, 1);
  assert.deepEqual(refreshes(effects), ["thread", "list", "panel"]);
  assert.equal(effects.filter((e) => e.type === "card").length, 1);
});

test("序號比較小的是舊的，丟掉；同一個序號重送也是", () => {
  const { state, effects } = apply(
    createState(),
    cardEvent({ seq: 5, events: [{ n: 3 }], card: { card_id: "card-1", events: 3 } }),
    cardEvent({ seq: 3, events: [{ n: 2 }], card: { card_id: "card-1", events: 2 } }),
    cardEvent({ seq: 5, events: [{ n: 3 }], card: { card_id: "card-1", events: 3 } }),
  );
  assert.equal(state.cards["card-1"].events, 3);
  assert.equal(effects.filter((e) => e.type === "card").length, 1);
});

test("重連之後序號重算，決策紀錄的筆數比手上少的還是舊的", () => {
  let state = apply(createState(), cardEvent({ seq: 9, events: [{ n: 4 }], card: { card_id: "card-1", events: 4 } })).state;
  state = reconnect(state);
  assert.equal(state.cards["card-1"].seq, 0);
  // 新的連線序號從 1 起算：序號擋不住，要靠筆數
  const stale = apply(state, cardEvent({ seq: 1, events: [], card: { card_id: "card-1", events: 2 } }));
  assert.equal(stale.state.cards["card-1"].events, 4);
  assert.equal(stale.effects.length, 0);
  // 筆數一樣的現況（events: []）是重述：記下但不重抓
  const same = apply(state, cardEvent({ seq: 1, events: [], card: { card_id: "card-1", events: 4 } }));
  assert.deepEqual(refreshes(same.effects), []);
  // 重連之前漏掉的：筆數比較多，重抓
  const missed = apply(state, cardEvent({ seq: 1, events: [], card: { card_id: "card-1", events: 6 } }));
  assert.deepEqual(refreshes(missed.effects), ["thread", "list", "panel"]);
});

test("每張卡各算各的，不互相擋", () => {
  const { state } = apply(
    createState(),
    cardEvent({ seq: 5, card_id: "a", card: { card_id: "a", events: 5 } }),
    cardEvent({ seq: 1, card_id: "b", card: { card_id: "b", events: 1 } }),
  );
  assert.equal(state.cards.a.events, 5);
  assert.equal(state.cards.b.events, 1);
});

test("訂閱時的現況（events: []）第一次看到只記下，不重抓：連上時畫面本來就會整個重抓", () => {
  const { state, effects } = apply(createState(), cardEvent({ events: [], card: { card_id: "card-1", events: 2 } }));
  assert.equal(state.cards["card-1"].events, 2);
  assert.deepEqual(refreshes(effects), []);
});

test("沒有序號的事件（舊主機）只靠筆數擋舊的", () => {
  const { state } = apply(
    createState(),
    cardEvent({ seq: undefined, events: [{ n: 3 }], card: { card_id: "card-1", events: 3 } }),
    cardEvent({ seq: undefined, events: [{ n: 2 }], card: { card_id: "card-1", events: 2 } }),
  );
  assert.equal(state.cards["card-1"].events, 3);
});

test("card 效果帶 changed：新事件、筆數變多是 true，重述的現況是 false（確認卡只在 changed 時才收）", () => {
  const first = apply(createState(), cardEvent({ seq: 1, events: [{ n: 2 }], card: { card_id: "card-1", events: 2 } }));
  assert.equal(first.effects.find((e) => e.type === "card").changed, true);
  const same = apply(first.state, cardEvent({ seq: 2, events: [], card: { card_id: "card-1", events: 2 } }));
  assert.equal(same.effects.find((e) => e.type === "card").changed, false);
  assert.equal(same.effects.find((e) => e.type === "card").card.events, 2);
});
