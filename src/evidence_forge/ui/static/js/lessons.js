// lesson 提議的「同意」「不要」（票 114、ADR-0026 §3.3）：純函式，不碰 DOM、不連網路，node 直接測（tests/js/lessons.test.mjs）。
// 按鈕在對話裡的小卡上（ui/templates/_thread.html）；按下去打對話主機的 API，由閘道把那一行寫進角色的 lessons.md（同意），
// 或什麼都不寫（不要）。agent 沒有對應的工具，所以這是唯一的入口。

const ACTIONS = ["approve", "reject"];

export function lessonRequest(conversationId, proposalId, action) {
  if (!ACTIONS.includes(action)) throw new Error(`沒有這個動作：${action}`);
  if (!/^L\d+$/.test(String(proposalId ?? ""))) throw new Error(`提議的編號不對：${proposalId}`);
  const url = `/api/conversations/${encodeURIComponent(conversationId)}/lessons/${encodeURIComponent(proposalId)}/${action}`;
  return { url, body: {} };
}

// 伺服器的回覆 → 畫面上說什麼。成功就什麼都不用說（小卡會換成一行系統小字）；失敗要說原因，而且是沒寫東西、提議還在
export function lessonOutcome(action, status, data) {
  if (status >= 200 && status < 300) return { ok: true, text: "" };
  const reason = (data && data.error) || `伺服器回 ${status}`;
  const what = action === "approve" ? "沒有寫進 lessons" : "沒有關掉這筆提議";
  return { ok: false, text: `${what}：${reason}` };
}
