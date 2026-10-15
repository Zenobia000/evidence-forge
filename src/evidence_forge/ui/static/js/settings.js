// 費用設定頁（/settings）：把表單整理成 JSON 送到 /api/settings/costs。
// costBody 是純函式，node 直接測（tests/js/settings.test.mjs）；下面接 DOM 的部分只在瀏覽器跑。

const MONEY_FIELDS = ["analysis_budget_usd", "rewrite_budget_usd", "image_cap_usd"];
// 跟伺服器的 cost_settings.MAX_USD 同一個數字；伺服器照樣會再驗一次
const MAX_USD = 1_000_000;

// 表單裡的字串換成金額；空的、不是數字、超過 MAX_USD、是負數（`strict` 時 0 也不行）回 null
function money(raw, { strict }) {
  const text = String(raw ?? "").trim();
  if (text === "") return null;
  const value = Number(text);
  if (!Number.isFinite(value) || value > MAX_USD) return null;
  return (strict ? value > 0 : value >= 0) ? value : null;
}

// values：{cap_mode: "none"｜"set", monthly_cap_usd, analysis_budget_usd, …}（表單裡的字串）
// 回 {body} 或 {error}：每月上限選「不設」送 null、設了可以是 0（什麼都不准花）；
// 單張預算與生圖上限要大於 0（0 的單一開就失敗）
export function costBody(values) {
  const body = {};
  if (values.cap_mode === "none") {
    body.monthly_cap_usd = null;
  } else {
    const cap = money(values.monthly_cap_usd, { strict: false });
    if (cap === null) return { error: "每月上限要填 0 到 1,000,000 的金額，或選「不設」" };
    body.monthly_cap_usd = cap;
  }
  for (const name of MONEY_FIELDS) {
    const value = money(values[name], { strict: true });
    if (value === null) return { error: "預算與生圖上限都要填大於 0、最多 1,000,000 的金額" };
    body[name] = value;
  }
  return { body };
}

// 存成功之後怎麼辦：平常整頁重讀（上面的數字跟著新設定重算）；改動紀錄沒寫成時設定其實已經生效，
// 留在這頁把話說清楚，不重讀（重讀就看不到這句了）
export function saveOutcome(reply) {
  if (reply?.log_failed) {
    const reason = reply.log_error ? `（${reply.log_error}）` : "";
    return { reload: false, message: `設定已經生效，但改動紀錄沒寫進去${reason}。重新整理頁面就會看到新的數字。` };
  }
  return { reload: true, message: "" };
}

if (typeof document !== "undefined") {
  const form = document.getElementById("cost-settings");
  const result = form?.querySelector("[data-result]");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const { body, error } = costBody(Object.fromEntries(new FormData(form)));
    if (error) {
      result.textContent = error;
      return;
    }
    result.textContent = "存檔中…";
    try {
      const response = await fetch("/api/settings/costs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const reply = await response.json();
      if (!response.ok) {
        result.textContent = reply.error ?? `沒存成（${response.status}）`;
        return;
      }
      const outcome = saveOutcome(reply);
      if (outcome.reload) {
        window.location.reload();
      } else {
        result.textContent = outcome.message;
      }
    } catch (problem) {
      result.textContent = `沒存成：${problem}`;
    }
  });
}
