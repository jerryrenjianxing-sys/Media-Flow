// Only local interface state. Messages and execution receipts remain server-owned.
const recentKey = "mediaflow-agent-recent-session";
const valid = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
export function resizeComposer(input) {
  if (!input) return;
  input.style.height = "36px";
  input.style.height = Math.min(200, Math.max(36, input.scrollHeight)) + "px";
}
export function rememberSession(storage, id) { try { if (valid(id)) storage.setItem(recentKey,id); } catch { /* optional preference */ } }
export function chooseSession(search, storage, ids) {
  const requested = new URLSearchParams(search).get("session");
  if (valid(requested)) return requested;
  try { const recent=storage.getItem(recentKey); if (ids.includes(recent)) return recent; } catch { /* unavailable storage */ }
  return ids[0] || "";
}
export function loadSessionUi(storage,id) {
  const blank={text:"",requestId:null,notice:"",answers:{}};
  try { const saved=JSON.parse(storage.getItem("mediaflow-agent-ui:"+id) || "null");
    if (!saved || typeof saved!=="object") return blank;
    return {text:typeof saved.text==="string"?saved.text.slice(0,12000):"",requestId:valid(saved.requestId)?saved.requestId:null,
      notice:typeof saved.notice==="string"?saved.notice.slice(0,2000):"",answers:saved.answers && typeof saved.answers==="object"?saved.answers:{}};
  } catch { return blank; }
}
export function saveSessionUi(storage,id,value) {
  if (!valid(id)) return false;
  try { storage.setItem("mediaflow-agent-ui:"+id,JSON.stringify(value)); return true; } catch { return false; }
}
