const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

const input = document.getElementById("backendUrl");
const forceSttInput = document.getElementById("forceStt");
const skipTranslationInput = document.getElementById("skipTranslation");
const saveBtn = document.getElementById("saveBtn");
const savedEl = document.getElementById("saved");
const dashboardLink = document.getElementById("dashboardLink");

chrome.storage.sync.get(
  { backendUrl: DEFAULT_BACKEND_URL, forceStt: false, skipTranslation: false },
  ({ backendUrl, forceStt, skipTranslation }) => {
    input.value = backendUrl;
    dashboardLink.href = backendUrl;
    forceSttInput.checked = forceStt;
    skipTranslationInput.checked = skipTranslation;
  }
);

saveBtn.addEventListener("click", () => {
  const backendUrl = input.value.trim() || DEFAULT_BACKEND_URL;
  chrome.storage.sync.set(
    {
      backendUrl,
      forceStt: forceSttInput.checked,
      skipTranslation: skipTranslationInput.checked,
    },
    () => {
      dashboardLink.href = backendUrl;
      savedEl.textContent = "Saved.";
      setTimeout(() => (savedEl.textContent = ""), 1500);
    }
  );
});
