// 聊天室畫面的控制：把伺服器產的片段（/ui/…）換進外框，接 SSE 事件，送訊息。
//
// 規矩：
// - 伺服器給的 HTML 片段已經跳脫過（Jinja、ui/markdown.py），才用 innerHTML 換進去；
//   這裡自己組的東西（進度列、你剛送出的訊息、串流中的文字）一律用 textContent。
// - 對話、工單的狀態只信 SSE 事件與伺服器片段，不自己估（NFR-OBS-02）；事件的新舊判斷在 state.js。
// - 跟 API 同源（#94 審查）：所有請求都用相對路徑。
// - 沒有輪詢工單：清單每 15 秒與視窗重新取得焦點時重抓一次，因為別的分頁、別的入口也會改清單。

import { buildMessage, fileProblem, restoreAfterFailure, suggestions, toBase64 } from "./compose.js";
import { initInterview } from "./interview-ui.js";
import { describe } from "./progress.js";
import { createState, reconnect, reduce, seedFinished } from "./state.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const shell = $("#ch-shell");
const els = {
  thread: $("#ch-thread"),
  live: $("#ch-live"),
  notes: $("#ch-notes"),
  stream: $("#ch-stream"),
  input: $("#ch-input"),
  send: $("#ch-send"),
  panel: $("#ch-panel"),
  listBody: $("#ch-list-body"),
  suggest: $("#ch-suggest"),
  attached: $("#ch-attached"),
  file: $("#ch-file"),
  box: $("#ch-box"),
  empty: $("#ch-empty"),
  loading: $("#ch-loading"),
  confirm: $("#ch-confirm"),
};

let conversationId = shell.dataset.conversation || "";
// 不在任何對話裡的工單：唯讀，沒有 Pi、沒有事件串流、不能送訊息
const loose = shell.dataset.loose === "true";
let state = createState();
let source = null;
let attachments = [];
const view = { panelOpen: false, tab: "report", job: null, diff: false, card: null };
const PANEL_TABS = ["report", "decisions", "article", "cost"];
const pending = []; // 剛送出、還沒進紀錄的訊息（畫面先放著）

// ── 小工具 ───────────────────────────────────────────────────────

const SVG = "http://www.w3.org/2000/svg";
const ICON = {
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
  pencil: ["M4 20h4L19 9l-4-4L4 16z"],
  gpu: ["M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z", "M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"],
  rate: ["M12 3v6M8 7l4-4 4 4", "M4 21h16", "M6 17h12"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-3.5-3.5"],
  globe: ["M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16", "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z"],
  script: ["M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"],
  resume: ["M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3", "M18 3v4h-4M6 21v-4h4"],
  warn: ["M12 3l9 16H3z", "M12 10v4", "M12 17h.01"],
  check: ["M5 12l5 5L20 7"],
};

function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICON[name] ?? []) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 私密視窗、被擋的網站資料：沒有也照樣能用
  }
}

