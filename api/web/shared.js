(function () {
  const MEETING_STATUS_LABELS = {
    created: "ایجاد شده",
    recording: "در حال ضبط",
    processing: "در حال پردازش",
    stopped: "پایان یافته",
    failed: "ناموفق",
  };

  const SPEAKER_COLORS = [
    "#60a5fa",
    "#f472b6",
    "#34d399",
    "#fbbf24",
    "#a78bfa",
    "#fb7185",
    "#2dd4bf",
    "#f97316",
  ];

  function meetingStatusClass(status) {
    if (status === "recording" || status === "processing") return "is-active";
    if (status === "failed") return "is-failed";
    return "is-done";
  }

  function formatDate(ts) {
    if (!ts) return "—";
    try {
      return new Intl.DateTimeFormat("fa-IR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(ts * 1000));
    } catch (_) {
      return new Date(ts * 1000).toLocaleString("fa-IR");
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

  function toPersianDigits(value) {
    return String(value).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]);
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
    if (!match) return String(speakerId || "سخنگو");
    const n = parseInt(match[1], 10);
    if (!Number.isFinite(n)) return String(speakerId);
    return `سخنگوی ${toPersianDigits(n + 1)}`;
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
    toPersianDigits,
    formatTs,
    speakerColor,
    speakerLabel,
    formatErrorDetail,
    parseApiError,
    redirectIfUnauthorized,
    bindAuthChrome,
  };
})();
