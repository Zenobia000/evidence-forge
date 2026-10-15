// lessons 的對話工具（ADR-0026 §3.3、創作者 2026-10-15 決定 G、#114）：跟對話的工具一起註冊（chat.ts）。
//
// - propose_lesson：提議一筆 lesson。只記提議、不花錢、**不寫角色資料夾**；創作者在畫面上按「同意」之後，
//   由閘道把那一行附加到角色的 lessons.md。同意與不要都是畫面的按鈕，這裡沒有對應的工具，模型叫不到。
// - /ef-lesson <提議> <approved|rejected> <角色>：對話主機在創作者按了同意或不要之後送進來的指令（不是模型能叫的工具）。
//   在對話紀錄放一則 ef-lesson 通知，**不開新的一輪**（不為了一個按鈕多花一次模型）；模型下一輪就讀得到。
//   同一筆提議同一個決定已經通知過就不再放。
//
// 範圍與格式檢查全部在閘道（gateway/chat_tools.py、lesson_proposals.py）：這裡只轉送。
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

type CallGateway = (tool: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }], details: undefined };
}

// 提議的編號 L<n>、角色名：指令的參數不合格就不理（角色名同 jobkey.py 的字元規則）
const PROPOSAL_ID = /^L\d{1,9}$/;
const PERSONA = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const DECISIONS: Record<string, string> = {
  approved: "approved",
  rejected: "dismissed",
};

export function registerLessonTools(pi: ExtensionAPI, callGateway: CallGateway): void {
  pi.registerTool(
    defineTool({
      name: "propose_lesson",
      label: "Propose lesson",
      description:
        "Propose one lesson for a persona: a rule that came from the creator rejecting or correcting something. It only records a proposal for the creator to approve or dismiss on screen; nothing is saved to the persona until they approve, and you can neither approve nor dismiss. Write text as ONE line in Traditional Chinese: which article (第 N 篇, or the topic) ｜ what went wrong → what to do instead, at most 200 characters, no line breaks, no HTML comment markers. why is the creator's own correction in a sentence or two (what they said), so they can judge the proposal. Costs nothing. At most one proposal per creator correction.",
      parameters: Type.Object({
        persona: Type.String({ description: "Persona name from list_personas whose lessons.md this rule belongs to; it must exist" }),
        text: Type.String({ description: "The rule as one line: 哪一篇｜發生什麼 → 改成什麼 (the date is added by the system)" }),
        why: Type.String({ description: "What the creator said that this rule comes from, in a sentence or two (shown to them with the proposal)" }),
      }),
      async execute(_id, params) {
        const data = await callGateway("propose_lesson", { persona: params.persona, text: params.text, why: params.why });
        const again = data.created === false ? " (the same proposal was already waiting; nothing new was added)" : "";
        return text(
          `proposal: ${String(data.proposal)}${again}\npersona: ${String(data.persona)}\nstatus: ${String(data.status)}\nIf approved, this line is added to the persona's lessons: ${String(data.line)}\nNOT SAVED YET: the creator approves or dismisses it on screen. Do not say it is saved; tell them the proposal is on screen.`,
        );
      },
    }),
  );

  pi.registerCommand("ef-lesson", {
    description: "Internal: the chat host reports that the creator approved or dismissed a lesson proposal.",
    handler: async (args, ctx) => {
      const [proposal = "", decision = "", persona = ""] = args.trim().split(/\s+/);
      if (!PROPOSAL_ID.test(proposal) || !(decision in DECISIONS) || !PERSONA.test(persona)) return;
      const already = ctx.sessionManager
        .getBranch()
        .some((entry: { type: string; customType?: string; details?: unknown }) => {
          const details = entry.details as { proposal?: string; decision?: string } | undefined;
          return (
            entry.type === "custom_message" &&
            entry.customType === "ef-lesson" &&
            details?.proposal === proposal &&
            details?.decision === decision
          );
        });
      if (already) return;
      const effect =
        decision === "approved"
          ? `It is now in the lessons of persona ${persona} (written by the system); read_lessons shows it.`
          : "Nothing was written.";
      pi.sendMessage(
        {
          customType: "ef-lesson",
          content: `[ef-lesson] The creator ${DECISIONS[decision]} your lesson proposal ${proposal} on screen. ${effect} It is recorded already; do not propose it again.`,
          display: true,
          details: { proposal, decision, persona },
        },
        // 不開新的一輪：只放進紀錄，下一輪就看得到
        { triggerTurn: false },
      );
    },
  });
}
