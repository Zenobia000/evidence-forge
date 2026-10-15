// 角色頁表單怎麼變成送出去的 JSON（src/evidence_forge/ui/static/js/persona.js）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { createBody, createdMessage, renameBody } from "../../src/evidence_forge/ui/static/js/persona.js";

test("新增：只有名稱就只送名稱，從範本開始", () => {
  assert.deepEqual(createBody({ name: " writer-2 ", display_name: "", from: "" }).body, { name: "writer-2" });
});

test("新增：顯示名稱與要複製的角色都帶上", () => {
  assert.deepEqual(createBody({ name: "b", display_name: " 小乙 ", from: "a" }).body, {
    name: "b",
    display_name: "小乙",
    from: "a",
  });
});

test("新增：名稱不合規則就不送（空、點開頭、斜線、..、空白、中文）", () => {
  for (const name of ["", ".hidden", "a/b", "a..b", "a b", "小明", "_x"]) {
    assert.ok(createBody({ name }).error, `應該擋：${JSON.stringify(name)}`);
  }
});

test("新增：顯示名稱太長或有換行就不送", () => {
  assert.ok(createBody({ name: "a", display_name: "字".repeat(41) }).error);
  assert.ok(createBody({ name: "a", display_name: "a\nb" }).error);
});

test("改名：不能清成空的", () => {
  assert.ok(renameBody({ display_name: "  " }).error);
  assert.deepEqual(renameBody({ display_name: " 新名字 " }).body, { display_name: "新名字" });
});

test("複製時有符號連結沒帶，要說出來", () => {
  assert.equal(createdMessage({}), "建好了");
  assert.match(createdMessage({ skipped_links: ["craft/x.md"] }), /1 個符號連結沒有複製：craft\/x\.md/);
});
