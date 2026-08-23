const $ = (selector) => document.querySelector(selector);
const state = { voices: [], selectedVoice: null, busy: false };

const toast = (message, error = false) => {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  if (typeof el.showPopover === "function") {
    if (!el.matches(":popover-open")) el.showPopover();
  } else {
    el.classList.add("show");
  }
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    if (typeof el.hidePopover === "function" && el.matches(":popover-open")) {
      el.hidePopover();
    } else {
      el.classList.remove("show");
    }
  }, 5200);
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function setBusy(button, busy, title, subtitle) {
  state.busy = busy;
  button.classList.toggle("busy", busy);
  button.disabled = busy;
  if (title) button.querySelector("b").textContent = title;
  if (subtitle) button.querySelector("small").textContent = subtitle;
  updateGenerateButton();
}

function updateGenerateButton() {
  const hasText = $("#scriptText").value.trim().length > 0;
  $("#generateBtn").disabled = state.busy || !state.selectedVoice || !hasText;
}

function renderVoices() {
  const list = $("#voiceList");
  list.innerHTML = "";
  $("#voiceEmpty").hidden = state.voices.length > 0;
  list.hidden = state.voices.length === 0;
  for (const voice of state.voices) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `voice-card${state.selectedVoice?.id === voice.id ? " selected" : ""}`;
    card.dataset.id = voice.id;
    card.innerHTML = `<span class="voice-avatar">${voice.name.slice(0, 2).toUpperCase()}</span><span><strong></strong><small></small></span><span class="delete-voice" title="Delete voice">×</span>`;
    card.querySelector("strong").textContent = voice.name;
    card.querySelector("small").textContent = voice.language_label;
    card.addEventListener("click", (event) => {
      if (event.target.closest(".delete-voice")) return deleteVoice(voice);
      state.selectedVoice = voice;
      $("#languageSelect").value = voice.language;
      setLanguageDefaults(voice.language);
      renderVoices();
      updateGenerateButton();
    });
    list.appendChild(card);
  }
}

async function loadVoices(selectNewest = false) {
  const data = await api("/api/voices");
  state.voices = data.voices;
  if (selectNewest || !state.voices.some(v => v.id === state.selectedVoice?.id)) {
    state.selectedVoice = state.voices[0] || null;
  }
  if (state.selectedVoice) {
    $("#languageSelect").value = state.selectedVoice.language;
    setLanguageDefaults(state.selectedVoice.language);
  }
  renderVoices();
  updateGenerateButton();
}

async function deleteVoice(voice) {
  if (!confirm(`Delete “${voice.name}”? This removes its saved voice state.`)) return;
  try {
    await api(`/api/voices/${encodeURIComponent(voice.id)}`, { method: "DELETE" });
    if (state.selectedVoice?.id === voice.id) state.selectedVoice = null;
    await loadVoices();
    toast("Voice deleted");
  } catch (error) { toast(error.message, true); }
}

function openDialog() {
  $("#voiceForm").reset();
  $("#fileLabel").textContent = "Choose or drop audio";
  $("#voiceDialog").showModal();
}

function setLanguageDefaults(language) {
  applyProfile($("#profileSelect").value, language);
}

function applyProfile(profile, language = $("#languageSelect").value) {
  const profiles = {
    natural: { temperature: language === "english" ? 0.5 : 0.7, steps: 3, speed: 1.0 },
    stable: { temperature: language === "english" ? 0.3 : 0.6, steps: 1, speed: 1.0 },
    expressive: { temperature: language === "english" ? 0.7 : 0.9, steps: 5, speed: 1.0 },
  };
  const values = profiles[profile] || profiles.natural;
  $("#temperature").value = values.temperature;
  $("#temperatureValue").textContent = values.temperature.toFixed(1);
  $("#decodeSteps").value = String(values.steps);
  $("#speed").value = values.speed;
  $("#speedValue").textContent = `${values.speed.toFixed(2)}×`;
}

