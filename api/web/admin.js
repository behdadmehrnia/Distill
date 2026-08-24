const ROLE_LABELS = { admin: "مدیر", user: "کاربر" };

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

  await loadUsers(user.id);
})();
