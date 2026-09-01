(function () {
  const MEETING_STATUS_LABELS = {
    created: "Created",
    recording: "Recording",
    processing: "Processing",
    stopped: "Finished",
    failed: "Failed",
  };

  // Low-saturation tints: distinguishable per speaker, still reads monochrome
  // against the near-black ground.
  const SPEAKER_COLORS = [
    "#e8e8e8",
    "#9fb4d0",
    "#d0b9a8",
    "#a8c4b4",
    "#c2b0d0",
    "#d0a8a8",
    "#a8c0c8",
    "#c8c49f",
  ];

  function meetingStatusClass(status) {
    if (status === "recording" || status === "processing") return "is-active";
    if (status === "failed") return "is-failed";
    return "is-done";
  }

  function formatDate(ts) {
    if (!ts) return "—";
    try {
      return new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(ts * 1000));
    } catch (_) {
      return new Date(ts * 1000).toLocaleString("en-US");
    }
  }

  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatTs(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  function speakerColor(speakerId) {
    const n = parseInt(String(speakerId).replace(/\D/g, ""), 10);
    const idx = Number.isFinite(n) ? n % SPEAKER_COLORS.length : 0;
    return SPEAKER_COLORS[idx];
  }

  function speakerLabel(speakerId, speakerMap) {
    const mapped = speakerMap && speakerMap[speakerId];
    if (mapped) return mapped;
    const match = String(speakerId || "").match(/(\d+)\s*$/);
    if (!match) return String(speakerId || "Speaker");
    const n = parseInt(match[1], 10);
    if (!Number.isFinite(n)) return String(speakerId);
    return `Speaker ${n + 1}`;
  }

  function formatErrorDetail(raw, maxLen = 400) {
    let text = String(raw || "").trim();
    if (!text) return "";
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") {
        text = String(parsed.detail || parsed.message || parsed.error || text);
      }
    } catch (_) {}
    text = text.replace(/\s+/g, " ").trim();
    if (text.length > maxLen) text = `${text.slice(0, maxLen)}…`;
    return text;
  }

  async function parseApiError(res, fallback) {
    let detail = fallback;
    try {
      const err = await res.json();
      if (err.detail) detail = String(err.detail);
    } catch (_) {}
    return detail;
  }

  function redirectIfUnauthorized(res, nextPath) {
    if (res.status !== 401) return false;
    const next = encodeURIComponent(nextPath || window.location.pathname);
    window.location.href = `/login?next=${next}`;
    return true;
  }

  async function bindAuthChrome() {
    if (!window.distillAuth) return null;
    const user = await distillAuth.requireAuth();
    if (!user) return null;

    const nameEl = document.getElementById("userName");
    if (nameEl) nameEl.textContent = user.display_name || user.email;

    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", () => distillAuth.logout());
    }

    return user;
  }

  window.distill = {
    MEETING_STATUS_LABELS,
    SPEAKER_COLORS,
    meetingStatusClass,
    formatDate,
    escapeHtml,
    formatTs,
    speakerColor,
    speakerLabel,
    formatErrorDetail,
    parseApiError,
    redirectIfUnauthorized,
    bindAuthChrome,
  };
})();
