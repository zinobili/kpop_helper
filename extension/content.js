(function () {
  const STAGE_LABELS = {
    queued: "Queued...",
    looking_up_video: "Looking up video...",
    fetching_captions: "Fetching captions...",
    transcribing_audio: "Transcribing audio locally (can take a few minutes)...",
    translating: "Translating to Traditional Chinese...",
    skipping_translation: "Skipping translation (original-language subtitles)...",
    done: "Done.",
  };

  const FONT_SIZE_KEY = "khFontSize";
  const MIN_FONT_SIZE = 14;
  const MAX_FONT_SIZE = 44;
  const DEFAULT_FONT_SIZE = 22;

  const BG_OPACITY_KEY = "khBgOpacity";
  const MIN_BG_OPACITY = 0.1;
  const MAX_BG_OPACITY = 1;
  const BG_OPACITY_STEP = 0.1;
  const DEFAULT_BG_OPACITY = 0.72;

  let currentVideoId = null;
  let cues = [];
  let visible = true;
  let syncTimer = null;
  let pollTimer = null;
  let controlEl = null;
  let statusEl = null;
  let loadBtn = null;
  let captionLangSelect = null;
  let overlayEl = null;
  let settingsPanel = null;
  let fontSize = DEFAULT_FONT_SIZE;
  let bgOpacity = DEFAULT_BG_OPACITY;

  function applyFontSize() {
    if (overlayEl) overlayEl.style.setProperty("--kh-font-size", `${fontSize}px`);
  }

  function applyBgOpacity() {
    if (overlayEl) overlayEl.style.setProperty("--kh-bg-opacity", bgOpacity);
  }

  chrome.storage.sync.get(
    { [FONT_SIZE_KEY]: DEFAULT_FONT_SIZE, [BG_OPACITY_KEY]: DEFAULT_BG_OPACITY },
    (items) => {
      fontSize = items[FONT_SIZE_KEY];
      bgOpacity = items[BG_OPACITY_KEY];
      applyFontSize();
      applyBgOpacity();
    }
  );

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
    loadBtn.onclick = onLoadClick;

    captionLangSelect = document.createElement("select");
    captionLangSelect.id = "kh-caption-lang";
    captionLangSelect.title = "Caption source";
    captionLangSelect.innerHTML = `
      <option value="">Auto</option>
      <option value="ko">KO</option>
      <option value="en">EN</option>
    `;

    const settingsBtn = document.createElement("button");
    settingsBtn.id = "kh-settings-btn";
    // Static markup (no user data), so innerHTML is safe here.
    settingsBtn.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>';
    settingsBtn.title = "Subtitle appearance";
    settingsBtn.setAttribute("aria-label", "Subtitle appearance");
    settingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      settingsPanel.classList.toggle("kh-open");
    });

    statusEl = document.createElement("span");
    statusEl.id = "kh-status";

    controlEl.appendChild(loadBtn);
    controlEl.appendChild(captionLangSelect);
    controlEl.appendChild(settingsBtn);
    controlEl.appendChild(statusEl);

    settingsPanel = document.createElement("div");
    settingsPanel.id = "kh-settings-panel";

    const sizeRow = document.createElement("label");
    sizeRow.textContent = "Size";
    const sizeRange = document.createElement("input");
    sizeRange.type = "range";
    sizeRange.min = MIN_FONT_SIZE;
    sizeRange.max = MAX_FONT_SIZE;
    sizeRange.step = 2;
    sizeRange.value = fontSize;
    sizeRange.addEventListener("input", () => {
      fontSize = Number(sizeRange.value);
      applyFontSize();
      chrome.storage.sync.set({ [FONT_SIZE_KEY]: fontSize });
    });
    sizeRow.appendChild(sizeRange);

    const opacityRow = document.createElement("label");
    opacityRow.textContent = "Bg";
    const opacityRange = document.createElement("input");
    opacityRange.type = "range";
    opacityRange.min = MIN_BG_OPACITY;
    opacityRange.max = MAX_BG_OPACITY;
    opacityRange.step = BG_OPACITY_STEP;
    opacityRange.value = bgOpacity;
    opacityRange.addEventListener("input", () => {
      bgOpacity = Number(opacityRange.value);
      applyBgOpacity();
      chrome.storage.sync.set({ [BG_OPACITY_KEY]: bgOpacity });
    });
    opacityRow.appendChild(opacityRange);

    settingsPanel.appendChild(sizeRow);
    settingsPanel.appendChild(opacityRow);
    settingsPanel.addEventListener("click", (e) => e.stopPropagation());
    controlEl.appendChild(settingsPanel);

    overlayEl = document.createElement("div");
    overlayEl.id = "kh-subtitle-overlay";
    applyFontSize();
    applyBgOpacity();

    player.appendChild(controlEl);
    player.appendChild(overlayEl);
  }

  function resetControlUI() {
    if (loadBtn) {
      loadBtn.textContent = "翻譯";
      loadBtn.disabled = false;
      loadBtn.onclick = onLoadClick;
    }
    if (captionLangSelect) captionLangSelect.value = "";
    if (statusEl) setStatus("");
    if (overlayEl) overlayEl.innerHTML = "";
  }

  // The status pill truncates long text with an ellipsis, so every status update also
  // sets data-tooltip; overlay.css shows it as a full-text tooltip on hover.
  function setStatus(text) {
    statusEl.textContent = text;
    if (text) statusEl.setAttribute("data-tooltip", text);
    else statusEl.removeAttribute("data-tooltip");
  }

  function showError(message) {
    setStatus(`Error: ${message}`);
    console.error("[kpop_helper]", message);
  }

  async function onLoadClick() {
    loadBtn.disabled = true;
    setStatus("Checking for existing translations...");
    try {
      const { forceStt, skipTranslation } = await new Promise((resolve) =>
        chrome.storage.sync.get({ forceStt: false, skipTranslation: false }, resolve)
      );
      const captionLang = captionLangSelect ? captionLangSelect.value : "";

      if (!skipTranslation) {
        const usedExisting = await offerExistingVariant(captionLang, forceStt);
        if (usedExisting) {
          loadBtn.disabled = false;
          return;
        }
      }

      setStatus("Submitting...");
      const { job_id } = await apiRequest("POST", "/process", {
        url: location.href,
        force_stt: forceStt,
        skip_translation: skipTranslation,
        caption_lang: captionLang || null,
      });
      pollJob(job_id);
    } catch (err) {
      showError(err.message);
      loadBtn.disabled = false;
    }
  }

  // Checks whether this video already has a cached translation for a *different* combination
  // of settings than the one about to be requested (e.g. a different LLM/model, since the
  // extension never offers that choice itself), and if so, asks before reusing it. Returns
  // true if an existing variant was loaded (caller should stop, not submit a new job).
  async function offerExistingVariant(captionLang, forceStt) {
    const params = new URLSearchParams({ url: location.href });
    if (captionLang) params.set("caption_lang", captionLang);
    if (forceStt) params.set("force_stt", "true");

    let preview;
    try {
      preview = await apiRequest("GET", `/translate-preview?${params.toString()}`);
    } catch (_err) {
      return false; // preview failed - just fall through to a normal /process request
    }

    if (preview.exact_match || !preview.alternates || preview.alternates.length === 0) {
      return false;
    }

    const alt = preview.alternates[0];
    const langLabel = preview.source_lang === "ko" ? "Korean" : "English";
    const when = alt.created_at ? new Date(alt.created_at).toLocaleString() : "earlier";
    const modelLabel = alt.translation_model ? `${alt.translation_provider}/${alt.translation_model}` : alt.translation_provider;
    const useExisting = confirm(
      `This video already has a ${langLabel} translation from ${modelLabel}, made ${when}.\n\n` +
      `Use it? (Cancel to translate a new version instead)`
    );
    if (!useExisting) return false;

    setStatus("Loading cached translation...");
    const result = await apiRequest("GET", `/variant/${alt.variant_id}`);
    applyResult(result);
    return true;
  }

  function applyResult(result) {
    cues = result.cues.map((c) => ({ start: c.start, end: c.end, text: c.text_zh }));
    setStatus(result.cached ? "Loaded from cache." : "Done.");
    setTimeout(() => {
      if (statusEl) setStatus("");
    }, 3000);
    visible = true;
    loadBtn.textContent = "隱藏字幕";
    loadBtn.disabled = false;
    loadBtn.onclick = onToggleClick;
    startSync();
  }

  function pollJob(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const job = await apiRequest("GET", `/jobs/${jobId}`);
        if (job.status === "running") {
          const label = STAGE_LABELS[job.stage] || "Processing...";
          setStatus(job.detail ? `${label} (${job.detail})` : label);
        } else if (job.status === "done") {
          clearInterval(pollTimer);
          applyResult(job.result);
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
      settingsPanel = null;
      captionLangSelect = null;
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

  document.addEventListener("click", () => {
    if (settingsPanel) settingsPanel.classList.remove("kh-open");
  });

  document.addEventListener("yt-navigate-finish", onNavigate);
  onNavigate();
})();
