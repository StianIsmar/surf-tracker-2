const state = { rides: [], start: null, end: null, saveTimer: null };
const video = document.querySelector("#sourceVideo");
const startText = document.querySelector("#selectionStart");
const endText = document.querySelector("#selectionEnd");
const saveButton = document.querySelector("#saveRide");
const note = document.querySelector("#rideNote");
const generateButton = document.querySelector("#generateFrames");

function formatTime(seconds, tenths = false) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const remainder = (seconds % 60).toFixed(tenths ? 1 : 0).padStart(tenths ? 4 : 2, "0");
  return `${minutes}:${remainder}`;
}

function showStatus(message) {
  const status = document.querySelector("#status");
  status.textContent = message;
  status.classList.add("visible");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => status.classList.remove("visible"), 1400);
}

function updateSelection() {
  startText.textContent = state.start === null ? "--:--.-" : formatTime(state.start, true);
  endText.textContent = state.end === null ? "--:--.-" : formatTime(state.end, true);
  saveButton.disabled = state.start === null || state.end === null || state.end <= state.start;
}

function renderRides() {
  document.querySelector("#rideCount").textContent = state.rides.length;
  const rows = state.rides.map((ride, index) => {
    const row = document.createElement("li");
    const seek = document.createElement("button");
    seek.type = "button";
    seek.className = "ride-row";
    seek.innerHTML = `<span class="clip-index">${String(index + 1).padStart(2, "0")}</span><span class="clip-time">${formatTime(ride.start, true)}–${formatTime(ride.end, true)}<small class="clip-duration">${(ride.end - ride.start).toFixed(1)} seconds${ride.note ? ` · ${ride.note}` : ""}</small></span>`;
    seek.addEventListener("click", () => { video.currentTime = ride.start; video.play(); });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete-ride";
    remove.textContent = "×";
    remove.title = "Delete ride interval";
    remove.setAttribute("aria-label", `Delete ride ${index + 1}`);
    remove.addEventListener("click", async () => {
      state.rides.splice(index, 1);
      await persist();
      renderRides();
    });
    row.className = "ride-list-item";
    row.append(seek, remove);
    return row;
  });
  document.querySelector("#rideList").replaceChildren(...rows);
}

async function persist() {
  const response = await fetch("/api/ground-truth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rides: state.rides }),
  });
  if (!response.ok) throw new Error("Ride intervals could not be saved");
  showStatus("Saved locally");
}

video.addEventListener("timeupdate", () => {
  document.querySelector("#clock").textContent = formatTime(video.currentTime, true);
});
document.querySelector("#markStart").addEventListener("click", () => {
  state.start = video.currentTime;
  if (state.end !== null && state.end <= state.start) state.end = null;
  updateSelection();
});
document.querySelector("#markEnd").addEventListener("click", () => {
  state.end = video.currentTime;
  updateSelection();
});
saveButton.addEventListener("click", async () => {
  state.rides.push({ start: state.start, end: state.end, note: note.value.trim() });
  state.rides.sort((a, b) => a.start - b.start);
  await persist();
  state.start = null;
  state.end = null;
  note.value = "";
  updateSelection();
  renderRides();
});

generateButton.addEventListener("click", async () => {
  generateButton.disabled = true;
  generateButton.textContent = "Generating…";
  try {
    const response = await fetch("/api/training-frames", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Training frames could not be generated");
    document.querySelector("#datasetCount").textContent =
      `${result.counts.ride} ride · ${result.counts.non_ride} non-ride`;
    showStatus("Training frames generated");
  } catch (error) {
    showStatus(error.message);
  } finally {
    generateButton.disabled = false;
    generateButton.textContent = "Generate training frames";
  }
});

fetch("/api/ground-truth")
  .then((response) => response.ok ? response.json() : Promise.reject(new Error("Could not load intervals")))
  .then((data) => { state.rides = data.rides; renderRides(); })
  .catch((error) => showStatus(error.message));