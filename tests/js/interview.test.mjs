// 訪談問題卡的邏輯（static/js/interview.js）：一次一張、鍵盤、送出的內容、草稿。
// 純函式，node 內建的 test runner，不連網路。

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  KIND,
  back,
  beginSubmit,
  cardRequest,
  confirm,
  countText,
  createStation,
  createStore,
  decisionFailure,
  describe,
  draftKey,
  endSubmit,
  failureText,
  isLast,
  isStaleConfirm,
  keyAction,
  mountStation,
  noteBox,
  payload,
  primaryLabel,
  problem,
  pruneStore,
  reaskNotice,
  resolveWriteConfirm,
  restoreDraft,
  saveDraft,
  select,
  setBusy,
  setNote,
  setOther,
  tabStates,
} from "../../src/evidence_forge/ui/static/js/interview.js";

const OPT = KIND.option;
const OTHER = KIND.other;
const NONE = KIND.noExample;

// 三張卡：方向（3 個選項＋其他）、命名、強度
const specs = () => [
  { id: "q1", kinds: [OPT, OPT, OPT, OTHER] },
  { id: "q2", kinds: [OPT, OPT, OTHER] },
  { id: "q3", kinds: [OPT, OPT, OTHER] },
];
const labels = [
  ["價目一直在降", "成本問題其實是架構問題", "兩個一起用最省", "其他"],
  ["「價目會降，架構不會」", "「長上下文稅」", "其他"],
  ["直接下結論", "留一點餘地", "其他"],
];
const key = (k, extra = {}) => ({ key: k, target: "radio", ...extra });

// --- 一次一張 ----------------------------------------------------------------------------

test("一關一次一張：第幾張、共幾張、最後一張的按鈕字不一樣", () => {
  let st = createStation(specs());
  assert.equal(countText(st), "第 1 張／共 3 張");
  assert.equal(primaryLabel(st), "確認，下一張");
  st = { ...st, index: 2 };
  assert.equal(countText(st), "第 3 張／共 3 張");
  assert.equal(isLast(st), true);
  assert.equal(primaryLabel(st), "確認並送出這一關");
});

test("只有一張的關：第一張就是送出", () => {
  const st = createStation([{ id: "q1", kinds: [OPT, OPT, OTHER] }]);
  assert.equal(primaryLabel(st), "確認並送出這一關");
  assert.equal(countText(st), "第 1 張／共 1 張");
});

test("沒選就不能往下：說要先選", () => {
  const st = createStation(specs());
  const { outcome, station } = confirm(st, 2, labels);
  assert.equal(outcome.type, "invalid");
  assert.match(outcome.error, /先選一個/);
  assert.equal(station.index, 0);
});

test("選了「其他」要寫字才能往下", () => {
  let st = select(createStation(specs()), 3);
  assert.match(confirm(st, 2, labels).outcome.error, /其他/);
  st = setOther(st, "   ");
  assert.equal(confirm(st, 2, labels).outcome.type, "invalid");
  st = setOther(st, "價目變，但帳更難算");
  const step = confirm(st, 2, labels);
  assert.equal(step.outcome.type, "next");
  assert.equal(step.station.index, 1);
});

test("確認，下一張：往下、前一張打勾；回上一張改，後面答過的照樣打勾", () => {
  let st = select(createStation(specs()), 1);
  st = confirm(st, 2, labels).station;
  assert.deepEqual(tabStates(st), ["done", "current", "todo"]);
  st = select(st, 0);
  st = confirm(st, 2, labels).station;
  assert.deepEqual(tabStates(st), ["done", "done", "current"]);
  st = back(back(st));
  assert.equal(st.index, 0);
  // 第 3 張只是到過，沒有按過確認：不打勾；第 2 張按過確認，回頭改第 1 張時照樣打勾
  assert.deepEqual(tabStates(st), ["current", "done", "todo"]);
  // 第一張再按上一張：不動
  assert.equal(back(st).index, 0);
});

test("回上一張改答案：改過的答案留著，其餘的也留著", () => {
  let st = select(createStation(specs()), 1);
  st = confirm(st, 2, labels).station;
  st = select(st, 1);
  st = back(st);
  assert.equal(st.cards[0].choice, 1);
  assert.equal(st.cards[1].choice, 1);
  st = select(st, 2);
  assert.equal(st.cards[0].choice, 2);
  assert.equal(st.cards[1].choice, 1);
});

