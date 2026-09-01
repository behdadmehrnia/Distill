/**
 * Distill Capture — background service worker.
 *
 * Creates multi_stream meetings via REST. Audio WebSocket lives in the Meet
 * content script so MV3 service-worker sleep cannot drop the connection.
 */

const API_BASE = "https://api.distill.app";
const SAMPLE_RATE = 16000;

/** @type {{
 *   token: string|null,
 *   user: object|null,
 *   capturing: boolean,
 *   meetingId: string|null,
 *   lastMeetingId: string|null,
 *   tabId: number|null,
 *   streams: Array<{id: string, name: string}>,
 *   lastError: string|null,
 *   progress: string|null,
 * }} */
let state = {
  token: null,
  user: null,
  capturing: false,
  meetingId: null,
  lastMeetingId: null,
  tabId: null,
  streams: [],
  lastError: null,
  progress: null,
};

async function loadPersisted() {
  const saved = await chrome.storage.local.get(["token", "user"]);
  state.token = saved.token || null;
  state.user = saved.user || null;
}

async function persistAuth() {
  await chrome.storage.local.set({
    token: state.token,
    user: state.user,
  });
}

function publicState() {
  return {
    apiBase: API_BASE,
    token: state.token,
    user: state.user,
    capturing: state.capturing,
    meetingId: state.meetingId,
    lastMeetingId: state.lastMeetingId,
    assistantUrl: state.lastMeetingId
      ? `${API_BASE}/assistant/${state.lastMeetingId}`
      : null,
    streams: state.streams,
    lastError: state.lastError,
    progress: state.progress,
  };
}

