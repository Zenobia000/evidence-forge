// 訪談問題卡、決策分頁按鈕、寫稿前最後確認卡的畫面接線（票 108）。
// 規則與狀態轉換都在 interview.js（純函式，node 測過）；這裡只做三件事：
// 把伺服器產的卡片（ui/templates/_interview.html）讀成狀態、把狀態畫回去、把送出打到 API。
//
// 規矩（跟 app.js 一樣）：自己組的東西一律用 textContent；請求都用相對路徑、同源。
// 問題卡會隨對話串流重抓而重新長出來，所以答到一半的草稿放在這裡的記憶體（drafts），重抓後照 id 接回去，
// 焦點（打到一半的「其他」、備註）也要接回去。

import {
  KIND,
  back,
  cardRequest,
  confirm,
  countText,
  createStation,
  decisionFailure,
  describe,
  draftKey,
  failureText,
  keyAction,
  noteBox,
  primaryLabel,
  reaskNotice,
  restoreDraft,
  select,
  setBusy,
  setNote,
  setOther,
  tabStates,
} from "./interview.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const SVG = "http://www.w3.org/2000/svg";
const PATHS = {
  check: ["M5 12l5 5L20 7"],
  warn: ["M12 3l9 16H3z", "M12 10v4", "M12 17h.01"],
};

function svg(name) {
  const node = document.createElementNS(SVG, "svg");
  node.setAttribute("viewBox", "0 0 24 24");
  node.setAttribute("aria-hidden", "true");
  for (const d of PATHS[name] ?? []) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    node.append(path);
  }
  return node;
}

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

// 焦點在卡片的哪一種東西上（keyAction 要知道：輸入框裡的鍵不能搶）
export function targetKind(node) {
  if (!(node instanceof Element)) return "card";
  if (node.matches("input[type=radio]")) return "radio";
  if (node.matches("textarea")) return "note";
  if (node.matches("input[type=text], input:not([type])")) return "text";
  if (node.closest("button")) return "button";
  return "card";
}

