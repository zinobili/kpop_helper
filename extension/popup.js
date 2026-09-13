const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

const input = document.getElementById("backendUrl");
const saveBtn = document.getElementById("saveBtn");
const savedEl = document.getElementById("saved");

chrome.storage.sync.get({ backendUrl: DEFAULT_BACKEND_URL }, ({ backendUrl }) => {
  input.value = backendUrl;
});

saveBtn.addEventListener("click", () => {
  const backendUrl = input.value.trim() || DEFAULT_BACKEND_URL;
  chrome.storage.sync.set({ backendUrl }, () => {
    savedEl.textContent = "Saved.";
    setTimeout(() => (savedEl.textContent = ""), 1500);
  });
});