test("超出範圍的選項編號不會動到狀態", () => {
  const st = createStation(specs());
  assert.equal(select(st, 9), st);
  assert.equal(select(st, -1), st);
  assert.equal(select(st, 1.5), st);
});

// --- 備註 ----------------------------------------------------------------------------

test("備註框：選了選項才出現；「其他」自己寫的就是答案，不出備註框", () => {
  let st = createStation(specs());
  assert.equal(noteBox(st.cards[0], 2, "").visible, false);
  st = select(st, 1);
  assert.deepEqual(noteBox(st.cards[0], 2, "成本問題其實是架構問題"), {
    visible: true,
    label: "給「成本問題其實是架構問題」的備註（選填）",
    required: false,
  });
  st = select(st, 3);
  assert.equal(noteBox(st.cards[0], 2, "其他").visible, false);
});

test("第 3 關的提示只是提示：要寫下自己的例子，備註必填", () => {
  const st = select(createStation([{ id: "q1", kinds: [OPT, OPT, NONE, OTHER] }]), 0);
  assert.equal(noteBox(st.cards[0], 3, "你自己的 API 帳單").required, true);
  assert.match(problem(st.cards[0], 3), /自己的例子/);
  assert.equal(problem(setNote(st, "上個月客服機器人改成先檢索").cards[0], 3), null);
  // 同一個選項在別關不用寫備註
  assert.equal(problem(st.cards[0], 2), null);
});

test("第 3 關選「沒有例子」或「其他」不用備註", () => {
  const base = createStation([{ id: "q1", kinds: [OPT, OPT, NONE, OTHER] }]);
  const none = select(base, 2);
  assert.equal(problem(none.cards[0], 3), null);
  assert.equal(noteBox(none.cards[0], 3, "沒有例子").visible, false);
  const other = setOther(select(base, 3), "客服機器人");
  assert.equal(problem(other.cards[0], 3), null);
});

// --- 送出的內容 ------------------------------------------------------------------------

test("整關一次送出：一張卡一筆，選項用名稱，備註只在有寫的時候帶", () => {
  let st = createStation(specs());
  st = select(st, 1);
  st = confirm(st, 2, labels).station;
  st = setNote(select(st, 0), "  標題裡不要出現「稅」  ");
  st = confirm(st, 2, labels).station;
  st = select(st, 0);
  const result = confirm(st, 2, labels);
  assert.equal(result.outcome.type, "submit");
  assert.deepEqual(result.outcome.payload, {
    answers: [
      { question: "q1", choice: "成本問題其實是架構問題" },
      { question: "q2", choice: "「價目會降，架構不會」", note: "標題裡不要出現「稅」" },
      { question: "q3", choice: "直接下結論" },
    ],
  });
});

test("「其他」送 other_text（去掉頭尾空白），不帶備註；沒有例子送 no_example", () => {
  const kinds = [OPT, OPT, NONE, OTHER];
  let st = createStation([
    { id: "q1", kinds },
    { id: "q2", kinds },
  ]);
  st = setNote(setOther(select(st, 3), "  自己寫的  "), "不該出現");
  st = confirm(st, 3, [["a", "b", "沒有例子", "其他"], ["a", "b", "沒有例子", "其他"]]).station;
  st = select(st, 2);
  const result = confirm(st, 3, [["a", "b", "沒有例子", "其他"], ["a", "b", "沒有例子", "其他"]]);
  assert.deepEqual(result.outcome.payload, {
    answers: [
      { question: "q1", other_text: "自己寫的" },
      { question: "q2", no_example: true },
    ],
  });
});

test("最後一張確認時整關再查一次：回上一張改成空的「其他」，會被擋在那一張", () => {
  let st = setOther(select(createStation(specs()), 3), "先寫著");
  st = confirm(st, 2, labels).station;
  st = confirm(select(st, 0), 2, labels).station;
  st = back(back(st));
  st = setOther(st, "");
  st = { ...st, index: 2 };
  const result = confirm(select(st, 0), 2, labels);
  assert.equal(result.outcome.type, "invalid");
  assert.equal(result.station.index, 0);
});

