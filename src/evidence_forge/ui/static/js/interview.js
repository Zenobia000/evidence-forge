// 訪談問題卡的邏輯（票 108）：一關 1～4 張卡，一次一張，整關答完一次送出。純函式，不碰 DOM、不連網路：
// node 直接測（tests/js/interview.test.mjs）。接 DOM 的部分在 interview-ui.js。
//
// 一關的狀態（createStation）：
//   { index: 現在第幾張（從 0 起）, busy: 送出中, cards: [{ id, kinds, choice, other, note, confirmed }] }
//   kinds：每個選項的種類，"option"（agent 列的）、"no_example"（第 3 關系統給的）、"other"（一定有，在最後）。
//   choice：選了第幾個選項（從 0 起），沒選是 null；other：「其他」寫的字；note：備註；confirmed：按過確認（進度標籤打勾）。
//
// 鍵盤（keyAction）只在焦點在卡片裡時有作用（呼叫端把 keydown 掛在卡片上），而且不搶輸入框的鍵：
//   數字鍵直接選、↑↓ 換選項、Enter 確認、← 回上一張。
//   焦點在單選鈕上時，↑↓ 讓瀏覽器自己換（它同時會選起來），這裡不重複處理；← 瀏覽器也會換選項，
//   所以要擋掉換成「回上一張」。焦點在文字框裡：數字、方向鍵都留給輸入，只有 Enter 確認
//   （備註是多行，Ctrl／Cmd + Enter 才確認）。輸入法選字中的鍵一律不管。

export const KIND = { option: "option", noExample: "no_example", other: "other" };
// 第 3 關：選項只是提示，作者自己的例子要寫在備註（cards.py 的 _example_problem）
export const EXAMPLE_STATION = 3;
export const MAX_DIGIT = 9;

export function createStation(specs) {
  return {
    index: 0,
    busy: false,
    cards: specs.map((spec) => ({
      id: spec.id,
      kinds: [...spec.kinds],
      choice: Number.isInteger(spec.choice) ? spec.choice : null,
      other: spec.other ?? "",
      note: spec.note ?? "",
      confirmed: false,
    })),
  };
}

export function countText(station) {
  return `第 ${station.index + 1} 張／共 ${station.cards.length} 張`;
}

export function isLast(station) {
  return station.index === station.cards.length - 1;
}

// 進度標籤：現在這一張、確認過的（打勾；回上一張改的時候後面確認過的照樣打勾）、還沒到的
export function tabStates(station) {
  return station.cards.map((card, i) => (i === station.index ? "current" : card.confirmed ? "done" : "todo"));
}

// 這張卡的選擇：「其他」要寫字、第 3 關的提示要寫例子，不然不能往下
export function problem(card, stationNumber) {
  if (card.choice === null) return "先選一個，再按確認。";
  const kind = card.kinds[card.choice];
  if (kind === KIND.other && card.other.trim() === "") return "選了「其他」，要寫下你的答案。";
  if (stationNumber === EXAMPLE_STATION && kind === KIND.option && card.note.trim() === "") {
    return "這一關要寫下你自己的例子：提示只是幫你想，不算答案。";
  }
  return null;
}

// 備註框：選了選項才出現；「其他」自己寫的就是答案，不另外出；沒有例子也不用寫。第 3 關的備註是必填
export function noteBox(card, stationNumber, optionLabel) {
  if (card.choice === null) return { visible: false, label: "", required: false };
  const kind = card.kinds[card.choice];
  if (kind !== KIND.option) return { visible: false, label: "", required: false };
  if (stationNumber === EXAMPLE_STATION) {
    return { visible: true, label: `寫下你的例子（給「${optionLabel}」，必填）`, required: true };
  }
  return { visible: true, label: `給「${optionLabel}」的備註（選填）`, required: false };
}

export function primaryLabel(station) {
  return isLast(station) ? "確認並送出這一關" : "確認，下一張";
}

export function select(station, index) {
  const card = station.cards[station.index];
  if (!Number.isInteger(index) || index < 0 || index >= card.kinds.length) return station;
  return withCard(station, station.index, { ...card, choice: index });
}

export function setOther(station, text) {
  return withCard(station, station.index, { ...station.cards[station.index], other: text });
}

