/**
 * Runs in the Meet page world (not the isolated content-script world).
 *
 * Strategy:
 *  - Scrape real display names from Meet tiles / people list
 *  - One local mic stream named after you (e.g. "Behdad Mehrnia")
 *  - At most one remote audio track per other person in the roster
 *  - Never invent speakers from leftover SFU tracks when you're alone
 */
(function () {
  if (window.__distillHookInstalled) return;
  window.__distillHookInstalled = true;

  const PAGE_SOURCE = "distill-page";
  const EXT_SOURCE = "distill-extension";
  const BATCH_MS = 1500;

  /** @type {Set<RTCPeerConnection>} */
  const peerConnections = new Set();
  /** @type {Map<string, any>} */
  const taps = new Map();
  /** @type {Set<string>} */
  const tappedTrackIds = new Set();
  /** @type {string[]} */
  let remoteNamePool = [];
  /** @type {ReturnType<typeof setInterval>|null} */
  let rescanTimer = null;
  /** @type {ReturnType<typeof setInterval>|null} */
  let flushTimer = null;

  let active = false;
  let sampleRate = 16000;
  let batchesSent = 0;
  let samplesSent = 0;
  /** @type {MediaStream|null} */
  let localMicStream = null;

  function post(payload) {
    window.postMessage({ source: PAGE_SOURCE, ...payload }, "*");
  }

  function floatToInt16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function downsample(float32, fromRate, toRate) {
    if (!fromRate || fromRate === toRate) return float32;
    const ratio = fromRate / toRate;
    const newLen = Math.max(1, Math.floor(float32.length / ratio));
    const out = new Float32Array(newLen);
    for (let i = 0; i < newLen; i++) {
      out[i] = float32[Math.floor(i * ratio)] || 0;
    }
    return out;
  }

  function isBadName(name) {
    const t = String(name || "").trim();
    if (!t || t.length < 2 || t.length > 64) return true;
    if (/[\/\\]/.test(t)) return true;
    if (/^(spaces|devices|meetings|users)\b/i.test(t)) return true;
    if (/^\d+$/.test(t)) return true;
    if (/^participant\s*\d*$/i.test(t)) return true;
    if (/^شرکت‌?کننده/.test(t)) return true;
    if (t.split(/\s+/).length > 6) return true;
    return /^(this call|meeting details|call feature|more activities|notifications|anyone|google meet|turn on|turn off|mute|unmute|camera|microphone|mic|chat|people|captions|present|share|leave|hand|host|options|settings|activities|open to|joining|invite|copy|link|info|details|action|button|menu|panel|tab|dialog|your presentation|presentation)/i.test(
      t
    );
  }

  function cleanName(raw) {
    let t = String(raw || "").trim();
    t = t.replace(/\s*\(you\)\s*/gi, " ");
    t = t.replace(/\s*\(شما\)\s*/gi, " ");
    t = t.replace(/'s presentation.*$/i, "");
    t = t.replace(/^Participant:\s*/i, "");
    t = t.split(",")[0].trim();
    t = t.replace(/\s+/g, " ").trim();
    return isBadName(t) ? null : t;
  }

  function addName(set, raw) {
    const n = cleanName(raw);
    if (n) set.add(n);
  }

  /**
   * Pull human-visible names from Meet's participant tiles / list.
   * data-participant-id itself is a device path — never use it as a name.
   */
  function scrapeRoster() {
    /** @type {Set<string>} */
    const names = new Set();
    let selfName = null;

    document.querySelectorAll("[data-self-name]").forEach((el) => {
      const n = cleanName(el.getAttribute("data-self-name"));
      if (n) {
        selfName = selfName || n;
        names.add(n);
      }
    });

    const nodeSelectors = [
      '[data-participant-id] [data-self-name]',
      '[data-participant-id] [role="heading"]',
      '[data-participant-id] span.notranslate',
      '[data-participant-id] span[class*="notranslate"]',
      'div[jsname="NfX98"]',
    ];
    nodeSelectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        addName(names, el.getAttribute("data-self-name"));
        addName(names, el.textContent);
      });
    });

    // Tile aria-labels like "Behdad Mehrnia" (not the long UI sentences).
    document.querySelectorAll("[data-participant-id]").forEach((el) => {
      const label = el.getAttribute("aria-label") || "";
      // Prefer "Name" when label is exactly a short name, or "Name (You)".
      const m = label.match(/^([^,(]{2,64})(?:\s*\(|$)/);
      if (m) addName(names, m[1]);
      // Visible first line of tile text is often the display name.
      const lines = (el.innerText || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (lines[0]) addName(names, lines[0]);
    });

    // Avatar alt text sometimes carries the person name.
    document.querySelectorAll('[data-participant-id] img[alt]').forEach((img) => {
      const alt = (img.getAttribute("alt") || "").trim();
      if (alt && !/^avatar$/i.test(alt) && !looksLikeIcon(alt)) {
        addName(names, alt);
      }
    });

    const all = [...names];
    const others = selfName ? all.filter((n) => n !== selfName) : all;
    return { selfName, others, all };
  }

  function looksLikeIcon(text) {
    return /^[a-z0-9_]+$/i.test(text) && !/\s/.test(text) && text.length < 24;
  }

  function refreshNames() {
    const roster = scrapeRoster();
    remoteNamePool = roster.others.slice();
    const local = taps.get("local");
    if (local) {
      const next = roster.selfName || local.name;
      if (next && next !== local.name && !isBadName(next)) {
        local.name = next;
        post({
          type: "DISTILL_TAP",
          speakerId: "local",
          name: next,
          taps: taps.size,
        });
      }
    }
    // Re-label existing remotes from the pool.
    let i = 0;
    for (const [sid, tap] of taps) {
      if (sid === "local") continue;
      const next = remoteNamePool[i] || tap.name;
      i += 1;
      if (next && next !== tap.name && !isBadName(next)) {
        tap.name = next;
        post({
          type: "DISTILL_TAP",
          speakerId: sid,
          name: next,
          taps: taps.size,
        });
      }
    }
    return roster;
  }

  function allocateRemoteName() {
    const roster = scrapeRoster();
    remoteNamePool = roster.others.slice();
    const used = new Set(
      [...taps.entries()]
        .filter(([id]) => id !== "local")
        .map(([, t]) => t.name)
    );
    for (const n of remoteNamePool) {
      if (!used.has(n)) return n;
    }
    return null;
  }

  function maxRemoteTracks() {
    const roster = scrapeRoster();
    // Only as many remotes as other people visible in Meet.
    return Math.max(0, roster.others.length);
  }

  function pcmToBase64(pcm) {
    const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function flushSpeaker(speakerId, { force = false } = {}) {
    const tap = taps.get(speakerId);
    if (!tap || tap.samples <= 0) return;
    const minSamples = Math.floor((sampleRate * BATCH_MS) / 1000);
    if (!force && tap.samples < minSamples) return;

    const merged = new Float32Array(tap.samples);
    let off = 0;
    for (const part of tap.buffers) {
      merged.set(part, off);
      off += part.length;
    }
    tap.buffers = [];
    tap.samples = 0;

    let energy = 0;
    for (let i = 0; i < merged.length; i += 8) {
      const v = merged[i];
      energy += v * v;
    }
    if (energy / Math.max(1, merged.length / 8) < 1e-7) return;

    const pcm = floatToInt16(merged);
    batchesSent += 1;
    samplesSent += pcm.length;
    post({
      type: "DISTILL_PCM",
      speakerId,
      name: tap.name,
      pcmBase64: pcmToBase64(pcm),
    });
    if (batchesSent === 1 || batchesSent % 5 === 0) {
      post({
        type: "DISTILL_STATS",
        taps: taps.size,
        batchesSent,
        audioSec: Math.round((samplesSent / sampleRate) * 10) / 10,
      });
    }
  }

  function flushAll({ force = false } = {}) {
    for (const speakerId of taps.keys()) flushSpeaker(speakerId, { force });
  }

  function remoteCount() {
    let n = 0;
    for (const id of taps.keys()) if (id !== "local") n += 1;
    return n;
  }

  function tapTrack(track, speakerId, displayName) {
    if (!active || !track || track.kind !== "audio") return false;
    if (track.readyState === "ended") return false;
    const trackKey = track.id || `${speakerId}:${tappedTrackIds.size}`;
    if (tappedTrackIds.has(trackKey)) return false;
    if (taps.has(speakerId)) return false;

    if (speakerId !== "local") {
      if (remoteCount() >= maxRemoteTracks()) return false;
    }

    try {
      const stream = new MediaStream([track.clone()]);
      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const gain = ctx.createGain();
      gain.gain.value = 0;
      const name =
        cleanName(displayName) ||
        (speakerId === "local"
          ? scrapeRoster().selfName || "شما"
          : allocateRemoteName() || `شرکت‌کننده ${remoteCount() + 1}`);

      const tap = {
        ctx,
        processor,
        gain,
        trackId: trackKey,
        buffers: [],
        samples: 0,
        name,
      };

      processor.onaudioprocess = (event) => {
        if (!active) return;
        const input = event.inputBuffer.getChannelData(0);
        const fromRate = event.inputBuffer.sampleRate || ctx.sampleRate;
        const resampled = downsample(input, fromRate, sampleRate);
        tap.buffers.push(resampled.slice());
        tap.samples += resampled.length;
        const minSamples = Math.floor((sampleRate * BATCH_MS) / 1000);
        if (tap.samples >= minSamples) flushSpeaker(speakerId);
      };

      source.connect(processor);
      processor.connect(gain);
      gain.connect(ctx.destination);
      const resume = () => {
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
      };
      resume();
      window.addEventListener("click", resume, { once: true, capture: true });

      taps.set(speakerId, tap);
      tappedTrackIds.add(trackKey);
      console.info("[Distill] tapping", speakerId, name);
      post({ type: "DISTILL_TAP", speakerId, name, taps: taps.size });
      return true;
    } catch (err) {
      console.warn("[Distill] failed to tap", speakerId, err);
      return false;
    }
  }

  function nextRemoteId() {
    return `remote_${remoteCount() + 1}`;
  }

  function scanPeerConnections() {
    if (!active) return;
    const limit = maxRemoteTracks();
    peerConnections.forEach((pc) => {
      try {
        if (limit > 0) {
          pc.getReceivers().forEach((receiver) => {
            if (receiver.track?.kind === "audio") {
              tapTrack(receiver.track, nextRemoteId());
            }
          });
        }
        if (!taps.has("local") && !localMicStream) {
          pc.getSenders().forEach((sender) => {
            if (sender.track?.kind === "audio") {
              const self = scrapeRoster().selfName;
              tapTrack(sender.track, "local", self);
            }
          });
        }
      } catch (_) {
        /* ignore */
      }
    });
  }

  async function captureLocalMic() {
    if (!active || taps.has("local") || localMicStream) return;
    try {
      localMicStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
        video: false,
      });
      const track = localMicStream.getAudioTracks()[0];
      const self = scrapeRoster().selfName;
      if (track) tapTrack(track, "local", self);
    } catch (err) {
      console.warn("[Distill] local mic unavailable", err);
      post({
        type: "DISTILL_WARN",
        message: "دسترسی میکروفون محلی گرفته نشد.",
      });
    }
  }

  function stopAllTaps() {
    flushAll({ force: true });
    active = false;
    if (rescanTimer != null) {
      clearInterval(rescanTimer);
      rescanTimer = null;
    }
    if (flushTimer != null) {
      clearInterval(flushTimer);
      flushTimer = null;
    }
    for (const [, tap] of taps) {
      try {
        tap.processor.disconnect();
        tap.gain.disconnect();
        tap.ctx.close();
      } catch (_) {
        /* ignore */
      }
    }
    taps.clear();
    tappedTrackIds.clear();
    remoteNamePool = [];
    if (localMicStream) {
      try {
        localMicStream.getTracks().forEach((t) => t.stop());
      } catch (_) {
        /* ignore */
      }
      localMicStream = null;
    }
  }

  function wrapPeerConnection() {
    const Original = window.RTCPeerConnection;
    if (!Original || Original.__distillWrapped) return;

    function WrappedPC(...args) {
      const pc = new Original(...args);
      peerConnections.add(pc);
      pc.addEventListener("connectionstatechange", () => {
        if (
          pc.connectionState === "closed" ||
          pc.connectionState === "failed"
        ) {
          peerConnections.delete(pc);
        }
      });
      pc.addEventListener("track", (event) => {
        if (event.track?.kind !== "audio") return;
        if (active) tapTrack(event.track, nextRemoteId());
      });
      return pc;
    }

    WrappedPC.prototype = Original.prototype;
    Object.keys(Original).forEach((key) => {
      try {
        WrappedPC[key] = Original[key];
      } catch (_) {
        /* ignore */
      }
    });
    WrappedPC.__distillWrapped = true;
    window.RTCPeerConnection = WrappedPC;
  }

  wrapPeerConnection();

  async function beginCapture(requestedRate) {
    sampleRate = requestedRate || 16000;
    active = true;
    batchesSent = 0;
    samplesSent = 0;
    const roster = refreshNames();
    console.info("[Distill] capture start", roster);
    await captureLocalMic();
    scanPeerConnections();
    rescanTimer = setInterval(() => {
      if (!active) return;
      refreshNames();
      scanPeerConnections();
      for (const [, tap] of taps) {
        if (tap.ctx.state === "suspended") tap.ctx.resume().catch(() => {});
      }
    }, 2500);
    flushTimer = setInterval(() => {
      if (active) flushAll({ force: false });
    }, BATCH_MS);
    post({
      type: "DISTILL_STARTED",
      taps: taps.size,
      selfName: roster.selfName,
      others: roster.others,
    });
    if (taps.size === 0) {
      post({
        type: "DISTILL_WARN",
        message:
          "هنوز ترک صوتی پیدا نشد. روی صفحه Meet کلیک کنید یا تب را رفرش کنید.",
      });
    }
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== EXT_SOURCE) return;

    if (data.type === "DISTILL_START") beginCapture(data.sampleRate || 16000);
    if (data.type === "DISTILL_STOP") {
      const stats = {
        taps: taps.size,
        batchesSent,
        audioSec: Math.round((samplesSent / sampleRate) * 10) / 10,
      };
      stopAllTaps();
      post({ type: "DISTILL_STOPPED", ...stats });
      console.info("[Distill] capture stopped", stats);
    }
  });
})();