test("送出中整張卡鎖住：不能再確認、不能再回上一張", () => {
  let st = createStation([{ id: "q1", kinds: [OPT, OPT, OTHER] }]);
  st = setBusy(select(st, 0), true);
  assert.equal(confirm(st, 2, labels).outcome.type, "ignored");
  const multi = setBusy({ ...createStation(specs()), index: 1 }, true);
  assert.equal(back(multi).index, 1);
  assert.equal(keyAction(multi, key("Enter")).type, "none");
});

test("送出失敗的說明：沒寫進決策紀錄，答案都還在", () => {
  const text = failureText(2, 3, "連不上伺服器");
  assert.equal(text.what, "第 2 關沒寫進決策紀錄，連不上伺服器。");
  assert.match(text.help, /3 張的答案都還在，再按一次就好/);
});

// --- 鍵盤 --------------------------------------------------------------------------------

test("數字鍵直接選：1～4（有幾個選項就到幾）", () => {
  const st = createStation(specs());
  assert.deepEqual(keyAction(st, key("2")), { type: "select", index: 1, prevent: true });
  assert.deepEqual(keyAction(st, key("4")), { type: "select", index: 3, prevent: true });
  // 第 2 張只有 3 個選項
  const second = { ...st, index: 1 };
  assert.equal(keyAction(second, key("4")).type, "none");
  assert.equal(keyAction(second, key("0")).type, "none");
});

test("↑↓：焦點在單選鈕上交給瀏覽器；在卡片上才由我們換（繞一圈）", () => {
  let st = createStation(specs());
  assert.equal(keyAction(st, key("ArrowDown")).type, "none");
  assert.deepEqual(keyAction(st, key("ArrowDown", { target: "card" })), { type: "select", index: 0, prevent: true });
  st = select(st, 3);
  assert.equal(keyAction(st, key("ArrowDown", { target: "card" })).index, 0);
  assert.equal(keyAction(select(st, 0), key("ArrowUp", { target: "card" })).index, 3);
});

test("Enter 確認；← 回上一張（單選鈕上瀏覽器會拿 ← 換選項，要擋）", () => {
  const st = createStation(specs());
  assert.deepEqual(keyAction(st, key("Enter")), { type: "confirm", prevent: true });
  assert.deepEqual(keyAction(st, key("ArrowLeft")), { type: "back", prevent: true });
  assert.deepEqual(keyAction(st, key("ArrowLeft", { target: "button" })), { type: "back", prevent: true });
});

test("不搶輸入框的鍵：打「其他」的字、備註時數字與方向鍵都留給輸入", () => {
  const st = select(createStation(specs()), 3);
  for (const target of ["text", "note"]) {
    for (const k of ["1", "2", "ArrowLeft", "ArrowDown", "ArrowUp"]) {
      assert.equal(keyAction(st, key(k, { target })).type, "none", `${target} ${k}`);
    }
  }
  // 任何輸入框裡的 Enter 都是打字（「其他」的框也一樣，不能打到一半就把整關送出）；
  // 要確認就按按鈕，或 Ctrl／Cmd + Enter
  for (const target of ["text", "note"]) {
    assert.equal(keyAction(st, key("Enter", { target })).type, "none", `${target} Enter`);
    assert.equal(keyAction(st, key("Enter", { target, ctrlKey: true })).type, "confirm", `${target} Ctrl+Enter`);
    assert.equal(keyAction(st, key("Enter", { target, metaKey: true })).type, "confirm", `${target} Cmd+Enter`);
  }
});

test("輸入法選字中的鍵、帶修飾鍵的組合不管；按鈕上的 Enter 讓按鈕自己處理", () => {
  const st = createStation(specs());
  assert.equal(keyAction(st, key("Enter", { isComposing: true })).type, "none");
  assert.equal(keyAction(st, key("1", { altKey: true })).type, "none");
  assert.equal(keyAction(st, key("1", { ctrlKey: true })).type, "none");
  assert.equal(keyAction(st, key("Enter", { target: "button" })).type, "none");
  assert.equal(keyAction(st, key("Enter", { shiftKey: true })).type, "none");
  assert.equal(keyAction(st, key("a")).type, "none");
});

test("第 3 關有 6 個選項時，數字鍵到 6", () => {
  const st = createStation([{ id: "q1", kinds: [OPT, OPT, OPT, OPT, NONE, OTHER] }]);
  assert.equal(keyAction(st, key("6")).index, 5);
  assert.equal(keyAction(st, key("7")).type, "none");
});

// --- 摘要與草稿 -----------------------------------------------------------------------------

