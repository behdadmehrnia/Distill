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
  /** @type {AudioContext|null} */
  let sharedCtx = null;
  /** @type {Promise<AudioContext>|null} */
  let workletReady = null;

  const WORKLET_NAME = "distill-capture";
  const WORKLET_SOURCE = `
class DistillCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("distill-capture", DistillCaptureProcessor);
`;

  async function ensureAudioContext() {
    if (sharedCtx && sharedCtx.state !== "closed") {
      if (workletReady) await workletReady;
      return sharedCtx;
    }
    sharedCtx = new AudioContext();
    const url = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: "application/javascript" })
    );
    workletReady = sharedCtx.audioWorklet
      .addModule(url)
      .then(() => sharedCtx)
      .finally(() => {
        try {
          URL.revokeObjectURL(url);
        } catch (_) {
          /* ignore */
        }
      });
    await workletReady;
    return sharedCtx;
  }

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
    // Never treat Meet's "You" label as a person name.
    if (/^(you|yourself|me)$/i.test(t)) return true;
    if (t.split(/\s+/).length > 6) return true;
    return /^(this call|meeting details|call feature|more activities|notifications|anyone|google meet|turn on|turn off|mute|unmute|camera|microphone|mic|chat|people|captions|present|share|leave|hand|host|options|settings|activities|open to|joining|invite|copy|link|info|details|action|button|menu|panel|tab|dialog|your presentation|presentation)/i.test(
      t
    );
  }

  function cleanName(raw) {
    let t = String(raw || "").trim();
    t = t.replace(/\s*\(you\)\s*/gi, " ");
    t = t.replace(/\s*\(you\)\s*/gi, " ");
    t = t.replace(/'s presentation.*$/i, "");
    t = t.replace(/^Participant:\s*/i, "");
    t = t.split(",")[0].trim();
    t = t.replace(/\s+/g, " ").trim();
    return isBadName(t) ? null : t;
  }

  function looksLikeIcon(text) {
    return /^[a-z0-9_]+$/i.test(text) && !/\s/.test(text) && text.length < 24;
  }

  function isSelfMarker(text) {
    return /\(\s*you\s*\)/i.test(String(text || ""));
  }

  /** Prefer the longer unique form ("Behdad Mehrnia" over "Behdad Meh"). */
  function preferLongerNames(names) {
    const list = [...names].filter(Boolean);
    list.sort((a, b) => b.length - a.length);
    /** @type {string[]} */
    const out = [];
    for (const n of list) {
      const lower = n.toLowerCase();
      if (out.some((kept) => kept.toLowerCase().startsWith(lower))) continue;
      const shorterIdx = out.findIndex((kept) =>
        lower.startsWith(kept.toLowerCase())
      );
      if (shorterIdx >= 0) out[shorterIdx] = n;
      else out.push(n);
    }
    return out;
  }

  function samePerson(a, b) {
    if (!a || !b) return false;
    const x = a.toLowerCase();
    const y = b.toLowerCase();
    return x === y || x.startsWith(y) || y.startsWith(x);
  }

  function uniqueParticipantTiles() {
    const ids = new Set();
    document.querySelectorAll("[data-participant-id]").forEach((el) => {
      const id = el.getAttribute("data-participant-id");
      if (id) ids.add(id);
    });
    return ids.size;
  }

  /**
   * Pull real Meet display names from tiles / people list.
   * "(You)" / data-self-name only decide which name is local — never shown as labels.
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
        const rawSelf = el.getAttribute("data-self-name");
        if (rawSelf) {
          const n = cleanName(rawSelf);
          if (n) {
            selfName = selfName || n;
            names.add(n);
          }
        }
        addName(names, el.textContent);
      });
    });

    document.querySelectorAll("[data-participant-id]").forEach((el) => {
      const label = el.getAttribute("aria-label") || "";
      const m = label.match(/^([^,(]{2,64})(?:\s*\(|$)/);
      if (m) {
        const n = cleanName(m[1]);
        if (n) {
          names.add(n);
          if (isSelfMarker(label)) selfName = selfName || n;
        }
      }
      const lines = (el.innerText || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (lines[0]) {
        const n = cleanName(lines[0]);
        if (n) {
          names.add(n);
          if (lines.some(isSelfMarker) || isSelfMarker(label)) {
            selfName = selfName || n;
          }
        }
      }
    });

    document.querySelectorAll('[data-participant-id] img[alt]').forEach((img) => {
      const alt = (img.getAttribute("alt") || "").trim();
      if (alt && !/^avatar$/i.test(alt) && !looksLikeIcon(alt)) {
        addName(names, alt);
      }
    });

    const all = preferLongerNames(names);
    if (selfName) {
      const match = all.find((n) => samePerson(n, selfName));
      if (match) selfName = match;
    }
    // Until we know the local display name, do not assign any scraped name to remotes
    // (that was putting "Behdad Mehrnia" on the other person's stream).
    const others = selfName
      ? all.filter((n) => !samePerson(n, selfName))
      : [];
    const tileCount = uniqueParticipantTiles();
    const otherCount = Math.max(
      others.length,
      tileCount > 0 ? Math.max(0, tileCount - 1) : 0
    );
    return { selfName, others, all, tileCount, otherCount };
  }

  function addName(set, raw) {
    const n = cleanName(raw);
    if (n) set.add(n);
  }

  function setTapName(speakerId, next) {
    const tap = taps.get(speakerId);
    const name = cleanName(next);
    if (!tap || !name || tap.name === name) return false;
    // Never put the local person's name on a remote stream.
    if (speakerId !== "local") {
      const local = taps.get("local");
      if (local && samePerson(local.name, name)) return false;
      const self = scrapeRoster().selfName;
      if (self && samePerson(self, name)) return false;
    }
    tap.name = name;
    post({
      type: "DISTILL_TAP",
      speakerId,
      name,
      taps: taps.size,
    });
    return true;
  }

  function refreshNames() {
    const roster = scrapeRoster();
    remoteNamePool = roster.others.slice();

    const local = taps.get("local");
    if (local && roster.selfName) {
      setTapName("local", roster.selfName);
    }

    // If a remote accidentally holds the self name, clear it first.
    for (const [sid, tap] of taps) {
      if (sid === "local") continue;
      if (roster.selfName && samePerson(tap.name, roster.selfName)) {
        tap.name = `Participant ${sid.replace(/\D/g, "") || "1"}`;
        post({
          type: "DISTILL_TAP",
          speakerId: sid,
          name: tap.name,
          taps: taps.size,
        });
      }
    }

    const used = new Set();
    if (local?.name) used.add(local.name.toLowerCase());
    let i = 0;
    for (const [sid] of taps) {
      if (sid === "local") continue;
      while (i < remoteNamePool.length && used.has(remoteNamePool[i].toLowerCase())) {
        i += 1;
      }
      const next = remoteNamePool[i];
      if (next) {
        used.add(next.toLowerCase());
        setTapName(sid, next);
        i += 1;
      }
    }
    return roster;
  }

  function allocateRemoteName() {
    const roster = scrapeRoster();
    remoteNamePool = roster.others.slice();
    const used = new Set();
    const local = taps.get("local");
    if (local?.name) used.add(local.name.toLowerCase());
    if (roster.selfName) used.add(roster.selfName.toLowerCase());
    for (const [id, t] of taps) {
      if (id === "local") continue;
      if (t.name) used.add(t.name.toLowerCase());
    }
    for (const n of remoteNamePool) {
      if (!used.has(n.toLowerCase()) && !isBadName(n)) return n;
    }
    return null;
  }

  function maxRemoteTracks() {
    const roster = scrapeRoster();
    // Cap by visible other participants, not by how many names we scraped yet.
    return Math.max(0, roster.otherCount || roster.others.length);
  }

  function fallbackSpeakerLabel(speakerId) {
    if (speakerId === "local") {
      const self = scrapeRoster().selfName;
      // Until we know who "you" are from Meet's DOM, do not label local.
      // The backend will still capture audio even without a name mapping.
      if (self) return self;
      return null;
    }
    return allocateRemoteName() || `Participant ${remoteCount() + 1}`;
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

  async function tapTrack(track, speakerId, displayName) {
    if (!active || !track || track.kind !== "audio") return false;
    if (track.readyState === "ended") return false;
    const trackKey = track.id || `${speakerId}:${tappedTrackIds.size}`;
    if (tappedTrackIds.has(trackKey)) return false;
    if (taps.has(speakerId)) return false;

    if (speakerId !== "local") {
      if (remoteCount() >= maxRemoteTracks()) return false;
    }

    // Reserve the speaker id before await so parallel scans don't double-tap.
    const name = cleanName(displayName) || fallbackSpeakerLabel(speakerId);
    const placeholder = {
      trackId: trackKey,
      buffers: [],
      samples: 0,
      name,
      pending: true,
    };
    taps.set(speakerId, placeholder);
    tappedTrackIds.add(trackKey);

    try {
      const ctx = await ensureAudioContext();
      if (!active || taps.get(speakerId) !== placeholder) {
        return false;
      }
      const stream = new MediaStream([track.clone()]);
      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, WORKLET_NAME, {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
      });
      const gain = ctx.createGain();
      gain.gain.value = 0;

      const tap = {
        ctx,
        source,
        worklet,
        gain,
        trackId: trackKey,
        buffers: [],
        samples: 0,
        name,
      };

      worklet.port.onmessage = (event) => {
        if (!active) return;
        const input = event.data;
        if (!input || !input.length) return;
        const fromRate = ctx.sampleRate;
        const resampled = downsample(input, fromRate, sampleRate);
        tap.buffers.push(resampled);
        tap.samples += resampled.length;
        const minSamples = Math.floor((sampleRate * BATCH_MS) / 1000);
        if (tap.samples >= minSamples) flushSpeaker(speakerId);
      };

      source.connect(worklet);
      worklet.connect(gain);
      gain.connect(ctx.destination);
      const resume = () => {
        if (ctx.state === "suspended") ctx.resume().catch(() => {});
      };
      resume();
      window.addEventListener("click", resume, { once: true, capture: true });

      taps.set(speakerId, tap);
      console.info("[Distill] tapping", speakerId, name);
      post({ type: "DISTILL_TAP", speakerId, name, taps: taps.size });
      return true;
    } catch (err) {
      taps.delete(speakerId);
      tappedTrackIds.delete(trackKey);
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
        message: "Could not get local microphone access.",
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
        tap.source?.disconnect();
        tap.worklet?.disconnect();
        tap.gain?.disconnect();
      } catch (_) {
        /* ignore */
      }
    }
    taps.clear();
    tappedTrackIds.clear();
    remoteNamePool = [];
    if (sharedCtx) {
      try {
        sharedCtx.close();
      } catch (_) {
        /* ignore */
      }
      sharedCtx = null;
      workletReady = null;
    }
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
      if (sharedCtx && sharedCtx.state === "suspended") {
        sharedCtx.resume().catch(() => {});
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
          "No audio track found yet. Click the Meet page or refresh the tab.",
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