function load(key) {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

async function fragment(url) {
  const response = await fetch(url, { headers: { accept: "text/html" } });
  return { status: response.status, html: await response.text() };
}

function nearBottom() {
  const s = els.stream;
  return s.scrollHeight - s.scrollTop - s.clientHeight < 120;
}

function scrollToEnd() {
  els.stream.scrollTop = els.stream.scrollHeight;
}

// ── 對話清單 ─────────────────────────────────────────────────────

function applyNewCount(count) {
  const badge = $("[data-new-count]");
  if (badge) {
    badge.textContent = `有新結果 ${count}`;
    badge.hidden = count === 0;
  }
  document.title = (count ? `(${count}) ` : "") + "Evidence Forge";
}

async function refreshList() {
  const query = conversationId ? `?current=${encodeURIComponent(conversationId)}` : "";
  try {
    const { html } = await fragment(`/ui/list${query}`);
    els.listBody.innerHTML = html;
    const root = $("[data-list-root]", els.listBody);
    if (root) applyNewCount(Number(root.dataset.newCount || 0));
    filterList();
  } catch {
    // 連不到伺服器：留著舊的清單
  }
}

function filterList() {
  const needle = $("#ch-search").value.trim().toLowerCase();
  for (const row of $$("[data-conversation]", els.listBody)) {
    const li = row.closest("li");
    if (li) li.hidden = needle !== "" && !(row.dataset.title ?? "").toLowerCase().includes(needle);
  }
}

// ── 對話串流 ─────────────────────────────────────────────────────

let threadBusy = false;
let threadDirty = false;
let threadTimer = null;

function refreshThread() {
  clearTimeout(threadTimer);
  threadTimer = setTimeout(loadThread, 120);
}

async function loadThread() {
  if (!conversationId) return;
  if (threadBusy) {
    threadDirty = true;
    return;
  }
  threadBusy = true;
  const first = !els.thread.firstChild;
  const stick = first || nearBottom();
  try {
    const { status, html } = await fragment(`/ui/c/${encodeURIComponent(conversationId)}/thread`);
    if (els.loading) els.loading.remove();
    els.loading = null;
    const focusToken = interview.captureFocus();
    els.thread.innerHTML = html;
    // 問題卡長出狀態：答到一半的草稿與焦點接回去（interview-ui.js）
    interview.mountAll(els.thread);
    interview.restoreFocus(focusToken);
    const root = $("[data-thread-root]", els.thread);
    if (status === 200 && root) {
      pending.length = 0;
      els.live.replaceChildren();
      $("#ch-title").textContent = root.dataset.title || "新對話";
      $("#ch-spent").textContent = root.dataset.spent || "—";
      $("#ch-spent-wrap").hidden = false;
      // 載入時串流裡已經是結果卡的工單：之後的現況事件不用為它們重抓
      state = seedFinished(state, $$(".ch-result[data-job]", els.thread).map((n) => n.dataset.job));
      renderSuggestions(root);
      renderAllProgress();
      reportSeen(root);
    }
  } catch (error) {
    els.thread.replaceChildren(el("div", { class: "s0-alert", role: "alert" }, icon("warn"), el("div", { text: `連不到伺服器：${error.message}` })));
  } finally {
    threadBusy = false;
    if (stick) scrollToEnd();
    if (threadDirty) {
      threadDirty = false;
      refreshThread();
    }
  }
}

function renderSuggestions(root) {
  const done = root.dataset.latestDone === "true" && root.dataset.latestFailed !== "true";
  const items = done ? suggestions(root.dataset.lastKind, root.dataset.persona || "me") : [];
  els.suggest.replaceChildren(
    ...items.map((text) => el("button", { class: "ch-chip", type: "button", "data-fill": text, text })),
  );
}

let lastSeen = null;
async function reportSeen(root) {
  if (loose) return;
  const latest = root.dataset.latestJob;
  if (!latest || root.dataset.latestDone !== "true" || root.dataset.latestFailed === "true") return;
  if (lastSeen === `${conversationId}:${latest}`) return;
  lastSeen = `${conversationId}:${latest}`;
  try {
    await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/seen`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ job_id: latest }),
    });
    refreshList();
  } catch {
    // 下次打開再報；清單頂多多標一次「有新結果」
  }
}

// ── 進度列 ───────────────────────────────────────────────────────

function renderProgress(node, job) {
  if (!job || job.finished) {
    node.hidden = true;
    return;
  }
  const info = describe(job, Date.now());
  const line = el("div", { class: "ch-progress__line" });
  line.append(el("span", { class: "ch-progress__state" }, icon(info.icon), document.createTextNode(info.label)));
  for (const part of info.parts) {
    if (part.kind === "stage") {
      line.append(el("span", {}, el("span", { class: "s0-mono", text: part.index }), document.createTextNode(` ${part.name}`)));
    } else if (part.kind === "waiting") {
      line.append(el("span", {}, icon(part.icon), document.createTextNode(part.text)));
    } else if (part.kind === "credits") {
      line.append(el("span", {}, el("span", { class: "s0-mono", text: `${part.used}／${part.total}` }), document.createTextNode(" credits")));
    } else if (part.kind === "cost") {
      line.append(el("span", { class: "s0-mono", text: part.text }));
    } else {
      line.append(el("span", { text: part.text }));
    }
  }
  node.replaceChildren(line);
  if (info.sub) node.append(el("p", { class: "ch-progress__sub", text: info.sub }));
  node.hidden = false;
}

function renderAllProgress() {
  for (const node of $$(".ch-progress[data-job]", els.thread)) {
    renderProgress(node, state.jobs[node.dataset.job]);
  }
}

setInterval(() => {
  if ($(".ch-progress:not([hidden])", els.thread)) renderAllProgress();
}, 1000);

// ── 說明列與串流中的文字 ──────────────────────────────────────────

function showNote(kind, text) {
  $$(`[data-note="${kind}"]`, els.notes).forEach((n) => n.remove());
  if (!text) return;
  const node =
    kind === "error"
      ? el("div", { class: "ch-error", role: "alert", "data-note": kind }, icon("warn"), el("span", { text }))
      : el("div", { class: "ch-note", role: "status", "data-note": kind }, icon("resume"), document.createTextNode(text));
  els.notes.append(node);
  if (nearBottom()) scrollToEnd();
}

function showLive(text) {
  let node = $(".ch-agent__live", els.live);
  if (!text) {
    node?.closest(".ch-agent")?.remove();
    return;
  }
  if (!node) {
    node = el("div", { class: "ch-agent__body ch-agent__live" });
    els.live.append(el("div", { class: "ch-agent" }, el("span", { class: "ch-agent__mark", "aria-hidden": "true", text: "EF" }), node));
  }
  const stick = nearBottom();
  node.textContent = text;
  if (stick) scrollToEnd();
}

function showPending() {
  $$(".ch-me[data-pending]", els.live).forEach((n) => n.remove());
  for (const text of pending) {
    els.live.prepend(el("div", { class: "ch-me", "data-pending": "" }, el("div", { class: "ch-me__bubble", text })));
  }
}

// ── 事件 ─────────────────────────────────────────────────────────

function applyEffects(effects) {
  for (const effect of effects) {
    if (effect.type === "progress") {
      const node = $(`.ch-progress[data-job="${CSS.escape(effect.job.job_id)}"]`, els.thread);
      if (node) renderProgress(node, effect.job);
    } else if (effect.type === "refresh") {
      if (effect.what === "thread") refreshThread();
      else if (effect.what === "list") refreshList();
      else if (effect.what === "panel" && view.panelOpen) loadPanel();
    } else if (effect.type === "live") {
      showLive(effect.text);
    } else if (effect.type === "note") {
      if (effect.kind === "clear") showNote(effect.kindOf, "");
      else showNote(effect.kind, effect.text);
    } else if (effect.type === "title") {
      $("#ch-title").textContent = effect.title;
    }
  }
}

function connect() {
  if (!conversationId || source || loose) return;
  source = new EventSource(`/api/conversations/${encodeURIComponent(conversationId)}/events`);
  source.onopen = () => {
    state = reconnect(state);
    // 每次連上（第一次，也包括斷線重連）都重抓一次串流：主機重開、積太多被斷開、
    // 第一次載入到連上之間漏掉的事件，都靠完整紀錄補回來（#94 審查）；編輯卡的事件同理，面板開著就一起重抓
    refreshThread();
    if (view.panelOpen) loadPanel();
  };
  source.onmessage = (message) => {
    let event;
    try {
      event = JSON.parse(message.data);
    } catch {
      return;
    }
    const result = reduce(state, event);
    state = result.state;
    applyEffects(result.effects);
  };
}

// ── 右側面板 ─────────────────────────────────────────────────────

function setPanel(open) {
  view.panelOpen = open;
  shell.dataset.panel = open ? "open" : "closed";
  els.panel.hidden = !open;
  $("#ch-panel-open").setAttribute("aria-expanded", String(open));
  save(`ef:panel:${conversationId}`, { open, tab: view.tab });
  syncPressed();
}

function syncPressed() {
  for (const button of $$("[data-open]", els.thread)) {
    const same = view.panelOpen && button.dataset.open === view.tab && (!view.job || button.dataset.job === view.job);
    button.setAttribute("aria-pressed", String(same));
  }
}

async function loadPanel() {
  if (!conversationId) return;
  const params = new URLSearchParams({ tab: view.tab });
  if (view.job) params.set("v", view.job);
  if (view.diff) params.set("diff", "1");
  if (view.tab === "decisions" && view.card) params.set("card", view.card);
  els.panel.setAttribute("aria-busy", "true");
  // 決策分頁第一次打開：先放骨架，讀編輯卡要一點時間
  if (view.tab === "decisions" && shell.dataset.tab !== "decisions" && !els.panel.hidden) {
    const body = $(".ch-panel__body", els.panel);
    if (body) body.replaceChildren(el("span", { class: "s0-skel", style: "width: 45%; height: 16px" }), el("span", { class: "s0-skel", style: "width: 90%; margin-top: 12px" }), el("span", { class: "s0-skel", style: "width: 80%; margin-top: 12px" }));
  }
  try {
    const { html } = await fragment(`/ui/c/${encodeURIComponent(conversationId)}/panel?${params}`);
    const body = $(".ch-panel__body", els.panel);
    const top = body ? body.scrollTop : 0;
    els.panel.innerHTML = html;
    const fresh = $(".ch-panel__body", els.panel);
    if (fresh) fresh.scrollTop = top;
    shell.dataset.tab = view.tab;
    // 伺服器選了哪一版就記哪一版
    const picked = $("[data-version]", els.panel);
    if (picked) view.job = picked.value;
    const card = $("[data-card-select]", els.panel);
    if (card) view.card = card.value;
    interview.paintDrafts();
  } catch (error) {
    els.panel.replaceChildren(el("div", { class: "ch-panel__body" }, el("div", { class: "s0-alert", role: "alert" }, icon("warn"), el("div", { text: `連不到伺服器：${error.message}` }))));
  } finally {
    els.panel.removeAttribute("aria-busy");
    syncPressed();
  }
}

function openPanel(tab, job = null) {
  view.tab = tab;
  view.job = job;
  setPanel(true);
  return loadPanel();
}

async function copyArticle(jobId, button) {
  const label = button?.querySelector("[data-copy-label]") ?? button;
  try {
    const response = await fetch(`/ui/c/${encodeURIComponent(conversationId)}/article.md?v=${encodeURIComponent(jobId)}`);
    if (!response.ok) throw new Error(await response.text());
    const text = await response.text();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 瀏覽器不讓複製：打開文章分頁，把原文放進可以手動選取的框
      await openPanel("article", jobId);
      const box = $("#ch-md-fallback", els.panel);
      if (box) {
        box.value = text;
        box.closest(".ch-fallback").hidden = false;
        box.select();
      }
      showNote("error", "瀏覽器不讓複製。請手動選取右邊面板下面的文字。");
      return;
    }
    const original = label.textContent;
    label.textContent = "已複製";
    setTimeout(() => {
      label.textContent = original;
    }, 2000);
  } catch (error) {
    showNote("error", `讀不到文章：${error.message}`);
  }
}

// ── 送訊息 ───────────────────────────────────────────────────────

async function postJson(url, body) {
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // 204 或沒有內容
  }
  if (!response.ok) throw new Error((data && data.error) || `${response.status}`);
  return data;
}

async function startConversation() {
  const created = await postJson("/api/conversations", {});
  conversationId = created.id;
  shell.dataset.conversation = conversationId;
  history.pushState({}, "", `/c/${encodeURIComponent(conversationId)}`);
  els.empty?.remove();
  els.empty = null;
  $("#ch-spent-wrap").hidden = false;
  connect();
  return conversationId;
}

// restore：送不出去（503、400、409、斷線）時把打的字和附檔放回輸入框，不讓人重打；
// 輸入框以外送的（「重試」按鈕）沒有東西要放回
async function send(text, { restore = false } = {}) {
  const message = buildMessage(text, attachments);
  if (!message) return;
  const keptAttachments = attachments;
  // 輸入框、附檔、畫面上先放著的訊息，各自有沒有動過：失敗時只還原動過的
  let pushed = false;
  let cleared = false;
  els.send.disabled = true;
  try {
    if (!conversationId) await startConversation();
    pending.push(text.trim() || attachments.map((a) => a.name).join("、"));
    pushed = true;
    showPending();
    scrollToEnd();
    els.input.value = "";
    attachments = [];
    cleared = true;
    renderAttached();
    autoGrow();
    await postJson(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, { text: message });
    showNote("error", "");
  } catch (error) {
    if (pushed) {
      pending.pop();
      showPending();
    }
    if (restore) {
      const back = restoreAfterFailure({
        text,
        inputNow: els.input.value,
        kept: keptAttachments,
        attachmentsNow: attachments,
        cleared,
      });
      els.input.value = back.input;
      attachments = back.attachments;
      renderAttached();
      autoGrow();
    }
    showNote("error", error.message);
  } finally {
    els.send.disabled = false;
    els.input.focus();
  }
}

function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
}

function renderAttached() {
  els.attached.hidden = attachments.length === 0;
  els.attached.replaceChildren(
    ...attachments.map((a, index) =>
      el("button", { class: "ch-chip", type: "button", "data-remove": String(index), "aria-label": `移除 ${a.name}` }, document.createTextNode(`${a.name}（${a.text.length.toLocaleString()} 字） ×`)),
    ),
  );
}

async function addFiles(files) {
  for (const file of files) {
    const problem = fileProblem(file.name, file.size);
    if (problem) {
      showNote("error", problem);
      continue;
    }
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const data = await postJson("/api/extract", { name: file.name, data: toBase64(bytes) });
      attachments.push({ name: data.name, text: data.text });
      showNote("error", "");
    } catch (error) {
      showNote("error", error.message);
    }
  }
  renderAttached();
}

// ── 綁事件 ───────────────────────────────────────────────────────

els.send.addEventListener("click", () => send(els.input.value, { restore: true }));
els.input.addEventListener("input", autoGrow);
els.input.addEventListener("keydown", (event) => {
  // 輸入法選字時的 Enter 不送出
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send(els.input.value, { restore: true });
  }
});
els.input.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.files ?? [])];
  if (files.length) {
    event.preventDefault();
    addFiles(files);
  }
});
$("#ch-attach").addEventListener("click", () => els.file.click());
els.file.addEventListener("change", () => {
  addFiles([...els.file.files]);
  els.file.value = "";
});
els.box.addEventListener("dragover", (event) => {
  event.preventDefault();
  els.box.dataset.drop = "";
});
els.box.addEventListener("dragleave", () => delete els.box.dataset.drop);
els.box.addEventListener("drop", (event) => {
  event.preventDefault();
  delete els.box.dataset.drop;
  addFiles([...(event.dataTransfer?.files ?? [])]);
});
els.attached.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (button) {
    attachments.splice(Number(button.dataset.remove), 1);
    renderAttached();
  }
});

// 建議按鈕只把字填進框裡，不送出（IA §4.4）
document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const fill = target.closest("[data-fill]");
  if (fill) {
    els.input.value = fill.dataset.fill;
    autoGrow();
    els.input.focus();
    return;
  }
  const example = target.closest("[data-example]");
  if (example) {
    if (example.dataset.example === "file") els.file.click();
    else {
      els.input.placeholder = example.dataset.example === "url" ? "貼上文章網址，然後按送出" : "貼上整篇文章，然後按送出";
      els.input.focus();
    }
    return;
  }
  const open = target.closest("[data-open]");
  if (open) {
    openPanel(open.dataset.open, open.dataset.job);
    return;
  }
  const copy = target.closest("[data-copy-article]");
  if (copy) {
    copyArticle(copy.dataset.copyArticle, copy);
    return;
  }
  const sendButton = target.closest("[data-send]");
  if (sendButton) {
    send(sendButton.dataset.send);
    return;
  }
  if (target.closest('[data-action="retry-thread"]')) {
    loadThread();
    return;
  }
  const tab = target.closest("[data-tab]");
  if (tab && els.panel.contains(tab)) {
    view.job = null;
    openPanel(tab.dataset.tab);
    return;
  }
  if (target.closest('[data-action="close-panel"]')) setPanel(false);
});
document.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof Element) || !els.panel.contains(target)) return;
  if (target.matches("[data-version]")) {
    view.job = target.value;
    loadPanel();
  } else if (target.matches("[data-card-select]")) {
    view.card = target.value;
    loadPanel();
  } else if (target.matches("[data-diff]")) {
    view.diff = target.checked;
    loadPanel();
  }
});
$("#ch-panel-open").addEventListener("click", () => openPanel(view.tab, view.job));
$("#ch-search").addEventListener("input", filterList);
$("#ch-list-toggle").addEventListener("click", () => {
  const open = shell.dataset.list !== "open";
  shell.dataset.list = open ? "open" : "closed";
  $("#ch-list-toggle").setAttribute("aria-expanded", String(open));
});
window.addEventListener("focus", refreshList);
setInterval(refreshList, 15000);

// ── 訪談：問題卡、決策分頁的按鈕、寫稿前的最後確認（interview-ui.js） ──────

// 「照這張編輯卡寫稿」的預留位置：寫稿本身（開 rewrite 工單、帶編輯卡的快照）是 票 110 的事。
// 最後確認卡按下「照這張寫」會走到這裡；票 110 接上之前只說明還沒接上，編輯卡照舊留著，不開工單、不花錢。
function startWritingFromCard(cardId) {
  showNote("info", `「照這張編輯卡寫稿」還沒接上（票 110）。編輯卡 ${cardId} 先留著，什麼都沒開、沒花錢。`);
  setTimeout(() => showNote("info", ""), 10000);
}

const interview = initInterview({
  conversation: () => conversationId,
  post: postJson,
  fragment,
  // 寫進決策紀錄之後：對話串流（摘要卡、系統小字）、決策分頁、清單都重抓
  afterWrite: () => {
    refreshThread();
    if (view.panelOpen) loadPanel();
    refreshList();
  },
  startWriting: startWritingFromCard,
  // 窄螢幕上面板是蓋住對話的抽屜：要看最後確認卡就先收起來
  revealChat: () => {
    if (window.matchMedia("(max-width: 1100px)").matches) setPanel(false);
  },
  confirmHost: els.confirm,
  panel: els.panel,
});

// ── 開頁 ─────────────────────────────────────────────────────────

(async function init() {
  autoGrow();
  if (conversationId) {
    await loadThread();
    connect();
    const wanted = shell.dataset.initialTab;
    const remembered = load(`ef:panel:${conversationId}`);
    if (PANEL_TABS.includes(wanted)) await openPanel(wanted);
    else if (remembered?.open) {
      view.tab = PANEL_TABS.includes(remembered.tab) ? remembered.tab : "report";
      await openPanel(view.tab);
    }
  }
  applyNewCount(Number($("[data-list-root]", els.listBody)?.dataset.newCount || 0));
})();