function broadcastState() {
  try {
    const p = chrome.runtime.sendMessage({
      type: "capture_state",
      state: publicState(),
    });
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch (_) {
    /* popup may be closed */
  }
}

function apiUrl(path) {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

async function readAccessTokenCookie() {
  try {
    const cookie = await chrome.cookies.get({
      url: API_BASE,
      name: "access_token",
    });
    return cookie?.value || null;
  } catch (_) {
    return null;
  }
}

async function clearAccessTokenCookie() {
  try {
    await chrome.cookies.remove({ url: API_BASE, name: "access_token" });
  } catch (_) {
    /* ignore */
  }
}

async function apiFetch(path, { method = "GET", body, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const auth = token || state.token;
  if (auth) headers.Authorization = `Bearer ${auth}`;
  const res = await fetch(apiUrl(path), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "include",
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.message || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function login(email, password) {
  if (!email || !password) {
    throw new Error("Email and password are required");
  }

  const res = await fetch(apiUrl("/auth/login"), {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.message || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  let token = data?.access_token || null;
  if (!token) {
    await new Promise((r) => setTimeout(r, 50));
    token = await readAccessTokenCookie();
  }
  if (!token) {
    throw new Error(
      "No sign-in token found. Allow cookies for api.distill.app."
    );
  }

  state.token = token;
  state.user = data?.user || null;
  if (!state.user) {
    const me = await apiFetch("/auth/me", { token });
    state.user = me.user;
  }
  state.lastError = null;
  await persistAuth();
  return publicState();
}

async function logout() {
  await stopCapture({ finalize: false });
  try {
    await fetch(apiUrl("/auth/logout"), {
      method: "POST",
      credentials: "include",
    });
  } catch (_) {
    /* ignore */
  }
  await clearAccessTokenCookie();
  state.token = null;
  state.user = null;
  await chrome.storage.local.remove(["token", "user"]);
  return publicState();
}

async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "distill_ping" });
    return;
  } catch (_) {
    /* inject */
  }
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content/meet-bridge.js"],
  });
}

async function startCapture(tabId, title) {
  if (!state.token) throw new Error("Sign in first");
  if (state.capturing) throw new Error("Recording is already active");

  await ensureContentScript(tabId);

  const meeting = await apiFetch("/meetings", {
    method: "POST",
    body: {
      title: title || "Google Meet meeting",
      capture_mode: "multi_stream",
      start: true,
      streams: [],
    },
  });

  state.meetingId = meeting.id;
  state.lastMeetingId = meeting.id;
  state.tabId = tabId;
  state.streams = [];
  state.lastError = null;
  state.progress = "Connected — recording";

  const res = await chrome.tabs.sendMessage(tabId, {
    type: "distill_start",
    meetingId: meeting.id,
    token: state.token,
    sampleRate: SAMPLE_RATE,
  });
  if (!res?.ok) {
    // Meeting was created; mark it stopped so it does not stay orphaned.
    try {
      await apiFetch(`/meetings/${meeting.id}/stop`, { method: "POST" });
    } catch (_) {
      /* ignore */
    }
    state.meetingId = null;
    throw new Error(res?.error || "Could not open a WebSocket in the Meet tab");
  }

  state.capturing = true;
  broadcastState();
  return publicState();
}

async function stopCapture({ finalize = true } = {}) {
  const tabId = state.tabId;
  const meetingId = state.meetingId;
  let framesSent = 0;
  let streamCount = state.streams.length;

  if (tabId != null) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, {
        type: "distill_stop",
        finalize,
      });
      if (res?.framesSent != null) framesSent = res.framesSent;
      if (res?.streams != null) streamCount = res.streams;
    } catch (_) {
      /* tab may be closed */
    }
  }

  if (finalize && meetingId && state.token) {
    try {
      await apiFetch(`/meetings/${meetingId}/stop`, { method: "POST" });
    } catch (_) {
      /* already stopped is fine */
    }
  }

  state.capturing = false;
  state.tabId = null;
  state.meetingId = null;
  state.progress = null;
  if (finalize && meetingId) {
    state.lastMeetingId = meetingId;
    if (framesSent === 0 && streamCount === 0) {
      state.lastError =
        "No audio was sent. Click the Meet tab, allow the microphone, or refresh the Meet page and start again. " +
        `Empty meeting: ${API_BASE}/assistant/${meetingId}`;
    } else {
      state.lastError = null;
      // framesSent is now batched packets (~1.5s), not ScriptProcessor ticks.
      state.progress =
        `Stopped — ${streamCount} speakers, ${framesSent} audio batches (~1.5s). ` +
        `Open the meeting in Distill.`;
    }
  } else {
    state.lastError = null;
  }
  broadcastState();
  return publicState();
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "distill-capture") return;
  // Port from Meet content script keeps this SW alive while capturing.
  port.onDisconnect.addListener(() => {});
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg?.type === "get_state") {
        sendResponse({ ok: true, ...publicState() });
        return;
      }
      if (msg?.type === "login") {
        sendResponse({ ok: true, state: await login(msg.email, msg.password) });
        return;
      }
      if (msg?.type === "logout") {
        sendResponse({ ok: true, state: await logout() });
        return;
      }
      if (msg?.type === "start_capture") {
        sendResponse({
          ok: true,
          state: await startCapture(msg.tabId, msg.title),
        });
        return;
      }
      if (msg?.type === "stop_capture") {
        sendResponse({ ok: true, state: await stopCapture({ finalize: true }) });
        return;
      }
      if (msg?.type === "stream_registered") {
        const id = String(msg.speakerId || "").trim();
        if (id) {
          const existing = state.streams.find((s) => s.id === id);
          const name = msg.name || id;
          if (existing) {
            if (existing.name !== name) {
              existing.name = name;
              state.progress = `Speaker name: ${name}`;
              broadcastState();
            }
          } else {
            state.streams.push({ id, name });
            state.progress = `New speaker: ${name}`;
            broadcastState();
          }
        }
        sendResponse({ ok: true });
        return;
      }
      if (msg?.type === "capture_progress") {
        if (state.capturing) {
          state.progress = msg.message || state.progress;
          broadcastState();
        }
        sendResponse({ ok: true });
        return;
      }
      if (msg?.type === "capture_transcript_hint") {
        if (state.capturing) {
          state.progress = `Live transcript: ${msg.segments || 0} segments`;
          broadcastState();
        }
        sendResponse({ ok: true });
        return;
      }
      if (msg?.type === "capture_ws_closed") {
        if (state.capturing) {
          state.capturing = false;
          state.meetingId = null;
          state.lastError = msg.error || `WebSocket closed (${msg.code})`;
          state.progress = null;
          broadcastState();
        }
        sendResponse({ ok: true });
        return;
      }
      sendResponse({ ok: false, error: "unknown message" });
    } catch (err) {
      state.lastError = err.message || String(err);
      state.capturing = false;
      broadcastState();
      sendResponse({ ok: false, error: state.lastError });
    }
  })();
  return true;
});

loadPersisted().catch((err) => {
  console.error("[Distill] failed to load stored auth", err);
});
