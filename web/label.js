const state = { samples: [], current: 0, boxes: [], drawing: null, zoom: 1, saveTimer: null, saving: false };
const canvas = document.querySelector("#labelCanvas");
const context = canvas.getContext("2d");
const image = new Image();

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

function showStatus(message) {
  const status = document.querySelector("#status");
  status.textContent = message;
  status.classList.add("visible");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => status.classList.remove("visible"), 1500);
}

function currentSample() { return state.samples[state.current]; }

function draw() {
  if (!image.complete || !image.naturalWidth) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  context.lineWidth = 3;
  context.strokeStyle = "#ffdf52";
  context.fillStyle = "rgba(255, 223, 82, 0.14)";
  for (const box of state.boxes) {
    const x = box.x * canvas.width;
    const y = box.y * canvas.height;
    const width = box.width * canvas.width;
    const height = box.height * canvas.height;
    context.fillRect(x, y, width, height);
    context.strokeRect(x, y, width, height);
  }
  if (state.drawing) {
    const { startX, startY, endX, endY } = state.drawing;
    context.setLineDash([8, 5]);
    context.strokeRect(startX, startY, endX - startX, endY - startY);
    context.setLineDash([]);
  }
}

function updateBoxSummary() {
  const count = state.boxes.length;
  document.querySelector("#boxCount").textContent = `${count} ${count === 1 ? "box" : "boxes"}`;
  document.querySelector("#undoBox").disabled = count === 0;
}

function updateProgress() {
  const reviewed = state.samples.filter((sample) => sample.reviewed).length;
  const remaining = state.samples.length - reviewed;
  document.querySelector("#reviewedCount").textContent = reviewed;
  document.querySelector("#totalCount").textContent = state.samples.length;
  document.querySelector("#remainingCount").textContent = remaining;
  document.querySelector("#labelProgress").style.width = `${state.samples.length ? reviewed / state.samples.length * 100 : 0}%`;
}

function renderFrameList() {
  const rows = state.samples.map((sample, index) => {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = index === state.current ? "current" : "";
    button.innerHTML = `<span class="clip-index">${String(index + 1).padStart(2, "0")}</span><span class="clip-time">${formatTime(sample.timestamp)}<small class="clip-duration">${sample.boxes.length} ${sample.boxes.length === 1 ? "box" : "boxes"}</small></span><span class="mark ${sample.reviewed ? "yes" : ""}">${sample.reviewed ? "✓" : "○"}</span>`;
    button.addEventListener("click", () => loadFrame(index));
    row.append(button);
    return row;
  });
  document.querySelector("#frameList").replaceChildren(...rows);
  document.querySelector("#frameList .current")?.scrollIntoView({ block: "nearest" });
}

function loadFrame(index) {
  if (!state.samples.length) return;
  state.current = Math.max(0, Math.min(index, state.samples.length - 1));
  const sample = currentSample();
  state.boxes = sample.boxes.map((box) => ({ ...box }));
  state.drawing = null;
  image.onload = () => {
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.style.width = `${image.naturalWidth * state.zoom}px`;
    canvas.style.height = `${image.naturalHeight * state.zoom}px`;
    draw();
  };
  image.src = sample.image_url;
  document.querySelector("#framePosition").textContent = `Frame ${state.current + 1} of ${state.samples.length}`;
  document.querySelector("#frameTimestamp").textContent = formatTime(sample.timestamp);
  document.querySelector("#previousFrame").disabled = state.current === 0;
  document.querySelector("#nextFrame").disabled = state.current === state.samples.length - 1;
  updateBoxSummary();
  renderFrameList();
}

function canvasPoint(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(canvas.width, (event.clientX - bounds.left) * canvas.width / bounds.width)),
    y: Math.max(0, Math.min(canvas.height, (event.clientY - bounds.top) * canvas.height / bounds.height)),
  };
}

canvas.addEventListener("pointerdown", (event) => {
  const point = canvasPoint(event);
  state.drawing = { startX: point.x, startY: point.y, endX: point.x, endY: point.y };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!state.drawing) return;
  const point = canvasPoint(event);
  state.drawing.endX = point.x;
  state.drawing.endY = point.y;
  draw();
});
canvas.addEventListener("pointerup", (event) => {
  if (!state.drawing) return;
  const point = canvasPoint(event);
  const x = Math.min(state.drawing.startX, point.x);
  const y = Math.min(state.drawing.startY, point.y);
  const width = Math.abs(point.x - state.drawing.startX);
  const height = Math.abs(point.y - state.drawing.startY);
  state.drawing = null;
  if (width >= 4 && height >= 4) {
    state.boxes.push({ x: x / canvas.width, y: y / canvas.height, width: width / canvas.width, height: height / canvas.height });
  }
  updateBoxSummary();
  draw();
});

async function saveAndAdvance(boxes) {
  if (state.saving || !state.samples.length) return;
  state.saving = true;
  const sample = currentSample();
  const savedBoxes = boxes.map((box) => ({ ...box }));
  try {
    const response = await fetch("/api/training-annotations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: sample.filename, boxes: savedBoxes }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Annotation could not be saved");
    sample.reviewed = true;
    sample.boxes = savedBoxes;
    updateProgress();
    const nextUnreviewed = state.samples.findIndex((item, index) => index > state.current && !item.reviewed);
    loadFrame(nextUnreviewed === -1 ? Math.min(state.current + 1, state.samples.length - 1) : nextUnreviewed);
    showStatus("Annotation saved");
  } finally {
    state.saving = false;
  }
}

document.querySelector("#saveAnnotation").addEventListener("click", () => {
  saveAndAdvance(state.boxes).catch((error) => showStatus(error.message));
});
document.querySelector("#emptyFrame").addEventListener("click", () => {
  saveAndAdvance([]).catch((error) => showStatus(error.message));
});
document.querySelector("#undoBox").addEventListener("click", () => {
  state.boxes.pop();
  updateBoxSummary();
  draw();
});
document.querySelector("#previousFrame").addEventListener("click", () => loadFrame(state.current - 1));
document.querySelector("#nextFrame").addEventListener("click", () => {
  saveAndAdvance(state.boxes).catch((error) => showStatus(error.message));
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowRight" || event.repeat) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  event.preventDefault();
  saveAndAdvance(state.boxes).catch((error) => showStatus(error.message));
});
document.querySelectorAll("[data-zoom]").forEach((button) => button.addEventListener("click", () => {
  state.zoom = Number(button.dataset.zoom);
  document.querySelectorAll("[data-zoom]").forEach((item) => item.classList.toggle("active", item === button));
  canvas.style.width = `${canvas.width * state.zoom}px`;
  canvas.style.height = `${canvas.height * state.zoom}px`;
}));

fetch("/api/training-annotations")
  .then((response) => response.ok ? response.json() : Promise.reject(new Error("Generate training frames first")))
  .then((data) => {
    state.samples = data.samples.filter((sample) => sample.label === "ride");
    if (!state.samples.length) throw new Error("Generate training frames first");
    updateProgress();
    const firstUnreviewed = state.samples.findIndex((sample) => !sample.reviewed);
    loadFrame(firstUnreviewed === -1 ? 0 : firstUnreviewed);
  })
  .catch((error) => showStatus(error.message));