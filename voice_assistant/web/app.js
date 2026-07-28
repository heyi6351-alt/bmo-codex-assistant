const LABELS = {
  idle: "READY",
  wake: "HI!",
  listening: "LISTENING",
  interrupted: "INTERRUPTED",
  thinking: "THINKING",
  tool_call: "ACTION",
  coding: "CODING",
  reminder: "REMINDER",
  planning: "PLANNING",
  confirmation: "CONFIRM?",
  speaking: "SPEAKING",
  done: "DONE",
  cancelled: "CANCELLED",
  failed: "FAILED",
  error: "CHECK ME",
  offline: "OFFLINE",
};
const ACTIVE_JOB_STATES = new Set([
  "queued",
  "inspecting",
  "coding",
  "testing",
  "cancelling",
]);

const body = document.body;
const face = document.querySelector(".face");
const status = document.querySelector("#status");
const clock = document.querySelector("#clock");
const subtitle = document.querySelector("#subtitle");
const intentPill = document.querySelector("#intent");
const connectionDot = document.querySelector("#connection");
const jobCard = document.querySelector("#job-card");
const jobId = document.querySelector("#job-id");
const jobProject = document.querySelector("#job-project");
const jobState = document.querySelector("#job-state");

let lastState = "idle";
let lastSubtitle = "";

function clampState(candidate) {
  const known = Object.hasOwn(LABELS, candidate) ? candidate : "idle";
  return known;
}

function setConnection(online) {
  connectionDot.classList.toggle("offline", !online);
  connectionDot.title = online ? "online" : "offline";
}

function formatJob(job) {
  if (!job || typeof job !== "object") return null;
  return {
    id: String(job.id || job.job_id || "--"),
    project: String(job.project || "--"),
    state: String(job.state || "--"),
  };
}

function applyState(payload) {
  const candidate = String(payload?.state || "idle").toLowerCase();
  const state = clampState(candidate);

  if (state !== lastState) {
    lastState = state;
    body.dataset.state = state;
    status.textContent = LABELS[state];
    face.setAttribute("aria-label", `BMO 当前状态：${LABELS[state]}`);
  }

  const intent = payload?.intent ? String(payload.intent).toUpperCase() : "";
  if (intent && state !== "idle") {
    intentPill.textContent = intent;
    intentPill.classList.remove("hidden");
  } else {
    intentPill.classList.add("hidden");
  }

  const text =
    payload?.subtitle ||
    payload?.reply ||
    payload?.transcript ||
    "";
  if (text !== lastSubtitle) {
    lastSubtitle = text;
    subtitle.textContent = text;
  }

  const job = formatJob(payload?.job);
  if (job && ACTIVE_JOB_STATES.has(job.state.toLowerCase())) {
    jobId.textContent = job.id;
    jobProject.textContent = job.project;
    jobState.textContent = job.state;
    jobCard.classList.remove("hidden");
  } else {
    jobCard.classList.add("hidden");
  }
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`state ${response.status}`);
    applyState(await response.json());
    setConnection(true);
  } catch {
    applyState({ state: "offline" });
    setConnection(false);
  }
}

function updateClock() {
  clock.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

pollState();
updateClock();
setInterval(pollState, 300);
setInterval(updateClock, 1000);
