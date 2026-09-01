(function () {
  const { escapeHtml } = window.distill;


  function buildStyles(styleScope, isPrintRoot) {
    const sheetMinHeight = isPrintRoot ? "277mm" : "240mm";
    const sheetHeight = isPrintRoot ? "277mm" : "auto";
    const headTitleSize = isPrintRoot ? "18pt" : "16pt";

    return `
  ${styleScope} .minutes-print-sheet {
    width: 100%;
    min-height: ${sheetMinHeight};
    ${isPrintRoot ? `height: ${sheetHeight};` : ""}
    display: flex;
    flex-direction: column;
    border: 1.6px solid #000;
    overflow: hidden;
    background: #fff;
    color: #000;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    direction: ltr;
    ${isPrintRoot ? "-webkit-font-smoothing: antialiased;" : ""}
  }
  ${styleScope} .minutes-print-sheet * {
    font-family: inherit;
    box-sizing: border-box;
  }
  ${styleScope} table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }
  ${styleScope} td,
  ${styleScope} th {
    border: 1px solid #000;
    padding: 5px 6px;
    vertical-align: middle;
    overflow: hidden;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }
  ${styleScope} .head-logo {
    width: 20%;
    text-align: center;
    padding: 8px 4px;
  }
  ${styleScope} .head-title {
    width: 48%;
    text-align: center;
    font-size: ${headTitleSize};
    font-weight: 700;
  }
  ${styleScope} .head-id {
    width: 32%;
    text-align: center;
    font-size: 7.5pt;
    line-height: 1.35;
    padding: 6px 8px;
    word-break: break-all;
  }
  ${styleScope} .head-id .id-label {
    display: block;
    font-weight: 700;
    margin-bottom: 3px;
    font-size: 8pt;
  }
  ${styleScope} .head-id .id-value {
    display: block;
    font-size: 7pt;
    direction: ltr;
    unicode-bidi: isolate;
  }
  ${styleScope} .brand {
    margin-top: 2px;
    font-size: 8.5pt;
    font-weight: 700;
  }
  ${styleScope} .lbl {
    width: 11%;
    font-weight: 700;
    white-space: nowrap;
    font-size: 9.5pt;
    background: #fafafa;
  }
  ${styleScope} .val {
    font-size: 9.5pt;
    max-width: 0;
  }
  ${styleScope} .field {
    font-size: 9.5pt;
    white-space: nowrap;
  }
  ${styleScope} .field b {
    font-weight: 700;
    margin-inline-end: 6px;
  }
  ${styleScope} .minutes-no { width: 24%; }
  ${styleScope} .minutes-no .blank {
    display: inline-block;
    min-width: 8.5ch;
    letter-spacing: 0.12em;
    vertical-align: bottom;
  }
  ${styleScope} .date-field { width: 18%; }
  ${styleScope} .secretary-field { width: 34%; }
  ${styleScope} .meta-2 .lbl { width: 12%; }
  ${styleScope} .vlabel {
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
  ${styleScope} .people {
    vertical-align: top;
    font-size: 9.5pt;
    line-height: 1.6;
    min-height: 36px;
  }
  ${styleScope} .attach {
    width: 28%;
    text-align: center;
    font-size: 8.5pt;
    white-space: nowrap;
  }
  ${styleScope} .box {
    display: inline-block;
    width: 10px;
    height: 10px;
    border: 1px solid #000;
    margin-inline: 2px 3px;
    vertical-align: -1px;
  }
  ${styleScope} .time-cell { padding: 0; }
  ${styleScope} .time-cell table td {
    border: 0;
    border-bottom: 1px solid #000;
    padding: 4px 6px;
    font-size: 9pt;
  }
  ${styleScope} .time-cell table tr:last-child td { border-bottom: 0; }
  ${styleScope} .grow {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  ${styleScope} .grow > table {
    flex: 1 1 auto;
    height: 100%;
  }
  ${styleScope} .decisions { height: 100%; }
  ${styleScope} .decisions thead th {
    background: #ececec;
    text-align: center;
    font-weight: 700;
    font-size: 9pt;
    padding: 4px 3px;
  }
  ${styleScope} .decisions tbody td {
    height: 7.2mm;
    font-size: 9pt;
    vertical-align: top;
    padding: 3px 4px;
  }
  ${styleScope} .num { width: 8%; text-align: center; vertical-align: middle !important; }
  ${styleScope} .desc { width: 54%; }
  ${styleScope} .center { width: 19%; text-align: center; vertical-align: middle !important; }
  ${styleScope} .sign-wrap { height: 28mm; }
  ${styleScope} .sign-wrap td { height: 28mm; vertical-align: top; }
  ${styleScope} .summary-box {
    border: 1px solid #000;
    border-top: 0;
    padding: 8px 10px;
    font-size: 9.5pt;
    white-space: pre-wrap;
    min-height: 48px;
  }`;
  }

  // Ink-on-paper version of the Distill mark: three narrowing strokes
  // converging on a drop.
  const LOGO_SVG = `<svg width="30" height="30" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <g stroke="#111111" stroke-width="2.2" stroke-linecap="round">
            <path d="M8 10h16"/>
            <path d="M11 16h10"/>
            <path d="M14 22h4"/>
          </g>
          <circle cx="16" cy="26.5" r="1.7" fill="#111111"/>
        </svg>`;

  function buildDecisionRows(data, minRows) {
    const decisions = [...(data.decisions || [])].filter(
      (d) => d.description || d.executor || d.due_date
    );
    while (decisions.length < minRows) {
      decisions.push({ description: "", executor: "", due_date: "", status: "" });
    }
    return decisions
      .map((d, idx) => {
        const numbered = !!(d.description || d.executor || d.due_date);
        return `<tr>
          <td class="num">${numbered ? idx + 1 : ""}</td>
          <td class="desc">${escapeHtml(d.description || "")}</td>
          <td class="center">${escapeHtml(d.executor || "")}</td>
          <td class="center">${escapeHtml(d.due_date || "")}</td>
        </tr>`;
      })
      .join("");
  }

  function buildAssistantSheet(data, meetingId, styleScope, minRows) {
    const subject = escapeHtml(data.subject || "");
    const dateFull = escapeHtml(data.meeting_date || "");
    const dateOnly = escapeHtml(String(data.meeting_date || "").split(/\s+/)[0] || "");
    const timeOnly = escapeHtml(
      (String(data.meeting_date || "").match(/\d{1,2}:\d{2}/) || [""])[0]
    );
    const location = escapeHtml(data.location || "");
    const secretary = escapeHtml(data.secretary || "");
    const attendees = escapeHtml((data.attendees || []).join(", "));
    const absentees = escapeHtml((data.absentees || []).join(", "));
    const idLabel = escapeHtml(meetingId || "—");
    const decisionRows = buildDecisionRows(data, minRows);

    return `
<style>${buildStyles(styleScope, styleScope === "#minutesPrintRoot")}</style>
<div class="minutes-print-sheet">
  <table>
    <tr>
      <td class="head-logo">${LOGO_SVG}<div class="brand">Distill</div></td>
      <td class="head-title">Meeting Minutes</td>
      <td class="head-id">
        <span class="id-label">Meeting ID</span>
        <span class="id-value">${idLabel}</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="lbl">Subject:</td>
      <td class="val" colspan="2">${subject}</td>
      <td class="field minutes-no"><b>Minutes no.:</b><span class="blank"></span></td>
      <td class="field date-field"><b>Date:</b>${dateOnly || dateFull}</td>
    </tr>
    <tr class="meta-2">
      <td class="lbl">Location:</td>
      <td class="val" colspan="2">${location}</td>
      <td class="time-cell">
        <table>
          <tr><td><b>Start:</b> ${timeOnly || dateFull}</td></tr>
          <tr><td><b>End:</b></td></tr>
        </table>
      </td>
      <td class="val" style="text-align:center; width:14%;">
        <b>Page</b><br/>1 of 1
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">Attendees</td>
      <td class="people" style="width:64%;">${attendees}</td>
      <td class="attach">
        <span><span class="box"></span>Attachment</span>
        &nbsp;
        <span><span class="box"></span>None</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">Absentees</td>
      <td class="people" style="width:58%;">${absentees}</td>
      <td class="field secretary-field"><b>Secretary:</b>${secretary}</td>
    </tr>
  </table>

  <div class="grow">
    <table class="decisions">
      <thead>
        <tr>
          <th class="num">#</th>
          <th class="desc">Decisions / proposals / follow-ups</th>
          <th class="center">Owner</th>
          <th class="center">Due</th>
        </tr>
      </thead>
      <tbody>${decisionRows}</tbody>
    </table>
  </div>

  <table class="sign-wrap">
    <tr>
      <td class="vlabel">Signatures</td>
      <td></td>
    </tr>
  </table>
</div>`;
  }

  function buildMonitorSheet(data, meetingId, styleScope, minRows) {
    const subject = escapeHtml(data.subject || "");
    const dateFull = escapeHtml(data.meeting_date || "");
    const dateOnly = escapeHtml(String(data.meeting_date || "").split(/\s+/)[0] || "");
    const timeOnly = escapeHtml(
      (String(data.meeting_date || "").match(/\d{1,2}:\d{2}/) || [""])[0]
    );
    const location = escapeHtml(data.location || "");
    const secretary = escapeHtml(data.secretary || "");
    const attendees = escapeHtml((data.attendees || []).join(", "));
    const absentees = escapeHtml((data.absentees || []).join(", "));
    const idLabel = escapeHtml(meetingId || "—");
    const decisionRows = buildDecisionRows(data, minRows);

    return `
<style>${buildStyles(styleScope, styleScope === "#minutesPrintRoot")}</style>
<div class="minutes-print-sheet">
  <table>
    <tr>
      <td class="head-logo">${LOGO_SVG}<div class="brand">Distill</div></td>
      <td class="head-title">Meeting Minutes</td>
      <td class="head-id">
        <span class="id-label">Meeting ID</span>
        <span class="id-value">${idLabel}</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="lbl">Subject:</td>
      <td class="val" colspan="2">${subject}</td>
      <td class="field"><b>Date:</b>${dateOnly || dateFull}</td>
      <td class="val" style="text-align:center; width:14%;">
        <b>Page</b><br/>1 of 1
      </td>
    </tr>
    <tr>
      <td class="lbl">Location:</td>
      <td class="val" colspan="2">${location}</td>
      <td class="time-cell" colspan="2">
        <table>
          <tr><td><b>Start:</b> ${timeOnly || dateFull}</td></tr>
          <tr><td><b>Secretary:</b> ${secretary}</td></tr>
        </table>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">Attendees</td>
      <td class="people" style="width:64%;">${attendees}</td>
      <td class="attach">
        <span><span class="box"></span>Attachment</span>
        &nbsp;
        <span><span class="box"></span>None</span>
      </td>
    </tr>
  </table>

  <table>
    <tr>
      <td class="vlabel">Absentees</td>
      <td class="people">${absentees}</td>
    </tr>
  </table>

  <div class="summary-box"><b>Summary:</b> ${escapeHtml(data.summary || "")}</div>

  <div class="grow">
    <table class="decisions">
      <thead>
        <tr>
          <th class="num">#</th>
          <th class="desc">Decisions / proposals / follow-ups</th>
          <th class="center">Owner</th>
          <th class="center">Due</th>
        </tr>
      </thead>
      <tbody>${decisionRows}</tbody>
    </table>
  </div>

  <table class="sign-wrap">
    <tr>
      <td class="vlabel">Signatures</td>
      <td></td>
    </tr>
  </table>
</div>`;
  }

  function buildMinutesPrintSheet(data, meetingId, options = {}) {
    const {
      styleScope = "#minutesPrintRoot",
      variant = styleScope === "#minutesPrintRoot" ? "assistant" : "monitor",
      minRows = variant === "assistant" ? 12 : 8,
    } = options;

    if (variant === "monitor") {
      return buildMonitorSheet(data, meetingId, styleScope, minRows);
    }
    return buildAssistantSheet(data, meetingId, styleScope, minRows);
  }

  async function print(data, meetingId, options = {}) {
    let root = document.getElementById("minutesPrintRoot");
    if (!root) {
      root = document.createElement("div");
      root.id = "minutesPrintRoot";
      root.setAttribute("aria-hidden", "true");
      document.body.appendChild(root);
    }
    root.innerHTML = buildMinutesPrintSheet(data, meetingId, {
      styleScope: "#minutesPrintRoot",
      ...options,
    });

    const prevTitle = document.title;
    document.title = "Meeting Minutes";
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
      window.focus();
      window.print();
    } catch (err) {
      cleanup();
      console.error(err);
      alert(`Print failed: ${err.message || err}`);
    }
  }

  window.distillMinutesPrint = {
    buildMinutesPrintSheet,
    print,
  };
})();
