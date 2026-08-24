const ROLE_LABELS = { admin: "مدیر", user: "کاربر" };
const MEETING_STATUS_LABELS = {
  created: "ایجاد شده",
  recording: "در حال ضبط",
  processing: "در حال پردازش",
  stopped: "پایان یافته",
  failed: "ناموفق",
};

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

  const activeBtn = document.createElement("button");
  activeBtn.type = "button";
  activeBtn.className = "btn btn-ghost btn-sm";
  activeBtn.textContent = u.is_active ? "غیرفعال کردن" : "فعال کردن";
  if (u.id === currentUserId && u.is_active) {
    activeBtn.disabled = true;
    activeBtn.title = "نمی‌توانید حساب خودتان را غیرفعال کنید";
  }
  activeBtn.addEventListener("click", async () => {
    activeBtn.disabled = true;
    const ok = await setActive(u.id, !u.is_active);
    activeBtn.disabled = false;
    if (ok) onChange();
  });

  actions.append(roleBtn, activeBtn);
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

  await Promise.all([loadUsers(user.id), loadAllMeetings(), loadOwnMeetings()]);
})();
