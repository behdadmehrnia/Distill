// Distill client
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
    this._captureSource = null; // "live" | "upload" | null
    this._reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Low-saturation tints: distinguishable per speaker, still reads
    // monochrome against the near-black ground. Mirrors shared.js.
    this.speakerColors = [
      "#e8e8e8",
      "#9fb4d0",
      "#d0b9a8",
      "#a8c4b4",
      "#c2b0d0",
      "#d0a8a8",
      "#a8c0c8",
      "#c8c49f",
    ];

    this.startBtn = document.getElementById("startBtn");
    this.stopBtn = document.getElementById("stopBtn");
    this.uploadBtn = document.getElementById("uploadBtn");
    this.uploadFile = document.getElementById("uploadFile");
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
    this._confirmAlertOnly = false;
    this._promptQueue = [];
    this._promptShowing = false;
    this._fallbackPromptShown = false;
    this._speakersContinueBusy = false;
    this._processingAbortController = null;

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
    this.showUploadMode = document.getElementById("showUploadMode");
    this.showLiveMode = document.getElementById("showLiveMode");

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
    this.setStatus("processing", "Loading meeting…");
    const res = await fetch(`/meetings/${meetingId}`);
    if (!res.ok) {
      this.setStatus("disconnected", "Meeting not found");
      if (this.meetingMeta) {
        this.meetingMeta.textContent = `Meeting not found: ${meetingId}`;
      }
      this.updateReviewAvailability();
      return;
    }
    const meeting = await res.json();
    this.meetingId = meeting.id;
    this.meetingStatus = meeting.status || "stopped";
    this.speakerMap = { ...(meeting.speaker_map || {}) };
    this.setHasRecording(!!meeting.has_recording);
    this.setCaptureSource(this.inferCaptureSource(meeting));
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
      this.setStatus("recording", "Recording");
      this.setRecordingControls({ recording: true });
    } else if (status === "processing") {
      this.setStatus("processing", "Processing");
      this.setRecordingControls({ processing: true });
    } else if (status === "created") {
      this.setStatus("connected", "Ready");
    } else {
      this.setStatus("connected", "Ready");
    }
    this.updateReviewAvailability();

    if (status === "stopped" && this.hasTranscriptContext()) {
      try {
        const minutesRes = await fetch(`/meetings/${this.meetingId}/minutes`);
        if (minutesRes.ok) {
          await this.openExistingMinutes();
        }
      } catch (err) {
        console.error(err);
      }
    }
  }

  onPageHide(ev) {
    // bfcache (back/forward) — page may return; do not finalize.
    if (ev && ev.persisted) return;
    if (!this.meetingId) return;
    // Refresh/navigation is not an explicit cancel. Finalize live capture when
    // possible; otherwise let server-side processing finish and heal on reload.
    if (this.isRecording) {
      this.isRecording = false;
      this.finalizeOnLeave();
      return;
    }
    if (this._processingAbortController) {
      try {
        this._processingAbortController.abort();
      } catch (_) {}
    }
    try {
      if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
      }
    } catch (_) {}
    this.ws = null;
  }

  shouldCancelOnLeave() {
    return false;
  }

  startProcessingRequest() {
    if (this._processingAbortController) {
      try {
        this._processingAbortController.abort();
      } catch (_) {}
    }
    this._processingAbortController = new AbortController();
    return this._processingAbortController.signal;
  }

  endProcessingRequest() {
    this._processingAbortController = null;
  }

  isLeaveAbort(err) {
    return !!(err && (err.name === "AbortError" || err.code === 20));
  }

  finalizeOnLeave() {
    if (!this.meetingId) return;
    try {
      if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
      }
    } catch (_) {}
    this.ws = null;
    try {
      fetch(`/meetings/${this.meetingId}/stop`, { method: "POST", keepalive: true });
    } catch (_) {}
  }

  cancelProcessingOnLeave() {
    if (!this.meetingId) return;
    if (this._processingAbortController) {
      try {
        this._processingAbortController.abort();
      } catch (_) {}
    }
    try {
      if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
      }
    } catch (_) {}
    this.ws = null;
    try {
      fetch(`/meetings/${this.meetingId}/cancel`, { method: "POST", keepalive: true });
    } catch (_) {}
  }

  mergeAbortSignals(...signals) {
    const active = signals.filter(Boolean);
    if (!active.length) return undefined;
    if (typeof AbortSignal.any === "function") return AbortSignal.any(active);
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    active.forEach((signal) => {
      if (signal.aborted) controller.abort();
      else signal.addEventListener("abort", onAbort, { once: true });
    });
    return controller.signal;
  }

  bindEvents() {
    this.startBtn.addEventListener("click", () => this.startLive());
    this.stopBtn.addEventListener("click", () => this.stopLive());
    this.uploadBtn.addEventListener("click", () => this.uploadRecording());
    // Tab close / refresh: finalize live capture when possible; do not cancel.
    window.addEventListener("pagehide", (ev) => this.onPageHide(ev));
    window.addEventListener("beforeunload", () => this.onPageHide());
    if (this.confirmCancelBtn) {
      this.confirmCancelBtn.addEventListener("click", () => this.resolveConfirm(false));
    }
    if (this.confirmOkBtn) {
      this.confirmOkBtn.addEventListener("click", () => this.resolveConfirm(true));
    }
    if (this.confirmPanel) {
      this.confirmPanel.addEventListener("click", (ev) => {
        if (ev.target === this.confirmPanel) {
          this.resolveConfirm(this._confirmAlertOnly ? true : false);
        }
      });
    }
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && this.confirmPanel && !this.confirmPanel.classList.contains("hidden")) {
        this.resolveConfirm(this._confirmAlertOnly ? true : false);
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
    if (this.showUploadMode) {
      this.showUploadMode.addEventListener("click", () => this.setSourceMode("upload"));
    }
    if (this.showLiveMode) {
      this.showLiveMode.addEventListener("click", () => this.setSourceMode("live"));
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
      const fireContinue = (ev) => {
        if (ev) {
          ev.preventDefault();
          ev.stopPropagation();
        }
        if (this._speakersContinueBusy) return;
        this.onSpeakersContinue().catch((err) => {
          console.error(err);
          this.showPrompt({
            title: "Could not continue",
            message: `Could not open the transcript review step.\n\n${err.message || err}`,
          });
        });
      };
      // Keep the button HTML-enabled forever; otherwise mobile WebViews swallow clicks.
      this.speakersContinueBtn.disabled = false;
      this.speakersContinueBtn.removeAttribute("disabled");
      this.speakersContinueBtn.addEventListener("click", fireContinue);
    }
    if (this.transcriptContinueBtn) {
      this.transcriptContinueBtn.addEventListener("click", () => {
        this.onTranscriptContinue().catch((err) => {
          console.error(err);
          this.showPrompt({
            title: "Error",
            message: `Could not continue to the minutes step.\n\n${err.message || err}`,
          });
        });
      });
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
        if (!btn || !this.speakerNamingList.contains(btn)) return;
        ev.preventDefault();
        ev.stopPropagation();
        this.toggleSpeakerSample(btn).catch((err) => {
          console.error(err);
          this.showPrompt({
            title: "Sample playback",
            message: `Could not play the sample clip.\n\n${err.message || err}`,
          }).catch(() => {
            window.alert(`Could not play the sample clip.\n\n${err.message || err}`);
          });
        });
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
    const minutesPrint = document.getElementById("minutesPrintBtn");
    if (minutesPrint) {
      minutesPrint.addEventListener("click", () => {
        this.printMinutes().catch((err) => {
          console.error(err);
          alert(`Print failed: ${err.message || err}`);
        });
      });
    }
    const minutesRegenerate = document.getElementById("minutesRegenerateBtn");
    if (minutesRegenerate) {
      minutesRegenerate.addEventListener("click", () => this.generateMinutes());
    }
    this.initDatePicker();
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
      if (item.hidden) return;
      const g = item.group || "Other";
      if (!groups[g]) groups[g] = [];
      groups[g].push(item);
    });
    this.tuningFields.innerHTML = "";
    Object.entries(groups).forEach(([group, items]) => {
      if (!items.length) return;
      const box = document.createElement("div");
      box.className = "tuning-group";
      box.innerHTML = `<h4>${this.escape(group)}</h4>`;
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "tuning-row";
        const applyLabel = item.apply === "live" ? "live" : "next meeting";
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
            ? `<div class="hint next-session-note">Applies to new meetings only, not the current one</div>`
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
      alert(`Could not save settings: ${await res.text()}`);
      return;
    }
    const data = await res.json();
    this._tuningValues = { ...(data.values || {}) };
    this.closeTuning();
    this.setStatus("connected", "Settings applied");
  }

  async resetTuning() {
    const res = await fetch("/tuning/reset", { method: "POST" });
    if (!res.ok) {
      alert(`Reset failed: ${await res.text()}`);
      return;
    }
    const data = await res.json();
    this._tuningSchema = data.schema || this._tuningSchema;
    this._tuningValues = { ...(data.values || {}) };
    this._tuningDefaults = { ...(data.defaults || {}) };
    this.renderTuningFields();
    this.setStatus("connected", "Settings restored to defaults");
  }

  setSourceMode(mode) {
    if (this._captureSource && this._captureSource !== mode) return;
    const isUpload = mode === "upload";
    if (this.liveSource) this.liveSource.classList.toggle("hidden", isUpload);
    if (this.uploadSource) this.uploadSource.classList.toggle("hidden", !isUpload);
  }

  inferCaptureSource(meeting) {
    if (!meeting) return null;
    const status = meeting.status || "";
    if (status === "recording") return "live";
    const path = String(meeting.audio_path || "");
    if (path) {
      const base = path.split("/").pop() || "";
      if (base.startsWith(`${meeting.id}_`)) return "upload";
      if (base === `${meeting.id}.wav` || path.includes("/uploads/")) {
        return path.includes("/uploads/") ? "upload" : "live";
      }
      if (path.includes("/audio/")) return "live";
    }
    return null;
  }

  setCaptureSource(source) {
    this._captureSource = source || null;
    if (this._captureSource === "live") {
      this.setSourceMode("live");
    } else if (this._captureSource === "upload") {
      this.setSourceMode("upload");
    }
    this.applyCaptureSourceLock();
  }

  applyCaptureSourceLock() {
    const source = this._captureSource;
    const liveLocked = source === "upload";
    const uploadLocked = source === "live";
    const uploadBusy =
      source === "upload" &&
      (this.meetingStatus === "processing" || this._reviewWizardOpen);

    if (this.showUploadMode) {
      this.showUploadMode.disabled = uploadLocked;
      this.showUploadMode.classList.toggle("is-locked", uploadLocked);
    }
    if (this.showLiveMode) {
      this.showLiveMode.disabled = liveLocked;
      this.showLiveMode.classList.toggle("is-locked", liveLocked);
    }

    if (source === "live") {
      if (this.liveSource) this.liveSource.classList.remove("hidden", "capture-locked");
      if (this.uploadSource) this.uploadSource.classList.add("hidden");
    } else if (source === "upload") {
      if (this.uploadSource) this.uploadSource.classList.remove("hidden", "capture-locked");
      if (this.liveSource) this.liveSource.classList.add("hidden");
    } else {
      if (this.liveSource) this.liveSource.classList.remove("capture-locked");
      if (this.uploadSource) this.uploadSource.classList.remove("capture-locked");
    }

    if (this.startBtn) this.startBtn.disabled = liveLocked;
    if (this.audioInput) this.audioInput.disabled = liveLocked;
    if (this.uploadBtn) this.uploadBtn.disabled = uploadLocked || uploadBusy;
    if (this.uploadFile) this.uploadFile.disabled = uploadLocked || uploadBusy;
  }

  async loadAudioDevices() {
    try {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((d) => d.kind === "audioinput");
      this.audioInput.innerHTML = '<option value="">System default</option>';
      inputs.forEach((device, idx) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || `Microphone ${idx + 1}`;
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
        "Browsers will not grant microphone access on 0.0.0.0.\n\n" +
        "Open this service at http://localhost:8000 or http://127.0.0.1:8000 instead."
      );
    }
    if (insecure || noApi) {
      return (
        "Microphone access requires localhost or HTTPS.\n\n" +
        `Current origin: ${window.location.origin}\n` +
        "Please open the app at http://localhost:8000."
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
      this.stopBtn.textContent = "Processing…";
      if (this.recordTimer) {
        this.recordTimer.classList.remove("idle");
        this.recordTimer.classList.add("is-processing");
      }
      return;
    }
    this.stopBtn.disabled = false;
    this.stopBtn.classList.remove("is-processing");
    this.stopBtn.textContent = "Stop";
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
      this.meetingMeta.textContent = "No meeting selected";
      this.updateReviewAvailability();
      return;
    }
    this.meetingMeta.innerHTML =
      `Meeting ID: <button type="button" class="session-link" data-copy-session title="Click to copy the meeting link">${this.escape(this.meetingId)}</button>`;
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

  askConfirm({
    title,
    message,
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    alertOnly = false,
  }) {
    return new Promise((resolve) => {
      if (!this.confirmPanel) {
        if (alertOnly) {
          window.alert(message);
          resolve(true);
        } else {
          resolve(window.confirm(message));
        }
        return;
      }
      this._confirmResolver = resolve;
      this._confirmAlertOnly = !!alertOnly;
      if (this.confirmTitle) this.confirmTitle.textContent = title || (alertOnly ? "Notice" : "Confirm");
      if (this.confirmMessage) this.confirmMessage.textContent = message || "";
      if (this.confirmOkBtn) {
        this.confirmOkBtn.textContent = confirmLabel || (alertOnly ? "Got it" : "Confirm");
        this.confirmOkBtn.classList.toggle("btn-danger", !alertOnly);
        this.confirmOkBtn.classList.toggle("btn-primary", !!alertOnly);
      }
      if (this.confirmCancelBtn) {
        this.confirmCancelBtn.textContent = cancelLabel || "Cancel";
        this.confirmCancelBtn.classList.toggle("hidden", !!alertOnly);
      }
      const panel = this.confirmPanel.querySelector(".confirm-panel");
      if (panel) panel.classList.toggle("is-alert", !!alertOnly);
      this.confirmPanel.classList.remove("hidden");
      if (this.confirmOkBtn) this.confirmOkBtn.focus();
    });
  }

  resolveConfirm(ok) {
    if (!this._confirmResolver) return;
    const resolve = this._confirmResolver;
    this._confirmResolver = null;
    this._confirmAlertOnly = false;
    if (this.confirmPanel) this.confirmPanel.classList.add("hidden");
    const panel = this.confirmPanel?.querySelector(".confirm-panel");
    if (panel) panel.classList.remove("is-alert");
    if (this.confirmCancelBtn) this.confirmCancelBtn.classList.remove("hidden");
    if (this.confirmOkBtn) {
      this.confirmOkBtn.classList.add("btn-danger");
      this.confirmOkBtn.classList.remove("btn-primary");
    }
    resolve(!!ok);
    this._drainPromptQueue();
  }

  /** In-app prompt (single OK). Queued so overlapping errors don't stack. */
  showPrompt({ title = "Notice", message, okLabel = "Got it" }) {
    const text = String(message || "").trim() || "An unexpected error occurred.";
    return new Promise((resolve) => {
      this._promptQueue.push({ title, message: text, okLabel, resolve });
      this._drainPromptQueue();
    });
  }

  _drainPromptQueue() {
    if (this._promptShowing || this._confirmResolver) return;
    const next = this._promptQueue.shift();
    if (!next) return;
    this._promptShowing = true;
    this.askConfirm({
      title: next.title,
      message: next.message,
      confirmLabel: next.okLabel,
      alertOnly: true,
    })
      .then((ok) => {
        this._promptShowing = false;
        next.resolve(ok);
        this._drainPromptQueue();
      })
      .catch(() => {
        this._promptShowing = false;
        next.resolve(true);
        this._drainPromptQueue();
      });
  }

  formatErrorDetail(raw, maxLen = 400) {
    return distill.formatErrorDetail(raw, maxLen);
  }

  async fetchWithTimeout(url, options = {}, timeoutMs = 12000) {
    const timeoutController = new AbortController();
    const timer = window.setTimeout(() => timeoutController.abort(), timeoutMs);
    const signal = this.mergeAbortSignals(timeoutController.signal, options.signal);
    try {
      const res = await fetch(url, { ...options, signal });
      return res;
    } catch (err) {
      if (this.isLeaveAbort(err)) throw err;
      if (err && (err.name === "AbortError" || err.code === 20)) {
        throw new Error(`No response from the server after ${Math.round(timeoutMs / 1000)}s`);
      }
      throw err;
    } finally {
      window.clearTimeout(timer);
    }
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
    this.showCopyToast("Meeting link copied");
  }

  showCopyToast(message = "Meeting link copied") {
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
    // Drop ASR event tags like (cough) / (Sound of a car) and keep real words
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
    if (this._captureSource === "upload") return;
    let startedOnServer = false;
    try {
      const title = this.requireMeetingTitle();
      if (!title) return;

      if (!(await this.ensureMicAvailable())) {
        this.setStatus("disconnected", "Microphone unavailable");
        return;
      }

      if (this.hasRecording) {
        const ok = await this.askConfirm({
          title: "Existing recording will be cleared",
          message:
            "This meeting already has a recording. Starting a new one will erase the current audio and transcript. Continue?",
          confirmLabel: "Erase and start",
        });
        if (!ok) return;
      }

      this.pauseRecordingPlayback();

      let meeting;
      if (this.meetingId) {
        const res = await fetch(`/meetings/${this.meetingId}/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reset: !!this.hasRecording, title }),
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
      this.setCaptureSource("live");
      this.setHasRecording(false);
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);

      await this.connectWebSocket(this.meetingId);
      await this.startMic();
      this.isRecording = true;
      this.setRecordingControls({ recording: true });
      this.setStatus("recording", "Recording");
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
      this.setStatus("disconnected", "Could not start");
      await this.showPrompt({
        title: "Could not start meeting",
        message: String(message),
      });
    }
  }

  async stopLive() {
    const snapshotHadText = this.hasTranscriptContext();
    let processError = null;
    const processingSignal = this.startProcessingRequest();
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
        this.setStatus("processing", "Processing…");
        let stopped = null;
        try {
          // Cap wait: server polish has its own budget; don't hang the wizard forever.
          const stopRes = await this.fetchWithTimeout(
            `/meetings/${this.meetingId}/stop`,
            { method: "POST", signal: processingSignal },
            120000
          );
          if (stopRes.status === 409) {
            this.meetingStatus = "created";
            this.closeReviewWizard(true);
            this.setCaptureSource(null);
            this.setStatus("connected", "Processing cancelled");
            return;
          }
          if (stopRes.ok) {
            try {
              stopped = await stopRes.json();
            } catch (_) {}
          } else {
            const detail = this.formatErrorDetail(await stopRes.text());
            processError =
              `Could not process the meeting (status ${stopRes.status}).` +
              (detail ? `\n\n${detail}` : "");
            console.error("stop failed", stopRes.status, detail);
          }
        } catch (stopErr) {
          if (this.isLeaveAbort(stopErr)) return;
          console.error(stopErr);
          processError = `Lost contact with the server while stopping the meeting.\n\n${
            stopErr.message || stopErr
          }`;
        }
        this.meetingStatus = "stopped";
        this.setHasRecording(!!(stopped && stopped.has_recording));
        try {
          await this.refreshTranscript();
        } catch (err) {
          console.error(err);
        }
        // If server returned empty but we had live text, keep the local snapshot.
        if (!this.hasTranscriptContext() && snapshotHadText) {
          console.warn(
            "Post-stop transcript empty while live text existed; keeping local segments"
          );
        }
        try {
          await this.refreshDebug();
        } catch (_) {}
        const hasText = this.hasTranscriptContext() || snapshotHadText;
        if (!hasText) {
          this.setStatus("connected", "Stopped — no transcript from STT");
          if (!processError) {
            processError =
              "Processing finished, but STT did not return usable text.\n\n" +
              "You can carry on naming speakers, record again, or check the service from Debug.";
          }
        } else {
          this.setStatus("connected", "Stopped — ready to analyse");
        }

        if (processError) {
          await this.showPrompt({
            title: "Meeting processing failed",
            message: processError,
            okLabel: "Got it",
          });
        }

        // Always continue the wizard when a meeting was processed — never dump
        // the user back to the idle screen just because STT came back empty.
        try {
          await this.advanceToSpeakerStep();
        } catch (err) {
          console.error(err);
          await this.showPrompt({
            title: "Could not advance",
            message: `Processing finished, but the next step could not be opened.\n\n${
              err.message || err
            }`,
          });
        }
      }
    } catch (err) {
      console.error(err);
      this.setStatus("disconnected", "Could not stop");
      await this.showPrompt({
        title: "Could not stop the meeting",
        message: `An unexpected error occurred while stopping.\n\n${err.message || err}`,
      });
      try {
        if (this._reviewWizardOpen && this.meetingId) {
          await this.refreshTranscript().catch(() => {});
          this.meetingStatus = "stopped";
          await this.advanceToSpeakerStep();
          return;
        }
      } catch (_) {}
      if (this._reviewWizardOpen) {
        try {
          await this.advanceToSpeakerStep();
          return;
        } catch (_) {}
      }
      this.closeReviewWizard(true);
    } finally {
      if (this.ws) {
        try { this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      this.endProcessingRequest();
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
        if (this.meetingStatus !== "processing") {
          this.setStatus("connected", "Connected");
        }
        resolve();
      };
      this.ws.onmessage = (ev) => this.onWsMessage(ev);
      this.ws.onclose = (event) => {
        this.isConnected = false;
        // Server closes with 4401/4403 when the auth cookie is missing/invalid
        // or belongs to another user; that looks like a normal drop otherwise.
        if (event && (event.code === 4401 || event.code === 4403)) {
          if (this.isRecording) {
            this.isRecording = false;
            this.stopMic();
            this.stopTimer(false);
          }
          const next = window.location.pathname + window.location.search;
          this.showPrompt({
            title: "Session expired",
            message: "Please sign in again to continue.",
          }).finally(() => {
            window.location.href = "/login?next=" + encodeURIComponent(next);
          });
          return;
        }
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
    // Binary PCM frame, not a JSON array of numbers: the server already
    // decodes raw bytes on this path (np.frombuffer(..., dtype=int16)),
    // and JSON-encoding each sample as decimal text bloats every ~128ms
    // chunk several times over for no benefit.
    this.ws.send(int16.buffer);
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
        if (msg.phase) this.updateProcessingPhase(msg.phase);
        if (msg.cancelled) {
          this.closeReviewWizard(true);
          this.setCaptureSource(null);
          this.setStatus("connected", "Processing cancelled");
          this.endProcessingRequest();
          this.applyCaptureSourceLock();
          return;
        }
        if (msg.status === "processing") {
          this.setStatus("processing", "Processing");
          if (msg.phase) {
            this.updateProcessingPhase(msg.phase);
            const phaseLabels = {
              save_audio: "Reading and saving audio…",
              flush_stt: "Transcribing…",
              diarize: "Identifying speakers…",
              review: "Polishing transcript…",
            };
            const label = phaseLabels[msg.phase];
            if (label) this.setStatus("processing", label);
          }
        }
        if (msg.status === "transcribing" && msg.progress) {
          this.updateProcessingPhase("flush_stt");
          const p = msg.progress;
          const done = p.done != null ? p.done : p.chunk;
          const total = p.total != null ? p.total : "?";
          this.updateProcessingPhase("flush_stt");
          this.setStatus("processing", `Transcribing ${done}/${total}`);
        }
        if (msg.status === "stopped") {
          this.setStatus("connected", "Stopped — ready to analyse");
          this.stopTimer(false);
          this.refreshTranscript().catch(() => {});
          this.refreshDebug().catch(() => {});
        }
        if (msg.status === "recording") this.setStatus("recording", "Recording");
        this.updateReviewAvailability();
      } else if (msg.type === "speaker_update") {
        this.refreshTranscript().catch(() => {});
      } else if (msg.type === "speaker_map" && msg.speaker_map) {
        this.speakerMap = { ...msg.speaker_map };
        this.renderTimeline();
      } else if (msg.type === "warning") {
        console.warn(msg.message || msg.code);
        if (msg.code === "fallback_diarization") {
          this.setStatus("processing", "Warning: basic diarization (no pyannote)");
          if (this._reviewWizardOpen && !this._fallbackPromptShown) {
            this._fallbackPromptShown = true;
            this.showPrompt({
              title: "Speaker detection warning",
              message:
                "The main diarization model is unavailable and the simple fallback is active.\n\n" +
                "Speaker labels may be less accurate, but you can continue.",
            }).catch(() => {});
          }
        }
      } else if (msg.type === "error") {
        console.error(msg.message);
        const errText = String(msg.message || "Unknown server error");
        this.setStatus("processing", `Error: ${errText.slice(0, 80)}`);
        if (this._reviewWizardOpen || this.meetingStatus === "processing") {
          this.showPrompt({
            title: "Processing error",
            message: errText,
          }).catch(() => {});
        }
      }
    } catch (err) {
      console.error(err);
    }
  }

  replaceTranscript(segments) {
    const nextList = (segments || []).filter((s) => (s.text || "").trim());
    // During/after stop processing, an empty WS payload (failed polish wipe, race)
    // must not erase the live transcript the user already saw.
    if (
      !nextList.length &&
      this.segments.size > 0 &&
      (this._reviewWizardOpen || this.meetingStatus === "processing")
    ) {
      console.warn("Ignoring empty transcript update that would wipe existing segments");
      return;
    }
    if (this._editingSegmentId) {
      // Avoid wiping an in-progress edit; sync after blur/save.
      this.segments.clear();
      nextList.forEach((s) => this.segments.set(s.id, s));
      return;
    }
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
        <p>Press “Start recording” to begin.</p>
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
          '<span class="badge badge-active">Live</span>' +
          '<span class="badge badge-locked">Processing — locked</span>';
      } else if (isLive) {
        statusBadge = '<span class="badge badge-muted">Interim</span>';
      } else if (polishKeys.has(key)) {
        statusBadge = '<span class="badge badge-polish">Polished</span>';
      } else {
        statusBadge = '<span class="badge badge-editable">Editable</span>';
      }
      // Live capture has no final diarization — hide speaker/overlap chrome until stop.
      const showSpeakers =
        !isLive &&
        !this.isRecording &&
        this.meetingStatus !== "recording";
      const speakerHtml = showSpeakers
        ? g.speakers
            .map((spk) => {
              const color = this.speakerColor(spk);
              const label = this.speakerLabel(spk);
              return `<button type="button" class="speaker-chip" data-speaker-id="${this.escape(spk)}" title="Click to rename">
            <span class="speaker-dot" style="background:${color}"></span>
            <span class="speaker-name" style="color:${color}">${this.escape(label)}</span>
          </button>`;
            })
            .join("")
        : "";
      const badge =
        showSpeakers && g.type === "overlap"
          ? '<span class="badge">Overlap</span>'
          : "";
      const editHint = isEditable
        ? '<span class="segment-edit-hint" aria-hidden="true">Edit</span>'
        : "";

      row.innerHTML = `
        <div class="segment-meta">
          ${speakerHtml ? `<div class="speaker-row">${speakerHtml}</div>` : ""}
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
    textEl.setAttribute("aria-label", "Edit segment text");
    textEl.setAttribute("data-segment-id", seg.id);
    textEl.title = "Click to edit — Enter to save, Esc to cancel";

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
          alert(`Could not save edit: ${err.message}`);
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
    if (!match) return String(speakerId || "Speaker");
    const n = parseInt(match[1], 10);
    if (!Number.isFinite(n)) return String(speakerId);
    return `Speaker ${n + 1}`;
  }

  speakerColor(speakerId) {
    const n = parseInt(String(speakerId).replace(/\D/g, ""), 10);
    const idx = Number.isFinite(n) ? n % this.speakerColors.length : 0;
    return this.speakerColors[idx];
  }

  formatTs(ms) {
    return distill.formatTs(ms);
  }

  async renameSpeaker(speakerId) {
    if (!this.meetingId || !speakerId) return;
    const fallback = this.defaultSpeakerLabel(speakerId);
    const current = this.speakerMap[speakerId] || fallback;
    const name = window.prompt(`Display name for ${fallback}:`, current === fallback ? "" : current);
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
      alert(`Could not rename: ${err.message}`);
    }
  }

  openReviewWizard() {
    if (!this.reviewWizard) return;
    this._reviewWizardOpen = true;
    this._fallbackPromptShown = false;
    this.reviewWizard.classList.remove("hidden");
    this.resetProcessingPhases();
    this.setReviewStep("processing");
    this.applyCaptureSourceLock();
  }

  closeReviewWizard(force = false) {
    if (!force && this._reviewStep !== "minutes") return;
    this._reviewWizardOpen = false;
    this.stopSpeakerSamplePlayback();
    if (this.reviewWizard) this.reviewWizard.classList.add("hidden");
    this.applyCaptureSourceLock();
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

  async toggleSpeakerSample(btn) {
    const src = btn.getAttribute("data-audio-src");
    if (!src) return;
    if (btn._loading) return;

    if (btn._audio && !btn._audio.paused) {
      btn._audio.pause();
      return;
    }
    if (this._activeSpeakerAudio && this._activeSpeakerAudio !== btn._audio) {
      try {
        this._activeSpeakerAudio.pause();
      } catch (_) {}
    }

    btn._loading = true;
    btn.classList.add("is-loading");
    try {
      // Fetch as blob first so deployment 404/proxy errors surface clearly
      // (native <audio src> often fails silently behind reverse proxies).
      const res = await this.fetchWithTimeout(src, {}, 12000);
      if (!res.ok) {
        throw new Error(
          res.status === 404
            ? "No sample available (recording or speaker interval is missing)."
            : `Could not fetch the sample clip (status ${res.status}).`
        );
      }
      const blob = await res.blob();
      if (!blob || blob.size < 44) {
        throw new Error("The sample clip is empty or incomplete.");
      }
      if (btn._objectUrl) {
        try {
          URL.revokeObjectURL(btn._objectUrl);
        } catch (_) {}
      }
      const objectUrl = URL.createObjectURL(blob);
      btn._objectUrl = objectUrl;

      if (!btn._audio) {
        const audio = new Audio();
        audio.preload = "auto";
        audio.addEventListener("timeupdate", () => {
          const pct = audio.duration ? audio.currentTime / audio.duration : 0;
          btn.style.setProperty("--progress", `${Math.min(1, Math.max(0, pct)) * 360}deg`);
        });
        audio.addEventListener("play", () => btn.classList.add("is-playing"));
        audio.addEventListener("pause", () => btn.classList.remove("is-playing"));
        audio.addEventListener("ended", () => {
          btn.classList.remove("is-playing");
          btn.style.setProperty("--progress", "0deg");
        });
        btn._audio = audio;
      }
      const audio = btn._audio;
      audio.src = objectUrl;
      audio.currentTime = 0;
      await audio.play();
      this._activeSpeakerAudio = audio;
    } catch (err) {
      console.error(err);
      btn.classList.remove("is-playing");
      await this.showPrompt({
        title: "Sample playback",
        message: `Could not play the sample clip.\n\n${err.message || err}`,
      });
    } finally {
      btn._loading = false;
      btn.classList.remove("is-loading");
    }
  }

  async loadSpeakerNamingStep() {
    if (!this.meetingId || !this.speakerNamingList) return;
    this.stopSpeakerSamplePlayback();
    this._confirmedSpeakers = new Set();
    this._speakersContinueBusy = false;
    this.speakerNamingList.innerHTML = '<p class="review-hint">Loading speakers…</p>';
    if (this.speakersContinueBtn) {
      this.speakersContinueBtn.disabled = false;
      this.speakersContinueBtn.removeAttribute("disabled");
      this.speakersContinueBtn.classList.add("is-muted");
      this.speakersContinueBtn.setAttribute("aria-disabled", "true");
    }
    try {
      const res = await this.fetchWithTimeout(
        `/meetings/${this.meetingId}/speakers`,
        {},
        12000
      );
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      // Only name speakers that have a playable sample clip.
      this._speakerList = (data.speakers || []).filter((s) => s.has_sample);
      this.renderSpeakerNamingList();
    } catch (err) {
      console.error(err);
      this.speakerNamingList.innerHTML =
        '<p class="review-hint">Could not load the speaker list.</p>';
      await this.showPrompt({
        title: "Could not load speakers",
        message: `The speaker list could not be fetched.\n\n${err.message || err}`,
      });
      this.updateSpeakersContinueState();
    }
  }

  renderSpeakerNamingList() {
    if (!this.speakerNamingList) return;
    this.speakerNamingList.innerHTML = "";
    if (!this._speakerList.length) {
      this.speakerNamingList.innerHTML =
        '<p class="review-hint">No speakers detected — you can continue straight on.</p>';
      this.updateSpeakersContinueState();
      return;
    }
    this._speakerList.forEach((spk) => {
      const card = document.createElement("div");
      card.className = "speaker-naming-card";
      card.dataset.speakerId = String(spk.id);
      const color = this.speakerColor(spk.id);
      const audioSrc = `/meetings/${this.meetingId}/speakers/${encodeURIComponent(spk.id)}/audio`;
      const audioHtml = `<button type="button" class="speaker-play-btn" data-audio-src="${this.escape(audioSrc)}" aria-label="Play sample clip">
            <svg class="play-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
            <svg class="pause-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>
          </button>`;
      card.innerHTML = `
        <span class="speaker-naming-dot" style="background:${color}"></span>
        <input
          type="text"
          class="speaker-naming-name"
          value="${this.escape(spk.custom_label || "")}"
          placeholder="${this.escape(spk.label)}"
        />
        ${audioHtml}
        <span class="speaker-naming-status">${spk.custom_label ? "Saved" : "Needs a name"}</span>
      `;
      if (spk.custom_label) {
        card.classList.add("is-confirmed");
        this._confirmedSpeakers.add(String(spk.id));
        this.speakerMap[spk.id] = spk.custom_label;
      }
      const input = card.querySelector(".speaker-naming-name");
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          input.blur();
        }
      });
      input.addEventListener("input", () => {
        const value = (input.value || "").trim();
        const statusEl = card.querySelector(".speaker-naming-status");
        if (value) {
          this._confirmedSpeakers.add(String(spk.id));
          card.classList.add("is-confirmed");
          if (statusEl) statusEl.textContent = "Ready";
        } else {
          this._confirmedSpeakers.delete(String(spk.id));
          card.classList.remove("is-confirmed");
          if (statusEl) statusEl.textContent = "Needs a name";
        }
        this.updateSpeakersContinueState();
      });
      input.addEventListener("blur", () => {
        this.confirmSpeakerName(String(spk.id), input, card).catch((err) => {
          console.error(err);
        });
      });
      this.speakerNamingList.appendChild(card);
    });
    this.updateSpeakersContinueState();
  }

  async confirmSpeakerName(speakerId, input, card) {
    const value = (input.value || "").trim();
    const statusEl = card.querySelector(".speaker-naming-status");
    const sid = String(speakerId);
    if (!value) {
      this._confirmedSpeakers.delete(sid);
      card.classList.remove("is-confirmed");
      if (statusEl) statusEl.textContent = "Needs a name";
      this.updateSpeakersContinueState();
      return;
    }
    // Optimistic local confirm so Continue never depends on a slow PATCH.
    this._confirmedSpeakers.add(sid);
    this.speakerMap[sid] = value;
    card.classList.add("is-confirmed");
    if (statusEl) statusEl.textContent = "Saved";
    this.updateSpeakersContinueState();

    if ((this.speakerMap[sid] || "") === value && card.dataset.savedLabel === value) {
      return;
    }
    try {
      const res = await this.fetchWithTimeout(
        `/meetings/${this.meetingId}/speakers`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [sid]: value }),
        },
        10000
      );
      if (!res.ok) throw new Error(this.formatErrorDetail(await res.text()) || `status ${res.status}`);
      const meeting = await res.json();
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      card.dataset.savedLabel = value;
      if (statusEl) statusEl.textContent = "Saved";
      this.renderTimeline();
    } catch (err) {
      console.error(err);
      if (statusEl) statusEl.textContent = "Could not save";
      throw err;
    } finally {
      this.updateSpeakersContinueState();
    }
  }

  speakerNamingCardsHaveNames() {
    if (!this.speakerNamingList) return !this._speakerList.length;
    const cards = Array.from(this.speakerNamingList.querySelectorAll(".speaker-naming-card"));
    if (!cards.length) return !this._speakerList.length;
    return cards.every((card) => {
      const input = card.querySelector(".speaker-naming-name");
      return !!(input && (input.value || "").trim());
    });
  }

  updateSpeakersContinueState() {
    if (!this.speakersContinueBtn || this._speakersContinueBusy) return;
    const ready = this.speakerNamingCardsHaveNames();
    // Keep the control enabled so clicks always fire; mute when incomplete.
    this.speakersContinueBtn.disabled = false;
    this.speakersContinueBtn.removeAttribute("disabled");
    this.speakersContinueBtn.classList.toggle("is-muted", !ready);
    this.speakersContinueBtn.setAttribute("aria-disabled", ready ? "false" : "true");
  }

  async onSpeakersContinue() {
    if (this._speakersContinueBusy) return;
    if (!this.speakerNamingCardsHaveNames()) {
      await this.showPrompt({
        title: "Some speakers are unnamed",
        message: "Give every speaker a name before continuing.",
      });
      return;
    }
    this._speakersContinueBusy = true;
    const btn = this.speakersContinueBtn;
    const prevLabel = btn ? btn.textContent : "";
    try {
      if (btn) {
        btn.textContent = "Continuing…";
        btn.classList.add("is-processing");
      }
      const cards = this.speakerNamingList
        ? Array.from(this.speakerNamingList.querySelectorAll(".speaker-naming-card"))
        : [];
      // Sync local map first so UI can advance even if one PATCH flakes.
      cards.forEach((card) => {
        const input = card.querySelector(".speaker-naming-name");
        const id = card.dataset.speakerId;
        if (!input || !id) return;
        const value = (input.value || "").trim();
        if (!value) return;
        this._confirmedSpeakers.add(String(id));
        this.speakerMap[id] = value;
      });

      const results = await Promise.allSettled(
        cards.map((card) => {
          const input = card.querySelector(".speaker-naming-name");
          const id = card.dataset.speakerId;
          if (!input || !id) return Promise.resolve();
          return this.confirmSpeakerName(id, input, card);
        })
      );
      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length) {
        // Names are already in local speakerMap — warn but continue the wizard.
        await this.showPrompt({
          title: "Some names were not saved",
          message:
            "Some names did not save on the server, but we will continue with what you entered.\n\n" +
            (failed[0].reason?.message || String(failed[0].reason || "")),
        });
      }
      this.stopSpeakerSamplePlayback();
      this.setReviewStep("transcript");
      await this.loadTranscriptReviewStep();
    } finally {
      this._speakersContinueBusy = false;
      if (btn) {
        btn.textContent = prevLabel || "Continue to transcript";
        btn.classList.remove("is-processing");
        this.updateSpeakersContinueState();
      }
    }
  }

  async loadTranscriptReviewStep() {
    if (!this.transcriptReviewList) {
      throw new Error("The transcript review section is missing from the page");
    }
    this.transcriptReviewList.innerHTML = '<p class="review-hint">Loading transcript…</p>';
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
        '<p class="review-hint">There is no transcript to show.</p>';
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
          return `<button type="button" class="speaker-chip" data-speaker-id="${this.escape(spk)}" title="Click to rename">
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
          alert(`Could not save edit: ${err.message}`);
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
      // Server minutes LLM budget is ~180s; keep UI waiting for a full agent pass.
      const res = await this.fetchWithTimeout(
        `/meetings/${this.meetingId}/minutes/generate`,
        { method: "POST" },
        200000
      );
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      this.renderMinutesForm(data, { fromAgent: true });
    } catch (err) {
      console.error(err);
      await this.showPrompt({
        title: "Could not generate minutes",
        message:
          "Automatic minutes generation failed. You can retry later or fill the form in by hand.\n\n" +
          this.formatErrorDetail(err.message || err),
      });
      this.renderMinutesForm(
        {
          subject: "",
          meeting_date: "",
          location: "",
          secretary: "",
          summary: "",
          attendees: this.registeredAttendeeNames(),
          absentees: [],
          decisions: [],
        },
        { fromAgent: true }
      );
    } finally {
      this.showMinutesLoading(false);
    }
  }

  registeredAttendeeNames() {
    const names = [];
    const seen = new Set();
    Object.values(this.speakerMap || {}).forEach((name) => {
      const cleaned = String(name || "").trim();
      if (!cleaned || seen.has(cleaned)) return;
      seen.add(cleaned);
      names.push(cleaned);
    });
    return names;
  }

  renderMinutesForm(data, { fromAgent = false } = {}) {
    if (fromAgent) {
      this._minutesAttendees = this.registeredAttendeeNames();
      this._minutesAbsentees = [];
    } else {
      this._minutesAttendees = [...(data.attendees || [])];
      this._minutesAbsentees = [...(data.absentees || [])];
    }
    this._minutesDecisions = (data.decisions || []).map((d) => {
      let executor = d.executor || "";
      if (fromAgent && executor && !this._minutesAttendees.includes(executor)) {
        executor = "";
      }
      return {
        id: d.id || `d-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        description: d.description || "",
        executor,
        due_date: d.due_date || "",
        status: d.status || "pending",
      };
    });
    if (this.minutesSubject) this.minutesSubject.value = data.subject || "";
    if (this.minutesLocation) this.minutesLocation.value = data.location || "";
    if (this.minutesSummary) this.minutesSummary.value = data.summary || "";

    let dateValue = (data.meeting_date || "").trim();
    if (!dateValue && this._captureSource === "live") {
      dateValue = this.nowLocalDateTime();
    }
    this.setDateValue(dateValue);

    this.renderMinutesChipList("attendees");
    this.renderMinutesChipList("absentees");
    const secretary =
      fromAgent && data.secretary && !this._minutesAttendees.includes(data.secretary)
        ? ""
        : data.secretary || "";
    this.refreshSecretaryOptions(secretary);
    this.renderMinutesDecisions();
    if (this.minutesSaveStatus) this.minutesSaveStatus.classList.add("hidden");
  }

  refreshSecretaryOptions(selected) {
    if (!this.minutesSecretary) return;
    const current = selected != null ? selected : this.minutesSecretary.value;
    const names = [...this._minutesAttendees];
    if (current && !names.includes(current)) names.unshift(current);
    this.minutesSecretary.innerHTML =
      '<option value="">Choose from attendees</option>' +
      names
        .map(
          (name) =>
            `<option value="${this.escape(name)}"${name === current ? " selected" : ""}>${this.escape(name)}</option>`
        )
        .join("");
  }

  renderMinutesChipList(kind) {
    const listEl = kind === "attendees" ? this.minutesAttendeesList : this.minutesAbsenteesList;
    const arr = kind === "attendees" ? this._minutesAttendees : this._minutesAbsentees;
    if (!listEl) return;
    listEl.innerHTML = "";
    arr.forEach((name, idx) => {
      const chip = document.createElement("span");
      chip.className = "minutes-chip";
      chip.innerHTML = `<span>${this.escape(name)}</span><button type="button" aria-label="Remove">×</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        arr.splice(idx, 1);
        this.renderMinutesChipList(kind);
        if (kind === "attendees") this.refreshSecretaryOptions();
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
    if (kind === "attendees") this.refreshSecretaryOptions();
  }

  initDatePicker() {
    // Native datetime-local carries its own calendar + clock UI, so there is
    // no popover to wire up here.
    if (!this.minutesDate) return;
  }

  // Stored form is "YYYY-MM-DD HH:MM" (what the minutes sheet parses);
  // the input wants "YYYY-MM-DDTHH:MM".
  setDateValue(text) {
    if (!this.minutesDate) return;
    const raw = String(text || "").trim();
    this.minutesDate.value = raw ? raw.replace(" ", "T").slice(0, 16) : "";
  }

  getDateValue() {
    if (!this.minutesDate) return "";
    const raw = String(this.minutesDate.value || "").trim();
    return raw ? raw.replace("T", " ").slice(0, 16) : "";
  }

  nowLocalDateTime() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  }

  renderMinutesDecisions() {
    if (!this.minutesDecisionsEl) return;
    this.minutesDecisionsEl.innerHTML = "";
    if (!this._minutesDecisions.length) {
      this.minutesDecisionsEl.innerHTML =
        '<p class="review-hint">No decisions yet — use “Add row” to create one.</p>';
      return;
    }
    this._minutesDecisions.forEach((d) => {
      const row = document.createElement("div");
      row.className = "minutes-decision-row";
      row.dataset.decisionId = d.id;
      row.innerHTML = `
        <textarea rows="2" data-field="description" placeholder="Decision / follow-up">${this.escape(d.description)}</textarea>
        <input type="text" data-field="executor" placeholder="Owner" value="${this.escape(d.executor)}" />
        <input type="text" data-field="due_date" placeholder="Due" value="${this.escape(d.due_date)}" />
        <select data-field="status">
          <option value="pending"${d.status === "pending" ? " selected" : ""}>Pending</option>
          <option value="done"${d.status === "done" ? " selected" : ""}>Done</option>
        </select>
        <button type="button" class="minutes-decision-remove">Remove</button>
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
    const form = this.collectMinutesFormData();
    const payload = {
      ...form,
      decisions: form.decisions.filter((d) => d.description),
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
        this.minutesSaveStatus.textContent = "Saved";
        this.minutesSaveStatus.classList.remove("hidden");
        clearTimeout(this._minutesSaveTimer);
        this._minutesSaveTimer = setTimeout(() => {
          this.minutesSaveStatus.classList.add("hidden");
        }, 2500);
      }
      this.updateReviewAvailability();
    } catch (err) {
      console.error(err);
      alert(`Could not save minutes: ${err.message}`);
    }
  }

  collectMinutesFormData() {
    return {
      subject: (this.minutesSubject?.value || "").trim(),
      meeting_date: this.getDateValue(),
      location: (this.minutesLocation?.value || "").trim(),
      secretary: (this.minutesSecretary?.value || "").trim(),
      summary: (this.minutesSummary?.value || "").trim(),
      attendees: [...this._minutesAttendees],
      absentees: [...this._minutesAbsentees],
      decisions: this._minutesDecisions.map((d) => ({
        id: d.id,
        description: (d.description || "").trim(),
        executor: (d.executor || "").trim(),
        due_date: (d.due_date || "").trim(),
        status: d.status || "pending",
      })),
    };
  }

  async printMinutes() {
    const data = this.collectMinutesFormData();
    await distillMinutesPrint.print(data, this.meetingId, { variant: "assistant" });
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
      alert("Start or open a meeting first");
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
    const res = await this.fetchWithTimeout(
      `/meetings/${this.meetingId}/transcript`,
      {},
      15000
    );
    if (!res.ok) return;
    const data = await res.json();
    this.replaceTranscript(data.segments || []);
  }

  async uploadRecording() {
    if (this._captureSource === "live") return;
    const file = this.uploadFile.files && this.uploadFile.files[0];
    if (!file) {
      alert("Choose an audio file first");
      return;
    }
    if (!file.size) {
      alert("The selected file is empty. If it lives in iCloud or Google Drive, download it locally first.");
      return;
    }
    const title = this.requireMeetingTitle();
    if (!title) return;

    this.setCaptureSource("upload");
    this.meetingStatus = "processing";
    this.openReviewWizard();
    this.applyCaptureSourceLock();
    this.updateProcessingPhase("save_audio");
    this.setStatus("processing", "Preparing file…");
    const processingSignal = this.startProcessingRequest();

    let processError = null;
    try {
      let buffer;
      try {
        buffer = await file.arrayBuffer();
      } catch (readErr) {
        console.error(readErr);
        throw new Error("Could not read the audio file. Please select it again.");
      }
      if (!buffer.byteLength) {
        throw new Error("The audio file is empty");
      }

      const created = await fetch("/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, start: false }),
      });
      if (!created.ok) {
        throw new Error(this.formatErrorDetail(await created.text()) || "Could not create the meeting");
      }
      const meeting = await created.json();
      this.meetingId = meeting.id;
      this.meetingStatus = "processing";
      this.speakerMap = { ...(meeting.speaker_map || {}) };
      this.setMeetingMeta();
      this.setSessionUrl(meeting.id);
      this.clearTimeline(true);
      this.applyCaptureSourceLock();

      await this.connectWebSocket(meeting.id);
      this.updateProcessingPhase("save_audio");
      this.setStatus("processing", "Uploading and transcribing…");

      const form = new FormData();
      form.append(
        "file",
        new Blob([buffer], { type: file.type || "application/octet-stream" }),
        file.name || "upload.wav",
      );
      const res = await this.fetchWithTimeout(
        `/meetings/${this.meetingId}/upload`,
        { method: "POST", body: form, signal: processingSignal },
        600000,
      );
      if (res.status === 409) {
        this.meetingStatus = "created";
        this.closeReviewWizard(true);
        this.setCaptureSource(null);
        this.setStatus("connected", "Processing cancelled");
        return;
      }
      if (!res.ok) {
        const detail = this.formatErrorDetail(await res.text());
        processError =
          `Upload or processing failed (status ${res.status}).` +
          (detail ? `\n\n${detail}` : "");
        throw new Error(processError);
      }
      const data = await res.json();
      this.meetingStatus = "stopped";
      this.setHasRecording(true);
      this.replaceTranscript(data.segments || []);
      try {
        await this.refreshTranscript();
      } catch (err) {
        console.error(err);
      }
      if (!this.hasTranscriptContext()) {
        this.setStatus("connected", "Transcription finished — no text returned");
        if (!processError) {
          processError =
            "The file finished processing, but STT did not return usable text.\n\n" +
            "You can carry on naming speakers or try a different file.";
        }
      } else {
        this.setStatus("connected", "File transcribed");
      }
      await this.refreshDebug();

      if (processError) {
        await this.showPrompt({
          title: "File processing error",
          message: processError,
          okLabel: "Got it",
        });
      }

      try {
        await this.advanceToSpeakerStep();
      } catch (err) {
        console.error(err);
        await this.showPrompt({
          title: "Could not advance",
          message: `Processing finished, but the next step could not be opened.\n\n${
            err.message || err
          }`,
        });
      }
    } catch (err) {
      if (this.isLeaveAbort(err)) return;
      console.error(err);
      this.setStatus("disconnected", "Upload failed");
      alert(`Upload failed: ${err.message}`);
      if (!this.meetingId) {
        this.setCaptureSource(null);
      }
      this.closeReviewWizard(true);
    } finally {
      if (this.ws) {
        try {
          this.ws.close();
        } catch (_) {}
        this.ws = null;
      }
      this.endProcessingRequest();
      this.applyCaptureSourceLock();
      this.updateReviewAvailability();
    }
  }

  escape(text) {
    return distill.escapeHtml(text);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const user = await distill.bindAuthChrome();
  if (!user) return;
  // Distinct from window.distill (the shared.js utils namespace).
  window.distillClient = new DistillClient();
});
