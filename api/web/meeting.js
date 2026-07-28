// Distill client — Distill
class DistillClient {
  constructor() {
    this.ws = null;
    this.audioContext = null;
    this.audioStream = null;
    this.processor = null;
    this.source = null;
    this.isRecording = false;
    this.isConnected = false;
    this.meetingId = null;
    this.segments = new Map();

    // Yellow/gray family accents for speakers
    this.speakerColors = [
      "#ffc828",
      "#d4d4d4",
      "#eab308",
      "#9ca3af",
      "#fde047",
      "#737373",
      "#facc15",
      "#a3a3a3",
    ];

    this.startBtn = document.getElementById("startBtn");
    this.stopBtn = document.getElementById("stopBtn");
    this.uploadBtn = document.getElementById("uploadBtn");
    this.uploadFile = document.getElementById("uploadFile");
    this.insightsBtn = document.getElementById("insightsBtn");
    this.clearBtn = document.getElementById("clearBtn");
    this.timeline = document.getElementById("timeline");
    this.connectionStatus = document.getElementById("connectionStatus");
    this.statusText = document.getElementById("statusText");
    this.audioLevel = document.getElementById("audioLevel");
    this.audioInput = document.getElementById("audioInput");
    this.meetingTitle = document.getElementById("meetingTitle");
    this.meetingMeta = document.getElementById("meetingMeta");
    this.insightsPanel = document.getElementById("insightsPanel");
    this.recordTimer = document.getElementById("recordTimer");
    this.levelMeter = document.getElementById("levelMeter");
    this.statusChip = document.getElementById("statusChip");
    this.tuningPanel = document.getElementById("tuningPanel");
    this.tuningFields = document.getElementById("tuningFields");
    this._timerStartedAt = null;
    this._timerInterval = null;
    this._tuningSchema = [];
    this._tuningValues = {};
    this._tuningDefaults = {};

    this.bindEvents();
    this.loadAudioDevices();
    this.loadTuning().catch((err) => console.error(err));
    if (this.meetingTitle) {
      this.meetingTitle.addEventListener("input", () => {
        this.meetingTitle.classList.remove("field-invalid");
        const err = document.getElementById("meetingTitleError");
        if (err) err.classList.add("hidden");
      });
    }
    this.bootstrapFromUrl().catch((err) => console.error(err));
  }

  meetingIdFromPath() {
    const parts = window.location.pathname.replace(/\/+$/, "").split("/");
    // /assistant/{uuid}
    if (parts.length >= 3 && parts[1] === "assistant" && parts[2]) {
      return parts[2];
    }
    return null;
  }

  setSessionUrl(meetingId) {
    if (!meetingId) return;
    const next = `/assistant/${meetingId}`;
    if (window.location.pathname !== next) {
      window.history.replaceState({ meetingId }, "", next);
    }
  }

  async bootstrapFromUrl() {
    const id = this.meetingIdFromPath();
    if (!id) return;
    await this.loadExistingMeeting(id);
  }

  async loadExistingMeeting(meetingId) {
    this.setStatus("processing", "در حال بارگذاری جلسه…");
    const res = await fetch(`/meetings/${meetingId}`);
    if (!res.ok) {
      this.setStatus("disconnected", "جلسه پیدا نشد");
      if (this.meetingMeta) {
        this.meetingMeta.textContent = `جلسه یافت نشد: ${meetingId}`;
      }
      return;
    }
    const meeting = await res.json();
    this.meetingId = meeting.id;
    if (this.meetingTitle) {
      this.meetingTitle.value = meeting.title || "";
      this.meetingTitle.classList.remove("field-invalid");
    }
    this.setMeetingMeta();
    this.setSessionUrl(meeting.id);
    await this.refreshTranscript();

    // Load saved insights if any (ignore 404)
    try {
      const insightsRes = await fetch(`/meetings/${meeting.id}/insights`);
      if (insightsRes.ok) {
        const insights = await insightsRes.json();
        // Keep panel closed; just enable knowing history exists
        this._cachedInsights = insights;
      }
    } catch (_) {}

    const status = meeting.status || "stopped";
    if (status === "recording") {
      this.setStatus("recording", "جلسه در حال ضبط (تاریخچه بارگذاری شد)");
    } else if (status === "processing") {
      this.setStatus("processing", "در حال پردازش");
    } else {
      this.setStatus("connected", "تاریخچه جلسه بارگذاری شد");
    }
    if (this.insightsBtn) this.insightsBtn.disabled = false;
  }

