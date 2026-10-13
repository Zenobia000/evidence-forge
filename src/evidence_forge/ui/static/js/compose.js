// 輸入框送出的訊息怎麼組：打的字加上附檔。純函式，node 直接測（tests/js/compose.test.mjs）。
//
// 附檔的文字抽好之後（/api/extract）放進訊息，格式跟 ui/thread.py 的 ATTACHMENT 對得上：
//   〔附檔 檔名〕
//   全文
//   〔附檔結束〕
// 對話裡顯示成檔名和字數，不把全文貼成一大塊泡泡；agent 看到的是標好界線的全文，照 analyze 的 text 開工。

export const ACCEPTED = [".md", ".markdown", ".txt", ".pdf"];
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

export function fileProblem(name, size) {
  const lower = String(name).toLowerCase();
  if (!ACCEPTED.some((suffix) => lower.endsWith(suffix))) {
    return `${name} 的格式不收；只收 Markdown、純文字、PDF`;
  }
  if (size > MAX_UPLOAD_BYTES) return `${name} 太大（上限 5 MB）`;
  return null;
}

// 檔名裡不能有會弄壞界線的字
function safeName(name) {
  return String(name).replace(/[〕\n\r]/g, " ").trim() || "upload";
}

export function buildMessage(text, attachments = []) {
  const typed = String(text ?? "").trim();
  const blocks = attachments.map((a) => `〔附檔 ${safeName(a.name)}〕\n${a.text}\n〔附檔結束〕`);
  return [typed, ...blocks].filter(Boolean).join("\n\n");
}

// 建議按鈕要填進框裡的字
export function suggestions(lastKind, persona) {
  if (lastKind === "analysis") {
    return [`用 ${persona} 寫一篇觀點文`, "再查一下有疑慮的那條", "這篇的立場是什麼？"];
  }
  if (lastKind === "rewrite") {
    return ["開頭直接講結論", "換一個標題", "換成 B2B 角色寫"];
  }
  return [];
}

export function toBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// 送不出去：打的字和附檔放回輸入框。失敗的這段期間人可能已經開始打下一句，不蓋掉他新打的：
// 舊的接在前面、新的在後面。回新的輸入框內容與附檔
export function restoreAfterFailure({ text, typedSince, kept, addedSince }) {
  const input = typedSince ? `${text}\n${typedSince}` : text;
  return { input, attachments: [...kept, ...addedSince] };
}
