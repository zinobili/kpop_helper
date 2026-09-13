const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.sync.get({ backendUrl: DEFAULT_BACKEND_URL });
  return backendUrl.replace(/\/+$/, "");
}

async function handleApiRequest({ method, path, body }) {
  const base = await getBackendUrl();
  const resp = await fetch(base + path, {
    method: method || "GET",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await resp.json();
  } catch (_e) {
    // no JSON body (e.g. plain-text .srt) - fine, callers that need it use path accordingly
  }
  if (!resp.ok) {
    throw new Error((data && data.detail) || `${resp.status} ${resp.statusText}`);
  }
  return data;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "apiRequest") {
    handleApiRequest(msg)
      .then((data) => sendResponse({ data }))
      .catch((err) => sendResponse({ error: err.message }));
    return true; // keep the message channel open for the async response
  }
  return false;
});
