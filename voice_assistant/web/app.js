const LABELS = {
  idle: "READY",
  wake: "HI!",
  listening: "LISTENING",
  interrupted: "LISTENING",
  thinking: "THINKING",
  speaking: "SPEAKING",
  error: "CHECK ME",
};

const body = document.body;
const face = document.querySelector(".face");
const status = document.querySelector("#status");
const clock = document.querySelector("#clock");
let lastState = "idle";

function applyState(payload) {
  const candidate = String(payload?.state || "idle").toLowerCase();
  const state = Object.hasOwn(LABELS, candidate) ? candidate : "idle";
  if (state === lastState) return;
  lastState = state;
  body.dataset.state = state;
  status.textContent = LABELS[state];
  face.setAttribute("aria-label", `BMO 当前状态：${LABELS[state]}`);
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`state ${response.status}`);
    applyState(await response.json());
  } catch {
    applyState({ state: "error" });
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
