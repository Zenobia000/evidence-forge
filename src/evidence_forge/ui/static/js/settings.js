// 費用設定頁（/settings）：把表單整理成 JSON 送到 /api/settings/costs。
// costBody 是純函式，node 直接測（tests/js/settings.test.mjs）；下面接 DOM 的部分只在瀏覽器跑。

const MONEY_FIELDS = ["analysis_budget_usd", "rewrite_budget_usd", "image_cap_usd"];

function money(raw) {
  const text = String(raw ?? "").trim();
  if (text === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

// values：{cap_mode: "none"｜"set", monthly_cap_usd, analysis_budget_usd, …}（表單裡的字串）
// 回 {body} 或 {error}：每月上限選「不設」送 null；其他金額要是 0 以上的數字
export function costBody(values) {
  const body = {};
  if (values.cap_mode === "none") {
    body.monthly_cap_usd = null;
  } else {
    const cap = money(values.monthly_cap_usd);
    if (cap === null) return { error: "每月上限要填 0 以上的金額，或選「不設」" };
    body.monthly_cap_usd = cap;
  }
  for (const name of MONEY_FIELDS) {
    const value = money(values[name]);
    if (value === null) return { error: "預算與生圖上限都要填 0 以上的金額" };
    body[name] = value;
  }
  return { body };
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
      // 上面的數字（剩多少、讀不懂的警告）跟著新設定重算：整頁重讀
      window.location.reload();
    } catch (problem) {
      result.textContent = `沒存成：${problem}`;
    }
  });
}
