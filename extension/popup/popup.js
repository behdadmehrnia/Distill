const els = {
  loginPanel: document.getElementById("login-panel"),
  sessionPanel: document.getElementById("session-panel"),
  email: document.getElementById("email"),
  password: document.getElementById("password"),
  loginBtn: document.getElementById("login-btn"),
  logoutBtn: document.getElementById("logout-btn"),
  userLabel: document.getElementById("user-label"),
  meetingTitle: document.getElementById("meeting-title"),
  statusText: document.getElementById("status-text"),
  meetingId: document.getElementById("meeting-id"),
  streamsText: document.getElementById("streams-text"),
  startBtn: document.getElementById("start-btn"),
  stopBtn: document.getElementById("stop-btn"),
  error: document.getElementById("error"),
};

function showError(msg) {
  els.error.textContent = msg || "";
  els.error.classList.toggle("hidden", !msg);
}

function setBusy(busy) {
  els.loginBtn.disabled = busy;
  els.startBtn.disabled = busy;
  els.stopBtn.disabled = busy;
}

async function getState() {
  return chrome.runtime.sendMessage({ type: "get_state" });
}

function render(state) {
  const loggedIn = Boolean(state?.token && state?.user);
  els.loginPanel.classList.toggle("hidden", loggedIn);
  els.sessionPanel.classList.toggle("hidden", !loggedIn);

  if (!loggedIn) return;

  els.userLabel.textContent =
    state.user.email || state.user.display_name || "وارد شده";
  els.statusText.textContent = state.capturing ? "در حال ضبط" : "آماده";
  els.meetingId.textContent = state.meetingId
    ? `Meeting: ${state.meetingId}`
    : "";
  const streams = state.streams || [];
  els.streamsText.textContent = streams.length
    ? `گویندگان: ${streams.map((s) => s.name || s.id).join("، ")}`
    : "";
  els.startBtn.disabled = Boolean(state.capturing);
  els.stopBtn.disabled = !state.capturing;
  if (state.lastError) showError(state.lastError);
}

els.loginBtn.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    const res = await chrome.runtime.sendMessage({
      type: "login",
      email: els.email.value.trim(),
      password: els.password.value,
    });
    if (!res?.ok) throw new Error(res?.error || "ورود ناموفق بود");
    render(await getState());
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(false);
  }
});

els.logoutBtn.addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "logout" });
  showError("");
  render(await getState());
});

els.startBtn.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.includes("meet.google.com")) {
      throw new Error("ابتدا تب Google Meet را باز کنید، سپس ضبط را شروع کنید.");
    }
    const res = await chrome.runtime.sendMessage({
      type: "start_capture",
      tabId: tab.id,
      title: els.meetingTitle.value.trim() || "جلسه گوگل میت",
    });
    if (!res?.ok) throw new Error(res?.error || "شروع ضبط ممکن نشد");
    render(await getState());
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(false);
    render(await getState());
  }
});

els.stopBtn.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    const res = await chrome.runtime.sendMessage({ type: "stop_capture" });
    if (!res?.ok) throw new Error(res?.error || "توقف ضبط ممکن نشد");
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(false);
    render(await getState());
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "capture_state") {
    render(msg.state);
  }
});

getState().then(render).catch((err) => showError(err.message || String(err)));
