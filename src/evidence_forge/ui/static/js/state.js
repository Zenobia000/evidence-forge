// 對話串流的狀態：把 SSE 事件（chat/host.py 的事件格式）收成「現在是什麼樣子」加「畫面要做什麼」。
// 純函式，不碰 DOM、不連網路：node 直接測（tests/js/state.test.mjs）。
//
// 事件順序（#94 審查留給這張票的）：主機給每則事件一個流水號 seq。
// 同一張工單、同一個 Pi 的狀態，序號比手上的小就是舊的，丟掉，不能讓舊的蓋過新的。
// 序號只在同一條連線內比：連線重開（EventSource 自動重連）時主機會先送一份現況，
// 而且主機重開之後序號從 1 重算，所以每次連上就 `reconnect(state)` 清掉序號。

export function createState() {
  return {
    // 每張工單最新的 ef_job 事件
    jobs: {},
    // Pi 的狀態：{state, reason, busy, seq}
    pi: { state: "unknown", reason: null, busy: false, seq: 0 },
    // 這條連線看過的最大序號（給沒有分門別類的事件用）
    lastSeq: 0,
    // 正在串流的那則 agent 回答的文字
    live: "",
    title: null,
  };
}

// 連線重開：序號重算，其餘（工單、Pi 狀態）留著，現況事件會蓋過去
export function reconnect(state) {
  return { ...state, lastSeq: 0, jobs: mapValues(state.jobs, (job) => ({ ...job, seq: 0 })), pi: { ...state.pi, seq: 0 } };
}

// 畫面載入時串流裡已經是結果卡的工單：之後的現況事件不用為它們重抓
export function seedFinished(state, jobIds) {
  const jobs = { ...state.jobs };
  for (const id of jobIds) {
    jobs[id] = { ...(jobs[id] ?? {}), type: "ef_job", job_id: id, finished: true, seq: 0 };
  }
  return { ...state, jobs };
}

function mapValues(object, fn) {
  return Object.fromEntries(Object.entries(object).map(([key, value]) => [key, fn(value)]));
}

function textOf(message) {
  const content = message && message.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && part.type === "text")
    .map((part) => String(part.text ?? ""))
    .join("");
}

// 回傳 { state, effects }。effects 是畫面要做的事，名字固定：
//   progress    某張工單的進度列要重畫（帶 job）
//   refresh     對話串流、清單要重抓（帶 what: "thread" | "list" | "panel"）
//   seen        創作者正在看的對話有新的結果出來了（帶 job_id）
//   live        串流中的回答文字變了（帶 text）
//   note        要顯示的一行說明（帶 kind: "resume" | "error"、text），kind 為 "clear" 時收掉
//   title       對話標題變了（帶 title）
export function reduce(state, event) {
  const effects = [];
  if (!event || typeof event !== "object") return { state, effects };
  const seq = typeof event.seq === "number" ? event.seq : null;
  switch (event.type) {
    case "ef_job": {
      const known = state.jobs[event.job_id];
      if (known && seq !== null && known.seq >= seq) return { state, effects };
      const job = { ...event, seq: seq ?? 0 };
      const next = { ...state, jobs: { ...state.jobs, [event.job_id]: job } };
      effects.push({ type: "progress", job });
      // 新結束的工單：串流多一張結果卡、清單要換狀態、面板多一版。
      // 重連後的現況事件也走這裡，但已經知道它結束了就不重做
      if (job.finished && !(known && known.finished)) {
        effects.push({ type: "refresh", what: "thread" }, { type: "refresh", what: "list" }, { type: "refresh", what: "panel" });
        if (job.outcome !== "failed") effects.push({ type: "seen", job_id: job.job_id });
      }
      return { state: next, effects };
    }
    case "ef_pi": {
      if (seq !== null && state.pi.seq >= seq) return { state, effects };
      const pi = { state: event.state, reason: event.reason ?? null, busy: Boolean(event.busy), seq: seq ?? 0 };
      if (pi.state === "starting") effects.push({ type: "note", kind: "resume", text: "接回對話中" });
      else effects.push({ type: "note", kind: "clear", kindOf: "resume" });
      return { state: { ...state, pi }, effects };
    }
    case "agent_start":
      return { state: { ...state, pi: { ...state.pi, busy: true }, live: "" }, effects };
    case "message_update": {
      const message = event.message;
      if (!message || message.role !== "assistant") return { state, effects };
      const live = textOf(message);
      effects.push({ type: "live", text: live });
      return { state: { ...state, live }, effects };
    }
    case "message_end": {
      effects.push({ type: "refresh", what: "thread" });
      const isAssistant = event.message && event.message.role === "assistant";
      if (isAssistant) effects.push({ type: "live", text: "" });
      return { state: isAssistant ? { ...state, live: "" } : state, effects };
    }
    case "agent_settled":
    case "agent_end":
      effects.push({ type: "refresh", what: "list" });
      return { state: { ...state, pi: { ...state.pi, busy: false } }, effects };
    case "ef_conversation":
      effects.push({ type: "title", title: event.title }, { type: "refresh", what: "list" });
      return { state: { ...state, title: event.title }, effects };
    case "ef_error":
      effects.push({ type: "note", kind: "error", text: String(event.message ?? "出錯了") });
      return { state, effects };
    default:
      return { state, effects };
  }
}