test("摘要一行字：選項名稱，或「其他：寫的字」；沒選是空的", () => {
  let st = createStation(specs());
  assert.equal(describe(st.cards[0], labels[0]), "");
  assert.equal(describe(select(st, 1).cards[0], labels[0]), "成本問題其實是架構問題");
  st = setOther(select(st, 3), " 自己寫 ");
  assert.equal(describe(st.cards[0], labels[0]), "其他：自己寫");
});

test("草稿 key：同一張卡同一關", () => {
  assert.equal(draftKey("card-1", 2), "card-1:2");
});

test("重抓對話串流後草稿接得回來：問題卡還是同一組就用草稿，換了一組就用新的預設", () => {
  const fresh = createStation(specs());
  const draft = { ...setNote(select({ ...fresh, index: 1 }, 1), "留著"), busy: true };
  const restored = restoreDraft(fresh, draft);
  assert.equal(restored.index, 1);
  assert.equal(restored.cards[1].note, "留著");
  // busy 不從草稿帶回來：是否還在送出由呼叫端說了算
  assert.equal(restored.busy, false);
  // 沒有草稿
  assert.equal(restoreDraft(fresh, undefined), fresh);
  // agent 重新提出了另一組問題：舊草稿不能套在新的卡上
  const changed = createStation([{ id: "q9", kinds: [OPT, OPT, OTHER] }]);
  assert.equal(restoreDraft(changed, draft), changed);
});

test("payload 直接呼叫：選項名稱照畫面上的順序對", () => {
  let st = createStation([{ id: "q1", kinds: [OPT, OPT, OTHER] }]);
  st = select(st, 1);
  assert.deepEqual(payload(st, [["甲", "乙", "其他"]]), { answers: [{ question: "q1", choice: "乙" }] });
});

// --- 打到 API 的請求：送出、沿用、重問 ------------------------------------------------------

test("三個動作打到的路徑：照 chat/api.py 的 …/cards/<卡>/stations/<關>/<動作>", () => {
  assert.deepEqual(cardRequest("c-1", "card-9", 2, "keep"), {
    url: "/api/conversations/c-1/cards/card-9/stations/2/keep",
    body: {},
  });
  assert.deepEqual(cardRequest("c-1", "card-9", 3, "reask"), {
    url: "/api/conversations/c-1/cards/card-9/stations/3/reask",
    body: {},
  });
  const body = { answers: [{ question: "q1", choice: "甲" }] };
  assert.deepEqual(cardRequest("c-1", "card-9", 1, "submit", body), {
    url: "/api/conversations/c-1/cards/card-9/stations/1/submit",
    body,
  });
});

test("路徑上的 id 都跳脫；不認得的動作直接丟錯，不打出去", () => {
  assert.equal(cardRequest("a/b", "c d", 1, "keep").url, "/api/conversations/a%2Fb/cards/c%20d/stations/1/keep");
  assert.throws(() => cardRequest("c", "k", 1, "approve"), /不認得的動作/);
});

test("重問：後面有選好的關先問一聲，說清楚哪幾關會變成要重確認；沒有就直接重問", () => {
  const notice = reaskNotice(2, ["3", "4"]);
  assert.equal(notice.needsConfirm, true);
  assert.equal(notice.title, "重問第 2 關？");
  assert.match(notice.text, /第 3 關、第 4 關會標成「要重確認」/);
  assert.match(notice.text, /原本的答案都留著/);
  assert.deepEqual(reaskNotice(6, []), { needsConfirm: false });
  assert.deepEqual(reaskNotice(2, undefined), { needsConfirm: false });
  // 前面的關不會因為重問後面而變成要重確認
  assert.deepEqual(reaskNotice(4, ["2"]), { needsConfirm: false });
});

test("沿用、重問失敗的說明", () => {
  assert.equal(decisionFailure(4, "連不上伺服器"), "第 4 關沒寫進決策紀錄，連不上伺服器。再按一次就好。");
});

// --- 草稿與送出中的記憶（store）：畫面重抓對話串流、卡片整張重長時不能掉、不能重複送 -----------------

const draftOf = (store, key) => store.drafts.get(key)?.station;

