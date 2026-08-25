const ROLE_LABELS = { admin: "مدیر", user: "کاربر" };
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

async function setRole(userId, role) {
  const res = await fetch(`/admin/users/${userId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) {
    let detail = "تغییر نقش ناموفق بود";
    try {
      const err = await res.json();
      if (err.detail) detail = String(err.detail);
    } catch (_) {}
    alert(detail);
    return false;
  }
  return true;
}

async function setActive(userId, isActive) {
  const res = await fetch(`/admin/users/${userId}/active`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!res.ok) {
    let detail = "تغییر وضعیت ناموفق بود";
    try {
      const err = await res.json();
      if (err.detail) detail = String(err.detail);
    } catch (_) {}
    alert(detail);
    return false;
  }
  return true;
}

async function deleteUser(userId, email) {
  const label = email || "این کاربر";
  if (!confirm(`${label} به‌طور دائمی حذف شود؟ این عمل قابل بازگشت نیست.`)) {
    return false;
  }
  const res = await fetch(`/admin/users/${userId}`, { method: "DELETE" });
  if (!res.ok) {
    let detail = "حذف کاربر ناموفق بود";
    try {
      const err = await res.json();
      if (err.detail) detail = String(err.detail);
    } catch (_) {}
    alert(detail);
    return false;
  }
  return true;
}

function renderRow(u, currentUserId, onChange) {
  const tr = document.createElement("tr");

  const userCell = document.createElement("td");
  const name = document.createElement("div");
  name.className = "admin-user-name";
  name.textContent = u.display_name || u.email;
  const email = document.createElement("div");
  email.className = "admin-user-email";
  email.textContent = u.email;
  userCell.append(name, email);

  const roleCell = document.createElement("td");
  const roleBadge = document.createElement("span");
  roleBadge.className = `role-badge ${u.role === "admin" ? "is-admin" : ""}`;
  roleBadge.textContent = ROLE_LABELS[u.role] || u.role;
  roleCell.append(roleBadge);

  const statusCell = document.createElement("td");
  const statusBadge = document.createElement("span");
  statusBadge.className = `meeting-status ${u.is_active ? "is-done" : "is-failed"}`;
  statusBadge.textContent = u.is_active ? "فعال" : "غیرفعال";
  statusCell.append(statusBadge);

  const dateCell = document.createElement("td");
  dateCell.textContent = formatDate(u.created_at);

  const actionsCell = document.createElement("td");
  const actions = document.createElement("div");
  actions.className = "admin-row-actions";

  if (u.is_active) {
    const roleBtn = document.createElement("button");
    roleBtn.type = "button";
    roleBtn.className = "btn btn-ghost btn-sm";
    roleBtn.textContent = u.role === "admin" ? "تنزل به کاربر" : "ارتقا به مدیر";
    roleBtn.addEventListener("click", async () => {
      const nextRole = u.role === "admin" ? "user" : "admin";
      roleBtn.disabled = true;
      const ok = await setRole(u.id, nextRole);
      roleBtn.disabled = false;
      if (ok) onChange();
    });

    const deactivateBtn = document.createElement("button");
    deactivateBtn.type = "button";
    deactivateBtn.className = "btn btn-ghost btn-sm";
    deactivateBtn.textContent = "غیرفعال کردن";
    if (u.id === currentUserId) {
      deactivateBtn.disabled = true;
      deactivateBtn.title = "نمی‌توانید حساب خودتان را غیرفعال کنید";
    }
    deactivateBtn.addEventListener("click", async () => {
      deactivateBtn.disabled = true;
      const ok = await setActive(u.id, false);
      deactivateBtn.disabled = false;
      if (ok) onChange();
    });

    actions.append(roleBtn, deactivateBtn);
  } else {
    const reactivateBtn = document.createElement("button");
    reactivateBtn.type = "button";
    reactivateBtn.className = "btn btn-ghost btn-sm";
    reactivateBtn.textContent = "فعال‌سازی مجدد";
    reactivateBtn.addEventListener("click", async () => {
      reactivateBtn.disabled = true;
      const ok = await setActive(u.id, true);
      reactivateBtn.disabled = false;
      if (ok) onChange();
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn btn-ghost btn-sm";
    deleteBtn.textContent = "حذف کاربر";
    deleteBtn.addEventListener("click", async () => {
      deleteBtn.disabled = true;
      const ok = await deleteUser(u.id, u.email);
      deleteBtn.disabled = false;
      if (ok) onChange();
    });

    actions.append(reactivateBtn, deleteBtn);
  }

  actionsCell.append(actions);

  tr.append(userCell, roleCell, statusCell, dateCell, actionsCell);
  return tr;
}

async function loadUsers(currentUserId) {
  const loading = document.getElementById("usersLoading");
  const errorBox = document.getElementById("usersError");
  const wrap = document.getElementById("usersTableWrap");
  const body = document.getElementById("usersTableBody");

  loading.hidden = false;
  errorBox.hidden = true;
  wrap.hidden = true;

  const res = await fetch("/admin/users");
  loading.hidden = true;

  if (res.status === 403) {
    window.location.href = "/dashboard";
    return;
  }
  if (res.status === 401) {
    window.location.href = "/login?next=/admin";
    return;
  }
  if (!res.ok) {
    errorBox.textContent = "خطا در بارگذاری کاربران";
    errorBox.hidden = false;
    return;
  }

  const data = await res.json();
  const users = data.users || [];
  body.innerHTML = "";
  users.forEach((u) =>
    body.appendChild(renderRow(u, currentUserId, () => loadUsers(currentUserId)))
  );
  wrap.hidden = false;
}

function renderMeetingRow(m) {
  const tr = document.createElement("tr");
  tr.className = "is-clickable";
  tr.tabIndex = 0;
  tr.dataset.meetingId = m.id;
  tr.title = "برای نظارت کلیک کنید";

  const titleCell = document.createElement("td");
  titleCell.textContent = m.title || "جلسه بدون عنوان";

  const ownerCell = document.createElement("td");
  if (m.owner) {
    const name = document.createElement("div");
    name.className = "admin-user-name";
    name.textContent = m.owner.display_name || m.owner.email;
    const email = document.createElement("div");
    email.className = "admin-user-email";
    email.textContent = m.owner.email;
    ownerCell.append(name, email);
  } else {
    ownerCell.textContent = "—";
  }

  const statusCell = document.createElement("td");
  const statusBadge = document.createElement("span");
  statusBadge.className = `meeting-status ${meetingStatusClass(m.status)}`;
  statusBadge.textContent = MEETING_STATUS_LABELS[m.status] || m.status;
  statusCell.append(statusBadge);

  const recordingCell = document.createElement("td");
  recordingCell.textContent = m.has_recording ? "دارد" : "—";

  const dateCell = document.createElement("td");
  dateCell.textContent = formatDate(m.created_at);

  tr.append(titleCell, ownerCell, statusCell, recordingCell, dateCell);

  const openMonitor = () => openMeetingMonitor(m.id);
  tr.addEventListener("click", openMonitor);
  tr.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openMonitor();
    }
  });

  return tr;
}

async function loadAllMeetings() {
  const loading = document.getElementById("meetingsAdminLoading");
  const errorBox = document.getElementById("meetingsAdminError");
  const empty = document.getElementById("meetingsAdminEmpty");
  const wrap = document.getElementById("meetingsAdminTableWrap");
  const body = document.getElementById("meetingsAdminTableBody");

  loading.hidden = false;
  errorBox.hidden = true;
  empty.hidden = true;
  wrap.hidden = true;

  const res = await fetch("/admin/meetings");
  loading.hidden = true;

  if (res.status === 403) {
    window.location.href = "/dashboard";
    return;
  }
  if (res.status === 401) {
    window.location.href = "/login?next=/admin";
    return;
  }
  if (!res.ok) {
    errorBox.textContent = "خطا در بارگذاری جلسات";
    errorBox.hidden = false;
    return;
  }

  const data = await res.json();
  const meetings = data.meetings || [];
  if (!meetings.length) {
    empty.hidden = false;
    return;
  }

  body.innerHTML = "";
  meetings.forEach((m) => body.appendChild(renderMeetingRow(m)));
  wrap.hidden = false;
}

async function createMeeting() {
  const res = await fetch("/meetings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "", start: false }),
  });
  if (res.status === 401) {
    window.location.href = "/login?next=" + encodeURIComponent("/admin");
    return;
  }
  if (!res.ok) throw new Error("create failed");
  const data = await res.json();
  window.location.href = `/assistant/${data.id}`;
}

async function deleteOwnMeeting(id, cardEl) {
  if (!confirm("این جلسه حذف شود؟")) return;
  const res = await fetch(`/meetings/${id}`, { method: "DELETE" });
  if (!res.ok) {
    alert("حذف ناموفق بود");
    return;
  }
  cardEl.remove();
  const list = document.getElementById("meetingsList");
  if (!list.children.length) {
    list.hidden = true;
    document.getElementById("meetingsEmpty").hidden = false;
  }
  await loadAllMeetings();
}

function renderOwnMeeting(m) {
  const li = document.createElement("li");
  li.className = "meeting-card";

  const meta = document.createElement("div");
  meta.className = "meeting-card-main";

  const title = document.createElement("h2");
  title.className = "meeting-card-title";
  title.textContent = m.title || "جلسه بدون عنوان";

  const status = document.createElement("span");
  status.className = `meeting-status ${meetingStatusClass(m.status)}`;
  status.textContent = MEETING_STATUS_LABELS[m.status] || m.status;

  const info = document.createElement("p");
  info.className = "meeting-card-meta";
  const parts = [formatDate(m.created_at)];
  if (m.has_recording) parts.push("ضبط موجود");
  info.textContent = parts.join(" · ");

  meta.append(title, status, info);

  const actions = document.createElement("div");
  actions.className = "meeting-card-actions";

  const openBtn = document.createElement("a");
  openBtn.className = "btn btn-primary btn-sm";
  openBtn.href = `/assistant/${m.id}`;
  openBtn.textContent =
    m.status === "recording" || m.status === "processing" ? "ادامه" : "باز کردن";

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn btn-ghost btn-sm";
  delBtn.textContent = "حذف";
  delBtn.addEventListener("click", () => deleteOwnMeeting(m.id, li));

  actions.append(openBtn, delBtn);
  li.append(meta, actions);
  return li;
}

async function loadOwnMeetings() {
  const loading = document.getElementById("meetingsLoading");
  const empty = document.getElementById("meetingsEmpty");
  const list = document.getElementById("meetingsList");

  const res = await fetch("/meetings");
  loading.hidden = true;

  if (res.status === 401) {
    window.location.href = "/login?next=/admin";
    return;
  }
  if (!res.ok) {
    loading.textContent = "خطا در بارگذاری جلسات";
    loading.hidden = false;
    return;
  }

  const data = await res.json();
  const meetings = data.meetings || [];
  if (!meetings.length) {
    empty.hidden = false;
    return;
  }

  list.innerHTML = "";
  meetings.forEach((m) => list.appendChild(renderOwnMeeting(m)));
  list.hidden = false;
}

/* ===== Meeting monitor ===== */

let monitorPrintContext = null;

function setMonitorTab(tab) {
  document.querySelectorAll("[data-monitor-tab]").forEach((btn) => {
    const active = btn.dataset.monitorTab === tab;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.tabPanel !== tab);
  });
}

function closeMeetingMonitor() {
  const overlay = document.getElementById("monitorOverlay");
  if (overlay) overlay.classList.add("hidden");
}

function renderMonitorInfo(meeting) {
  const owner = meeting.owner;
  const ownerLabel = owner
    ? `${owner.display_name || owner.email} (${owner.email})`
    : "—";
  const speakers = Object.values(meeting.speaker_map || {}).filter(Boolean);
  const participants = (meeting.participants || []).filter(Boolean);
  return `
    <dl class="monitor-meta-grid">
      <div class="monitor-meta-item">
        <dt>عنوان</dt>
        <dd>${escapeHtml(meeting.title || "جلسه بدون عنوان")}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>وضعیت</dt>
        <dd>${escapeHtml(MEETING_STATUS_LABELS[meeting.status] || meeting.status)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>ایجادکننده</dt>
        <dd>${escapeHtml(ownerLabel)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>شناسه جلسه</dt>
        <dd dir="ltr">${escapeHtml(meeting.id)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>تاریخ ایجاد</dt>
        <dd>${escapeHtml(formatDate(meeting.created_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>شروع</dt>
        <dd>${escapeHtml(formatDate(meeting.started_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>پایان</dt>
        <dd>${escapeHtml(formatDate(meeting.stopped_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>ضبط</dt>
        <dd>${meeting.has_recording ? "دارد" : "ندارد"}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>سخنگوها</dt>
        <dd>${escapeHtml(speakers.length ? speakers.join("، ") : "—")}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>شرکت‌کنندگان</dt>
        <dd>${escapeHtml(participants.length ? participants.join("، ") : "—")}</dd>
      </div>
    </dl>
  `;
}

function renderMonitorSummary(minutes, insights) {
  const summary =
    (minutes && minutes.summary && minutes.summary.trim()) ||
    (insights && insights.summary && insights.summary.trim()) ||
    "";
  if (!summary) {
    return '<p class="monitor-empty-hint">خلاصه‌ای برای این جلسه ثبت نشده است.</p>';
  }
  const extras = [];
  if (insights) {
    if ((insights.highlights || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>نکات برجسته</dt><dd>${escapeHtml(
          insights.highlights.join(" · ")
        )}</dd></div>`
      );
    }
    if ((insights.decisions || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>تصمیمات (insights)</dt><dd>${escapeHtml(
          insights.decisions.join(" · ")
        )}</dd></div>`
      );
    }
    if ((insights.action_items || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>اقدامات</dt><dd>${escapeHtml(
          insights.action_items.join(" · ")
        )}</dd></div>`
      );
    }
  }
  return `
    <p class="monitor-summary-text">${escapeHtml(summary)}</p>
    ${extras.length ? `<dl class="monitor-meta-grid" style="margin-top:1rem">${extras.join("")}</dl>` : ""}
  `;
}

function renderMonitorTranscript(segments, speakerMap) {
  const list = (segments || []).filter(
    (s) => !s.provisional && (s.text || "").trim()
  );
  if (!list.length) {
    return '<p class="monitor-empty-hint">متن STT برای این جلسه موجود نیست.</p>';
  }
  const rows = list
    .slice()
    .sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0))
    .map((seg) => {
      const speakers = seg.is_overlap
        ? seg.overlap_speakers && seg.overlap_speakers.length
          ? seg.overlap_speakers
          : [seg.speaker_id]
        : [seg.speaker_id];
      const chips = speakers
        .map((spk) => {
          const color = speakerColor(spk);
          const label = speakerLabel(spk, speakerMap);
          return `<span class="monitor-speaker-chip">
            <span class="monitor-speaker-dot" style="background:${color}"></span>
            <span style="color:${color}">${escapeHtml(label)}</span>
          </span>`;
        })
        .join("");
      return `<div class="monitor-transcript-row">
        <div class="monitor-transcript-meta">
          <span>${chips}</span>
          <span>${formatTs(seg.start_ms)} – ${formatTs(seg.end_ms)}</span>
        </div>
        <div class="monitor-transcript-text">${escapeHtml(seg.text || "")}</div>
      </div>`;
    })
    .join("");
  return `<div class="monitor-transcript-list">${rows}</div>`;
}

function buildMinutesPrintSheet(data, meetingId, styleScope = ".monitor-minutes-preview") {
  const s = styleScope;
  const isPrintRoot = s === "#minutesPrintRoot";
  const sheetMinHeight = isPrintRoot ? "277mm" : "240mm";
  const sheetHeight = isPrintRoot ? "277mm" : "auto";
  const subject = escapeHtml(data.subject || "");
  const dateFull = escapeHtml(data.meeting_date || "");
  const dateOnly = escapeHtml(
    String(data.meeting_date || "").split(/\s+/)[0] || ""
  );
  const timeOnly = escapeHtml(
    (String(data.meeting_date || "").match(/\d{1,2}:\d{2}/) || [""])[0]
  );
  const location = escapeHtml(data.location || "");
  const secretary = escapeHtml(data.secretary || "");
  const attendees = escapeHtml((data.attendees || []).join("، "));
  const absentees = escapeHtml((data.absentees || []).join("، "));
  const idLabel = escapeHtml(meetingId || "—");

  const decisions = [...(data.decisions || [])].filter(
    (d) => d.description || d.executor || d.due_date
  );
  const minRows = 8;
  while (decisions.length < minRows) {
    decisions.push({ description: "", executor: "", due_date: "", status: "" });
  }

  const decisionRows = decisions
    .map((d, idx) => {
      const numbered = !!(d.description || d.executor || d.due_date);
      return `<tr>
          <td class="num">${numbered ? toPersianDigits(idx + 1) : ""}</td>
          <td class="desc">${escapeHtml(d.description || "")}</td>
          <td class="center">${escapeHtml(d.executor || "")}</td>
          <td class="center">${escapeHtml(d.due_date || "")}</td>
        </tr>`;
    })
    .join("");

  return `
<style>
  ${s} .minutes-print-sheet {
    width: 100%;
    min-height: ${sheetMinHeight};
    ${isPrintRoot ? `height: ${sheetHeight};` : ""}
    display: flex;
    flex-direction: column;
    border: 1.6px solid #000;
    overflow: hidden;
    background: #fff;
    color: #000;
    font-family: Vazirmatn, Tahoma, 'Segoe UI', sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    direction: rtl;
  }
  ${s} .minutes-print-sheet * {
    font-family: inherit;
    box-sizing: border-box;
  }
  ${s} table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }
  ${s} td,
  ${s} th {
    border: 1px solid #000;
    padding: 5px 6px;
    vertical-align: middle;
    overflow: hidden;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }
  ${s} .head-logo {
    width: 20%;
    text-align: center;
    padding: 8px 4px;
  }
  ${s} .head-title {
    width: 48%;
    text-align: center;
    font-size: ${isPrintRoot ? "18pt" : "16pt"};
    font-weight: 700;
  }
  ${s} .head-id {
    width: 32%;
    text-align: center;
    font-size: 7.5pt;
    line-height: 1.35;
    padding: 6px 8px;
    word-break: break-all;
  }
  ${s} .head-id .id-label {
    display: block;
    font-weight: 700;
    margin-bottom: 3px;
    font-size: 8pt;
  }
  ${s} .head-id .id-value {
    display: block;
    font-size: 7pt;
    direction: ltr;
    unicode-bidi: isolate;
  }
  ${s} .brand {
    margin-top: 2px;
    font-size: 8.5pt;
    font-weight: 700;
  }
  ${s} .lbl {
    width: 11%;
    font-weight: 700;
    white-space: nowrap;
    font-size: 9.5pt;
    background: #fafafa;
  }
  ${s} .val {
    font-size: 9.5pt;
    max-width: 0;
  }
  ${s} .field {
    font-size: 9.5pt;
    white-space: nowrap;
  }
  ${s} .field b {
    font-weight: 700;
    margin-inline-end: 6px;
  }
  ${s} .vlabel {
    width: 28px;
    max-width: 28px;
    text-align: center;
    font-weight: 700;
    font-size: 9pt;
    writing-mode: vertical-rl;
    transform: rotate(180deg);
    letter-spacing: 0.12em;
    padding: 6px 2px;
    background: #fafafa;
  }
  ${s} .people {
    vertical-align: top;
    font-size: 9.5pt;
    line-height: 1.6;
    min-height: 36px;
  }
  ${s} .attach {
    width: 28%;
    text-align: center;
    font-size: 8.5pt;
    white-space: nowrap;
  }
  ${s} .box {
    display: inline-block;
    width: 10px;
    height: 10px;
    border: 1px solid #000;
    margin-inline: 2px 3px;
    vertical-align: -1px;
  }
  ${s} .time-cell { padding: 0; }
  ${s} .time-cell table td {
    border: 0;
    border-bottom: 1px solid #000;
    padding: 4px 6px;
    font-size: 9pt;
  }
  ${s} .time-cell table tr:last-child td { border-bottom: 0; }
  ${s} .grow {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  ${s} .grow > table {
    flex: 1 1 auto;
    height: 100%;
  }
  ${s} .decisions thead th {
    background: #f6e59a;
    text-align: center;
    font-weight: 700;
    font-size: 9pt;
    padding: 4px 3px;
  }
  ${s} .decisions tbody td {
    height: 7.2mm;
    font-size: 9pt;
    vertical-align: top;
    padding: 3px 4px;
  }
  ${s} .num { width: 8%; text-align: center; vertical-align: middle !important; }
  ${s} .desc { width: 54%; }
  ${s} .center { width: 19%; text-align: center; vertical-align: middle !important; }
  ${s} .sign-wrap { height: 28mm; }
  ${s} .sign-wrap td { height: 28mm; vertical-align: top; }
  ${s} .summary-box {
    border: 1px solid #000;
    border-top: 0;
    padding: 8px 10px;
    font-size: 9.5pt;
    white-space: pre-wrap;
    min-height: 48px;
  }
</style>
<div class="minutes-print-sheet">
  <table>
    <tr>
      <td class="head-logo">
        <svg width="68" height="32" viewBox="0 0 36 17" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <path d="M8.5 0.5C12.9183 0.5 16.5 4.08172 16.5 8.5C16.5 12.9183 12.9183 16.5 8.5 16.5C4.08172 16.5 0.5 12.9183 0.5 8.5C0.5 4.08172 4.08172 0.5 8.5 0.5Z" stroke="#B8860B" stroke-width="1.2"/>
          <path d="M27.5 17C32.1944 17 36 13.1944 36 8.5C36 3.80558 32.1944 0 27.5 0C22.8056 0 19 3.80558 19 8.5C19 13.1944 22.8056 17 27.5 17Z" fill="#B8860B"/>
        </svg>
        <div class="brand">Distill</div>
      </td>
      <td class="head-title">فرم صورت جلسه</td>
      <td class="head-id">
        <span class="id-label">شناسه جلسه</span>
        <span class="id-value">${idLabel}</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="lbl">موضوع:</td>
      <td class="val" colspan="2">${subject}</td>
      <td class="field"><b>تاریخ:</b>${dateOnly || dateFull}</td>
      <td class="val" style="text-align:center; width:14%;">
        <b>صفحه</b><br/>${toPersianDigits(1)} از ${toPersianDigits(1)}
      </td>
    </tr>
    <tr>
      <td class="lbl">محل برگزاری:</td>
      <td class="val" colspan="2">${location}</td>
      <td class="time-cell" colspan="2">
        <table>
          <tr><td><b>شروع:</b> ${timeOnly || dateFull}</td></tr>
          <tr><td><b>دبیرجلسه:</b> ${secretary}</td></tr>
        </table>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">حاضرین</td>
      <td class="people" style="width:64%;">${attendees}</td>
      <td class="attach">
        <span><span class="box"></span>پیوست دارد</span>
        &nbsp;
        <span><span class="box"></span>ندارد</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">غائبین</td>
      <td class="people">${absentees}</td>
    </tr>
  </table>

  <div class="summary-box"><b>خلاصه:</b> ${escapeHtml(data.summary || "")}</div>

  <div class="grow">
    <table class="decisions">
      <thead>
        <tr>
          <th class="num">ردیف</th>
          <th class="desc">شرح مصوبات/ پیشنهادات/ پیگیری ها</th>
          <th class="center">مجری</th>
          <th class="center">سر رسید</th>
        </tr>
      </thead>
      <tbody>
        ${decisionRows}
      </tbody>
    </table>
  </div>

  <table class="sign-wrap">
    <tr>
      <td class="vlabel">امضاء حاضرین</td>
      <td></td>
    </tr>
  </table>
</div>`;
}

function renderMonitorMinutes(minutes, meetingId) {
  if (!minutes) {
    return '<p class="monitor-empty-hint">صورت جلسه‌ای برای این جلسه ثبت نشده است.</p>';
  }
  return `
    <div class="monitor-minutes-toolbar">
      <button type="button" id="monitorMinutesPrintBtn" class="btn btn-ghost btn-sm">
        پرینت
      </button>
    </div>
    <div class="monitor-minutes-preview">${buildMinutesPrintSheet(
      minutes,
      meetingId
    )}</div>`;
}

async function printMonitorMinutes() {
  if (!monitorPrintContext) return;

  const { minutes, meetingId } = monitorPrintContext;
  let root = document.getElementById("minutesPrintRoot");
  if (!root) {
    root = document.createElement("div");
    root.id = "minutesPrintRoot";
    root.setAttribute("aria-hidden", "true");
    document.body.appendChild(root);
  }
  root.innerHTML = buildMinutesPrintSheet(minutes, meetingId, "#minutesPrintRoot");

  const prevTitle = document.title;
  document.title = "فرم صورت جلسه";
  document.body.classList.add("is-printing-minutes");

  const cleanup = () => {
    document.body.classList.remove("is-printing-minutes");
    document.title = prevTitle;
    root.innerHTML = "";
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);

  await new Promise((resolve) => requestAnimationFrame(() => resolve()));
  try {
    if (document.fonts?.load) {
      await Promise.all([
        document.fonts.load("400 12px Vazirmatn"),
        document.fonts.load("700 12px Vazirmatn"),
      ]);
    }
  } catch (_) {
    /* ignore */
  }

  try {
    window.focus();
    window.print();
  } catch (err) {
    cleanup();
    console.error(err);
    alert(`پرینت ناموفق بود: ${err.message || err}`);
  }
}

function populateMonitor(data) {
  const meeting = data.meeting || {};
  const titleEl = document.getElementById("monitorTitle");
  titleEl.textContent = meeting.title
    ? `نظارت: ${meeting.title}`
    : "نظارت بر جلسه";

  document.getElementById("monitorTabInfo").innerHTML =
    renderMonitorInfo(meeting);
  document.getElementById("monitorTabSummary").innerHTML = renderMonitorSummary(
    data.minutes,
    data.insights
  );
  document.getElementById("monitorTabTranscript").innerHTML =
    renderMonitorTranscript(data.segments, meeting.speaker_map || {});
  monitorPrintContext =
    data.minutes && meeting.id
      ? { minutes: data.minutes, meetingId: meeting.id }
      : null;
  document.getElementById("monitorTabMinutes").innerHTML = renderMonitorMinutes(
    data.minutes,
    meeting.id
  );
}

async function openMeetingMonitor(meetingId) {
  const overlay = document.getElementById("monitorOverlay");
  const loading = document.getElementById("monitorLoading");
  const errorBox = document.getElementById("monitorError");
  const body = document.getElementById("monitorBody");

  overlay.classList.remove("hidden");
  loading.hidden = false;
  errorBox.hidden = true;
  body.classList.add("hidden");
  setMonitorTab("info");

  const res = await fetch(`/admin/meetings/${meetingId}`);
  loading.hidden = true;

  if (res.status === 401) {
    window.location.href = "/login?next=/admin";
    return;
  }
  if (res.status === 403) {
    window.location.href = "/dashboard";
    return;
  }
  if (!res.ok) {
    errorBox.textContent =
      res.status === 404 ? "جلسه یافت نشد." : "خطا در بارگذاری جزئیات جلسه";
    errorBox.hidden = false;
    return;
  }

  const data = await res.json();
  populateMonitor(data);
  body.classList.remove("hidden");
}

function bindMonitorUi() {
  const overlay = document.getElementById("monitorOverlay");
  const closeBtn = document.getElementById("monitorCloseBtn");

  closeBtn.addEventListener("click", closeMeetingMonitor);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeMeetingMonitor();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !overlay.classList.contains("hidden")) {
      closeMeetingMonitor();
    }
  });
  document.querySelectorAll("[data-monitor-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setMonitorTab(btn.dataset.monitorTab));
  });
  document.getElementById("monitorTabMinutes").addEventListener("click", (ev) => {
    if (ev.target.id === "monitorMinutesPrintBtn") {
      printMonitorMinutes().catch((err) => {
        console.error(err);
        alert(`پرینت ناموفق بود: ${err.message || err}`);
      });
    }
  });
}

(async function init() {
  const user = await distillAuth.requireAuth();
  if (!user) return;
  if (user.role !== "admin") {
    window.location.href = "/dashboard";
    return;
  }

  document.getElementById("userName").textContent =
    user.display_name || user.email;

  document.getElementById("logoutBtn").addEventListener("click", () => {
    distillAuth.logout();
  });

  document.getElementById("newMeetingBtn").addEventListener("click", createMeeting);
  document.getElementById("emptyNewBtn").addEventListener("click", createMeeting);

  bindMonitorUi();

  await Promise.all([loadUsers(user.id), loadAllMeetings(), loadOwnMeetings()]);
})();
