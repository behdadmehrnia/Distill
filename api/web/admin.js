const ROLE_LABELS = { admin: "Admin", user: "User" };

const {
  MEETING_STATUS_LABELS,
  meetingStatusClass,
  formatDate,
  escapeHtml,
  formatTs,
  speakerColor,
  speakerLabel,
  parseApiError,
  redirectIfUnauthorized,
  bindAuthChrome,
} = distill;

async function setRole(userId, role) {
  const res = await fetch(`/admin/users/${userId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) {
    alert(await parseApiError(res, "Could not change role"));
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
    alert(await parseApiError(res, "Could not change status"));
    return false;
  }
  return true;
}

async function deleteUser(userId, email) {
  const label = email || "this user";
  if (!confirm(`Permanently delete ${label}? This cannot be undone.`)) {
    return false;
  }
  const res = await fetch(`/admin/users/${userId}`, { method: "DELETE" });
  if (!res.ok) {
    alert(await parseApiError(res, "Could not delete the user"));
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
  statusBadge.textContent = u.is_active ? "Active" : "Inactive";
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
    roleBtn.textContent = u.role === "admin" ? "Demote to user" : "Promote to admin";
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
    deactivateBtn.textContent = "Deactivate";
    if (u.id === currentUserId) {
      deactivateBtn.disabled = true;
      deactivateBtn.title = "You cannot deactivate your own account";
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
    reactivateBtn.textContent = "Reactivate";
    reactivateBtn.addEventListener("click", async () => {
      reactivateBtn.disabled = true;
      const ok = await setActive(u.id, true);
      reactivateBtn.disabled = false;
      if (ok) onChange();
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn btn-ghost btn-sm";
    deleteBtn.textContent = "Delete user";
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
    errorBox.textContent = "Could not load users";
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
  tr.title = "Click to monitor";

  const titleCell = document.createElement("td");
  titleCell.textContent = m.title || "Untitled meeting";

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
  recordingCell.textContent = m.has_recording ? "Yes" : "—";

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
    errorBox.textContent = "Could not load meetings";
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
        <dt>Title</dt>
        <dd>${escapeHtml(meeting.title || "Untitled meeting")}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Status</dt>
        <dd>${escapeHtml(MEETING_STATUS_LABELS[meeting.status] || meeting.status)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Created by</dt>
        <dd>${escapeHtml(ownerLabel)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Meeting ID</dt>
        <dd dir="ltr">${escapeHtml(meeting.id)}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Created</dt>
        <dd>${escapeHtml(formatDate(meeting.created_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Started</dt>
        <dd>${escapeHtml(formatDate(meeting.started_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Ended</dt>
        <dd>${escapeHtml(formatDate(meeting.stopped_at))}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Recording</dt>
        <dd>${meeting.has_recording ? "Yes" : "No"}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Speakers</dt>
        <dd>${escapeHtml(speakers.length ? speakers.join(", ") : "—")}</dd>
      </div>
      <div class="monitor-meta-item">
        <dt>Participants</dt>
        <dd>${escapeHtml(participants.length ? participants.join(", ") : "—")}</dd>
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
    return '<p class="monitor-empty-hint">No summary recorded for this meeting.</p>';
  }
  const extras = [];
  if (insights) {
    if ((insights.highlights || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>Highlights</dt><dd>${escapeHtml(
          insights.highlights.join(" · ")
        )}</dd></div>`
      );
    }
    if ((insights.decisions || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>Decisions</dt><dd>${escapeHtml(
          insights.decisions.join(" · ")
        )}</dd></div>`
      );
    }
    if ((insights.action_items || []).length) {
      extras.push(
        `<div class="monitor-meta-item"><dt>Action items</dt><dd>${escapeHtml(
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
    return '<p class="monitor-empty-hint">No STT transcript available for this meeting.</p>';
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


function renderMonitorMinutes(minutes, meetingId) {
  if (!minutes) {
    return '<p class="monitor-empty-hint">No minutes recorded for this meeting.</p>';
  }
  return `
    <div class="monitor-minutes-toolbar">
      <button type="button" id="monitorMinutesPrintBtn" class="btn btn-ghost btn-sm">
        Print
      </button>
    </div>
    <div class="monitor-minutes-preview">${distillMinutesPrint.buildMinutesPrintSheet(
      minutes,
      meetingId,
      { variant: "monitor" }
    )}</div>`;
}

async function printMonitorMinutes() {
  if (!monitorPrintContext) return;
  const { minutes, meetingId } = monitorPrintContext;
  await distillMinutesPrint.print(minutes, meetingId, { variant: "monitor" });
}

function populateMonitor(data) {
  const meeting = data.meeting || {};
  const titleEl = document.getElementById("monitorTitle");
  titleEl.textContent = meeting.title
    ? `Monitoring: ${meeting.title}`
    : "Meeting monitor";

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
      res.status === 404 ? "Meeting not found." : "Could not load meeting details";
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
        alert(`Print failed: ${err.message || err}`);
      });
    }
  });
}

(async function init() {
  const user = await bindAuthChrome();
  if (!user) return;
  if (user.role !== "admin") {
    window.location.href = "/dashboard";
    return;
  }

  distillMeetings.bindNewMeetingButtons({ loginNext: "/admin" });
  bindMonitorUi();

  await Promise.all([
    loadUsers(user.id),
    loadAllMeetings(),
    distillMeetings.loadMeetings({
      loginNext: "/admin",
      onDelete: loadAllMeetings,
    }),
  ]);
})();