test("重長一張卡：有草稿就用草稿（答案、第幾張、備註都在），沒有就用伺服器給的預設", () => {
  const store = createStore();
  const key = draftKey("card-1", 2);
  const fresh = createStation(specs());
  assert.equal(mountStation(store, key, fresh, labels).index, 0);
  // 使用者答到一半
  let st = select(fresh, 1);
  st = confirm(st, 2, labels).station;
  st = setNote(select(st, 0), "留著");
  saveDraft(store, key, st, labels);
  // 對話串流重抓：卡片換成新的 DOM，伺服器又給一次預設值
  const again = mountStation(store, key, createStation(specs()), labels);
  assert.equal(again.index, 1);
  assert.equal(again.cards[0].choice, 1);
  assert.equal(again.cards[1].note, "留著");
});

test("agent 重新提出另一組問題：舊草稿不能套在新的卡上", () => {
  const store = createStore();
  const key = draftKey("card-1", 2);
  saveDraft(store, key, select(createStation(specs()), 1), labels);
  const other = createStation([{ id: "q9", kinds: [OPT, OPT, OTHER] }]);
  assert.equal(mountStation(store, key, other, labels), other);
});

test("送出中重長的卡照樣鎖著；這一關已經在送了，再開始送出會被擋", () => {
  const store = createStore();
  const key = draftKey("card-1", 2);
  assert.equal(beginSubmit(store, key), true);
  assert.equal(beginSubmit(store, key), false); // 連點第二下
  assert.equal(mountStation(store, key, createStation(specs()), labels).busy, true);
  endSubmit(store, key, { recorded: true });
  assert.equal(store.inflight.has(key), false);
  assert.equal(draftOf(store, key), undefined); // 寫進紀錄了：草稿不用留
  assert.equal(mountStation(store, key, createStation(specs()), labels).busy, false);
});

test("送出失敗：解鎖、草稿留著，可以再按一次", () => {
  const store = createStore();
  const key = draftKey("card-1", 2);
  saveDraft(store, key, select(createStation(specs()), 1), labels);
  beginSubmit(store, key);
  endSubmit(store, key, { recorded: false });
  assert.equal(beginSubmit(store, key), true);
  assert.equal(draftOf(store, key).cards[0].choice, 1);
});

test("輸入法組字中不存草稿：選定之後才存", () => {
  const store = createStore();
  const key = draftKey("card-1", 2);
  const st = setOther(select(createStation(specs()), 3), "ㄐ");
  assert.equal(saveDraft(store, key, st, labels, { composing: true }), false);
  assert.equal(draftOf(store, key), undefined);
  assert.equal(saveDraft(store, key, setOther(st, "價"), labels, { composing: false }), true);
  assert.equal(draftOf(store, key).cards[0].other, "價");
});

test("畫面上已經不在的卡，草稿清掉；還在的留著", () => {
  const store = createStore();
  saveDraft(store, "a:1", createStation(specs()), labels);
  saveDraft(store, "b:2", createStation(specs()), labels);
  pruneStore(store, ["b:2"]);
  assert.deepEqual([...store.drafts.keys()], ["b:2"]);
});

// --- 寫稿前的最後確認卡 --------------------------------------------------------------------

const confirmStation = () => createStation([{ id: "go", kinds: [OPT, OPT], choice: 0 }]);

test("最後確認：選「照這張寫」按確認就開始寫；草稿一併清掉（這條路徑曾因為用到不存在的變數而整個壞掉）", () => {
  const store = createStore();
  const key = "confirm:card-1";
  const station = confirmStation();
  saveDraft(store, key, station, [["照這張寫", "先不要"]]);
  const step = confirm(station, 0, [["照這張寫", "先不要"]]);
  assert.equal(step.outcome.type, "submit"); // 單張的卡，確認就是送出
  assert.deepEqual(resolveWriteConfirm(store, key, step.station), { go: true });
  assert.equal(store.drafts.has(key), false);
});

test("最後確認：選「先不要」不開始寫", () => {
  const store = createStore();
  const station = select(confirmStation(), 1);
  assert.deepEqual(resolveWriteConfirm(store, "confirm:card-1", station), { go: false });
});

test("確認卡過期：卡的決策紀錄已經比確認卡手上的新", () => {
  assert.equal(isStaleConfirm("14", 15), true);
  assert.equal(isStaleConfirm("14", 14), false);
  assert.equal(isStaleConfirm(14, 13), false);
  // 畫面還不知道這張卡長到幾筆：不當成過期
  assert.equal(isStaleConfirm("14", undefined), false);
});