async function extractVoice(event) {
  event.preventDefault();
  const button = $("#extractBtn");
  const form = new FormData($("#voiceForm"));
  form.set("consent", $("#consent").checked ? "true" : "false");
  setBusy(button, true, "Extracting voice…", "The first model download can take a few minutes");
  try {
    await api("/api/voices", { method: "POST", body: form });
    $("#voiceDialog").close();
    await loadVoices(true);
    toast("Voice saved and ready");
  } catch (error) { toast(error.message, true); }
  finally { setBusy(button, false, "Extract & save voice", "Done once, then reused instantly"); }
}

async function generate() {
  if (!state.selectedVoice) return;
  const button = $("#generateBtn");
  const form = new FormData();
  form.set("voice_id", state.selectedVoice.id);
  form.set("text", $("#scriptText").value.trim());
  form.set("language", $("#languageSelect").value);
  form.set("temperature", $("#temperature").value);
  form.set("decode_steps", $("#decodeSteps").value);
  form.set("speed", $("#speed").value);
  form.set("quantize", $("#quantize").checked ? "true" : "false");
  if ($("#tailFrames").value !== "") form.set("frames_after_eos", $("#tailFrames").value);
  setBusy(button, true, "Generating…", "Pocket TTS is speaking on your CPU");
  $("#result").hidden = true;
  try {
    const data = await api("/api/generate", { method: "POST", body: form });
    $("#audioPlayer").src = `${data.audio_url}?t=${Date.now()}`;
    $("#downloadLink").href = data.download_url;
    $("#duration").textContent = `${data.duration.toFixed(1)} sec · 24 kHz WAV`;
    $("#result").hidden = false;
    $("#audioPlayer").play().catch(() => {});
    toast("Speech generated");
  } catch (error) { toast(error.message, true); }
  finally { setBusy(button, false, "Generate speech", "Runs locally with Pocket TTS"); }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#newVoiceBtn").addEventListener("click", openDialog);
  $("#emptyUploadBtn").addEventListener("click", openDialog);
  $(".dialog-close").addEventListener("click", () => $("#voiceDialog").close());
  $("#voiceForm").addEventListener("submit", extractVoice);
  $("#generateBtn").addEventListener("click", generate);
  $("#scriptText").addEventListener("input", event => {
    $("#characterCount").textContent = `${event.target.value.length.toLocaleString()} / 12,000`;
    updateGenerateButton();
  });
  $("#settingsBtn").addEventListener("click", event => {
    const panel = $("#settingsPanel");
    panel.hidden = !panel.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("#temperature").addEventListener("input", event => $("#temperatureValue").textContent = event.target.value);
  $("#speed").addEventListener("input", event => $("#speedValue").textContent = `${Number(event.target.value).toFixed(2)}×`);
  $("#profileSelect").addEventListener("change", event => applyProfile(event.target.value));
  $("#copySampleBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#sampleScript").textContent.trim());
      toast("Recording script copied");
    } catch (_) {
      toast("Select and copy the recording script manually.", true);
    }
  });
  $("#languageSelect").addEventListener("change", event => {
    const voice = state.selectedVoice;
    if (voice && event.target.value !== voice.language) {
      event.target.value = voice.language;
      toast(`This saved voice uses the ${voice.language_label} model. Add a separate sample for another language.`, true);
    }
  });
  $("#audioFile").addEventListener("change", event => {
    $("#fileLabel").textContent = event.target.files[0]?.name || "Choose or drop audio";
  });
  for (const eventName of ["dragenter", "dragover"]) $("#dropZone").addEventListener(eventName, () => $("#dropZone").classList.add("drag"));
  for (const eventName of ["dragleave", "drop"]) $("#dropZone").addEventListener(eventName, () => $("#dropZone").classList.remove("drag"));
  try {
    const status = await api("/api/status");
    $(".status").classList.add("ready");
    $("#statusText").textContent = status.engine.loaded ? `${status.engine.device} · model ready` : "CPU · ready";
    await loadVoices();
  } catch (error) {
    $("#statusText").textContent = "Engine unavailable";
    toast(error.message, true);
  }
});
