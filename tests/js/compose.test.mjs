// 輸入框送出的訊息（static/js/compose.js）：打的字加附檔、附檔的格式跟伺服器認得的一致、建議按鈕。

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { buildMessage, fileProblem, restoreAfterFailure, suggestions, toBase64 } from "../../src/evidence_forge/ui/static/js/compose.js";

test("只有打的字：原樣送出，去掉前後空白", () => {
  assert.equal(buildMessage("  幫我分析這篇  "), "幫我分析這篇");
});

test("字加附檔：附檔用標好界線的區塊接在後面", () => {
  const message = buildMessage("請分析", [{ name: "note.md", text: "第一段\n第二段" }]);
  assert.equal(message, "請分析\n\n〔附檔 note.md〕\n第一段\n第二段\n〔附檔結束〕");
});

test("只有附檔、沒打字也能送", () => {
  assert.equal(buildMessage("", [{ name: "a.txt", text: "內容" }]), "〔附檔 a.txt〕\n內容\n〔附檔結束〕");
});

test("檔名裡的界線字元不能弄壞格式", () => {
  const message = buildMessage("", [{ name: "x〕\n.md", text: "內容" }]);
  assert.match(message, /^〔附檔 x  \.md〕\n內容\n〔附檔結束〕$/);
});

test("格式跟伺服器（ui/thread.py）認的是同一個：從原始碼把正規式讀出來比對", () => {
  const source = readFileSync(new URL("../../src/evidence_forge/ui/thread.py", import.meta.url), "utf8");
  assert.ok(source.includes("〔附檔 (?P<name>[^〕\\n]+)〕\\n(?P<body>.*?)\\n〔附檔結束〕"));
});

test("只收 Markdown、純文字、PDF，太大的擋下", () => {
  assert.equal(fileProblem("a.md", 10), null);
  assert.equal(fileProblem("A.PDF", 10), null);
  assert.match(fileProblem("a.docx", 10), /格式不收/);
  assert.match(fileProblem("a.txt", 6 * 1024 * 1024), /太大/);
});

test("建議按鈕：剛分析完和剛寫完各三個；其他時候沒有", () => {
  assert.deepEqual(suggestions("analysis", "me"), ["用 me 寫一篇理論教學", "再查一下有疑慮的那條", "這篇的立場是什麼？"]);
  assert.equal(suggestions("rewrite", "me").length, 3);
  assert.deepEqual(suggestions("", "me"), []);
});

test("base64 跟 node 自己算的一樣（含大檔分段）", () => {
  const bytes = new Uint8Array(70000).map((_, i) => i % 251);
  assert.equal(toBase64(bytes), Buffer.from(bytes).toString("base64"));
});

test("送不出去：打的字和附檔放回去，不用重打", () => {
  const kept = [{ name: "a.md", text: "內容" }];
  const back = restoreAfterFailure({ text: "請分析", inputNow: "", kept, attachmentsNow: [], cleared: true });
  assert.deepEqual(back, { input: "請分析", attachments: kept });
});

test("送不出去的這段期間又打了新的一句：舊的在前、新的在後，不蓋掉", () => {
  const back = restoreAfterFailure({
    text: "第一句",
    inputNow: "第二句",
    kept: [],
    attachmentsNow: [{ name: "b.txt", text: "x" }],
    cleared: true,
  });
  assert.equal(back.input, "第一句\n第二句");
  assert.deepEqual(back.attachments.map((a) => a.name), ["b.txt"]);
});

test("開新對話就失敗、輸入框還沒清：原封不動，不會變成兩份", () => {
  const kept = [{ name: "a.md", text: "內容" }];
  const back = restoreAfterFailure({ text: "請分析", inputNow: "請分析", kept, attachmentsNow: kept, cleared: false });
  assert.equal(back.input, "請分析");
  assert.deepEqual(back.attachments, kept);
});
