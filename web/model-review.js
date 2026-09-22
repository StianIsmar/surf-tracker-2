const state = { clips: [], current: 0, filter: "all", saveTimer: null, seeking: false, youtube: null, export: null, source: "surf-session.mp4" };
const video = document.querySelector("#video");
const note = document.querySelector("#note");
const accept = document.querySelector("#accept");
const reject = document.querySelector("#reject");
const list = document.querySelector("#clipList");

function formatTime(seconds) {
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor(rounded % 3600 / 60).toString().padStart(2, "0");
  const remainder = (rounded % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${remainder}` : `${minutes}:${remainder}`;
}

function currentClip() { return state.clips[state.current]; }
function showStatus(message) {
  const status = document.querySelector("#status");
  status.textContent = message;
  status.classList.add("visible");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => status.classList.remove("visible"), 1200);
}

function render() {
  const clip = currentClip();
  if (!clip) return;
  note.value = clip.note || "";
  accept.classList.toggle("selected", clip.ride === true);
  reject.classList.toggle("selected", clip.ride === false);
  document.querySelector("#timeRange").textContent = `${formatTime(clip.start)}–${formatTime(clip.end)}`;
  document.querySelector("#currentNumber").textContent = clip.clip;
  document.querySelector("#clipCount").textContent = state.clips.length;
  const reviewed = state.clips.filter((item) => item.ride !== null).length;
  const kept = state.clips.filter((item) => item.ride === true).length;
  const discarded = state.clips.filter((item) => item.ride === false).length;
  document.querySelector("#reviewed").textContent = reviewed;
  document.querySelector("#total").textContent = ` / ${state.clips.length} reviewed`;
  document.querySelector("#progress").style.width = `${100 * reviewed / state.clips.length}%`;
  document.querySelector("#exportSummary").textContent = `${kept} included · ${discarded} excluded`;
  document.querySelector("#exportHeading").textContent = reviewed === state.clips.length ? "Ready to create final video" : `${state.clips.length - reviewed} decisions remaining`;
  const exportActive = ["preparing", "encoding", "joining", "stopping"].includes(state.export?.state);
  document.querySelector("#exportCompilation").disabled = exportActive || reviewed !== state.clips.length || kept === 0;
  document.querySelector("#publishYoutube").disabled = reviewed !== state.clips.length || kept === 0 || !state.youtube?.connected;
  const visible = state.clips.filter((item) => state.filter === "all" || state.filter === "unreviewed" && item.ride === null || state.filter === "rides" && item.ride === true);
  list.replaceChildren(...visible.map((item) => {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.classList.toggle("current", item.clip === clip.clip);
    const mark = item.ride === true ? "▲" : item.ride === false ? "▼" : "·";
    button.innerHTML = `<span class="clip-index">${String(item.clip).padStart(3, "0")}</span><span class="clip-time">${formatTime(item.start)}–${formatTime(item.end)}<small class="clip-duration">${item.duration.toFixed(1)} sec · ${item.detections} hits</small></span><span class="mark ${item.ride === true ? "yes" : item.ride === false ? "no" : ""}">${mark}</span>`;
    button.addEventListener("click", () => selectClip(state.clips.indexOf(item)));
    row.append(button);
    return row;
  }));
}

function selectClip(index) {
  state.current = Math.max(0, Math.min(state.clips.length - 1, index));
  render();
  state.seeking = true;
  video.currentTime = currentClip().start;
  video.play().catch(() => {});
}

async function saveDecision(ride) {
  const clip = currentClip();
  const clipIndex = state.current;
  const previousRide = clip.ride;
  const previousNote = clip.note;
  clip.ride = ride;
  clip.note = note.value.trim();
  render();
  const next = state.clips.findIndex((item, index) => index > clipIndex && item.ride === null);
  if (next !== -1) selectClip(next);
  const response = await fetch("/api/model-feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clip: clip.clip, ride, note: clip.note }) });
  if (!response.ok) {
    clip.ride = previousRide;
    clip.note = previousNote;
    render();
    throw new Error("Feedback could not be saved");
  }
  showStatus("Saved");
}

function typingInControl(target) {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(target.tagName));
}

document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey || typingInControl(event.target)) return;
  const key = event.key.toLowerCase();
  if ((key === "1" || key === "x") && !event.repeat) {
    event.preventDefault();
    saveDecision(false).catch((error) => showStatus(error.message));
  } else if ((key === "2" || key === "k") && !event.repeat) {
    event.preventDefault();
    saveDecision(true).catch((error) => showStatus(error.message));
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    selectClip(state.current - 1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    selectClip(state.current + 1);
  } else if (event.key === " ") {
    event.preventDefault();
    if (video.paused) video.play().catch(() => {}); else video.pause();
  } else if (key === "r" && !event.repeat) {
    event.preventDefault();
    selectClip(state.current);
  }
});

video.addEventListener("seeked", () => { state.seeking = false; });
video.addEventListener("timeupdate", () => {
  if (!state.seeking && currentClip() && video.currentTime >= currentClip().end) video.pause();
});
accept.addEventListener("click", () => saveDecision(true).catch((error) => showStatus(error.message)));
reject.addEventListener("click", () => saveDecision(false).catch((error) => showStatus(error.message)));
document.querySelector("#previous").addEventListener("click", () => selectClip(state.current - 1));
document.querySelector("#next").addEventListener("click", () => selectClip(state.current + 1));
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; document.querySelectorAll("[data-filter]").forEach((item) => item.classList.toggle("active", item === button)); render(); }));
function chooseExportDestination() {
  const handler = window.webkit?.messageHandlers?.saveVideo;
  if (!handler) return Promise.resolve(null);
  const sourceName = state.source.replace(/\.[^.]+$/, "");
  return new Promise((resolve) => {
    window.addEventListener("surf-native-export", (event) => resolve(event.detail), { once: true });
    handler.postMessage({ suggestedName: `${sourceName}-surf-rides.mp4` });
  });
}

document.querySelector("#exportCompilation").addEventListener("click", async () => {
  const download = document.querySelector("#downloadCompilation");
  download.hidden = true;
  try {
    const destination = await chooseExportDestination();
    if (destination?.cancelled) return;
    showStatus("Building compilation. Keep this page open.");
    const response = await fetch("/api/model-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(destination?.path ? { path: destination.path } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Export failed");
    await refreshExport();
  } catch (error) {
    showStatus(error.message);
  }
});

document.querySelector("#stopExport").addEventListener("click", async () => {
  const button = document.querySelector("#stopExport");
  button.disabled = true;
  try {
    const response = await fetch("/api/model-export/cancel", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Export could not be stopped");
    await refreshExport();
  } catch (error) {
    button.disabled = false;
    showStatus(error.message);
  }
});

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`;
}

function renderExport(status) {
  state.export = status;
  const active = ["preparing", "encoding", "joining", "stopping"].includes(status.state);
  const progress = document.querySelector("#exportProgress");
  progress.hidden = !active && !["failed", "cancelled"].includes(status.state);
  const labels = { preparing: "Preparing clips", encoding: "Encoding clips", joining: "Joining final video", stopping: "Stopping export", failed: "Export failed", cancelled: "Export stopped" };
  document.querySelector("#exportStage").textContent = labels[status.state] || "Preparing export";
  const percent = Math.round((status.progress || 0) * 100);
  document.querySelector("#exportPercent").textContent = `${percent}%`;
  document.querySelector("#exportBar").style.width = `${percent}%`;
  document.querySelector("#exportDetail").textContent = status.state === "failed" ? status.error || "Try exporting again." : status.state === "cancelled" ? "No new final video was saved." : status.state === "stopping" ? "Finishing the current operation and cleaning up." : "Keep Surf Tracker open while the video is created.";
  const button = document.querySelector("#exportCompilation");
  if (active) button.textContent = "Exporting…";
  else if (status.state === "complete") button.textContent = "Create final video again";
  else button.textContent = "Save final video to Mac";
  const stopButton = document.querySelector("#stopExport");
  stopButton.hidden = !active;
  stopButton.disabled = status.state === "stopping";
  const download = document.querySelector("#downloadCompilation");
  download.hidden = status.state !== "complete";
  if (status.state === "complete") {
    download.href = `${status.url}?v=${Date.now()}`;
    download.textContent = `Saved ${status.file} · ${formatTime(status.duration)} · ${formatBytes(status.size)}`;
    download.title = status.output_path || "";
  }
  render();
}

async function refreshExport() {
  const response = await fetch("/api/model-export/status");
  renderExport(await response.json());
}

function renderYoutube(status) {
  state.youtube = status;
  document.querySelector("#youtubeSetup").hidden = status.configured;
  document.querySelector("#connectYoutube").hidden = !status.configured || status.connected;
  document.querySelector("#youtubeForm").hidden = !status.connected;
  const active = status.state === "encoding" || status.state === "uploading";
  document.querySelector("#youtubeProgress").hidden = !active && status.state !== "failed";
  document.querySelector("#publishYoutube").disabled = active || !status.connected || state.clips.some((clip) => clip.ride === null) || !state.clips.some((clip) => clip.ride === true);
  if (status.state === "encoding") {
    document.querySelector("#youtubeStage").textContent = "Creating final video";
    document.querySelector("#youtubeDetail").textContent = "The temporary video will be removed after publishing.";
  } else if (status.state === "uploading") {
    document.querySelector("#youtubeStage").textContent = "Uploading to YouTube";
    document.querySelector("#youtubeDetail").textContent = "Publishing as unlisted. Keep Surf Tracker open.";
  } else if (status.state === "failed") {
    document.querySelector("#youtubeStage").textContent = "Publishing stopped";
    document.querySelector("#youtubeDetail").textContent = status.error || "Try publishing again.";
  }
  const percent = Math.round((status.progress || 0) * 100);
  document.querySelector("#youtubePercent").textContent = status.state === "encoding" ? "Encoding" : `${percent}%`;
  document.querySelector("#youtubeBar").style.width = `${percent}%`;
  const link = document.querySelector("#youtubeLink");
  link.hidden = status.state !== "published";
  if (status.video_url) link.href = status.video_url;
}

async function refreshYoutube() {
  const response = await fetch("/api/youtube/status");
  renderYoutube(await response.json());
}

document.querySelector("#youtubeCredentials").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const response = await fetch("/api/youtube/credentials", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Credentials could not be imported");
    await refreshYoutube();
    showStatus("YouTube setup imported securely");
  } catch (error) {
    showStatus(error.message);
  }
});

document.querySelector("#connectYoutube").addEventListener("click", async () => {
  const response = await fetch("/api/youtube/auth");
  const result = await response.json();
  if (!response.ok) return showStatus(result.error || "YouTube connection could not start");
  window.location.href = result.authorization_url;
});

document.querySelector("#publishYoutube").addEventListener("click", async () => {
  const title = document.querySelector("#youtubeTitle").value.trim();
  const description = document.querySelector("#youtubeDescription").value.trim();
  if (!title) return showStatus("Enter a YouTube title");
  const response = await fetch("/api/youtube/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, description }) });
  const result = await response.json();
  if (!response.ok) return showStatus(result.error || "Publishing could not start");
  await refreshYoutube();
});

fetch("/api/model-clips").then((response) => response.json()).then((data) => {
  state.clips = data.clips;
  state.source = data.source;
  const baseName = data.source.replace(/\.[^.]+$/, "");
  document.querySelector("#youtubeTitle").value = `${baseName} surf session`;
  render();
  selectClip(0);
  return Promise.all([refreshYoutube(), refreshExport()]);
}).catch((error) => showStatus(error.message));
setInterval(() => refreshYoutube().catch(() => {}), 1500);
setInterval(() => refreshExport().catch(() => {}), 1000);