export function setNote(station, text) {
  return withCard(station, station.index, { ...station.cards[station.index], note: text });
}

function withCard(station, index, card) {
  return { ...station, cards: station.cards.map((c, i) => (i === index ? card : c)) };
}

export function back(station) {
  if (station.busy || station.index === 0) return station;
  return { ...station, index: station.index - 1 };
}

// 「確認」：這張卡沒問題就往下一張；最後一張就是送出整關。回傳 {station, outcome}
//   outcome：{ type: "invalid", error } | { type: "next" } | { type: "submit", payload }
export function confirm(station, stationNumber, labels) {
  if (station.busy) return { station, outcome: { type: "ignored" } };
  const card = station.cards[station.index];
  const error = problem(card, stationNumber);
  if (error) return { station, outcome: { type: "invalid", error } };
  if (!isLast(station)) {
    const marked = withCard(station, station.index, { ...card, confirmed: true });
    return { station: { ...marked, index: station.index + 1 }, outcome: { type: "next" } };
  }
  // 一關整個送出前再把每一張都查一次（回上一張改過的也算）
  for (let i = 0; i < station.cards.length; i++) {
    const found = problem(station.cards[i], stationNumber);
    if (found) return { station: { ...station, index: i }, outcome: { type: "invalid", error: found } };
  }
  return { station, outcome: { type: "submit", payload: payload(station, labels) } };
}

// 送給 POST …/stations/<n>/submit 的內容：一張卡一筆，照卡片的順序。
//   選的選項  → { question, choice: 選項名稱, note? }
//   「其他」  → { question, other_text }（不帶備註）
//   沒有例子  → { question, no_example: true }
// labels：每張卡每個選項的名稱（跟畫面上的選項同順序）
export function payload(station, labels) {
  const answers = station.cards.map((card, i) => {
    const kind = card.kinds[card.choice];
    if (kind === KIND.other) return { question: card.id, other_text: card.other.trim() };
    if (kind === KIND.noExample) return { question: card.id, no_example: true };
    const answer = { question: card.id, choice: labels[i][card.choice] };
    const note = card.note.trim();
    if (note) answer.note = note;
    return answer;
  });
  return { answers };
}

export function setBusy(station, busy) {
  return { ...station, busy };
}

// 鍵盤。key：event.key；target：焦點在哪（"radio"｜"text"｜"note"｜"button"｜"card"｜"other"）。
// 回傳 { type: "none" } 或 { type: "select"｜"move"｜"confirm"｜"back", …, prevent }
export function keyAction(station, event) {
  const none = { type: "none", prevent: false };
  if (event.isComposing || event.altKey || station.busy) return none;
  const { key, target } = event;
  const card = station.cards[station.index];
  const typing = target === "text" || target === "note";
  if (key === "Enter") {
    if (target === "button") return none; // 按鈕自己會被按下去
    // 輸入框裡的 Enter 是打字（「其他」的輸入框也一樣：打字中不能因為按到 Enter 就把整關送出）；
    // 要確認就按按鈕，或 Ctrl／Cmd + Enter
    if (typing && !(event.ctrlKey || event.metaKey)) return none;
    if (event.shiftKey) return none;
    return { type: "confirm", prevent: true };
  }
  if (event.ctrlKey || event.metaKey || typing) return none;
  if (/^[1-9]$/.test(key)) {
    const index = Number(key) - 1;
    return index < card.kinds.length ? { type: "select", index, prevent: true } : none;
  }
  if (key === "ArrowLeft") return { type: "back", prevent: true };
  if ((key === "ArrowDown" || key === "ArrowUp") && target !== "radio") {
    const step = key === "ArrowDown" ? 1 : -1;
    const from = card.choice ?? (step === 1 ? -1 : 0);
    const index = (from + step + card.kinds.length) % card.kinds.length;
    return { type: "select", index, prevent: true };
  }
  return none;
}

// 摘要用的一行字（正在問的那一關，決策分頁先列答完的卡）
export function describe(card, labels) {
  if (card.choice === null) return "";
  const kind = card.kinds[card.choice];
  if (kind === KIND.other) return `其他：${card.other.trim()}`;
  return labels[card.choice];
}

