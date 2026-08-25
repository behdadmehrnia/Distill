/**
 * Runs in the Meet page world (not the isolated content-script world).
 * Hooks RTCPeerConnection to capture each remote audio MediaStreamTrack and
 * the local outbound audio track, then posts Int16 PCM chunks to the bridge.
 *
 * Google Meet DOM/WebRTC internals change often — this is a working demo, not
 * a guaranteed long-term integration.
 */
(function () {
  if (window.__distillHookInstalled) return;
  window.__distillHookInstalled = true;

  const PAGE_SOURCE = "distill-page";
  const EXT_SOURCE = "distill-extension";

  /** @type {Map<string, {ctx: AudioContext, processor: ScriptProcessorNode, gain: GainNode, name: string}>} */
  const taps = new Map();
  let active = false;
  let sampleRate = 16000;
  let remoteSeq = 0;

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
    if (fromRate === toRate) return float32;
    const ratio = fromRate / toRate;
    const newLen = Math.max(1, Math.floor(float32.length / ratio));
    const out = new Float32Array(newLen);
    for (let i = 0; i < newLen; i++) {
      const idx = Math.floor(i * ratio);
      out[i] = float32[idx] || 0;
    }
    return out;
  }

  function scrapeParticipantNames() {
    const names = [];
    const seen = new Set();
    const nodes = document.querySelectorAll(
      "[data-participant-id], [data-self-name], [aria-label]"
    );
    nodes.forEach((el) => {
      const self = el.getAttribute("data-self-name");
      if (self && !seen.has(self)) {
        seen.add(self);
        names.push(self);
      }
      const label = el.getAttribute("aria-label") || "";
      // Meet often uses "Name's presentation" / "Name (You)" style labels.
      const m = label.match(/^([^,(]+?)(?:\s*\(|'s |,|$)/);
      if (m) {
        const name = m[1].trim();
        if (
          name &&
          name.length < 60 &&
          !/microphone|camera|turn|mute|chat|people|captions/i.test(name) &&
          !seen.has(name)
        ) {
          seen.add(name);
          names.push(name);
        }
      }
    });
    return names;
  }

  function guessName(speakerId) {
    const names = scrapeParticipantNames();
    if (speakerId === "local") {
      return names.find((n) => /\(you\)/i.test(n)) || names[0] || "You";
    }
    const remotes = names.filter((n) => !/\(you\)/i.test(n));
    const idx = Number(String(speakerId).replace(/\D/g, "")) - 1;
    if (idx >= 0 && remotes[idx]) return remotes[idx];
    return remotes[0] || speakerId;
  }

  function tapTrack(track, speakerId) {
    if (!active || !track || track.kind !== "audio") return;
    if (taps.has(speakerId)) return;

    try {
      const stream = new MediaStream([track.clone()]);
      const ctx = new AudioContext({ sampleRate });
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const gain = ctx.createGain();
      gain.gain.value = 0;

      processor.onaudioprocess = (event) => {
        if (!active) return;
        const input = event.inputBuffer.getChannelData(0);
        const fromRate = event.inputBuffer.sampleRate || ctx.sampleRate;
        const resampled = downsample(input, fromRate, sampleRate);
        // Skip near-silence to cut bandwidth.
        let energy = 0;
        for (let i = 0; i < resampled.length; i += 8) {
          const v = resampled[i];
          energy += v * v;
        }
        if (energy / Math.max(1, resampled.length / 8) < 1e-6) return;

        const pcm = floatToInt16(resampled);
        const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
        let binary = "";
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        post({
          type: "DISTILL_PCM",
          speakerId,
          name: guessName(speakerId),
          pcmBase64: btoa(binary),
        });
      };

      source.connect(processor);
      processor.connect(gain);
      gain.connect(ctx.destination);
      taps.set(speakerId, { ctx, processor, gain, name: guessName(speakerId) });
      console.info("[Distill] tapping audio stream", speakerId);
    } catch (err) {
      console.warn("[Distill] failed to tap track", speakerId, err);
    }
  }

  function stopAllTaps() {
    active = false;
    for (const [, tap] of taps) {
      try {
        tap.processor.disconnect();
        tap.gain.disconnect();
        tap.ctx.close();
      } catch {
        /* ignore */
      }
    }
    taps.clear();
  }

  function wrapPeerConnection() {
    const Original = window.RTCPeerConnection;
    if (!Original || Original.__distillWrapped) return;

    function WrappedPC(...args) {
      const pc = new Original(...args);

      pc.addEventListener("track", (event) => {
        if (!active) return;
        if (event.track?.kind !== "audio") return;
        remoteSeq += 1;
        const speakerId = `remote_${remoteSeq}`;
        tapTrack(event.track, speakerId);
      });

      const origAddTrack = pc.addTrack.bind(pc);
      pc.addTrack = function (...addArgs) {
        const track = addArgs[0];
        if (active && track?.kind === "audio") {
          tapTrack(track, "local");
        }
        return origAddTrack(...addArgs);
      };

      return pc;
    }

    WrappedPC.prototype = Original.prototype;
    Object.keys(Original).forEach((key) => {
      try {
        WrappedPC[key] = Original[key];
      } catch {
        /* ignore */
      }
    });
    WrappedPC.__distillWrapped = true;
    window.RTCPeerConnection = WrappedPC;
  }

  wrapPeerConnection();

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== EXT_SOURCE) return;

    if (data.type === "DISTILL_START") {
      sampleRate = data.sampleRate || 16000;
      active = true;
      remoteSeq = 0;
      console.info("[Distill] capture armed — join/refresh Meet audio if needed");
    }
    if (data.type === "DISTILL_STOP") {
      stopAllTaps();
      console.info("[Distill] capture stopped");
    }
  });
})();
