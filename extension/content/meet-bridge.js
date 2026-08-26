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
  let capturing = false;
  let framesSent = 0;
  let meetingId = null;

  function injectHook() {
    // Always (re)inject if the page hook vanished after SPA navigation.
    if (
      document.documentElement.dataset.distillInjected === "1" &&
      document.querySelector("script[data-distill-hook]")
    ) {
      return;
    }
    document.documentElement.dataset.distillInjected = "1";
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("content/meet-injected.js");
    script.dataset.distillHook = "1";
    script.async = false;
    (document.head || document.documentElement).appendChild(script);
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

  function openAudioWs(id, token) {
    return new Promise((resolve, reject) => {
      const url = `${API_BASE.replace(/^http/, "ws")}/meetings/${id}/audio?token=${encodeURIComponent(token)}`;
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
        }, 15000);
        resolve(socket);
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new Error("WebSocket error"));
      };
      socket.onclose = (ev) => {
        clearPing();
        if (ws === socket) ws = null;
        if (capturing && !ev.wasClean && ev.code !== 1000) {
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
          if (data.type === "transcript") {
            notifyBackground({
              type: "capture_transcript_hint",
              segments: (data.segments || []).length,
            });
          }
        } catch (_) {
          /* ignore */
        }
      };
    });
  }

  const registeredNames = new Map();

  function ensureRegistered(speakerId, name) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    let clean = String(name || "").trim();
    const isLocal =
      speakerId === "local" || String(speakerId) === "SPEAKER_local";
    if (
      !clean ||
      /[\/\\]/.test(clean) ||
      /^(spaces|devices)\b/i.test(clean) ||
      /^شرکت‌?کننده/.test(clean) ||
      /^participant\s*\d*$/i.test(clean) ||
      /^(you|yourself|me|شما)$/i.test(clean) ||
      clean.length > 64
    ) {
      // For local: do not register any placeholder name.
      // Wait until we have the real scraped Meet display name.
      if (isLocal) return;

      // Keep a previous good name if we already have one.
      if (registeredNames.has(speakerId)) return;
      clean = `شرکت‌کننده ${registeredNames.size || 1}`;
    }
    if (registeredNames.get(speakerId) === clean) return;
    registeredNames.set(speakerId, clean);
    ws.send(
      JSON.stringify({
        type: "register_stream",
        speaker_id: speakerId,
        name: clean,
      })
    );
    notifyBackground({
      type: "stream_registered",
      speakerId,
      name: clean,
    });
  }

  async function startCapture(msg) {
    if (capturing) return { ok: true };
    injectHook();
    // Give the injected script a tick to install listeners.
    await new Promise((r) => setTimeout(r, 50));
    registeredNames.clear();
    framesSent = 0;
    meetingId = msg.meetingId;
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
    notifyBackground({
      type: "capture_progress",
      message: "در حال شنود ترک‌های صوتی Meet…",
    });
    return { ok: true };
  }

  async function stopCapture({ finalize = true } = {}) {
    const sent = framesSent;
    const streamCount = registeredNames.size;
    window.postMessage({ source: SOURCE, type: "DISTILL_STOP" }, "*");
    // Allow final batched flush from the page hook.
    await new Promise((r) => setTimeout(r, 200));

    if (finalize && ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch (_) {
        /* ignore */
      }
      await new Promise((r) => setTimeout(r, 400));
    }

    capturing = false;
    closeWs({ intentional: true });
    closeKeepAlive();
    registeredNames.clear();
    meetingId = null;
    framesSent = 0;
    return { ok: true, framesSent: sent, streams: streamCount };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      try {
        if (msg?.type === "distill_ping") {
          sendResponse({ ok: true, capturing, framesSent, streams: registeredNames.size });
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

    if (data.type === "DISTILL_WARN" || data.type === "DISTILL_STARTED") {
      notifyBackground({
        type: "capture_progress",
        message: data.message || `ترک‌های فعال: ${data.taps || 0}`,
        taps: data.taps,
      });
      return;
    }

    if (data.type === "DISTILL_TAP") {
      ensureRegistered(data.speakerId, data.name);
      notifyBackground({
        type: "stream_registered",
        speakerId: data.speakerId,
        name: data.name,
      });
      return;
    }

    if (data.type === "DISTILL_STATS") {
      const sec = data.audioSec != null ? data.audioSec : "?";
      notifyBackground({
        type: "capture_progress",
        message: `ارسال صوت… ${sec}ث · ${data.batchesSent || 0} بسته · ${data.taps || 0} ترک`,
        taps: data.taps,
        batchesSent: data.batchesSent,
        audioSec: data.audioSec,
      });
      return;
    }

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
        framesSent += 1;
      } catch (_) {
        /* ignore */
      }
    }
  });
})();
