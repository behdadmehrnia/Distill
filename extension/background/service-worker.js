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
 *   tabId: number|null,
 *   streams: Array<{id: string, name: string}>,
 *   lastError: string|null,
 * }} */
let state = {
  token: null,
  user: null,
  capturing: false,
  meetingId: null,
  tabId: null,
  streams: [],
  lastError: null,
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
    streams: state.streams,
    lastError: state.lastError,
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
    throw new Error("ایمیل و رمز عبور لازم است");
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
      "توکن ورود پیدا نشد. اجازه cookies برای api.distill.app را فعال کنید."
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
  if (!state.token) throw new Error("ابتدا وارد شوید");
  if (state.capturing) throw new Error("ضبط از قبل فعال است");

  await ensureContentScript(tabId);

  const meeting = await apiFetch("/meetings", {
    method: "POST",
    body: {
      title: title || "جلسه گوگل میت",
      capture_mode: "multi_stream",
      start: true,
      streams: [],
    },
  });

  state.meetingId = meeting.id;
  state.tabId = tabId;
  state.streams = [];
  state.lastError = null;

  const res = await chrome.tabs.sendMessage(tabId, {
    type: "distill_start",
    meetingId: meeting.id,
    token: state.token,
    sampleRate: SAMPLE_RATE,
  });
  if (!res?.ok) {
    throw new Error(res?.error || "اتصال WebSocket در تب Meet برقرار نشد");
  }

  state.capturing = true;
  broadcastState();
  return publicState();
}

async function stopCapture({ finalize = true } = {}) {
  const tabId = state.tabId;
  const meetingId = state.meetingId;

  if (tabId != null) {
    try {
      await chrome.tabs.sendMessage(tabId, {
        type: "distill_stop",
        finalize,
      });
    } catch (_) {
      /* tab may be closed */
    }
  }

  // REST stop as safety net (WS stop may already have finalized).
  if (finalize && meetingId && state.token) {
    try {
      await apiFetch(`/meetings/${meetingId}/stop`, { method: "POST" });
    } catch (_) {
      /* already stopped is fine */
    }
  }

  state.capturing = false;
  state.tabId = null;
  state.lastError = null;
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
        if (id && !state.streams.some((s) => s.id === id)) {
          state.streams.push({ id, name: msg.name || id });
          broadcastState();
        }
        sendResponse({ ok: true });
        return;
      }
      if (msg?.type === "capture_ws_closed") {
        if (state.capturing) {
          state.capturing = false;
          state.lastError = msg.error || `WebSocket closed (${msg.code})`;
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