  bindEvents() {
    this.startBtn.addEventListener("click", () => this.startLive());
    this.stopBtn.addEventListener("click", () => this.stopLive());
    this.uploadBtn.addEventListener("click", () => this.uploadRecording());
    this.insightsBtn.addEventListener("click", () => this.generateInsights());
    this.clearBtn.addEventListener("click", () => this.clearTimeline(true));

    this.liveSource = document.getElementById("liveSource");
    this.uploadSource = document.getElementById("uploadSource");
    const showUpload = document.getElementById("showUploadMode");
    const showLive = document.getElementById("showLiveMode");
    if (showUpload) {
      showUpload.addEventListener("click", () => this.setSourceMode("upload"));
    }
    if (showLive) {
      showLive.addEventListener("click", () => this.setSourceMode("live"));
    }

    if (this.statusChip) {
      this.statusChip.addEventListener("click", () => this.openTuning());
      this.statusChip.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          this.openTuning();
        }
      });
    }
    const closeTuning = document.getElementById("closeTuningBtn");
    const saveTuning = document.getElementById("saveTuningBtn");
    const resetTuning = document.getElementById("resetTuningBtn");
    if (closeTuning) closeTuning.addEventListener("click", () => this.closeTuning());
    if (saveTuning) saveTuning.addEventListener("click", () => this.saveTuning());
    if (resetTuning) resetTuning.addEventListener("click", () => this.resetTuning());
    if (this.tuningPanel) {
      this.tuningPanel.addEventListener("click", (ev) => {
        if (ev.target === this.tuningPanel) this.closeTuning();
      });
    }

    const closeInsights = document.getElementById("closeInsightsBtn");
    if (closeInsights) {
      closeInsights.addEventListener("click", () => this.closeInsights());
    }
    if (this.insightsPanel) {
      this.insightsPanel.addEventListener("click", (ev) => {
        if (ev.target === this.insightsPanel) this.closeInsights();
      });
    }
  }

  async loadTuning() {
    const res = await fetch("/tuning");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    this._tuningSchema = data.schema || [];
    this._tuningValues = { ...(data.values || {}) };
    this._tuningDefaults = { ...(data.defaults || {}) };
    this.renderTuningFields();
  }

  renderTuningFields() {
    if (!this.tuningFields) return;
    const groups = {};
    this._tuningSchema.forEach((item) => {
      const g = item.group || "سایر";
      if (!groups[g]) groups[g] = [];
      groups[g].push(item);
    });
    this.tuningFields.innerHTML = "";
    Object.entries(groups).forEach(([group, items]) => {
      const box = document.createElement("div");
      box.className = "tuning-group";
      box.innerHTML = `<h4>${this.escape(group)}</h4>`;
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "tuning-row";
        const applyLabel = item.apply === "live" ? "زنده" : "جلسه بعد";
        const unit = item.unit ? ` (${item.unit})` : "";
        const val = this._tuningValues[item.key];
        row.innerHTML = `
          <label for="tune_${item.key}">
            ${this.escape(item.label)}${this.escape(unit)}
            <span class="apply-tag">${applyLabel}</span>
          </label>
          <input id="tune_${item.key}" data-key="${item.key}" type="${item.type === "number" ? "number" : "text"}"
            ${item.min != null ? `min="${item.min}"` : ""}
            ${item.max != null ? `max="${item.max}"` : ""}
            ${item.step != null ? `step="${item.step}"` : ""}
            value="${this.escape(String(val ?? ""))}" />
          ${item.help ? `<div class="hint">${this.escape(item.help)}</div>` : ""}
        `;
        box.appendChild(row);
      });
      this.tuningFields.appendChild(box);
    });
  }

  collectTuningFromForm() {
    const values = { ...this._tuningValues };
    if (!this.tuningFields) return values;
    this.tuningFields.querySelectorAll("input[data-key]").forEach((input) => {
      const key = input.getAttribute("data-key");
      const schema = this._tuningSchema.find((s) => s.key === key);
      if (!schema) return;
      if (schema.type === "number") {
        const n = Number(input.value);
        if (!Number.isNaN(n)) values[key] = n;
      } else {
        values[key] = input.value;
      }
    });
    return values;
  }

  openTuning() {
    if (!this.tuningPanel) return;
    this.renderTuningFields();
    this.tuningPanel.classList.remove("hidden");
  }

  closeTuning() {
    if (this.tuningPanel) this.tuningPanel.classList.add("hidden");
  }

  async saveTuning() {
    const values = this.collectTuningFromForm();
    const res = await fetch("/tuning", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    if (!res.ok) {
      alert(`ذخیره تنظیمات ناموفق: ${await res.text()}`);
      return;
    }
    const data = await res.json();
    this._tuningValues = { ...(data.values || {}) };
    this.closeTuning();
    this.setStatus("connected", "تنظیمات اعمال شد");
  }

  async resetTuning() {
    const res = await fetch("/tuning/reset", { method: "POST" });
    if (!res.ok) {
      alert(`بازنشانی ناموفق: ${await res.text()}`);
      return;
    }
    const data = await res.json();
    this._tuningSchema = data.schema || this._tuningSchema;
    this._tuningValues = { ...(data.values || {}) };
    this._tuningDefaults = { ...(data.defaults || {}) };
    this.renderTuningFields();
    this.setStatus("connected", "تنظیمات به پیش‌فرض برگشت");
  }

  setSourceMode(mode) {
    const isUpload = mode === "upload";
    if (this.liveSource) this.liveSource.classList.toggle("hidden", isUpload);
    if (this.uploadSource) this.uploadSource.classList.toggle("hidden", !isUpload);
  }

  async loadAudioDevices() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((d) => d.kind === "audioinput");
      this.audioInput.innerHTML = '<option value="">میکروفون پیش‌فرض</option>';
      inputs.forEach((device, idx) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || `میکروفون ${idx + 1}`;
        this.audioInput.appendChild(option);
      });
    } catch (err) {
      console.error(err);
    }
  }

  setStatus(kind, text) {
    this.connectionStatus.className = "status-dot";
    if (kind === "connected") {
      this.connectionStatus.classList.add("is-ok");
    } else if (kind === "recording") {
      this.connectionStatus.classList.add("is-recording");
    } else if (kind === "processing") {
      this.connectionStatus.classList.add("is-processing");
    }
    this.statusText.textContent = text;
  }

  startTimer() {
    this._timerStartedAt = Date.now();
    if (this.recordTimer) {
      this.recordTimer.classList.remove("idle");
      this.recordTimer.textContent = "00:00";
    }
    this.stopTimer(false);
    this._timerInterval = setInterval(() => this.tickTimer(), 250);
  }

  tickTimer() {
    if (!this.recordTimer || !this._timerStartedAt) return;
    const elapsed = Math.floor((Date.now() - this._timerStartedAt) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    this.recordTimer.textContent = `${m}:${s}`;
  }

  stopTimer(resetDisplay = true) {
    if (this._timerInterval) {
      clearInterval(this._timerInterval);
      this._timerInterval = null;
    }
    if (resetDisplay && this.recordTimer) {
      this.recordTimer.classList.add("idle");
    }
  }

  setMeetingMeta() {
    if (!this.meetingId) {
      this.meetingMeta.textContent = "جلسه‌ای انتخاب نشده";
      this.insightsBtn.disabled = true;
      return;
    }
    this.meetingMeta.textContent = `شناسه جلسه: ${this.meetingId}`;
    this.insightsBtn.disabled = false;
  }

  requireMeetingTitle() {
    const title = (this.meetingTitle?.value || "").trim();
    const err = document.getElementById("meetingTitleError");
    if (!title) {
      if (this.meetingTitle) {
        this.meetingTitle.focus();
        this.meetingTitle.classList.add("field-invalid");
      }
      if (err) err.classList.remove("hidden");
      return null;
    }
    if (this.meetingTitle) this.meetingTitle.classList.remove("field-invalid");
    if (err) err.classList.add("hidden");
    return title;
  }

  async startLive() {
    try {
      const title = this.requireMeetingTitle();
      if (!title) return;
      const res = await fetch("/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, start: true }),
      });
      if (!res.ok) throw new Error(await res.text());
      const meeting = await res.json();
      this.meetingId = meeting.id;
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);

      await this.connectWebSocket(this.meetingId);
      await this.startMic();
      this.isRecording = true;
      this.startBtn.classList.add("hidden");
      this.stopBtn.classList.remove("hidden");
      this.setStatus("recording", "در حال ضبط");
      this.audioLevel.classList.remove("hidden");
      this.startTimer();
      if (this.levelMeter) this.levelMeter.classList.add("active");
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "خطا در شروع");
      alert(`شروع جلسه ناموفق بود: ${err.message}`);
    }
  }

  async stopLive() {
    try {
      this.isRecording = false;
      this.stopMic();
      // Only stop via HTTP — avoid double-stop race with WS "stop"
      if (this.meetingId) {
        this.setStatus("processing", "در حال پردازش…");
        await fetch(`/meetings/${this.meetingId}/stop`, { method: "POST" });
        await this.refreshTranscript();
        const hasText = Array.from(this.segments.values()).some((s) => (s.text || "").trim());
        if (!hasText) {
          this.setStatus("connected", "متوقف شد — متنی دریافت نشد (STT)");
        } else {
          this.setStatus("connected", "متوقف شد — آماده تحلیل");
        }
      }
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "خطا در توقف");
    } finally {
      if (this.ws) {
        try { this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      this.startBtn.classList.remove("hidden");
      this.stopBtn.classList.add("hidden");
      this.audioLevel.classList.add("hidden");
      this.stopTimer(false);
      if (this.levelMeter) this.levelMeter.classList.remove("active");
      if (this.insightsBtn && this.meetingId) this.insightsBtn.disabled = false;
    }
  }

  connectWebSocket(meetingId) {
    return new Promise((resolve, reject) => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/meetings/${meetingId}/audio`;
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.isConnected = true;
        this.setStatus("connected", "متصل");
        resolve();
      };
      this.ws.onmessage = (ev) => this.onWsMessage(ev);
      this.ws.onclose = () => {
        this.isConnected = false;
        if (this.isRecording) this.stopLive();
      };
      this.ws.onerror = (err) => reject(err);
    });
  }

  async startMic() {
    const constraints = {
      audio: {
        deviceId: this.audioInput.value ? { exact: this.audioInput.value } : undefined,
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    };
    this.audioStream = await navigator.mediaDevices.getUserMedia(constraints);
    this.audioContext = new AudioContext({ sampleRate: 16000 });
    this.source = this.audioContext.createMediaStreamSource(this.audioStream);
    this.processor = this.audioContext.createScriptProcessor(2048, 1, 1);
    this.processor.onaudioprocess = (event) => {
      if (!this.isRecording || !this.isConnected) return;
      const input = event.inputBuffer.getChannelData(0);
      this.sendAudio(input);
      this.updateLevel(input);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);
  }

  stopMic() {
    try {
      if (this.processor) this.processor.disconnect();
      if (this.source) this.source.disconnect();
      if (this.audioContext) this.audioContext.close();
      if (this.audioStream) this.audioStream.getTracks().forEach((t) => t.stop());
    } catch (_) {}
    this.processor = null;
    this.source = null;
    this.audioContext = null;
    this.audioStream = null;
  }

  sendAudio(float32) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const int16 = this.floatTo16BitPCM(float32);
    this.ws.send(JSON.stringify({ type: "audio", data: Array.from(int16) }));
  }

  floatTo16BitPCM(input) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  updateLevel(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.audioLevel.style.opacity = String(Math.min(1, 0.25 + rms * 8));

    if (this.levelMeter) {
      const bars = this.levelMeter.querySelectorAll("span");
      bars.forEach((bar, idx) => {
        const boost = 0.35 + (idx % 3) * 0.12;
        const h = Math.max(12, Math.min(100, rms * 900 * boost));
        bar.style.height = `${h}%`;
      });
    }
  }

  onWsMessage(event) {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "transcript" && Array.isArray(msg.segments)) {
        this.replaceTranscript(msg.segments);
      } else if (msg.type === "segment" && msg.segment) {
        this.upsertSegment(msg.segment);
      } else if (msg.type === "status") {
        if (msg.status === "processing") this.setStatus("processing", "در حال پردازش");
        if (msg.status === "stopped") {
          this.setStatus("connected", "متوقف شد – آماده تحلیل");
          this.stopTimer(false);
          if (this.insightsBtn) this.insightsBtn.disabled = false;
          this.refreshTranscript().catch(() => {});
        }
        if (msg.status === "recording") this.setStatus("recording", "در حال ضبط");
      } else if (msg.type === "speaker_update") {
        this.refreshTranscript().catch(() => {});
      } else if (msg.type === "insights") {
        this.renderInsights(msg.insights);
      } else if (msg.type === "error") {
        console.error(msg.message);
        this.setStatus("processing", `خطا: ${String(msg.message || "").slice(0, 80)}`);
      }
    } catch (err) {
      console.error(err);
    }
  }

  replaceTranscript(segments) {
    this.segments.clear();
    (segments || []).forEach((s) => this.segments.set(s.id, s));
    this.renderTimeline();
  }

  speakerColor(speakerId) {
    const n = parseInt(String(speakerId).replace(/\D/g, ""), 10);
    const idx = Number.isFinite(n) ? n % this.speakerColors.length : 0;
    return this.speakerColors[idx];
  }

  formatTs(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  upsertSegment(seg) {
    this.segments.set(seg.id, seg);
    this.renderTimeline();
  }

  clearTimeline(resetData) {
    if (resetData) this.segments.clear();
    this.timeline.innerHTML = `
      <div class="timeline-empty">
        <div class="rec-ring">●</div>
        <p>برای شروع ضبط، دکمه زرد را بزنید.</p>
      </div>`;
  }

  renderTimeline() {
    const list = Array.from(this.segments.values())
      .filter((s) => (s.text || "").trim())
      .sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms || String(a.speaker_id).localeCompare(String(b.speaker_id)));

    if (!list.length) {
      this.clearTimeline(false);
      return;
    }

    // Group simultaneous-talk rows (same span + same text) into one visual card
    const groups = [];
    const used = new Set();
    list.forEach((seg, idx) => {
      if (used.has(idx)) return;
      if (seg.is_overlap) {
        const peers = list.filter((other, j) => {
          if (j < idx) return false;
          return (
            other.is_overlap &&
            other.start_ms === seg.start_ms &&
            other.end_ms === seg.end_ms &&
            other.text === seg.text
          );
        });
        peers.forEach((p) => {
          const j = list.indexOf(p);
          if (j >= 0) used.add(j);
        });
        const speakers = [
          ...new Set([
            ...(seg.overlap_speakers || []),
            ...peers.map((p) => p.speaker_id),
          ]),
        ];
        groups.push({ type: "overlap", seg, speakers });
      } else {
        used.add(idx);
        groups.push({ type: "single", seg, speakers: [seg.speaker_id] });
      }
    });

    this.timeline.innerHTML = "";
    groups.forEach((g) => {
      const row = document.createElement("div");
      row.className = g.type === "overlap" ? "segment segment-overlap" : "segment";
      const provisional = g.seg.provisional
        ? '<span class="badge badge-muted">موقت</span>'
        : "";
      const speakerHtml = g.speakers
        .map((spk) => {
          const color = this.speakerColor(spk);
          return `<span class="speaker-chip"><span class="speaker-dot" style="background:${color}"></span><span class="speaker-name" style="color:${color}">${this.escape(spk)}</span></span>`;
        })
        .join("");
      const badge =
        g.type === "overlap"
          ? '<span class="badge">هم‌صحبتی</span>'
          : "";
      row.innerHTML = `
        <div class="segment-meta">
          <div class="speaker-row">${speakerHtml}</div>
          <span>${this.formatTs(g.seg.start_ms)} – ${this.formatTs(g.seg.end_ms)}</span>
          ${badge}${provisional}
        </div>
        <div class="segment-text">${this.escape(g.seg.text)}</div>
      `;
      this.timeline.appendChild(row);
    });
    this.timeline.scrollTop = this.timeline.scrollHeight;
  }

  async refreshTranscript() {
    if (!this.meetingId) return;
    const res = await fetch(`/meetings/${this.meetingId}/transcript`);
    if (!res.ok) return;
    const data = await res.json();
    this.segments.clear();
    (data.segments || []).forEach((s) => this.segments.set(s.id, s));
    this.renderTimeline();
  }

  async uploadRecording() {
    const file = this.uploadFile.files && this.uploadFile.files[0];
    if (!file) {
      alert("ابتدا یک فایل صوتی انتخاب کنید");
      return;
    }
    const title = this.requireMeetingTitle();
    if (!title) return;
    try {
      this.setStatus("processing", "آپلود و پیاده‌سازی…");
      const created = await fetch("/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, start: false }),
      });
      if (!created.ok) throw new Error(await created.text());
      const meeting = await created.json();
      this.meetingId = meeting.id;
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);

      const form = new FormData();
      form.append("file", file, file.name);
      const res = await fetch(`/meetings/${this.meetingId}/upload`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.segments.clear();
      (data.segments || []).forEach((s) => this.segments.set(s.id, s));
      this.renderTimeline();
      this.setStatus("connected", "پیاده‌سازی فایل انجام شد");
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "خطا در آپلود");
      alert(`آپلود ناموفق: ${err.message}`);
    }
  }

  async generateInsights() {
    if (!this.meetingId) return;
    try {
      this.insightsBtn.disabled = true;
      this.insightsBtn.textContent = "در حال تولید…";

      // Show existing insights first if already saved for this session
      if (this._cachedInsights) {
        this.renderInsights(this._cachedInsights);
      }

      const res = await fetch(`/meetings/${this.meetingId}/insights`, {
        method: "POST",
      });
      if (!res.ok) {
        if (this._cachedInsights) return;
        throw new Error(await res.text());
      }
      const data = await res.json();
      this._cachedInsights = data;
      this.renderInsights(data);
    } catch (err) {
      console.error(err);
      if (!this._cachedInsights) {
        alert(`تولید تحلیل ناموفق: ${err.message}`);
      }
    } finally {
      this.insightsBtn.disabled = false;
      this.insightsBtn.textContent = "تولید خلاصه و تصمیمات";
    }
  }

  renderInsights(data) {
    this.insightsPanel.classList.remove("hidden");
    document.getElementById("insightSummary").textContent = data.summary || "—";
    this.fillList("insightHighlights", data.highlights || []);
    this.fillList("insightDecisions", data.decisions || []);
    this.fillList("insightActions", data.action_items || []);
  }

  closeInsights() {
    if (this.insightsPanel) this.insightsPanel.classList.add("hidden");
  }

  fillList(id, items) {
    const el = document.getElementById(id);
    el.innerHTML = "";
    if (!items.length) {
      el.innerHTML = '<li style="color:var(--muted)">موردی نیست</li>';
      return;
    }
    items.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      el.appendChild(li);
    });
  }

  escape(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.distillClient = new DistillClient();
});
