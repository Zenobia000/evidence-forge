// 費用設定頁的表單怎麼變成送出去的 JSON（src/evidence_forge/ui/static/js/settings.js）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { costBody, saveOutcome } from "../../src/evidence_forge/ui/static/js/settings.js";

const BUDGETS = { analysis_budget_usd: "0.5", rewrite_budget_usd: "0.8", image_cap_usd: "0.25" };

test("每月上限選不設就送 null，填的數字不管", () => {
  assert.deepEqual(costBody({ cap_mode: "none", monthly_cap_usd: "12", ...BUDGETS }).body, {
    monthly_cap_usd: null,
    analysis_budget_usd: 0.5,
    rewrite_budget_usd: 0.8,
    image_cap_usd: 0.25,
  });
});

test("設上限就送數字，0 也算", () => {
  assert.equal(costBody({ cap_mode: "set", monthly_cap_usd: "0", ...BUDGETS }).body.monthly_cap_usd, 0);
});

test("設上限卻沒填、填負數或不是數字，就不送並說明", () => {
  for (const cap of ["", "-1", "十塊", "Infinity"]) {
    assert.match(costBody({ cap_mode: "set", monthly_cap_usd: cap, ...BUDGETS }).error, /每月上限/);
  }
});

test("預算沒填、是負數或是 0 就不送（0 的單一開就失敗）", () => {
  assert.ok(costBody({ cap_mode: "none", ...BUDGETS, rewrite_budget_usd: "" }).error);
  assert.ok(costBody({ cap_mode: "none", ...BUDGETS, image_cap_usd: "-0.1" }).error);
  assert.ok(costBody({ cap_mode: "none", ...BUDGETS, analysis_budget_usd: "0" }).error);
});

test("金額超過一百萬就不送，剛好一百萬可以", () => {
  assert.match(costBody({ cap_mode: "set", monthly_cap_usd: "1e25", ...BUDGETS }).error, /每月上限/);
  assert.ok(costBody({ cap_mode: "none", ...BUDGETS, rewrite_budget_usd: "1000000.01" }).error);
  assert.equal(costBody({ cap_mode: "set", monthly_cap_usd: "1000000", ...BUDGETS }).body.monthly_cap_usd, 1000000);
});

test("存成功但改動紀錄沒寫成：說清楚設定已經生效，不重讀頁面", () => {
  const outcome = saveOutcome({ log_failed: true, log_error: "changes.jsonl 寫不進去：Is a directory" });
  assert.equal(outcome.reload, false);
  assert.match(outcome.message, /已經生效/);
  assert.match(outcome.message, /changes\.jsonl 寫不進去/);
});

test("存成功而且紀錄也寫了：重讀頁面", () => {
  assert.deepEqual(saveOutcome({ log_failed: false, log_error: null }), { reload: true, message: "" });
});