// 草稿：整關送出前換頁、重抓對話串流時不能掉。key 是 `${卡 id}:${第幾關}`
export function draftKey(cardId, stationNumber) {
  return `${cardId}:${stationNumber}`;
}

// ── 草稿與送出中的記憶（畫面重抓對話串流時，卡片會整張換掉重長）─────────────────────────
// store：{ drafts: Map(key → { station, labels }), inflight: Set(key) }。key 是 draftKey()。

export function createStore() {
  return { drafts: new Map(), inflight: new Set() };
}

// 畫面長出一張卡：伺服器給預設值 `fresh`；有草稿（而且問題卡還是同一組）就用草稿；正在送出的照樣鎖著
export function mountStation(store, key, fresh, labels) {
  let station = restoreDraft(fresh, store.drafts.get(key)?.station);
  if (store.inflight.has(key)) station = setBusy(station, true);
  store.drafts.set(key, { station, labels });
  return station;
}

// 存草稿：輸入法選字中不存（組字中的字還不是答案）。回傳有沒有存
export function saveDraft(store, key, station, labels, { composing = false } = {}) {
  if (composing) return false;
  store.drafts.set(key, { station, labels });
  return true;
}

// 開始送出：回 false 表示這一關已經在送了（重複按），呼叫端不能再送
export function beginSubmit(store, key) {
  if (store.inflight.has(key)) return false;
  store.inflight.add(key);
  return true;
}

// 送出結束。recorded：寫進紀錄了 → 草稿不用留（畫面重抓之後這一關是摘要卡）；失敗草稿留著
export function endSubmit(store, key, { recorded }) {
  store.inflight.delete(key);
  if (recorded) store.drafts.delete(key);
}

// 畫面上已經不在的卡片（整關送出收成摘要、被重問換掉）：草稿與送出中的記憶都不用留
export function pruneStore(store, aliveKeys) {
  const alive = new Set(aliveKeys);
  for (const key of [...store.drafts.keys()]) if (!alive.has(key)) store.drafts.delete(key);
}

// 重抓對話串流後，伺服器給的是預設值：有草稿就用草稿（只要問題卡還是同一組）
export function restoreDraft(fresh, draft) {
  if (!draft) return fresh;
  const same =
    draft.cards.length === fresh.cards.length &&
    draft.cards.every((c, i) => c.id === fresh.cards[i].id && c.kinds.join() === fresh.cards[i].kinds.join());
  if (!same) return fresh;
  return { ...draft, busy: false, index: Math.min(draft.index, fresh.cards.length - 1) };
}

// 送出失敗的說明（伺服器的原因＋接下來能做什麼）
export function failureText(stationNumber, count, reason) {
  return {
    what: `第 ${stationNumber} 關沒寫進決策紀錄，${reason}。`,
    help: `卡片已經解鎖，${count} 張的答案都還在，再按一次就好。`,
  };
}

// ── 打到 API 的請求（畫面上的按鈕只帶 data-card、data-station，其餘在這裡組） ──────────

// action：submit（整關送出，帶 payload）、keep（沿用）、reask（重問這一關）。三個都只有畫面叫得到（ADR-0025）
export function cardRequest(conversationId, cardId, station, action, body = {}) {
  if (!["submit", "keep", "reask"].includes(action)) throw new Error(`不認得的動作：${action}`);
  const url = `/api/conversations/${encodeURIComponent(conversationId)}/cards/${encodeURIComponent(cardId)}/stations/${station}/${action}`;
  return { url, body };
}

// 重問這一關之前要不要先問一聲：後面有選好的關，重問會讓它們變成要重確認；沒有就直接重問
export function reaskNotice(station, later) {
  const numbers = (later ?? []).map(Number).filter((n) => Number.isInteger(n) && n > station);
  if (numbers.length === 0) return { needsConfirm: false };
  return {
    needsConfirm: true,
    title: `重問第 ${station} 關？`,
    text: `後面已經答好的${numbers.map((n) => `第 ${n} 關`).join("、")}會標成「要重確認」，到時候每關可以沿用或重問。原本的答案都留著。`,
  };
}

// 沿用、重問失敗的說明
export function decisionFailure(station, reason) {
  return `第 ${station} 關沒寫進決策紀錄，${reason}。再按一次就好。`;
}
