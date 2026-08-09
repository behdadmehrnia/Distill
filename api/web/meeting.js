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
    this._lastDebug = null;
    this._prevGroupSnapshot = [];
    this._editingSegmentId = null;
    this._editingDraft = "";
    this._reviewWizardOpen = false;
    this._reviewStep = null;
    this._confirmedSpeakers = new Set();
    this._speakerList = [];
    this._minutesAttendees = [];
    this._minutesAbsentees = [];
    this._minutesDecisions = [];
    this._minutesSaveTimer = null;
    this._activeSpeakerAudio = null;
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
    this.clearBtn = document.getElementById("clearBtn");
    this.timeline = document.getElementById("timeline");
    this.connectionStatus = document.getElementById("connectionStatus");
    this.statusText = document.getElementById("statusText");
    this.audioLevel = document.getElementById("audioLevel");
    this.audioInput = document.getElementById("audioInput");
    this.meetingTitle = document.getElementById("meetingTitle");
    this.meetingMeta = document.getElementById("meetingMeta");
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

    this.reviewWizard = document.getElementById("reviewWizard");
    this.reviewStepDots = this.reviewWizard
      ? Array.from(this.reviewWizard.querySelectorAll(".review-step-dot"))
      : [];
    this.reviewStepProcessing = document.getElementById("reviewStepProcessing");
    this.reviewStepSpeakers = document.getElementById("reviewStepSpeakers");
    this.reviewStepTranscript = document.getElementById("reviewStepTranscript");
    this.reviewStepMinutes = document.getElementById("reviewStepMinutes");
    this.reviewWizardFoot = document.getElementById("reviewWizardFoot");
    this.closeReviewWizardBtn = document.getElementById("closeReviewWizardBtn");
    this.processingPhaseList = document.getElementById("processingPhaseList");
    this.speakerNamingList = document.getElementById("speakerNamingList");
    this.speakersContinueBtn = document.getElementById("speakersContinueBtn");
    this.transcriptReviewList = document.getElementById("transcriptReviewList");
    this.transcriptContinueBtn = document.getElementById("transcriptContinueBtn");
    this.minutesLoading = document.getElementById("minutesLoading");
    this.minutesForm = document.getElementById("minutesForm");
    this.minutesSubject = document.getElementById("minutesSubject");
    this.minutesDate = document.getElementById("minutesDate");
    this.minutesLocation = document.getElementById("minutesLocation");
    this.minutesSecretary = document.getElementById("minutesSecretary");
    this.minutesAttendeesList = document.getElementById("minutesAttendeesList");
    this.minutesAttendeesInput = document.getElementById("minutesAttendeesInput");
    this.minutesAbsenteesList = document.getElementById("minutesAbsenteesList");
    this.minutesAbsenteesInput = document.getElementById("minutesAbsenteesInput");
    this.minutesSummary = document.getElementById("minutesSummary");
    this.minutesDecisionsEl = document.getElementById("minutesDecisions");
    this.minutesSaveStatus = document.getElementById("minutesSaveStatus");
    this.reopenReviewBtn = document.getElementById("reopenReviewBtn");

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
      this.updateReviewAvailability();
      return;
    }
    const meeting = await res.json();
    this.meetingId = meeting.id;
    this.meetingStatus = meeting.status || "stopped";
    this.speakerMap = { ...(meeting.speaker_map || {}) };
    this.setHasRecording(!!meeting.has_recording);
    if (this.meetingTitle) {
      this.meetingTitle.value = meeting.title || "";
      this.meetingTitle.classList.remove("field-invalid");
    }
    this.setMeetingMeta();
    this.setSessionUrl(meeting.id);
    await this.refreshTranscript();

    const status = meeting.status || "stopped";
    this.meetingStatus = status;
    if (status === "recording") {
      this.setStatus("recording", "جلسه در حال ضبط (تاریخچه بارگذاری شد)");
    } else if (status === "processing") {
      this.setStatus("processing", "در حال پردازش");
    } else {
      this.setStatus("connected", "تاریخچه جلسه بارگذاری شد");
    }
    this.updateReviewAvailability();
  }

  bindEvents() {
    this.startBtn.addEventListener("click", () => this.startLive());
    this.stopBtn.addEventListener("click", () => this.stopLive());
    this.uploadBtn.addEventListener("click", () => this.uploadRecording());
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

    if (this.closeReviewWizardBtn) {
      this.closeReviewWizardBtn.addEventListener("click", () => this.closeReviewWizard());
    }
    if (this.reviewWizard) {
      this.reviewWizard.addEventListener("click", (ev) => {
        if (ev.target === this.reviewWizard) this.closeReviewWizard();
      });
    }
    if (this.speakersContinueBtn) {
      this.speakersContinueBtn.addEventListener("click", () => this.onSpeakersContinue());
    }
    if (this.transcriptContinueBtn) {
      this.transcriptContinueBtn.addEventListener("click", () => this.onTranscriptContinue());
    }
    if (this.transcriptReviewList) {
      this.transcriptReviewList.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-speaker-id]");
        if (btn) {
          ev.preventDefault();
          this.renameSpeaker(btn.getAttribute("data-speaker-id"));
        }
      });
    }
    if (this.speakerNamingList) {
      this.speakerNamingList.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".speaker-play-btn");
        if (btn) this.toggleSpeakerSample(btn);
      });
    }
    if (this.reopenReviewBtn) {
      this.reopenReviewBtn.addEventListener("click", () => this.openExistingMinutes());
    }
    const minutesAttendeesAdd = document.getElementById("minutesAttendeesAddBtn");
    if (minutesAttendeesAdd) {
      minutesAttendeesAdd.addEventListener("click", () => this.addMinutesChip("attendees"));
    }
    if (this.minutesAttendeesInput) {
      this.minutesAttendeesInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          this.addMinutesChip("attendees");
        }
      });
    }
    const minutesAbsenteesAdd = document.getElementById("minutesAbsenteesAddBtn");
    if (minutesAbsenteesAdd) {
      minutesAbsenteesAdd.addEventListener("click", () => this.addMinutesChip("absentees"));
    }
    if (this.minutesAbsenteesInput) {
      this.minutesAbsenteesInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          this.addMinutesChip("absentees");
        }
      });
    }
    const minutesAddDecision = document.getElementById("minutesAddDecisionBtn");
    if (minutesAddDecision) {
      minutesAddDecision.addEventListener("click", () => this.addMinutesDecisionRow());
    }
    const minutesSave = document.getElementById("minutesSaveBtn");
    if (minutesSave) {
      minutesSave.addEventListener("click", () => this.saveMinutes());
    }
    const minutesRegenerate = document.getElementById("minutesRegenerateBtn");
    if (minutesRegenerate) {
      minutesRegenerate.addEventListener("click", () => this.generateMinutes());
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
      this.recordTimer.classList.remove("idle", "is-processing");
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
      this.recordTimer.classList.remove("is-processing");
      this.recordTimer.classList.add("idle");
    }
  }

  setRecordingControls({ recording = false, processing = false } = {}) {
    if (!this.startBtn || !this.stopBtn) return;
    if (processing) {
      this.startBtn.classList.add("hidden");
      this.stopBtn.classList.remove("hidden");
      this.stopBtn.disabled = true;
      this.stopBtn.classList.add("is-processing");
      this.stopBtn.textContent = "در حال پردازش…";
      if (this.recordTimer) {
        this.recordTimer.classList.remove("idle");
        this.recordTimer.classList.add("is-processing");
      }
      return;
    }
    this.stopBtn.disabled = false;
    this.stopBtn.classList.remove("is-processing");
    this.stopBtn.textContent = "توقف";
    if (this.recordTimer) this.recordTimer.classList.remove("is-processing");
    if (recording) {
      this.startBtn.classList.add("hidden");
      this.stopBtn.classList.remove("hidden");
    } else {
      this.startBtn.classList.remove("hidden");
      this.stopBtn.classList.add("hidden");
    }
  }

  setMeetingMeta() {
    if (!this.meetingMeta) return;
    if (!this.meetingId) {
      this.meetingMeta.textContent = "جلسه‌ای انتخاب نشده";
      this.updateReviewAvailability();
      return;
    }
    this.meetingMeta.innerHTML =
      `شناسه جلسه: <button type="button" class="session-link" data-copy-session title="کلیک برای کپی لینک جلسه">${this.escape(this.meetingId)}</button>`;
    this.updateReviewAvailability();
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

  updateReviewAvailability() {
    if (!this.reopenReviewBtn) return;
    const canShow =
      !!this.meetingId &&
      this.meetingStatus === "stopped" &&
      !this.isRecording &&
      this.hasTranscriptContext();
    this.reopenReviewBtn.classList.toggle("hidden", !canShow);
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
      this.setHasRecording(false);
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);

      await this.connectWebSocket(this.meetingId);
      await this.startMic();
      this.isRecording = true;
      this.setRecordingControls({ recording: true });
      this.setStatus("recording", "در حال ضبط");
      this.audioLevel.classList.remove("hidden");
      this.startTimer();
      if (this.levelMeter) this.levelMeter.classList.add("active");
      this.updateReviewAvailability();
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
      this.setRecordingControls({ recording: false });
      this.audioLevel.classList.add("hidden");
      this.stopTimer(false);
      if (this.levelMeter) this.levelMeter.classList.remove("active");
      this.updateReviewAvailability();

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
      this.stopTimer(false);
      this.setRecordingControls({ processing: true });
      this.audioLevel.classList.add("hidden");
      if (this.levelMeter) this.levelMeter.classList.remove("active");
      this.updateReviewAvailability();
      this.openReviewWizard();
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
        if (hasText) {
          await this.advanceToSpeakerStep();
        } else {
          this.closeReviewWizard(true);
        }
      }
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "خطا در توقف");
      this.closeReviewWizard(true);
    } finally {
      if (this.ws) {
        try { this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      this.setRecordingControls({ recording: false });
      this.audioLevel.classList.add("hidden");
      this.stopTimer(false);
      if (this.levelMeter) this.levelMeter.classList.remove("active");
      this.updateReviewAvailability();
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
      } else if (msg.type === "segment_update" && Array.isArray(msg.segments)) {
        msg.segments.forEach((s) => this.segments.set(s.id, s));
        if (
          !this._editingSegmentId ||
          !msg.segments.some((s) => s.id === this._editingSegmentId)
        ) {
          this.renderTimeline();
          this._prevGroupSnapshot = this.buildTimelineGroups(
            Array.from(this.segments.values()).filter((s) => (s.text || "").trim())
          ).map((g) => this.snapshotGroup(g));
        }
      } else if (msg.type === "status") {
        if (msg.status) this.meetingStatus = msg.status;
        if (msg.status === "processing") {
          this.setStatus("processing", "در حال پردازش");
          if (msg.phase) this.updateProcessingPhase(msg.phase);
        }
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
        this.updateReviewAvailability();
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
      } else if (msg.type === "error") {
        console.error(msg.message);
        this.setStatus("processing", `خطا: ${String(msg.message || "").slice(0, 80)}`);
      }
    } catch (err) {
      console.error(err);
    }
  }

  replaceTranscript(segments) {
    if (this._editingSegmentId) {
      // Avoid wiping an in-progress edit; sync after blur/save.
      const nextList = (segments || []).filter((s) => (s.text || "").trim());
      this.segments.clear();
      nextList.forEach((s) => this.segments.set(s.id, s));
      return;
    }
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
    this.updateReviewAvailability();
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
      const isEditable = !isLive;
      row.className = g.type === "overlap" ? "segment segment-overlap" : "segment";
      if (isLive) row.classList.add("segment-live");
      if (isActive) row.classList.add("segment-listening");
      if (isEditable) row.classList.add("segment-finalized", "segment-editable");
      if (polishKeys.has(key)) row.classList.add("segment-polish");
      row.dataset.groupKey = key;
      row.dataset.segmentId = g.seg.id;
      row.dataset.editable = isEditable ? "1" : "0";

      let statusBadge = "";
      if (isActive) {
        statusBadge =
          '<span class="badge badge-active">فعال</span>' +
          '<span class="badge badge-locked">درحال پردازش متن فعال (غیر قابل ویرایش)</span>';
      } else if (isLive) {
        statusBadge = '<span class="badge badge-muted">موقت</span>';
      } else if (polishKeys.has(key)) {
        statusBadge = '<span class="badge badge-polish">پالیش‌شده</span>';
      } else {
        statusBadge = '<span class="badge badge-editable">قابل ویرایش</span>';
      }
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
      const editHint = isEditable
        ? '<span class="segment-edit-hint" aria-hidden="true">ویرایش</span>'
        : "";

      row.innerHTML = `
        <div class="segment-meta">
          <div class="speaker-row">${speakerHtml}</div>
          <span>${this.formatTs(g.seg.start_ms)} – ${this.formatTs(g.seg.end_ms)}</span>
          ${badge}${statusBadge}${editHint}
        </div>
        <div class="segment-text" data-role="text"></div>
      `;

      const textEl = row.querySelector('[data-role="text"]');
      const justPolished = polishKeys.has(key);
      this.applyStreamingText(
        textEl,
        g.seg.text || "",
        prev ? prev.text : "",
        justPolished
      );
      if (isEditable) {
        if (justPolished && !this._reduceMotion) {
          window.setTimeout(() => {
            if (!textEl.isConnected) return;
            textEl.textContent = g.seg.text || "";
            this.enableSegmentEditing(textEl, g.seg);
          }, 920);
        } else {
          this.enableSegmentEditing(textEl, g.seg);
        }
      }

      row.querySelectorAll("[data-speaker-id]").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          this.renameSpeaker(btn.getAttribute("data-speaker-id"));
        });
      });
      this.timeline.appendChild(row);
    });
    this.timeline.scrollTop = this.timeline.scrollHeight;
    this.updateReviewAvailability();
  }

  enableSegmentEditing(textEl, seg) {
    if (!textEl || !seg || !seg.id) return;
    textEl.contentEditable = "true";
    textEl.spellcheck = true;
    textEl.setAttribute("role", "textbox");
    textEl.setAttribute("aria-label", "ویرایش متن بخش");
    textEl.setAttribute("data-segment-id", seg.id);
    textEl.title = "برای ویرایش کلیک کنید — Enter برای ذخیره، Esc برای انصراف";

    textEl.addEventListener("focus", () => {
      this._editingSegmentId = seg.id;
      this._editingDraft = textEl.innerText || "";
      textEl.closest(".segment")?.classList.add("segment-editing");
    });

    textEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        textEl.textContent = this._editingDraft;
        textEl.blur();
        return;
      }
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        textEl.blur();
      }
    });

    textEl.addEventListener("blur", () => {
      textEl.closest(".segment")?.classList.remove("segment-editing");
      const next = (textEl.innerText || "").trim();
      const prev = (this._editingDraft || "").trim();
      const editingId = this._editingSegmentId;
      this._editingSegmentId = null;
      this._editingDraft = "";
      if (!editingId || editingId !== seg.id) return;
      if (!next) {
        textEl.textContent = prev || seg.text || "";
        this.renderTimeline();
        return;
      }
      if (next === prev) {
        this.renderTimeline();
        return;
      }
      this.saveSegmentText(seg.id, next, textEl, prev)
        .then(() => this.renderTimeline())
        .catch((err) => {
          console.error(err);
          textEl.textContent = prev;
          alert(`ذخیره ویرایش ناموفق: ${err.message}`);
        });
    });
  }

  async saveSegmentText(segmentId, text, textEl, fallback) {
    if (!this.meetingId || !segmentId) return;
    textEl?.closest(".segment")?.classList.add("segment-saving");
    try {
      const res = await fetch(
        `/meetings/${this.meetingId}/segments/${encodeURIComponent(segmentId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        }
      );
      if (!res.ok) throw new Error(await res.text());
      const body = await res.json();
      (body.segments || []).forEach((s) => this.segments.set(s.id, s));
      const local = this.segments.get(segmentId);
      if (local) local.text = text;
      if (textEl) textEl.textContent = text;
      this._prevGroupSnapshot = this.buildTimelineGroups(
        Array.from(this.segments.values()).filter((s) => (s.text || "").trim())
      ).map((g) => this.snapshotGroup(g));
    } catch (err) {
      if (textEl && fallback != null) textEl.textContent = fallback;
      throw err;
    } finally {
      textEl?.closest(".segment")?.classList.remove("segment-saving");
    }
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
    const mapped = this.speakerMap[speakerId];
    if (mapped) return mapped;
    return this.defaultSpeakerLabel(speakerId);
  }

  defaultSpeakerLabel(speakerId) {
    const match = String(speakerId || "").match(/(\d+)\s*$/);
    if (!match) return String(speakerId || "سخنگو");
    const n = parseInt(match[1], 10);
    if (!Number.isFinite(n)) return String(speakerId);
    return `سخنگوی ${this.toPersianDigits(n + 1)}`;
  }

  toPersianDigits(value) {
    return String(value).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]);
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
    const fallback = this.defaultSpeakerLabel(speakerId);
    const current = this.speakerMap[speakerId] || fallback;
    const name = window.prompt(`نام نمایشی برای ${fallback}:`, current === fallback ? "" : current);
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

  openReviewWizard() {
    if (!this.reviewWizard) return;
    this._reviewWizardOpen = true;
    this.reviewWizard.classList.remove("hidden");
    this.resetProcessingPhases();
    this.setReviewStep("processing");
  }

  closeReviewWizard(force = false) {
    if (!force && this._reviewStep !== "minutes") return;
    this._reviewWizardOpen = false;
    this.stopSpeakerSamplePlayback();
    if (this.reviewWizard) this.reviewWizard.classList.add("hidden");
  }

  setReviewStep(step) {
    this._reviewStep = step;
    const map = {
      processing: this.reviewStepProcessing,
      speakers: this.reviewStepSpeakers,
      transcript: this.reviewStepTranscript,
      minutes: this.reviewStepMinutes,
    };
    Object.entries(map).forEach(([key, el]) => {
      if (el) el.classList.toggle("active", key === step);
    });
    const order = ["processing", "speakers", "transcript", "minutes"];
    const idx = order.indexOf(step);
    this.reviewStepDots.forEach((dot) => {
      const dotStep = dot.getAttribute("data-step");
      const dotIdx = order.indexOf(dotStep);
      dot.classList.toggle("is-active", dotStep === step);
      dot.classList.toggle("is-done", dotIdx >= 0 && dotIdx < idx);
    });
    // User cannot exit the wizard until the final (minutes) step is reached.
    if (this.reviewWizardFoot) {
      this.reviewWizardFoot.classList.toggle("hidden", step !== "minutes");
    }
  }

  resetProcessingPhases() {
    if (!this.processingPhaseList) return;
    this.processingPhaseList.querySelectorAll("li").forEach((li) => {
      li.classList.remove("is-active", "is-done");
    });
  }

  updateProcessingPhase(phase) {
    if (!this.processingPhaseList) return;
    const items = Array.from(this.processingPhaseList.querySelectorAll("li"));
    const idx = items.findIndex((li) => li.getAttribute("data-phase") === phase);
    if (idx === -1) return;
    items.forEach((li, i) => {
      li.classList.toggle("is-done", i < idx);
      li.classList.toggle("is-active", i === idx);
    });
  }

  markProcessingDone() {
    if (!this.processingPhaseList) return;
    this.processingPhaseList.querySelectorAll("li").forEach((li) => {
      li.classList.remove("is-active");
      li.classList.add("is-done");
    });
  }

  async advanceToSpeakerStep() {
    if (!this._reviewWizardOpen) return;
    this.markProcessingDone();
    this.setReviewStep("speakers");
    await this.loadSpeakerNamingStep();
  }

  stopSpeakerSamplePlayback() {
    if (this._activeSpeakerAudio) {
      try {
        this._activeSpeakerAudio.pause();
      } catch (_) {}
      this._activeSpeakerAudio = null;
    }
  }

  toggleSpeakerSample(btn) {
    const src = btn.getAttribute("data-audio-src");
    if (!src) return;
    if (!btn._audio) {
      const audio = new Audio(src);
      audio.preload = "none";
      audio.addEventListener("timeupdate", () => {
        const pct = audio.duration ? audio.currentTime / audio.duration : 0;
        btn.style.setProperty("--progress", `${Math.min(1, Math.max(0, pct)) * 360}deg`);
      });
      audio.addEventListener("play", () => {
        btn.classList.add("is-playing");
      });
      audio.addEventListener("pause", () => {
        btn.classList.remove("is-playing");
      });
      audio.addEventListener("ended", () => {
        btn.classList.remove("is-playing");
        btn.style.setProperty("--progress", "0deg");
      });
      btn._audio = audio;
    }
    const audio = btn._audio;
    if (this._activeSpeakerAudio && this._activeSpeakerAudio !== audio) {
      this._activeSpeakerAudio.pause();
    }
    if (audio.paused) {
      audio.currentTime = audio.ended ? 0 : audio.currentTime;
      audio.play().catch((err) => console.error(err));
      this._activeSpeakerAudio = audio;
    } else {
      audio.pause();
    }
  }

  async loadSpeakerNamingStep() {
    if (!this.meetingId || !this.speakerNamingList) return;
    this.stopSpeakerSamplePlayback();
    this._confirmedSpeakers = new Set();
    this.speakerNamingList.innerHTML = '<p class="review-hint">در حال بارگذاری سخنگوها…</p>';
    if (this.speakersContinueBtn) this.speakersContinueBtn.disabled = true;
    try {
      const res = await fetch(`/meetings/${this.meetingId}/speakers`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this._speakerList = data.speakers || [];
      this.renderSpeakerNamingList();
    } catch (err) {
      console.error(err);
      this.speakerNamingList.innerHTML =
        '<p class="review-hint">دریافت لیست سخنگوها ناموفق بود.</p>';
    }
  }

  renderSpeakerNamingList() {
    if (!this.speakerNamingList) return;
    this.speakerNamingList.innerHTML = "";
    if (!this._speakerList.length) {
      this.speakerNamingList.innerHTML =
        '<p class="review-hint">سخنگویی شناسایی نشد؛ می‌توانید مستقیم ادامه دهید.</p>';
      if (this.speakersContinueBtn) this.speakersContinueBtn.disabled = false;
      return;
    }
    this._speakerList.forEach((spk) => {
      const card = document.createElement("div");
      card.className = "speaker-naming-card";
      card.dataset.speakerId = spk.id;
      const color = this.speakerColor(spk.id);
      const audioSrc = `/meetings/${this.meetingId}/speakers/${encodeURIComponent(spk.id)}/audio`;
      const audioHtml = spk.has_sample
        ? `<button type="button" class="speaker-play-btn" data-audio-src="${this.escape(audioSrc)}" aria-label="پخش نمونه صدا">
            <svg class="play-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
            <svg class="pause-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>
          </button>`
        : '<span class="speaker-naming-status">نمونه صدایی موجود نیست</span>';
      card.innerHTML = `
        <span class="speaker-naming-dot" style="background:${color}"></span>
        <input
          type="text"
          class="speaker-naming-name"
          value="${this.escape(spk.custom_label || "")}"
          placeholder="${this.escape(spk.label)}"
        />
        ${audioHtml}
        <span class="speaker-naming-status">${spk.custom_label ? "ثبت شد" : "در انتظار نام"}</span>
      `;
      if (spk.custom_label) {
        card.classList.add("is-confirmed");
        this._confirmedSpeakers.add(spk.id);
      }
      const input = card.querySelector(".speaker-naming-name");
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          input.blur();
        }
      });
      input.addEventListener("blur", () => {
        this.confirmSpeakerName(spk.id, input, card);
      });
      this.speakerNamingList.appendChild(card);
    });
    this.updateSpeakersContinueState();
  }

  async confirmSpeakerName(speakerId, input, card) {
    const value = (input.value || "").trim();
    const statusEl = card.querySelector(".speaker-naming-status");
    if (!value) {
      this._confirmedSpeakers.delete(speakerId);
      card.classList.remove("is-confirmed");
      if (statusEl) statusEl.textContent = "در انتظار نام";
      this.updateSpeakersContinueState();
      return;
    }
    try {
      const res = await fetch(`/meetings/${this.meetingId}/speakers`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [speakerId]: value }),
      });
      if (!res.ok) throw new Error(await res.text());
      const meeting = await res.json();
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      this._confirmedSpeakers.add(speakerId);
      card.classList.add("is-confirmed");
      if (statusEl) statusEl.textContent = "ثبت شد";
      this.renderTimeline();
    } catch (err) {
      console.error(err);
      if (statusEl) statusEl.textContent = "خطا در ذخیره";
    }
    this.updateSpeakersContinueState();
  }

  updateSpeakersContinueState() {
    if (!this.speakersContinueBtn) return;
    const allConfirmed = this._speakerList.every((s) => this._confirmedSpeakers.has(s.id));
    this.speakersContinueBtn.disabled = !allConfirmed;
  }

  async onSpeakersContinue() {
    this.setReviewStep("transcript");
    await this.loadTranscriptReviewStep();
  }

  async loadTranscriptReviewStep() {
    if (!this.transcriptReviewList) return;
    this.transcriptReviewList.innerHTML = '<p class="review-hint">در حال بارگذاری متن…</p>';
    try {
      await this.refreshTranscript();
    } catch (err) {
      console.error(err);
    }
    this.renderTranscriptReviewList();
  }

  renderTranscriptReviewList() {
    if (!this.transcriptReviewList) return;
    const list = Array.from(this.segments.values()).filter(
      (s) => !s.provisional && (s.text || "").trim()
    );
    const groups = this.buildTimelineGroups(list);
    this.transcriptReviewList.innerHTML = "";
    if (!groups.length) {
      this.transcriptReviewList.innerHTML =
        '<p class="review-hint">متنی برای نمایش وجود ندارد.</p>';
      return;
    }
    groups.forEach((g) => {
      const row = document.createElement("div");
      row.className = "transcript-review-row";
      row.dataset.segmentId = g.seg.id;
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
      row.innerHTML = `
        <div class="transcript-review-meta">
          <span class="speaker-row">${speakerHtml}</span>
          <span>${this.formatTs(g.seg.start_ms)} – ${this.formatTs(g.seg.end_ms)}</span>
        </div>
        <div class="transcript-review-text" contenteditable="true" spellcheck="true"></div>
      `;
      const textEl = row.querySelector(".transcript-review-text");
      textEl.textContent = g.seg.text || "";
      this.bindTranscriptReviewEditing(textEl, g.seg, row);
      this.transcriptReviewList.appendChild(row);
    });
  }

  bindTranscriptReviewEditing(textEl, seg, row) {
    let draft = "";
    textEl.addEventListener("focus", () => {
      draft = textEl.innerText || "";
    });
    textEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        textEl.textContent = draft;
        textEl.blur();
        return;
      }
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        textEl.blur();
      }
    });
    textEl.addEventListener("blur", () => {
      const next = (textEl.innerText || "").trim();
      const prev = draft.trim();
      if (!next) {
        textEl.textContent = prev || seg.text || "";
        return;
      }
      if (next === prev) {
        textEl.textContent = next;
        return;
      }
      row.classList.add("is-saving");
      this.saveSegmentText(seg.id, next, textEl, prev)
        .then(() => this.renderTimeline())
        .catch((err) => {
          console.error(err);
          alert(`ذخیره ویرایش ناموفق: ${err.message}`);
        })
        .finally(() => row.classList.remove("is-saving"));
    });
  }

  async onTranscriptContinue() {
    this.setReviewStep("minutes");
    await this.generateMinutes();
  }

  showMinutesLoading(loading) {
    if (this.minutesLoading) this.minutesLoading.classList.toggle("hidden", !loading);
    if (this.minutesForm) this.minutesForm.classList.toggle("hidden", loading);
  }

  async generateMinutes() {
    if (!this.meetingId) return;
    this.showMinutesLoading(true);
    try {
      const res = await fetch(`/meetings/${this.meetingId}/minutes/generate`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.renderMinutesForm(data);
    } catch (err) {
      console.error(err);
      alert(`تولید صورت جلسه ناموفق بود؛ فرم خالی برای تکمیل دستی نمایش داده می‌شود.\n${err.message}`);
      this.renderMinutesForm({
        subject: "",
        meeting_date: "",
        location: "",
        secretary: "",
        summary: "",
        attendees: Object.values(this.speakerMap || {}),
        absentees: [],
        decisions: [],
      });
    } finally {
      this.showMinutesLoading(false);
    }
  }

  renderMinutesForm(data) {
    this._minutesAttendees = [...(data.attendees || [])];
    this._minutesAbsentees = [...(data.absentees || [])];
    this._minutesDecisions = (data.decisions || []).map((d) => ({
      id: d.id || `d-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      description: d.description || "",
      executor: d.executor || "",
      due_date: d.due_date || "",
      status: d.status || "pending",
    }));
    if (this.minutesSubject) this.minutesSubject.value = data.subject || "";
    if (this.minutesDate) this.minutesDate.value = data.meeting_date || "";
    if (this.minutesLocation) this.minutesLocation.value = data.location || "";
    if (this.minutesSecretary) this.minutesSecretary.value = data.secretary || "";
    if (this.minutesSummary) this.minutesSummary.value = data.summary || "";
    this.renderMinutesChipList("attendees");
    this.renderMinutesChipList("absentees");
    this.renderMinutesDecisions();
    if (this.minutesSaveStatus) this.minutesSaveStatus.classList.add("hidden");
  }

  renderMinutesChipList(kind) {
    const listEl = kind === "attendees" ? this.minutesAttendeesList : this.minutesAbsenteesList;
    const arr = kind === "attendees" ? this._minutesAttendees : this._minutesAbsentees;
    if (!listEl) return;
    listEl.innerHTML = "";
    arr.forEach((name, idx) => {
      const chip = document.createElement("span");
      chip.className = "minutes-chip";
      chip.innerHTML = `<span>${this.escape(name)}</span><button type="button" aria-label="حذف">×</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        arr.splice(idx, 1);
        this.renderMinutesChipList(kind);
      });
      listEl.appendChild(chip);
    });
  }

  addMinutesChip(kind) {
    const inputEl = kind === "attendees" ? this.minutesAttendeesInput : this.minutesAbsenteesInput;
    const arr = kind === "attendees" ? this._minutesAttendees : this._minutesAbsentees;
    const value = (inputEl?.value || "").trim();
    if (!value) return;
    if (!arr.includes(value)) arr.push(value);
    if (inputEl) inputEl.value = "";
    this.renderMinutesChipList(kind);
  }

  renderMinutesDecisions() {
    if (!this.minutesDecisionsEl) return;
    this.minutesDecisionsEl.innerHTML = "";
    if (!this._minutesDecisions.length) {
      this.minutesDecisionsEl.innerHTML =
        '<p class="review-hint">مصوبه‌ای ثبت نشده — با «افزودن ردیف» یکی اضافه کنید.</p>';
      return;
    }
    this._minutesDecisions.forEach((d) => {
      const row = document.createElement("div");
      row.className = "minutes-decision-row";
      row.dataset.decisionId = d.id;
      row.innerHTML = `
        <textarea rows="2" data-field="description" placeholder="شرح مصوبه / پیگیری">${this.escape(d.description)}</textarea>
        <input type="text" data-field="executor" placeholder="مجری" value="${this.escape(d.executor)}" />
        <input type="text" data-field="due_date" placeholder="سررسید" value="${this.escape(d.due_date)}" />
        <select data-field="status">
          <option value="pending"${d.status === "pending" ? " selected" : ""}>در انتظار</option>
          <option value="done"${d.status === "done" ? " selected" : ""}>انجام‌شده</option>
        </select>
        <button type="button" class="minutes-decision-remove">حذف</button>
      `;
      row.querySelectorAll("[data-field]").forEach((fieldEl) => {
        const field = fieldEl.getAttribute("data-field");
        const eventName = fieldEl.tagName === "SELECT" ? "change" : "input";
        fieldEl.addEventListener(eventName, () => {
          d[field] = fieldEl.value;
        });
      });
      row.querySelector(".minutes-decision-remove").addEventListener("click", () => {
        this._minutesDecisions = this._minutesDecisions.filter((x) => x.id !== d.id);
        this.renderMinutesDecisions();
      });
      this.minutesDecisionsEl.appendChild(row);
    });
  }

  addMinutesDecisionRow() {
    this._minutesDecisions.push({
      id: `d-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      description: "",
      executor: "",
      due_date: "",
      status: "pending",
    });
    this.renderMinutesDecisions();
  }

  async saveMinutes() {
    if (!this.meetingId) return;
    const payload = {
      subject: (this.minutesSubject?.value || "").trim(),
      meeting_date: (this.minutesDate?.value || "").trim(),
      location: (this.minutesLocation?.value || "").trim(),
      secretary: (this.minutesSecretary?.value || "").trim(),
      summary: (this.minutesSummary?.value || "").trim(),
      attendees: [...this._minutesAttendees],
      absentees: [...this._minutesAbsentees],
      decisions: this._minutesDecisions
        .map((d) => ({
          id: d.id,
          description: (d.description || "").trim(),
          executor: (d.executor || "").trim(),
          due_date: (d.due_date || "").trim(),
          status: d.status || "pending",
        }))
        .filter((d) => d.description),
    };
    try {
      const res = await fetch(`/meetings/${this.meetingId}/minutes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.renderMinutesForm(data);
      if (this.minutesSaveStatus) {
        this.minutesSaveStatus.textContent = "ذخیره شد";
        this.minutesSaveStatus.classList.remove("hidden");
        clearTimeout(this._minutesSaveTimer);
        this._minutesSaveTimer = setTimeout(() => {
          this.minutesSaveStatus.classList.add("hidden");
        }, 2500);
      }
      this.updateReviewAvailability();
    } catch (err) {
      console.error(err);
      alert(`ذخیره صورت جلسه ناموفق: ${err.message}`);
    }
  }

  async openExistingMinutes() {
    if (!this.meetingId) return;
    this.openReviewWizard();
    this.markProcessingDone();
    this.setReviewStep("minutes");
    this.showMinutesLoading(true);
    try {
      const res = await fetch(`/meetings/${this.meetingId}/minutes`);
      if (res.ok) {
        const data = await res.json();
        this.renderMinutesForm(data);
        this.showMinutesLoading(false);
      } else {
        this.setReviewStep("speakers");
        await this.loadSpeakerNamingStep();
      }
    } catch (err) {
      console.error(err);
      this.showMinutesLoading(false);
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

  escape(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.distillClient = new DistillClient();
});
