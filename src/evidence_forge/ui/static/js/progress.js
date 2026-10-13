// 工作進度列要顯示的字（IA §4.2、NFR-OBS-02）。全部是真的狀態：第幾段、跑多久、在等什麼、花了多少，
// 不估算、不放百分比進度條。純函式，node 直接測（tests/js/progress.test.mjs）。

const STAGE_NAME = {
  brief: "brief",
  investigate: "查證與找題材",
  rewrite: "重構",
};
const WAITING_KIND = {
  gpu: { icon: "gpu", text: "等 GPU" },
  rate_limit: { icon: "rate", text: "等限流" },
  model: { icon: "clock", text: "等模型回應" },
};
const TOOL = {
  web_search: { icon: "search", text: "搜尋中" },
  fetch_url: { icon: "globe", text: "抓取網頁中" },
  run_script: { icon: "script", text: "跑腳本中" },
};

export function stageName(stage) {
  if (!stage) return "";
  return STAGE_NAME[stage.id] ?? STAGE_NAME[stage.skill] ?? stage.id;
}

export function waitingText(waiting) {
  if (!waiting) return null;
  if (waiting.kind === "tool") {
    return TOOL[waiting.tool] ?? { icon: "globe", text: `執行工具中：${waiting.tool ?? ""}` };
  }
  return WAITING_KIND[waiting.kind] ?? null;
}

// `3 分 12 秒`；負的（時鐘差）當 0
export function elapsedText(startedAt, nowMs) {
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return null;
  const seconds = Math.max(0, Math.floor((nowMs - started) / 1000));
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function money(value) {
  return Number(value ?? 0).toFixed(2);
}

// 一張還沒結束的工單 → 進度列。`nowMs` 由呼叫的人給（測試才不用看時鐘）
export function describe(job, nowMs) {
  const writing = job.kind === "rewrite";
  const label = job.status === "queued" ? "排隊中" : writing ? "寫作中" : "分析中";
  const icon = job.status === "queued" ? "clock" : writing ? "pencil" : "clock";
  const parts = [];
  if (job.stage) {
    parts.push({ kind: "stage", index: `${job.stage.index}／${job.stage.total}`, name: stageName(job.stage) });
    const elapsed = elapsedText(job.stage.started_at, nowMs);
    if (elapsed) parts.push({ kind: "elapsed", text: elapsed });
  }
  const waiting = job.status === "running" ? waitingText(job.waiting) : null;
  if (waiting) parts.push({ kind: "waiting", ...waiting });
  parts.push({ kind: "cost", text: `US$${money(job.spent_usd)}／${money(job.budget_usd)}` });
  if (job.budget_credits > 0) {
    parts.push({ kind: "credits", used: job.credits_used ?? 0, total: job.budget_credits });
  }
  const attempt = job.stage && job.stage.attempt > 1 ? `這一段第 ${job.stage.attempt} 次` : null;
  return { label, icon, parts, sub: attempt };
}
