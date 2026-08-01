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
    this.meetingStatus = null;
    this.segments = new Map();
    this.speakerMap = {};
    this._insightsBusy = false;
    this._cachedInsights = null;
    this._lastDebug = null;
    this._prevGroupSnapshot = [];
    this._reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
    this.debugPanel = document.getElementById("debugPanel");
    this.debugBody = document.getElementById("debugBody");
    this._timerStartedAt = null;
    this._timerInterval = null;
    this._tuningSchema = [];
    this._tuningValues = {};
    this._tuningDefaults = {};
    this.hasRecording = false;
    this.recordingPlayer = document.getElementById("recordingPlayer");
    this.recordingAudio = document.getElementById("recordingAudio");
    this.confirmPanel = document.getElementById("confirmPanel");
    this.confirmTitle = document.getElementById("confirmTitle");
    this.confirmMessage = document.getElementById("confirmMessage");
    this.confirmOkBtn = document.getElementById("confirmOkBtn");
    this.confirmCancelBtn = document.getElementById("confirmCancelBtn");
    this._confirmResolver = null;

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
      this.updateInsightsAvailability();
      return;
    }
    const meeting = await res.json();
    this.meetingId = meeting.id;
    this.meetingStatus = meeting.status || "stopped";
    this.speakerMap = { ...(meeting.speaker_map || {}) };
    this._cachedInsights = null;
    this.setHasRecording(!!meeting.has_recording);
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
    this.meetingStatus = status;
    if (status === "recording") {
      this.setStatus("recording", "جلسه در حال ضبط (تاریخچه بارگذاری شد)");
    } else if (status === "processing") {
      this.setStatus("processing", "در حال پردازش");
    } else {
      this.setStatus("connected", "تاریخچه جلسه بارگذاری شد");
    }
    this.updateInsightsAvailability();
  }

  bindEvents() {
    this.startBtn.addEventListener("click", () => this.startLive());
    this.stopBtn.addEventListener("click", () => this.stopLive());
    this.uploadBtn.addEventListener("click", () => this.uploadRecording());
    this.insightsBtn.addEventListener("click", () => this.generateInsights());
    this.clearBtn.addEventListener("click", () => this.clearTimeline(true));
    if (this.confirmCancelBtn) {
      this.confirmCancelBtn.addEventListener("click", () => this.resolveConfirm(false));
    }
    if (this.confirmOkBtn) {
      this.confirmOkBtn.addEventListener("click", () => this.resolveConfirm(true));
    }
    if (this.confirmPanel) {
      this.confirmPanel.addEventListener("click", (ev) => {
        if (ev.target === this.confirmPanel) this.resolveConfirm(false);
      });
    }
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && this.confirmPanel && !this.confirmPanel.classList.contains("hidden")) {
        this.resolveConfirm(false);
      }
    });
    const debugBtn = document.getElementById("debugBtn");
    if (debugBtn) debugBtn.addEventListener("click", () => this.openDebug());
    const closeDebug = document.getElementById("closeDebugBtn");
    if (closeDebug) closeDebug.addEventListener("click", () => this.closeDebug());
    if (this.debugPanel) {
      this.debugPanel.addEventListener("click", (ev) => {
        if (ev.target === this.debugPanel) this.closeDebug();
      });
    }
    if (this.meetingMeta) {
      this.meetingMeta.addEventListener("click", (ev) => {
        if (ev.target.closest("[data-copy-session]")) {
          this.copySessionLink();
        }
      });
    }

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
        let control = "";
        if (item.type === "select" && Array.isArray(item.options)) {
          const opts = item.options
            .map((o) => {
              const selected = String(val) === String(o) ? " selected" : "";
              return `<option value="${this.escape(String(o))}"${selected}>${this.escape(String(o))}</option>`;
            })
            .join("");
          control = `<select id="tune_${item.key}" data-key="${item.key}">${opts}</select>`;
        } else {
          control = `<input id="tune_${item.key}" data-key="${item.key}" type="${item.type === "number" ? "number" : "text"}"
            ${item.min != null ? `min="${item.min}"` : ""}
            ${item.max != null ? `max="${item.max}"` : ""}
            ${item.step != null ? `step="${item.step}"` : ""}
            value="${this.escape(String(val ?? ""))}" />`;
        }
        const nextOnly =
          item.apply === "next_session"
            ? `<div class="hint next-session-note">فقط روی جلسات جدید اثر دارد (نه جلسه جاری)</div>`
            : "";
        row.innerHTML = `
          <label for="tune_${item.key}">
            ${this.escape(item.label)}${this.escape(unit)}
            <span class="apply-tag">${applyLabel}</span>
          </label>
          ${control}
          ${item.help ? `<div class="hint">${this.escape(item.help)}</div>` : ""}
          ${nextOnly}
        `;
        box.appendChild(row);
      });
      this.tuningFields.appendChild(box);
    });
  }

  collectTuningFromForm() {
    const values = { ...this._tuningValues };
    if (!this.tuningFields) return values;
    this.tuningFields.querySelectorAll("input[data-key], select[data-key]").forEach((input) => {
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
      if (!navigator.mediaDevices?.enumerateDevices) return;
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

  micUnavailableReason() {
    const host = window.location.hostname || "";
    const insecure = window.isSecureContext === false;
    const noApi = !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia;

    if (!noApi && !insecure) return null;

    if (host === "0.0.0.0") {
      return (
        "مرورگر روی آدرس 0.0.0.0 به میکروفون دسترسی نمی‌دهد.\n\n" +
        "همین سرویس را با http://localhost:8000 یا http://127.0.0.1:8000 باز کنید."
      );
    }
    if (insecure || noApi) {
      return (
        "دسترسی به میکروفون فقط روی localhost یا HTTPS فعال است.\n\n" +
        `آدرس فعلی: ${window.location.origin}\n` +
        "لطفاً با http://localhost:8000 باز کنید."
      );
    }
    return null;
  }

  async ensureMicAvailable() {
    const reason = this.micUnavailableReason();
    if (reason) {
      alert(reason);
      return false;
    }
    return true;
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
    if (!this.meetingMeta) return;
    if (!this.meetingId) {
      this.meetingMeta.textContent = "جلسه‌ای انتخاب نشده";
      this.updateInsightsAvailability();
      return;
    }
    this.meetingMeta.innerHTML =
      `شناسه جلسه: <button type="button" class="session-link" data-copy-session title="کلیک برای کپی لینک جلسه">${this.escape(this.meetingId)}</button>`;
    this.updateInsightsAvailability();
  }

  setHasRecording(has) {
    this.hasRecording = !!has;
    if (!this.recordingPlayer || !this.recordingAudio) return;
    if (this.hasRecording && this.meetingId) {
      const url = `/meetings/${this.meetingId}/recording?t=${Date.now()}`;
      if (this.recordingAudio.getAttribute("src") !== url) {
        this.recordingAudio.src = url;
      }
      this.recordingPlayer.classList.remove("hidden");
    } else {
      this.pauseRecordingPlayback();
      this.recordingAudio.removeAttribute("src");
      this.recordingAudio.load();
      this.recordingPlayer.classList.add("hidden");
    }
  }

  pauseRecordingPlayback() {
    if (!this.recordingAudio) return;
    try {
      this.recordingAudio.pause();
    } catch (_) {}
  }

  askConfirm({ title, message, confirmLabel = "تأیید" }) {
    return new Promise((resolve) => {
      if (!this.confirmPanel) {
        resolve(window.confirm(message));
        return;
      }
      this._confirmResolver = resolve;
      if (this.confirmTitle) this.confirmTitle.textContent = title || "تأیید";
      if (this.confirmMessage) this.confirmMessage.textContent = message || "";
      if (this.confirmOkBtn) this.confirmOkBtn.textContent = confirmLabel;
      this.confirmPanel.classList.remove("hidden");
      if (this.confirmOkBtn) this.confirmOkBtn.focus();
    });
  }

  resolveConfirm(ok) {
    if (!this._confirmResolver) return;
    const resolve = this._confirmResolver;
    this._confirmResolver = null;
    if (this.confirmPanel) this.confirmPanel.classList.add("hidden");
    resolve(!!ok);
  }

  sessionLink() {
    if (!this.meetingId) return "";
    return `${window.location.origin}/assistant/${this.meetingId}`;
  }

  async copySessionLink() {
    const url = this.sessionLink();
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } finally {
        document.body.removeChild(ta);
      }
    }
    this.showCopyToast("لینک جلسه کپی شد");
  }

  showCopyToast(message = "لینک جلسه کپی شد") {
    const toast = document.getElementById("copyToast");
    const text = document.getElementById("copyToastText");
    if (!toast) return;
    if (text) text.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(this._copyFlashTimer);
    this._copyFlashTimer = setTimeout(() => {
      toast.classList.remove("is-visible");
    }, 1800);
  }

  hasTranscriptContext() {
    return Array.from(this.segments.values()).some((s) => this.isMeaningfulSpeech(s.text));
  }

  isMeaningfulSpeech(text) {
    if (!text) return false;
    // Drop ASR event tags like (سرفه) / (Sound of a car) and keep real words
    const cleaned = String(text)
      .replace(/[\(\[][^\)\]]*[\)\]]/g, " ")
      .replace(/\b(sound of a \w+|background noise|music playing)\b/gi, " ")
      .trim();
    const words = cleaned.match(/[\w\u0600-\u06FF]+/g) || [];
    return words.length >= 1 && words.join("").length >= 2;
  }

  canShowInsights() {
    if (!this.meetingId) return false;
    if (this.isRecording || this.meetingStatus === "recording") return false;
    return this.hasTranscriptContext();
  }

  updateInsightsAvailability() {
    if (!this.insightsBtn) return;
    const canShow = this.canShowInsights();
    const visible = canShow || this._insightsBusy;
    this.insightsBtn.classList.toggle("hidden", !visible);
    this.insightsBtn.disabled = !canShow || this._insightsBusy;
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
    let startedOnServer = false;
    try {
      const title = this.requireMeetingTitle();
      if (!title) return;

      if (!(await this.ensureMicAvailable())) {
        this.setStatus("disconnected", "میکروفون در دسترس نیست");
        return;
      }

      if (this.hasRecording) {
        const ok = await this.askConfirm({
          title: "پاک شدن ضبط قبلی",
          message:
            "این جلسه یک فایل ضبط‌شده دارد. شروع ضبط جدید، فایل صدا و متن فعلی را پاک می‌کند. مطمئن هستید؟",
          confirmLabel: "پاک کردن و شروع",
        });
        if (!ok) return;
      }

      this.pauseRecordingPlayback();

      let meeting;
      if (this.meetingId) {
        const res = await fetch(`/meetings/${this.meetingId}/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reset: !!this.hasRecording }),
        });
        if (!res.ok) throw new Error(await res.text());
        meeting = await res.json();
      } else {
        const res = await fetch("/meetings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, start: true }),
        });
        if (!res.ok) throw new Error(await res.text());
        meeting = await res.json();
      }
      startedOnServer = true;

      this.meetingId = meeting.id;
      this.meetingStatus = "recording";
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      this._cachedInsights = null;
      this.setHasRecording(false);
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
      this.updateInsightsAvailability();
    } catch (err) {
      console.error(err);
      this.isRecording = false;
      this.stopMic();
      if (this.ws) {
        try { this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      if (startedOnServer && this.meetingId) {
        try {
          await fetch(`/meetings/${this.meetingId}/stop`, { method: "POST" });
        } catch (_) {}
        this.meetingStatus = "stopped";
      }
      this.startBtn.classList.remove("hidden");
      this.stopBtn.classList.add("hidden");
      this.audioLevel.classList.add("hidden");
      this.stopTimer(false);
      if (this.levelMeter) this.levelMeter.classList.remove("active");
      this.updateInsightsAvailability();

      const micHint = this.micUnavailableReason();
      const message = micHint || err.message || String(err);
      this.setStatus("disconnected", "خطا در شروع");
      alert(`شروع جلسه ناموفق بود:\n${message}`);
    }
  }

  async stopLive() {
    try {
      this.isRecording = false;
      this.meetingStatus = "processing";
      this.stopMic();
      // Only stop via HTTP — avoid double-stop race with WS "stop"
      if (this.meetingId) {
        this.setStatus("processing", "در حال پردازش…");
        const stopRes = await fetch(`/meetings/${this.meetingId}/stop`, { method: "POST" });
        let stopped = null;
        if (stopRes.ok) {
          try {
            stopped = await stopRes.json();
          } catch (_) {}
        }
        this.meetingStatus = "stopped";
        this.setHasRecording(!!(stopped && stopped.has_recording));
        await this.refreshTranscript();
        await this.refreshDebug();
        const hasText = this.hasTranscriptContext();
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
      this.updateInsightsAvailability();
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
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(this.micUnavailableReason() || "getUserMedia unavailable");
    }
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
        if (msg.status) this.meetingStatus = msg.status;
        if (msg.status === "processing") this.setStatus("processing", "در حال پردازش");
        if (msg.status === "transcribing" && msg.progress) {
          const p = msg.progress;
          const done = p.done != null ? p.done : p.chunk;
          const total = p.total != null ? p.total : "?";
          this.setStatus("processing", `پیاده‌سازی ${done}/${total}`);
        }
        if (msg.status === "stopped") {
          this.setStatus("connected", "متوقف شد – آماده تحلیل");
          this.stopTimer(false);
          this.refreshTranscript().catch(() => {});
          this.refreshDebug().catch(() => {});
        }
        if (msg.status === "recording") this.setStatus("recording", "در حال ضبط");
        this.updateInsightsAvailability();
      } else if (msg.type === "speaker_update") {
        this.refreshTranscript().catch(() => {});
      } else if (msg.type === "speaker_map" && msg.speaker_map) {
        this.speakerMap = { ...msg.speaker_map };
        this.renderTimeline();
      } else if (msg.type === "warning") {
        console.warn(msg.message || msg.code);
        if (msg.code === "fallback_diarization") {
          this.setStatus("processing", "هشدار: diarization ساده (بدون pyannote)");
        }
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
    const nextList = (segments || []).filter((s) => (s.text || "").trim());
    const nextGroups = this.buildTimelineGroups(nextList);
    const mergeFx = this.detectMergePolish(this._prevGroupSnapshot, nextGroups);
    const shouldMergeAnim =
      !this._reduceMotion &&
      mergeFx &&
      mergeFx.mergeFrom >= 2 &&
      this.timeline.querySelector(".segment");

    const apply = () => {
      this.segments.clear();
      nextList.forEach((s) => this.segments.set(s.id, s));
      this.renderTimeline(nextGroups, mergeFx);
      this._prevGroupSnapshot = nextGroups.map((g) => this.snapshotGroup(g));
    };

    if (shouldMergeAnim) {
      this.timeline.classList.add("timeline-is-merging");
      this.timeline.querySelectorAll(".segment").forEach((el) => {
        el.classList.add("segment-merge-out");
      });
      window.setTimeout(apply, 320);
    } else {
      apply();
    }
  }

  upsertSegment(seg) {
    this.segments.set(seg.id, seg);
    this.replaceTranscript(Array.from(this.segments.values()));
  }

  clearTimeline(resetData) {
    if (resetData) {
      this.segments.clear();
      this._prevGroupSnapshot = [];
    }
    this.timeline.innerHTML = `
      <div class="timeline-empty">
        <div class="rec-ring">●</div>
        <p>برای شروع ضبط، دکمه زرد را بزنید.</p>
      </div>`;
    this.updateInsightsAvailability();
  }

  snapshotGroup(g) {
    return {
      key: this.groupKey(g),
      text: g.seg.text || "",
      provisional: !!g.seg.provisional,
      start_ms: g.seg.start_ms,
      end_ms: g.seg.end_ms,
      speakers: [...g.speakers],
    };
  }

  groupKey(g) {
    const spk = (g.speakers || []).slice().sort().join(",");
    // Bucket by ~1.5s so hop-coalesced rows keep a stable DOM identity
    const bucket = Math.floor((g.seg.start_ms || 0) / 1500);
    return `${spk}|${bucket}`;
  }

  buildTimelineGroups(list) {
    const ordered = [...list].sort(
      (a, b) =>
        a.start_ms - b.start_ms ||
        a.end_ms - b.end_ms ||
        String(a.speaker_id).localeCompare(String(b.speaker_id))
    );
    const groups = [];
    const used = new Set();
    ordered.forEach((seg, idx) => {
      if (used.has(idx)) return;
      if (seg.is_overlap) {
        const peers = ordered.filter((other, j) => {
          if (j < idx) return false;
          return (
            other.is_overlap &&
            other.start_ms === seg.start_ms &&
            other.end_ms === seg.end_ms &&
            other.text === seg.text
          );
        });
        peers.forEach((p) => {
          const j = ordered.indexOf(p);
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
    return groups;
  }

  detectMergePolish(prev, next) {
    if (!prev.length || !next.length) {
      return { polishKeys: new Set(), mergeFrom: 0 };
    }
    const nextKeys = new Set(next.map((g) => this.groupKey(g)));
    const polishKeys = new Set();
    let mergeFrom = 0;

    // Many provisional rows → fewer coalesced rows overlapping same span
    const prevProv = prev.filter((p) => p.provisional);

    prevProv.forEach((p) => {
      const survivors = next.filter((g) => {
        const ov =
          Math.min(p.end_ms, g.seg.end_ms) - Math.max(p.start_ms, g.seg.start_ms);
        return ov > 0 && (g.speakers || []).some((s) => p.speakers.includes(s));
      });
      if (survivors.length === 1 && prevProv.length > next.length) {
        polishKeys.add(this.groupKey(survivors[0]));
        mergeFrom = Math.max(mergeFrom, prevProv.length);
      }
      // Provisional → finalized
      survivors.forEach((g) => {
        if (p.provisional && !g.seg.provisional) {
          polishKeys.add(this.groupKey(g));
        }
        // Same slot text jumped to a fuller polished sentence
        if (
          p.provisional &&
          g.seg.provisional &&
          g.seg.text !== p.text &&
          g.seg.text.length > p.text.length + 8 &&
          next.length <= prev.length
        ) {
          polishKeys.add(this.groupKey(g));
        }
      });
    });

    if (prev.length > next.length + 0) {
      next.forEach((g) => {
        if (!g.seg.provisional || polishKeys.size) return;
        // collapsed hop variants into one live row
        const related = prev.filter((p) => {
          const ov =
            Math.min(p.end_ms, g.seg.end_ms) - Math.max(p.start_ms, g.seg.start_ms);
          return ov > 200;
        });
        if (related.length >= 2) polishKeys.add(this.groupKey(g));
      });
      mergeFrom = Math.max(mergeFrom, prev.length - next.length + 1);
    }

    return { polishKeys, mergeFrom, nextKeys };
  }

  renderTimeline(groups, mergeFx) {
    const listGroups =
      groups ||
      this.buildTimelineGroups(
        Array.from(this.segments.values()).filter((s) => (s.text || "").trim())
      );

    if (!listGroups.length) {
      this.clearTimeline(false);
      return;
    }

    const prevByKey = new Map(
      (this._prevGroupSnapshot || []).map((p) => [p.key, p])
    );
    const polishKeys = (mergeFx && mergeFx.polishKeys) || new Set();

    this.timeline.classList.remove("timeline-is-merging");
    this.timeline.innerHTML = "";

    // Prefer the last provisional row, or the one whose text just changed
    let activeKey = null;
    for (let i = listGroups.length - 1; i >= 0; i--) {
      const g = listGroups[i];
      if (!g.seg.provisional) continue;
      const key = this.groupKey(g);
      const prev = prevByKey.get(key);
      if (!prev || prev.text !== (g.seg.text || "")) {
        activeKey = key;
        break;
      }
    }
    if (!activeKey) {
      for (let i = listGroups.length - 1; i >= 0; i--) {
        if (listGroups[i].seg.provisional) {
          activeKey = this.groupKey(listGroups[i]);
          break;
        }
      }
    }

    listGroups.forEach((g) => {
      const key = this.groupKey(g);
      const prev = prevByKey.get(key);
      const row = document.createElement("div");
      const isLive = !!g.seg.provisional;
      const isActive = key === activeKey;
      row.className = g.type === "overlap" ? "segment segment-overlap" : "segment";
      if (isLive) row.classList.add("segment-live");
      if (isActive) row.classList.add("segment-listening");
      if (polishKeys.has(key)) row.classList.add("segment-polish");
      row.dataset.groupKey = key;

      const provisional = isLive
        ? '<span class="badge badge-muted">موقت</span>'
        : polishKeys.has(key)
          ? '<span class="badge badge-polish">پالیش‌شده</span>'
          : "";
      const speakerHtml = g.speakers
        .map((spk) => {
          const color = this.speakerColor(spk);
          const label = this.speakerLabel(spk);
          return `<button type="button" class="speaker-chip" data-speaker-id="${this.escape(spk)}" title="کلیک برای نام‌گذاری">
            <span class="speaker-dot" style="background:${color}"></span>
            <span class="speaker-name" style="color:${color}">${this.escape(label)}</span>
          </button>`;
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
        <div class="segment-text" data-role="text"></div>
      `;

      const textEl = row.querySelector('[data-role="text"]');
      this.applyStreamingText(
        textEl,
        g.seg.text || "",
        prev ? prev.text : "",
        polishKeys.has(key)
      );

      row.querySelectorAll("[data-speaker-id]").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          this.renameSpeaker(btn.getAttribute("data-speaker-id"));
        });
      });
      this.timeline.appendChild(row);
    });
    this.timeline.scrollTop = this.timeline.scrollHeight;
    this.updateInsightsAvailability();
  }

  applyStreamingText(el, newText, oldText, isPolish) {
    if (!el) return;
    const next = newText || "";
    const prev = oldText || "";

    if (this._reduceMotion) {
      el.textContent = next;
      return;
    }

    if (isPolish) {
      el.classList.add("is-polishing");
      el.innerHTML = "";
      const words = next.split(/(\s+)/).filter((w) => w.length);
      words.forEach((w, i) => {
        const span = document.createElement("span");
        span.className = "polish-word";
        span.textContent = w;
        span.style.animationDelay = `${Math.min(i * 22, 480)}ms`;
        el.appendChild(span);
      });
      window.setTimeout(() => el.classList.remove("is-polishing"), 900);
      return;
    }

    if (!prev) {
      el.classList.add("is-streaming");
      el.innerHTML = "";
      const words = next.split(/(\s+)/).filter((w) => w.length);
      words.forEach((w, i) => {
        const span = document.createElement("span");
        span.className = "stream-word";
        span.textContent = w;
        span.style.animationDelay = `${Math.min(i * 26, 520)}ms`;
        el.appendChild(span);
      });
      window.setTimeout(() => {
        el.classList.remove("is-streaming");
      }, Math.min(600 + words.length * 26, 1400));
      return;
    }

    if (next === prev) {
      el.textContent = next;
      return;
    }

    // Growing / revised live utterance → soft restream
    el.classList.add("is-streaming", "is-revising");
    el.innerHTML = "";
    const words = next.split(/(\s+)/).filter((w) => w.length);
    const prevWords = prev.split(/(\s+)/).filter((w) => w.length);
    words.forEach((w, i) => {
      const span = document.createElement("span");
      const shared = i < prevWords.length && prevWords[i] === w;
      span.className = shared ? "stream-word is-stable" : "stream-word is-new";
      span.textContent = w;
      span.style.animationDelay = shared
        ? "0ms"
        : `${Math.min((i - Math.min(i, prevWords.length)) * 30 + 40, 560)}ms`;
      el.appendChild(span);
    });
    window.setTimeout(() => {
      el.classList.remove("is-streaming", "is-revising");
    }, 900);
  }

  speakerLabel(speakerId) {
    return this.speakerMap[speakerId] || speakerId;
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

  async renameSpeaker(speakerId) {
    if (!this.meetingId || !speakerId) return;
    const current = this.speakerLabel(speakerId);
    const name = window.prompt(`نام نمایشی برای ${speakerId}:`, current === speakerId ? "" : current);
    if (name == null) return;
    const trimmed = String(name).trim();
    if (!trimmed) return;
    try {
      const res = await fetch(`/meetings/${this.meetingId}/speakers`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [speakerId]: trimmed }),
      });
      if (!res.ok) throw new Error(await res.text());
      const meeting = await res.json();
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      this.renderTimeline();
    } catch (err) {
      console.error(err);
      alert(`نام‌گذاری ناموفق: ${err.message}`);
    }
  }

  async refreshDebug() {
    if (!this.meetingId) return;
    const res = await fetch(`/meetings/${this.meetingId}/debug`);
    if (!res.ok) return;
    this._lastDebug = await res.json();
    this.renderDebug();
  }

  renderDebug() {
    if (!this.debugBody || !this._lastDebug) return;
    const d = this._lastDebug;
    const hit =
      d.stt_cache_hit_rate == null
        ? "—"
        : `${Math.round(d.stt_cache_hit_rate * 100)}%`;
    this.debugBody.innerHTML = `
      <dl class="debug-grid">
        <div><dt>chunks</dt><dd>${d.chunks_processed ?? 0}</dd></div>
        <div><dt>retries</dt><dd>${d.stt_retries ?? 0}</dd></div>
        <div><dt>dropped</dt><dd>${d.stt_dropped ?? 0}</dd></div>
        <div><dt>STT calls</dt><dd>${d.stt_calls ?? 0}</dd></div>
        <div><dt>STT time</dt><dd>${d.stt_total_ms ?? 0} ms</dd></div>
        <div><dt>timings</dt><dd>${d.stt_with_timings ?? 0}</dd></div>
        <div><dt>cache hit</dt><dd>${hit}</dd></div>
        <div><dt>diarization</dt><dd>${this.escape(String(d.diarization_backend || "—"))}</dd></div>
        <div><dt>speakers</dt><dd>${(d.speaker_ids || []).map((s) => this.escape(this.speakerLabel(s))).join(", ") || "—"}</dd></div>
      </dl>
    `;
  }

  async openDebug() {
    if (!this.debugPanel) return;
    if (!this.meetingId) {
      alert("ابتدا یک جلسه شروع یا باز کنید");
      return;
    }
    await this.refreshDebug();
    this.debugPanel.classList.remove("hidden");
  }

  closeDebug() {
    if (this.debugPanel) this.debugPanel.classList.add("hidden");
  }

  async refreshTranscript() {
    if (!this.meetingId) return;
    const res = await fetch(`/meetings/${this.meetingId}/transcript`);
    if (!res.ok) return;
    const data = await res.json();
    this.replaceTranscript(data.segments || []);
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
      this.meetingStatus = meeting.status || "stopped";
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      this._cachedInsights = null;
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);

      await this.connectWebSocket(meeting.id);

      const form = new FormData();
      form.append("file", file, file.name);
      const res = await fetch(`/meetings/${this.meetingId}/upload`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.meetingStatus = "stopped";
      this.setHasRecording(true);
      this.replaceTranscript(data.segments || []);
      this.setStatus("connected", "پیاده‌سازی فایل انجام شد");
      await this.refreshDebug();
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "خطا در آپلود");
      alert(`آپلود ناموفق: ${err.message}`);
    } finally {
      if (this.ws) {
        try {
          this.ws.close();
        } catch (_) {}
        this.ws = null;
      }
    }
  }

  async generateInsights() {
    if (!this.canShowInsights()) return;
    try {
      this._insightsBusy = true;
      this.updateInsightsAvailability();
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
      this._insightsBusy = false;
      this.insightsBtn.textContent = "تولید خلاصه و تصمیمات";
      this.updateInsightsAvailability();
    }
  }

  renderInsights(data) {
    this.insightsPanel.classList.remove("hidden");
    const summary = (data.summary || "").trim();
    const summaryEl = document.getElementById("insightSummary");
    summaryEl.textContent = summary || "خلاصه‌ای تولید نشد.";
    summaryEl.style.color = summary ? "" : "var(--muted, #888)";
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