// env：
//   conversation()      目前的對話 id
//   post(url, body)     送 JSON，失敗丟 Error（訊息是伺服器給的原因）
//   fragment(url)       抓 HTML 片段 → {status, html}
//   afterWrite()        寫進決策紀錄之後：重抓對話串流、決策分頁、清單
//   startWriting(cardId)  最後確認之後開始寫稿（票 110 接；現在是預留的位置）
//   revealChat()        窄螢幕時把蓋住對話的面板收起來
//   confirmHost         放最後確認卡的容器
//   panel               右側面板
export function initInterview(env) {
  const states = new WeakMap(); // 卡片元素 → { station, labels }
  const drafts = new Map(); // draftKey → { station, labels }
  const inflight = new Set(); // 正在送出的 draftKey

  const keyOf = (qset) =>
    qset.hasAttribute("data-confirm") ? `confirm:${qset.dataset.card}` : draftKey(qset.dataset.card, qset.dataset.station);
  const stationNumber = (qset) => (qset.hasAttribute("data-confirm") ? 0 : Number(qset.dataset.station));
  const qsetOf = (node) => (node instanceof Element ? node.closest(".ch-qset[data-qset]") : null);
  const liveQset = (key) => $$(".ch-qset[data-qset]").find((q) => keyOf(q) === key) ?? null;

  function readSpecs(qset) {
    return $$(".ch-q", qset).map((fieldset) => {
      const radios = $$("input[type=radio]", fieldset);
      const picked = radios.findIndex((r) => r.checked);
      return {
        id: fieldset.dataset.q,
        kinds: radios.map((r) => r.dataset.kind),
        choice: picked >= 0 ? picked : null,
        other: $("[data-other]", fieldset)?.value ?? "",
        note: $("[data-note-input]", fieldset)?.value ?? "",
      };
    });
  }

  const readLabels = (qset) => $$(".ch-q", qset).map((fieldset) => $$("input[type=radio]", fieldset).map((r) => r.dataset.label));

  function current(qset) {
    return states.get(qset).station;
  }

  function commit(qset, station, { focus = false } = {}) {
    const entry = states.get(qset);
    states.set(qset, { ...entry, station });
    drafts.set(keyOf(qset), { station, labels: entry.labels });
    render(qset, { focus });
    paintDrafts();
  }

  function mount(qset) {
    const fresh = createStation(readSpecs(qset));
    const labels = readLabels(qset);
    const key = keyOf(qset);
    let station = restoreDraft(fresh, drafts.get(key)?.station);
    if (inflight.has(key)) station = setBusy(station, true);
    states.set(qset, { station, labels });
    drafts.set(key, { station, labels });
    render(qset);
  }

  function render(qset, { focus = false } = {}) {
    const { station, labels } = states.get(qset);
    const number = stationNumber(qset);
    const confirmCard = qset.hasAttribute("data-confirm");
    const fieldsets = $$(".ch-q", qset);
    fieldsets.forEach((fieldset, i) => {
      const card = station.cards[i];
      fieldset.hidden = i !== station.index;
      $$("input[type=radio]", fieldset).forEach((radio, j) => {
        radio.checked = card.choice === j;
        radio.disabled = station.busy;
      });
      const other = $("[data-other]", fieldset);
      if (other) {
        other.hidden = card.choice === null || card.kinds[card.choice] !== KIND.other;
        other.disabled = station.busy;
        if (other.value !== card.other) other.value = card.other;
      }
      const box = $("[data-note]", fieldset);
      if (box && !confirmCard) {
        const info = noteBox(card, number, card.choice === null ? "" : labels[i][card.choice]);
        box.hidden = !info.visible;
        $("[data-note-label]", box).textContent = info.label;
        const area = $("[data-note-input]", box);
        area.disabled = station.busy;
        area.required = info.required;
        if (area.value !== card.note) area.value = card.note;
      }
    });
    // 進度標籤：答過的打勾，現在這一張反白
    const states_ = tabStates(station);
    $$("[data-qtab]", qset).forEach((tab, i) => {
      tab.dataset.header ??= tab.textContent;
      tab.replaceChildren(...(states_[i] === "done" ? [svg("check")] : []), document.createTextNode(tab.dataset.header));
      if (states_[i] === "current") tab.setAttribute("aria-current", "step");
      else tab.removeAttribute("aria-current");
      if (states_[i] === "current") tab.removeAttribute("data-state");
      else tab.dataset.state = states_[i];
    });
    const count = $("[data-qcount]", qset);
    if (count && !confirmCard) count.textContent = countText(station);
    // 答完的卡先列出來（設計稿：現在這一張上面）
    const done = $("[data-qdone]", qset);
    if (done) {
      const rows = station.cards
        .map((card, i) => ({ card, i }))
        .filter(({ card, i }) => i !== station.index && card.confirmed && card.choice !== null)
        .map(({ card, i }) => {
          const fieldset = fieldsets[i];
          return h(
            "li",
            {},
            svg("check"),
            h("span", { class: "ch-qdone__chip", text: $(".ch-q__chip", fieldset).textContent }),
            h("span", { class: "ch-qdone__q", text: $(".ch-q__q", fieldset).textContent }),
            h("span", { class: "ch-qdone__a" }, document.createTextNode("→ "), h("b", { text: describe(card, labels[i]) })),
          );
        });
      done.replaceChildren(...rows);
      done.hidden = rows.length === 0;
    }
    const backButton = $('[data-q-action="back"]', qset);
    if (backButton) {
      backButton.hidden = station.index === 0;
      backButton.disabled = station.busy;
    }
    const primary = $('[data-q-action="next"]', qset);
    if (primary) {
      primary.disabled = station.busy;
      primary.toggleAttribute("aria-busy", station.busy);
      $("[data-q-primary]", primary).textContent = station.busy ? "送出中" : confirmCard ? "確認" : primaryLabel(station);
    }
    qset.toggleAttribute("aria-busy", station.busy);
    if (focus) fieldsets[station.index]?.focus();
  }

  function showError(qset, what, help = "") {
    const line = $("[data-qerror]", qset);
    if (!line) return;
    line.replaceChildren();
    line.hidden = !what;
    if (!what) return;
    line.append(svg("warn"), h("span", {}, document.createTextNode(what), ...(help ? [h("span", { class: "ch-error__help", text: ` ${help}` })] : [])));
  }

  // ── 草稿也列在決策分頁「正在問」的那一關 ─────────────────────────────

  function paintDrafts() {
    for (const item of $$(".ch-dec__item[data-asking]", env.panel)) {
      const draft = drafts.get(draftKey(item.dataset.card, item.dataset.station));
      if (!draft) continue;
      const { station, labels } = draft;
      const badge = $("[data-asking-badge]", item);
      if (badge) badge.textContent = `正在問 ${station.index + 1}／${station.cards.length}`;
      station.cards.forEach((card, i) => {
        const row = $(`li[data-q="${CSS.escape(card.id)}"]`, item);
        if (!row) return;
        const text = card.choice === null ? "" : describe(card, labels[i]);
        const answer = $("[data-draft]", row);
        answer.textContent = text;
        answer.hidden = text === "";
        $("[data-draft-hint]", row).hidden = text !== "";
      });
    }
  }

  // ── 對話串流重抓之後 ─────────────────────────────────────────────────

  // 重抓前記下焦點在哪，重抓後接回去（打到一半的「其他」、備註不能被打斷）
  function captureFocus() {
    const active = document.activeElement;
    const qset = qsetOf(active);
    if (!qset) return null;
    const fieldset = active.closest(".ch-q");
    const token = { key: keyOf(qset), q: fieldset?.dataset.q ?? null, kind: targetKind(active), start: null, end: null };
    if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) {
      token.start = active.selectionStart;
      token.end = active.selectionEnd;
    }
    if (token.kind === "radio") token.value = active.value;
    return token;
  }

  function restoreFocus(token) {
    if (!token) return;
    const qset = liveQset(token.key);
    if (!qset) return;
    const fieldset = token.q ? $$(".ch-q", qset).find((f) => f.dataset.q === token.q) : null;
    let target = null;
    if (fieldset && !fieldset.hidden) {
      if (token.kind === "radio") target = $(`input[type=radio][value="${CSS.escape(token.value)}"]`, fieldset);
      else if (token.kind === "text") target = $("[data-other]", fieldset);
      else if (token.kind === "note") target = $("[data-note-input]", fieldset);
      else target = fieldset;
    }
    if (!target || target.hidden) return;
    target.focus({ preventScroll: true });
    if (token.start !== null && typeof target.setSelectionRange === "function") target.setSelectionRange(token.start, token.end);
  }

  // 對話串流換了新內容：每張還沒答完的問題卡長出狀態（呼叫端自己在換內容前後抓與接回焦點）
  function mountAll(root = document) {
    $$(".ch-qset[data-qset]", root).forEach(mount);
    // 已經不在畫面上的卡片（整關已經送出、收成摘要）：草稿不用留
    const alive = new Set($$(".ch-qset[data-qset]").map(keyOf));
    for (const key of [...drafts.keys()]) if (!alive.has(key)) drafts.delete(key);
    paintDrafts();
  }

  // ── 確認、送出 ──────────────────────────────────────────────────────

  async function submit(qset, payload) {
    const key = keyOf(qset);
    const number = stationNumber(qset);
    const count = payload.answers.length;
    inflight.add(key);
    commit(qset, setBusy(current(qset), true));
    showError(qset, "");
    try {
      const request = cardRequest(env.conversation(), qset.dataset.card, number, "submit", payload);
      await env.post(request.url, request.body);
      inflight.delete(key);
      drafts.delete(key);
      // 寫進紀錄了：串流重抓之後這一關就是摘要卡（標「點選」），不用等 agent 回話
      env.afterWrite();
    } catch (error) {
      inflight.delete(key);
      const live = liveQset(key) ?? qset;
      if (states.has(live)) commit(live, setBusy(current(live), false));
      const text = failureText(number, count, error.message);
      showError(live, text.what, text.help);
    }
  }

  function confirmStep(qset) {
    const state = states.get(qset);
    const { station, outcome } = confirm(state.station, stationNumber(qset), state.labels);
    if (outcome.type === "ignored") return;
    if (outcome.type === "invalid") {
      commit(qset, station, { focus: station.index !== state.station.index });
      showError(qset, outcome.error);
      return;
    }
    showError(qset, "");
    if (outcome.type === "next") {
      commit(qset, station, { focus: true });
      return;
    }
    if (qset.hasAttribute("data-confirm")) {
      const go = station.cards[0].choice === 0;
      qset.remove();
      drafts.delete(keyOf(qset));
      if (go) env.startWriting(qset.dataset.card);
      return;
    }
    submit(qset, outcome.payload);
  }

  function apply(qset, action) {
    const station = current(qset);
    if (action.type === "select" || action.type === "move") {
      commit(qset, select(station, action.index));
      const fieldset = $$(".ch-q", qset)[station.index];
      // 數字鍵選到「其他」：那一列變成輸入框，接著就是打字；其他選項讓焦點留在單選鈕上，方向鍵才接得下去
      const target = station.cards[station.index].kinds[action.index] === KIND.other ? $("[data-other]", fieldset) : null;
      (target ?? $$("input[type=radio]", fieldset)[action.index])?.focus();
    } else if (action.type === "confirm") {
      confirmStep(qset);
    } else if (action.type === "back") {
      commit(qset, back(station), { focus: true });
    }
  }

  // 滑鼠、手指點到「其他」就直接進輸入框；用方向鍵走過去不搶焦點（不然方向鍵就走不出來了）
  let pointerChoice = false;
  document.addEventListener("pointerdown", () => (pointerChoice = true), true);
  document.addEventListener("keydown", () => (pointerChoice = false), true);

  document.addEventListener("change", (event) => {
    const target = event.target;
    const qset = qsetOf(target);
    if (!qset || !states.has(qset) || !(target instanceof HTMLInputElement) || target.type !== "radio") return;
    const fieldset = target.closest(".ch-q");
    const index = $$("input[type=radio]", fieldset).indexOf(target);
    commit(qset, select(current(qset), index));
    showError(qset, "");
    if (pointerChoice && target.dataset.kind === KIND.other) $("[data-other]", fieldset)?.focus();
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    const qset = qsetOf(target);
    if (!qset || !states.has(qset)) return;
    if (target.matches("[data-other]")) commit(qset, setOther(current(qset), target.value));
    else if (target.matches("[data-note-input]")) commit(qset, setNote(current(qset), target.value));
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest("[data-q-action]");
    if (button) {
      const qset = qsetOf(button);
      if (!qset || !states.has(qset)) return;
      apply(qset, { type: button.dataset.qAction === "back" ? "back" : "confirm" });
    }
  });

  // 鍵盤只在焦點在卡片裡時有作用：掛在卡片上，不是整頁（不搶主輸入框的鍵）
  document.addEventListener("keydown", (event) => {
    const qset = qsetOf(event.target);
    if (!qset || !states.has(qset)) return;
    const action = keyAction(current(qset), {
      key: event.key,
      target: targetKind(event.target),
      isComposing: event.isComposing,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
    });
    if (action.prevent) event.preventDefault();
    if (action.type !== "none") apply(qset, action);
  });

  // ── 決策分頁：沿用、重問；照這張編輯卡寫稿 ────────────────────────────

  function decisionError(item, text) {
    const line = $("[data-dec-error]", item);
    if (!line) return;
    line.replaceChildren();
    line.hidden = !text;
    if (text) line.append(svg("warn"), h("span", { text }));
  }

  async function decide(button, action) {
    const item = button.closest(".ch-dec__item");
    const station = button.dataset.station;
    decisionError(item, "");
    const buttons = $$("button", item);
    buttons.forEach((b) => (b.disabled = true));
    try {
      const request = cardRequest(env.conversation(), button.dataset.card, station, action);
      await env.post(request.url, request.body);
      // 這裡立刻變：重抓決策分頁，對話裡會多一行系統小字，agent 接著往下問
      env.afterWrite();
    } catch (error) {
      buttons.forEach((b) => (b.disabled = false));
      decisionError(item, decisionFailure(station, error.message));
    }
  }

  function askBeforeReask(button) {
    const item = button.closest(".ch-dec__item");
    const notice = reaskNotice(Number(button.dataset.station), (button.dataset.later || "").split(",").filter(Boolean));
    $(".ch-dec__confirm", item)?.remove();
    const box = h(
      "div",
      { class: "ch-dec__confirm", role: "group", "aria-label": notice.title },
      h("p", { text: notice.title, class: "ch-dec__confirm-title" }),
      h("p", { text: notice.text }),
      h(
        "div",
        { class: "s0-row" },
        h("button", { class: "lm-btn", "data-variant": "primary", type: "button", "data-dec-action": "reask-confirmed", "data-card": button.dataset.card, "data-station": button.dataset.station, text: "重問" }),
        h("button", { class: "lm-btn lm-btn--line", "data-variant": "quiet", type: "button", "data-dec-action": "cancel", text: "取消" }),
      ),
    );
    $("[data-actions]", item).after(box);
    $("button", box).focus();
  }

  async function openWriteConfirm(cardId) {
    const { status, html } = await env.fragment(`/ui/c/${encodeURIComponent(env.conversation())}/write-confirm?card=${encodeURIComponent(cardId)}`);
    env.confirmHost.innerHTML = html; // 伺服器產的片段，內容已經跳脫
    env.revealChat();
    const qset = $(".ch-qset[data-qset]", env.confirmHost);
    if (qset && status === 200) {
      mount(qset);
      qset.scrollIntoView({ block: "nearest" });
      $(".ch-q", qset)?.focus({ preventScroll: true });
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const dec = target.closest("[data-dec-action]");
    if (dec && env.panel.contains(dec)) {
      const action = dec.dataset.decAction;
      if (action === "keep") decide(dec, "keep");
      else if (action === "reask") {
        const notice = reaskNotice(Number(dec.dataset.station), (dec.dataset.later || "").split(",").filter(Boolean));
        if (notice.needsConfirm) askBeforeReask(dec);
        else decide(dec, "reask");
      } else if (action === "reask-confirmed") decide(dec, "reask");
      else if (action === "cancel") dec.closest(".ch-dec__confirm")?.remove();
      return;
    }
    const write = target.closest('[data-action="write-from-card"]');
    if (write) openWriteConfirm(write.dataset.card);
  });

  return { mountAll, paintDrafts, captureFocus, restoreFocus };
}
