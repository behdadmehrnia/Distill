const STATUS_LABELS = {
  created: "ایجاد شده",
  recording: "در حال ضبط",
  processing: "در حال پردازش",
  stopped: "پایان یافته",
  failed: "ناموفق",
};

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

function statusClass(status) {
  if (status === "recording" || status === "processing") return "is-active";
  if (status === "failed") return "is-failed";
  return "is-done";
}

async function createMeeting() {
  const res = await fetch("/meetings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "", start: false }),
  });
  if (res.status === 401) {
    window.location.href = "/login?next=" + encodeURIComponent("/dashboard");
    return;
  }
  if (!res.ok) throw new Error("create failed");
  const data = await res.json();
  window.location.href = `/assistant/${data.id}`;
}

async function deleteMeeting(id, cardEl) {
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

function renderMeeting(m) {
  const li = document.createElement("li");
  li.className = "meeting-card";

  const meta = document.createElement("div");
  meta.className = "meeting-card-main";

  const title = document.createElement("h2");
  title.className = "meeting-card-title";
  title.textContent = m.title || "جلسه بدون عنوان";

  const status = document.createElement("span");
  status.className = `meeting-status ${statusClass(m.status)}`;
  status.textContent = STATUS_LABELS[m.status] || m.status;

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
  delBtn.addEventListener("click", () => deleteMeeting(m.id, li));

  actions.append(openBtn, delBtn);
  li.append(meta, actions);
  return li;
}

async function loadMeetings() {
  const loading = document.getElementById("meetingsLoading");
  const empty = document.getElementById("meetingsEmpty");
  const list = document.getElementById("meetingsList");

  const res = await fetch("/meetings");
  loading.hidden = true;

  if (res.status === 401) {
    window.location.href = "/login?next=/dashboard";
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
  meetings.forEach((m) => list.appendChild(renderMeeting(m)));
  list.hidden = false;
}

(async function init() {
  const user = await distillAuth.requireAuth();
  if (!user) return;

  document.getElementById("userName").textContent =
    user.display_name || user.email;

  document.getElementById("logoutBtn").addEventListener("click", () => {
    distillAuth.logout();
  });

  document.getElementById("newMeetingBtn").addEventListener("click", createMeeting);
  document.getElementById("emptyNewBtn").addEventListener("click", createMeeting);

  await loadMeetings();
})();
