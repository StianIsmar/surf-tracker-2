const state = { session: null, saveTimer: null, polling: false };
const dropZone = document.querySelector("#dropZone");
const browserNotice = document.querySelector("#browserNotice");

function formatDuration(seconds) {
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor(rounded % 3600 / 60);
  return hours ? `${hours} hr ${minutes} min` : `${minutes} min`;
}

function render() {
  const hasSession = Boolean(state.session);
  dropZone.hidden = hasSession;
  document.querySelector("#selectedVideo").hidden = !hasSession;
  document.querySelector("#nextStep").hidden = !hasSession;
  if (!hasSession) return;
  document.querySelector("#videoName").textContent = state.session.name;
  document.querySelector("#videoDetails").textContent = `${formatDuration(state.session.duration)} · ${state.session.width}×${state.session.height} · ${(state.session.size / 1024 ** 3).toFixed(1)} GB`;
}

function showStatus(message) {
  const status = document.querySelector("#status");
  status.textContent = message;
  status.classList.add("visible");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => status.classList.remove("visible"), 1200);
}

function updateScanProgress(status) {
  const progress = Math.round((status.progress || 0) * 100);
  document.querySelector("#scanProgress").hidden = false;
  document.querySelector("#scanPercent").textContent = `${progress}%`;
  document.querySelector("#scanBar").style.width = `${progress}%`;
  document.querySelector("#scanStatus").textContent = status.state === "failed" ? "Detection stopped" : "Finding potential rides";
  document.querySelector("#scanDetail").textContent = status.state === "failed" ? status.error : "The original file is being analyzed locally. You can leave this window open.";
}

async function pollScan() {
  if (state.polling) return;
  state.polling = true;
  try {
    while (true) {
      const response = await fetch("/api/session-scan");
      const status = await response.json();
      updateScanProgress(status);
      if (status.state === "complete") {
        document.querySelector("#scanStatus").textContent = `${status.candidates} potential rides found`;
        document.querySelector("#scanDetail").textContent = "Opening Final Edit…";
        window.location.href = "/model-review.html";
        return;
      }
      if (status.state === "failed") {
        document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = false; });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } finally {
    state.polling = false;
  }
}

async function startCurrentModelScan() {
  document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = true; });
  updateScanProgress({ state: "running", progress: 0 });
  const response = await fetch("/api/session-scan", { method: "POST" });
  const result = await response.json();
  if (!response.ok) {
    document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = false; });
    throw new Error(result.error || "Detection could not be started");
  }
  pollScan().catch((error) => showStatus(error.message));
}

async function registerVideo(path) {
  showStatus("Reading video details…");
  const response = await fetch("/api/session-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Video could not be selected");
  state.session = result.session;
  render();
  showStatus("Original video selected. No copy was made.");
}

function openNativePicker() {
  const handler = window.webkit?.messageHandlers?.selectVideo;
  if (handler) {
    handler.postMessage(null);
    return;
  }
  browserNotice.hidden = false;
}

window.addEventListener("surf-native-file", (event) => {
  registerVideo(event.detail.path).catch((error) => showStatus(error.message));
});
document.querySelector("#chooseVideo").addEventListener("click", openNativePicker);
document.querySelector("#changeVideo").addEventListener("click", openNativePicker);
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("selected", item === button));
  if (button.dataset.mode === "scan") {
    startCurrentModelScan().catch((error) => showStatus(error.message));
  } else {
    showStatus("10-minute calibration is the next workflow to add");
  }
}));
document.addEventListener("dragover", (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
document.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
document.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  if (!window.webkit?.messageHandlers?.selectVideo) browserNotice.hidden = false;
});

fetch("/api/session")
  .then((response) => response.json())
  .then(async (result) => {
    state.session = result.session;
    render();
    const response = await fetch("/api/session-scan");
    const status = await response.json();
    if (status.state === "running") {
      document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = true; });
      pollScan().catch((error) => showStatus(error.message));
    }
  })
  .catch((error) => showStatus(error.message));