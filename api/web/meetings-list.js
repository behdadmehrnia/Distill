(function () {
  const {
    MEETING_STATUS_LABELS,
    meetingStatusClass,
    formatDate,
    redirectIfUnauthorized,
  } = window.distill;

  function renderMeetingCard(m, { onDelete } = {}) {
    const li = document.createElement("li");
    li.className = "meeting-card";

    const meta = document.createElement("div");
    meta.className = "meeting-card-main";

    const title = document.createElement("h2");
    title.className = "meeting-card-title";
    title.textContent = m.title || "Untitled meeting";

    const status = document.createElement("span");
    status.className = `meeting-status ${meetingStatusClass(m.status)}`;
    status.textContent = MEETING_STATUS_LABELS[m.status] || m.status;

    const info = document.createElement("p");
    info.className = "meeting-card-meta";
    const parts = [formatDate(m.created_at)];
    if (m.has_recording) parts.push("Has recording");
    info.textContent = parts.join(" · ");

    meta.append(title, status, info);

    const actions = document.createElement("div");
    actions.className = "meeting-card-actions";

    const openBtn = document.createElement("a");
    openBtn.className = "btn btn-primary btn-sm";
    openBtn.href = `/assistant/${m.id}`;
    openBtn.textContent =
      m.status === "recording" || m.status === "processing" ? "Resume" : "Open";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-ghost btn-sm";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", () => onDelete(m.id, li));

    actions.append(openBtn, delBtn);
    li.append(meta, actions);
    return li;
  }

  async function deleteMeeting(id, cardEl, { onAfterDelete } = {}) {
    if (!confirm("Delete this meeting?")) return;
    const res = await fetch(`/meetings/${id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Could not delete the meeting");
      return;
    }
    cardEl.remove();
    const list = document.getElementById("meetingsList");
    if (list && !list.children.length) {
      list.hidden = true;
      const empty = document.getElementById("meetingsEmpty");
      if (empty) empty.hidden = false;
    }
    if (onAfterDelete) await onAfterDelete();
  }

  async function createMeeting({ loginNext = "/dashboard" } = {}) {
    const res = await fetch("/meetings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "", start: false }),
    });
    if (redirectIfUnauthorized(res, loginNext)) return;
    if (!res.ok) throw new Error("create failed");
    const data = await res.json();
    window.location.href = `/assistant/${data.id}`;
  }

  async function loadMeetings({ loginNext = "/dashboard", onDelete } = {}) {
    const loading = document.getElementById("meetingsLoading");
    const empty = document.getElementById("meetingsEmpty");
    const list = document.getElementById("meetingsList");

    const res = await fetch("/meetings");
    if (loading) loading.hidden = true;

    if (redirectIfUnauthorized(res, loginNext)) return;
    if (!res.ok) {
      if (loading) {
        loading.textContent = "Could not load meetings";
        loading.hidden = false;
      }
      return;
    }

    const data = await res.json();
    const meetings = data.meetings || [];
    if (!meetings.length) {
      if (empty) empty.hidden = false;
      return;
    }

    if (list) {
      list.innerHTML = "";
      const handleDelete = (id, cardEl) =>
        deleteMeeting(id, cardEl, { onAfterDelete: onDelete });
      meetings.forEach((m) => list.appendChild(renderMeetingCard(m, { onDelete: handleDelete })));
      list.hidden = false;
    }
  }

  function bindNewMeetingButtons({ loginNext = "/dashboard" } = {}) {
    const handler = () => createMeeting({ loginNext });
    document.getElementById("newMeetingBtn")?.addEventListener("click", handler);
    document.getElementById("emptyNewBtn")?.addEventListener("click", handler);
  }

  window.distillMeetings = {
    renderMeetingCard,
    deleteMeeting,
    createMeeting,
    loadMeetings,
    bindNewMeetingButtons,
  };
})();
