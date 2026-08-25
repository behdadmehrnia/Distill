/**
 * Content-script bridge on meet.google.com.
 *
 * Owns the audio WebSocket (MV3 service workers sleep and would drop it).
 * Injects the page-world WebRTC hook and streams tagged PCM to Distill.
 */

(function () {
  const SOURCE = "distill-extension";
  const API_BASE = "https://api.distill.app";

  /** @type {WebSocket|null} */
  let ws = null;
  /** @type {ReturnType<typeof setInterval>|null} */
  let pingTimer = null;
  /** @type {chrome.runtime.Port|null} */
  let keepAlivePort = null;
  const registered = new Set();
  let capturing = false;

  function injectHook() {
    if (document.documentElement.dataset.distillInjected === "1") return;
    document.documentElement.dataset.distillInjected = "1";
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("content/meet-injected.js");
    script.async = false;
    (document.head || document.documentElement).appendChild(script);
    script.remove();
  }

  injectHook();

  function notifyBackground(msg) {
    try {
      chrome.runtime.sendMessage(msg).catch(() => {});
    } catch (_) {
      /* ignore */
    }
  }

  function encodeFrame(speakerId, int16Array) {
    const nameBytes = new TextEncoder().encode(speakerId);
    if (nameBytes.length < 1 || nameBytes.length > 64) return null;
    const out = new Uint8Array(1 + nameBytes.length + int16Array.byteLength);
    out[0] = nameBytes.length;
    out.set(nameBytes, 1);
    out.set(
      new Uint8Array(
        int16Array.buffer,
        int16Array.byteOffset,
        int16Array.byteLength
      ),
      1 + nameBytes.length
    );
    return out.buffer;
  }

  function base64ToInt16(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const even = bytes.byteLength - (bytes.byteLength % 2);
    return new Int16Array(bytes.buffer, 0, even / 2);
  }

  function clearPing() {
    if (pingTimer != null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  function closeKeepAlive() {
    if (keepAlivePort) {
      try {
        keepAlivePort.disconnect();
      } catch (_) {
        /* ignore */
      }
      keepAlivePort = null;
    }
  }

  function openKeepAlive() {
    closeKeepAlive();
    try {
      keepAlivePort = chrome.runtime.connect({ name: "distill-capture" });
      keepAlivePort.onDisconnect.addListener(() => {
        keepAlivePort = null;
      });
    } catch (_) {
      /* ignore */
    }
  }

  function closeWs({ intentional = false } = {}) {
    clearPing();
    const socket = ws;
    ws = null;
    if (!socket) return;
    try {
      if (socket.readyState === WebSocket.OPEN) socket.close(1000, "stop");
      else socket.close();
    } catch (_) {
      /* ignore */
    }
    if (!intentional && capturing) {
      capturing = false;
      notifyBackground({
        type: "capture_ws_closed",
        code: 1006,
        error: "اتصال صوتی قطع شد",
      });
    }
  }

  function openAudioWs(meetingId, token) {
    return new Promise((resolve, reject) => {
      const url = `${API_BASE.replace(/^http/, "ws")}/meetings/${meetingId}/audio?token=${encodeURIComponent(token)}`;
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";
      const timer = setTimeout(() => {
        try {
          socket.close();
        } catch (_) {
          /* ignore */
        }
        reject(new Error("WebSocket connect timeout"));
      }, 15000);

      socket.onopen = () => {
        clearTimeout(timer);
        pingTimer = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            try {
              socket.send(JSON.stringify({ type: "ping" }));
            } catch (_) {
              /* ignore */
            }
          }
        }, 20000);
        resolve(socket);
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new Error("WebSocket error"));
      };
      socket.onclose = (ev) => {
        clearPing();
        if (ws === socket) ws = null;
        if (capturing && !ev.wasClean) {
          capturing = false;
          notifyBackground({
            type: "capture_ws_closed",
            code: ev.code,
            error: `WebSocket closed (${ev.code})`,
          });
        }
      };
      socket.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "stream_registered") {
            notifyBackground({
              type: "stream_registered",
              speakerId: data.speaker_id,
              name: data.name,
            });
          }
        } catch (_) {
          /* binary or non-json */
        }
      };
    });
  }

  function ensureRegistered(speakerId, name) {
    if (!ws || ws.readyState !== WebSocket.OPEN || registered.has(speakerId)) {
      return;
    }
    registered.add(speakerId);
    ws.send(
      JSON.stringify({
        type: "register_stream",
        speaker_id: speakerId,
        name: name || speakerId,
      })
    );
    notifyBackground({
      type: "stream_registered",
      speakerId,
      name: name || speakerId,
    });
  }

  async function startCapture(msg) {
    if (capturing) return { ok: true };
    injectHook();
    registered.clear();
    openKeepAlive();
    ws = await openAudioWs(msg.meetingId, msg.token);
    capturing = true;
    window.postMessage(
      {
        source: SOURCE,
        type: "DISTILL_START",
        sampleRate: msg.sampleRate || 16000,
      },
      "*"
    );
    return { ok: true };
  }

  async function stopCapture({ finalize = true } = {}) {
    window.postMessage({ source: SOURCE, type: "DISTILL_STOP" }, "*");
    if (finalize && ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch (_) {
        /* ignore */
      }
    }
    capturing = false;
    closeWs({ intentional: true });
    closeKeepAlive();
    registered.clear();
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      try {
        if (msg?.type === "distill_ping") {
          sendResponse({ ok: true });
          return;
        }
        if (msg?.type === "distill_start") {
          sendResponse(await startCapture(msg));
          return;
        }
        if (msg?.type === "distill_stop") {
          sendResponse(await stopCapture({ finalize: msg.finalize !== false }));
          return;
        }
        sendResponse({ ok: false, error: "unknown" });
      } catch (err) {
        capturing = false;
        closeWs({ intentional: true });
        closeKeepAlive();
        sendResponse({ ok: false, error: err.message || String(err) });
      }
    })();
    return true;
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "distill-page") return;
    if (data.type !== "DISTILL_PCM" || !capturing) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    const speakerId = String(data.speakerId || "").trim();
    if (!speakerId || !data.pcmBase64) return;
    ensureRegistered(speakerId, data.name);
    const samples = base64ToInt16(data.pcmBase64);
    const frame = encodeFrame(speakerId, samples);
    if (frame) {
      try {
        ws.send(frame);
      } catch (_) {
        /* ignore */
      }
    }
  });
})();
