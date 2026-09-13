(function () {
  const STAGE_LABELS = {
    queued: "Queued...",
    looking_up_video: "Looking up video...",
    fetching_captions: "Fetching captions...",
    transcribing_audio: "Transcribing audio locally (can take a few minutes)...",
    translating: "Translating to Traditional Chinese...",
    done: "Done.",
  };

  const FONT_SIZE_KEY = "khFontSize";
  const MIN_FONT_SIZE = 14;
  const MAX_FONT_SIZE = 44;
  const DEFAULT_FONT_SIZE = 22;

  let currentVideoId = null;
  let cues = [];
  let visible = true;
  let syncTimer = null;
  let pollTimer = null;
  let controlEl = null;
  let statusEl = null;
  let loadBtn = null;
  let overlayEl = null;
  let fontSize = DEFAULT_FONT_SIZE;

  function applyFontSize() {
    if (overlayEl) overlayEl.style.setProperty("--kh-font-size", `${fontSize}px`);
  }

  function changeFontSize(delta) {
    fontSize = Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, fontSize + delta));
    applyFontSize();
    chrome.storage.sync.set({ [FONT_SIZE_KEY]: fontSize });
  }

  chrome.storage.sync.get({ [FONT_SIZE_KEY]: DEFAULT_FONT_SIZE }, (items) => {
    fontSize = items[FONT_SIZE_KEY];
    applyFontSize();
  });

  function apiRequest(method, path, body) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "apiRequest", method, path, body }, (response) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (response && response.error) return reject(new Error(response.error));
        resolve(response ? response.data : null);
      });
    });
  }

  function getVideoIdFromUrl() {
    const params = new URLSearchParams(location.search);
    return params.get("v");
  }

  function waitFor(selector, timeoutMs, intervalMs) {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const tick = () => {
        const el = document.querySelector(selector);
        if (el) return resolve(el);
        if (Date.now() - start > timeoutMs) return reject(new Error(`Timed out waiting for ${selector}`));
        setTimeout(tick, intervalMs);
      };
      tick();
    });
  }

  function ensureControlUI(player) {
    if (controlEl && document.body.contains(controlEl)) return;

    controlEl = document.createElement("div");
    controlEl.id = "kh-control";

    loadBtn = document.createElement("button");
    loadBtn.textContent = "翻譯";
    loadBtn.title = "Load Traditional Chinese subtitles";
    loadBtn.addEventListener("click", onLoadClick);

    const sizeDownBtn = document.createElement("button");
    sizeDownBtn.textContent = "A-";
    sizeDownBtn.title = "Smaller subtitles";
    sizeDownBtn.addEventListener("click", () => changeFontSize(-2));

    const sizeUpBtn = document.createElement("button");
    sizeUpBtn.textContent = "A+";
    sizeUpBtn.title = "Larger subtitles";
    sizeUpBtn.addEventListener("click", () => changeFontSize(2));

    statusEl = document.createElement("span");
    statusEl.id = "kh-status";

    controlEl.appendChild(loadBtn);
    controlEl.appendChild(sizeDownBtn);
    controlEl.appendChild(sizeUpBtn);
    controlEl.appendChild(statusEl);

    overlayEl = document.createElement("div");
    overlayEl.id = "kh-subtitle-overlay";
    applyFontSize();

    player.appendChild(controlEl);
    player.appendChild(overlayEl);
  }

  function resetControlUI() {
    if (loadBtn) {
      loadBtn.textContent = "翻譯";
      loadBtn.disabled = false;
      loadBtn.onclick = onLoadClick;
    }
    if (statusEl) statusEl.textContent = "";
    if (overlayEl) overlayEl.innerHTML = "";
  }

  function showError(message) {
    statusEl.textContent = `Error: ${message}`;
    statusEl.title = message; // full text on hover - the pill itself truncates with an ellipsis
    console.error("[kpop_helper]", message);
  }

  async function onLoadClick() {
    loadBtn.disabled = true;
    statusEl.textContent = "Submitting...";
    statusEl.title = "";
    try {
      const { job_id } = await apiRequest("POST", "/process", { url: location.href });
      pollJob(job_id);
    } catch (err) {
      showError(err.message);
      loadBtn.disabled = false;
    }
  }

  function pollJob(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const job = await apiRequest("GET", `/jobs/${jobId}`);
        if (job.status === "running") {
          const label = STAGE_LABELS[job.stage] || "Processing...";
          statusEl.textContent = job.detail ? `${label} (${job.detail})` : label;
        } else if (job.status === "done") {
          clearInterval(pollTimer);
          cues = job.result.cues.map((c) => ({ start: c.start, end: c.end, text: c.text_zh }));
          statusEl.textContent = job.result.cached ? "Loaded from cache." : "Done.";
          setTimeout(() => {
            if (statusEl) statusEl.textContent = "";
          }, 3000);
          visible = true;
          loadBtn.textContent = "隱藏字幕";
          loadBtn.disabled = false;
          loadBtn.onclick = onToggleClick;
          startSync();
        } else if (job.status === "error") {
          clearInterval(pollTimer);
          showError(job.error);
          loadBtn.disabled = false;
        }
      } catch (err) {
        clearInterval(pollTimer);
        showError(err.message);
        loadBtn.disabled = false;
      }
    }, 1500);
  }

  function onToggleClick() {
    visible = !visible;
    loadBtn.textContent = visible ? "隱藏字幕" : "顯示字幕";
    if (!visible && overlayEl) overlayEl.innerHTML = "";
  }

  function findCue(t) {
    for (let i = 0; i < cues.length; i++) {
      if (t >= cues[i].start && t < cues[i].end) return cues[i];
    }
    return null;
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function startSync() {
    if (syncTimer) clearInterval(syncTimer);
    syncTimer = setInterval(() => {
      if (!visible || !overlayEl) return;
      const video = document.querySelector("#movie_player video");
      const player = document.querySelector("#movie_player");
      if (!video || (player && player.classList.contains("ad-showing"))) {
        overlayEl.innerHTML = "";
        return;
      }
      const cue = findCue(video.currentTime);
      overlayEl.innerHTML = cue ? `<span>${escapeHtml(cue.text)}</span>` : "";
    }, 200);
  }

  function stopSync() {
    if (syncTimer) clearInterval(syncTimer);
    syncTimer = null;
    if (overlayEl) overlayEl.innerHTML = "";
  }

  async function onNavigate() {
    const videoId = getVideoIdFromUrl();
    if (videoId === currentVideoId) return;
    currentVideoId = videoId;

    if (pollTimer) clearInterval(pollTimer);
    stopSync();
    cues = [];

    if (!videoId) {
      if (controlEl) controlEl.remove();
      if (overlayEl) overlayEl.remove();
      controlEl = null;
      overlayEl = null;
      return;
    }

    try {
      const player = await waitFor("#movie_player", 10000, 200);
      ensureControlUI(player);
      resetControlUI();
    } catch (_err) {
      // player never showed up (e.g. non-video page matched unexpectedly) - nothing to do
    }
  }

  document.addEventListener("yt-navigate-finish", onNavigate);
  onNavigate();
})();
