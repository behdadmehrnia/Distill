const els = {
  loginPanel: document.getElementById("login-panel"),
  sessionPanel: document.getElementById("session-panel"),
  email: document.getElementById("email"),
  password: document.getElementById("password"),
  loginBtn: document.getElementById("login-btn"),
  logoutBtn: document.getElementById("logout-btn"),
  userLabel: document.getElementById("user-label"),
  meetingTitle: document.getElementById("meeting-title"),
  setupBlock: document.getElementById("setup-block"),
  recordingBlock: document.getElementById("recording-block"),
  doneBlock: document.getElementById("done-block"),
  progressText: document.getElementById("progress-text"),
  meetingId: document.getElementById("meeting-id"),
  streamsText: document.getElementById("streams-text"),
  doneProgress: document.getElementById("done-progress"),
  doneMeetingId: document.getElementById("done-meeting-id"),
  openMeeting: document.getElementById("open-meeting"),
  startBtn: document.getElementById("start-btn"),
  stopBtn: document.getElementById("stop-btn"),
  newCaptureBtn: document.getElementById("new-capture-btn"),
  hint: document.getElementById("hint"),
  error: document.getElementById("error"),
};

/** @type {'setup'|'recording'|'done'} */
let phase = "setup";

function showError(msg) {
  els.error.textContent = msg || "";
  els.error.classList.toggle("hidden", !msg);
}

function setBusy(busy) {
  els.loginBtn.disabled = busy;
  els.startBtn.disabled = busy;
  els.stopBtn.disabled = busy;
  els.newCaptureBtn.disabled = busy;
}

async function getState() {
  return chrome.runtime.sendMessage({ type: "get_state" });
}

function setPhase(next) {
  phase = next;
  els.setupBlock.classList.toggle("hidden", next !== "setup");
  els.recordingBlock.classList.toggle("hidden", next !== "recording");
  els.doneBlock.classList.toggle("hidden", next !== "done");
  els.hint.classList.toggle("hidden", next !== "setup");
}

function render(state) {
  const loggedIn = Boolean(state?.token && state?.user);
  els.loginPanel.classList.toggle("hidden", loggedIn);
  els.sessionPanel.classList.toggle("hidden", !loggedIn);
  if (!loggedIn) return;

  els.userLabel.textContent =
    state.user.email || state.user.display_name || "Signed in";

  if (state.capturing) {
    setPhase("recording");
    els.progressText.textContent = state.progress || "Sending audio…";
    els.meetingId.textContent = state.meetingId
      ? `Meeting: ${state.meetingId}`
      : "";
    const streams = state.streams || [];
    els.streamsText.textContent = streams.length
      ? `Speakers: ${streams.map((s) => s.name || s.id).join(", ")}`
      : "Detecting tracks…";
  } else if (state.lastMeetingId && phase !== "setup") {
    setPhase("done");
    els.doneProgress.textContent = state.progress || "Recording stopped.";
    els.doneMeetingId.textContent = `Last meeting: ${state.lastMeetingId}`;
    const url =
      state.assistantUrl ||
      `https://api.distill.app/assistant/${state.lastMeetingId}`;
    els.openMeeting.href = url;
  } else {
    setPhase("setup");
  }

  if (state.lastError) showError(state.lastError);
  else showError("");
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
    if (!res?.ok) throw new Error(res?.error || "Sign-in failed");
    phase = "setup";
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
  phase = "setup";
  render(await getState());
});

els.startBtn.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.includes("meet.google.com")) {
      throw new Error("Open a Google Meet tab first, then start recording.");
    }
    // Switch UI immediately so title/start disappear while connecting.
    setPhase("recording");
    els.progressText.textContent = "Connecting…";
    els.meetingId.textContent = "";
    els.streamsText.textContent = "";

    const res = await chrome.runtime.sendMessage({
      type: "start_capture",
      tabId: tab.id,
      title: els.meetingTitle.value.trim() || "Google Meet meeting",
    });
    if (!res?.ok) {
      phase = "setup";
      throw new Error(res?.error || "Could not start recording");
    }
    render(await getState());
  } catch (err) {
    phase = "setup";
    setPhase("setup");
    showError(err.message || String(err));
  } finally {
    setBusy(false);
  }
});

els.stopBtn.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    const res = await chrome.runtime.sendMessage({ type: "stop_capture" });
    if (!res?.ok) throw new Error(res?.error || "Could not stop recording");
    phase = "done";
    render(await getState());
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(false);
  }
});

els.newCaptureBtn.addEventListener("click", async () => {
  showError("");
  phase = "setup";
  setPhase("setup");
  render(await getState());
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "capture_state") {
    render(msg.state);
  }
});

getState()
  .then((state) => {
    if (state?.capturing) phase = "recording";
    else if (state?.lastMeetingId) phase = "done";
    else phase = "setup";
    render(state);
  })
  .catch((err) => showError(err.message || String(err)));
