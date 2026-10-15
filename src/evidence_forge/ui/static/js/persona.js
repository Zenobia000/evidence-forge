// 角色頁（/personas、/personas/<角色>）：新增與改顯示名稱的表單，整理成 JSON 送到 /api/personas。
// createBody、renameBody 是純函式，node 直接測（tests/js/persona.test.mjs）；下面接 DOM 的部分只在瀏覽器跑。
// 規則跟伺服器的 jobkey.check_name、persona.clean_display_name 一樣；伺服器照樣會再驗一次。

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const MAX_DISPLAY = 40;

// 顯示名稱：去頭尾空白；空的回 null（新增時代表不填），太長或有控制字元回 { error }
function displayName(raw) {
  const text = String(raw ?? "").trim();
  if (text === "") return { value: null };
  if ([...text].length > MAX_DISPLAY) return { error: `顯示名稱最多 ${MAX_DISPLAY} 字` };
  if (/[\u0000-\u001f\u007f]/.test(text)) return { error: "顯示名稱不能有換行或控制字元" };
  return { value: text };
}

// values：{name, display_name, from}（表單裡的字串）→ {body} 或 {error}
export function createBody(values) {
  const name = String(values.name ?? "").trim();
  if (!NAME.test(name) || name.includes("..")) {
    return { error: "資料夾名稱要英數字開頭，之後只能用英數字、點、底線、連字號" };
  }
  const shown = displayName(values.display_name);
  if (shown.error) return { error: shown.error };
  const body = { name };
  if (shown.value !== null) body.display_name = shown.value;
  const origin = String(values.from ?? "").trim();
  if (origin !== "") body.from = origin;
  return { body };
}

// 改顯示名稱不能清成空的（要回到資料夾名稱就填資料夾名稱）
export function renameBody(values) {
  const shown = displayName(values.display_name);
  if (shown.error) return { error: shown.error };
  if (shown.value === null) return { error: "顯示名稱不能是空的" };
  return { body: { display_name: shown.value } };
}

// 伺服器的回應整理成一句話；`skipped_links` 有東西時要說，免得創作者以為連結也複製了
export function createdMessage(reply) {
  const links = reply?.skipped_links ?? [];
  return links.length ? `建好了。${links.length} 個符號連結沒有複製：${links.join("、")}` : "建好了";
}

async function send(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let reply = {};
  try {
    reply = await response.json();
  } catch {
    // 伺服器沒回 JSON：照狀態碼說
  }
  return { ok: response.ok, reply, status: response.status };
}

if (typeof document !== "undefined") {
  const create = document.getElementById("persona-create");
  create?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = create.querySelector("[data-result]");
    const { body, error } = createBody(Object.fromEntries(new FormData(create)));
    if (error) {
      result.textContent = error;
      return;
    }
    result.textContent = "建立中…";
    try {
      const { ok, reply, status } = await send("/api/personas", body);
      if (!ok) {
        result.textContent = reply.error ?? `沒建成（${status}）`;
        return;
      }
      if ((reply.skipped_links ?? []).length) {
        result.textContent = createdMessage(reply);
        return;
      }
      window.location.assign(`/personas/${encodeURIComponent(reply.persona)}`);
    } catch (problem) {
      result.textContent = `沒建成：${problem}`;
    }
  });

  const rename = document.getElementById("persona-rename");
  rename?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = rename.querySelector("[data-result]");
    const { body, error } = renameBody(Object.fromEntries(new FormData(rename)));
    if (error) {
      result.textContent = error;
      return;
    }
    result.textContent = "存檔中…";
    try {
      const { ok, reply, status } = await send(
        `/api/personas/${encodeURIComponent(rename.dataset.persona)}/rename`,
        body,
      );
      if (!ok) {
        result.textContent = reply.error ?? `沒存成（${status}）`;
        return;
      }
      document.querySelector("[data-display-name]").textContent = reply.display_name;
      result.textContent = "改好了";
    } catch (problem) {
      result.textContent = `沒存成：${problem}`;
    }
  });
}
