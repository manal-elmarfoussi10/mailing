let selectedId = null;
let loadedId = null;
let filter = "all";
let pollTimer = null;
let liveCampaignId = null;
let mailboxQuery = "";
let mailboxPage = 0;
let selectedBoxes = new Set();
let pickKey = "";
let settingsFilled = false;
let queueId = null;
let latestData = null;
let activityDays = 7;

const $ = (id) => document.getElementById(id);

document.addEventListener("click", (event) => {
  const button = event.target.closest("#script-generate");
  if (!button) return;
  event.preventDefault();
  generateScript(button);
});

function text(node, value) {
  if (node) node.textContent = value == null ? "" : String(value);
}

function toast(message, kind) {
  const stack = $("toasts");
  if (!stack || !message) return;
  const item = document.createElement("p");
  item.className = `toast ${kind === "bad" ? "bad" : "ok"}`;
  item.textContent = message;
  stack.appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

function bumpNumber(node, value) {
  if (!node) return;
  const next = String(value);
  if (node.textContent && node.textContent !== next) {
    node.classList.remove("bump");
    void node.offsetWidth;
    node.classList.add("bump");
  }
  node.textContent = next;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("Sign in required");
  }
  if (!response.ok) {
    const detail = data.detail || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function parseRoute() {
  const parts = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  const pages = ["mailboxes", "campaigns", "activity", "subscribers", "queues", "deliverability", "media", "offers", "settings", "suppression"];
  if (parts[0] === "campaigns" && parts[1]) return { name: "campaign", campaignId: Number(parts[1]) };
  if (pages.includes(parts[0])) return { name: parts[0] };
  return { name: "overview" };
}

function setHeader(crumb, title) {
  text($("crumb"), crumb);
  text($("header-title"), title);
  document.title = title ? `${title} · Sendora` : "Sendora";
}

function applyRoute(route) {
  const page = route.name === "suppression" ? "subscribers" : route.name;
  document.querySelectorAll("[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    const onCampaigns = route.name === "campaign" && link.dataset.nav === "campaigns";
    link.classList.toggle("active", link.dataset.nav === route.name || onCampaigns);
  });
  const headerCopy = {
    overview: ["Sendora", "Dashboard"],
    mailboxes: ["Mailboxes", "All mailboxes"],
    campaigns: ["Campaigns", "All campaigns"],
    campaign: ["Campaigns", "Campaign"],
    subscribers: ["Audience", "Contacts"],
    suppression: ["Audience", "Suppression"],
    queues: ["Sending", "Queues"],
    deliverability: ["Analytics", "Reports"],
    activity: ["Analytics", "Activity"],
    media: ["Resources", "Media library"],
    offers: ["Resources", "Offers"],
    settings: ["Settings", "General"],
  };
  const copy = headerCopy[route.name] || ["Sendora", "Dashboard"];
  if (route.name === "settings") {
    const active = document.querySelector("[data-settings].on");
    const names = { general: "General", defaults: "Campaign defaults", audience: "Audience", security: "Security", data: "Data" };
    if (active) copy[1] = names[active.dataset.settings] || "General";
  }
  setHeader(copy[0], copy[1]);
  document.body.classList.remove("nav-open");
  if (route.campaignId) selectedId = route.campaignId;
  if (route.name === "suppression") {
    const anchor = $("suppression-anchor");
    if (anchor) requestAnimationFrame(() => anchor.scrollIntoView({ block: "start" }));
  }
}

function campaignFields() {
  const form = new FormData($("campaign-form"));
  return {
    name: form.get("name") || "Campaign",
    subject: form.get("subject") || "",
    from_name: form.get("from_name") || "",
    reply_to: form.get("reply_to") || "",
    html_body: form.get("html_body") || "",
    text_body: form.get("text_body") || "",
    offer_id: Number(form.get("offer_id") || 0),
    ...sendTarget(),
  };
}

function sendTarget() {
  const form = new FormData($("run-form"));
  if (form.get("send_with") === "server") {
    return { server_name: form.get("server_name") || "", mailbox_ids: "" };
  }
  const ids = [...$("mailbox-picks").querySelectorAll("input:checked")].map((el) => el.value);
  return { server_name: "", mailbox_ids: ids.join(",") };
}

function timingFields() {
  const form = new FormData($("run-form"));
  return {
    delay_amount: Number(form.get("delay_amount") || 0),
    delay_unit: form.get("delay_unit"),
    rotation: form.get("rotation"),
    auto_retries: Number(form.get("auto_retries") || 0),
  };
}

async function saveCampaign() {
  if (!selectedId) return;
  await api(`/api/campaigns/${selectedId}`, {
    method: "PATCH",
    body: JSON.stringify({ ...campaignFields(), ...timingFields() }),
  });
}

function fillCampaign(campaign) {
  const form = $("campaign-form");
  form.name.value = campaign.name || "";
  form.subject.value = campaign.subject || "";
  form.from_name.value = campaign.from_name || "";
  form.reply_to.value = campaign.reply_to || "";
  form.html_body.value = campaign.html_body || "";
  form.text_body.value = campaign.text_body || "";
  form.offer_id.value = campaign.offer_id ? String(campaign.offer_id) : "";
  const run = $("run-form");
  run.server_name.value = campaign.server_name || "";
  run.send_with.value = campaign.server_name ? "server" : "mailboxes";
  run.delay_amount.value = campaign.delay_amount;
  run.delay_unit.value = campaign.delay_unit;
  run.rotation.value = campaign.rotation;
  run.auto_retries.value = campaign.auto_retries;
  syncRotationButtons();
}

function clockTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function dailyCap(box) {
  return box.accountKind === "workspace" ? 1500 : 100;
}

function paint(data, route) {
  const running = data.campaigns.some((row) => row.status === "running");
  const paused = data.campaigns.some((row) => row.status === "paused");
  const now = new Date();
  text($("overview-clock"), now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }));
  paintNotices(data);
  if (route && route.name === "campaign" && data.campaign) setHeader("Campaigns", campaignLabel(data.campaign));

  const queued = data.campaigns.reduce((sum, row) => sum + Number(row.queued || 0), 0);
  const sent = data.campaigns.reduce((sum, row) => sum + Number(row.sent || 0), 0);
  const failed = data.campaigns.reduce((sum, row) => sum + Number(row.failed || 0), 0);
  const off = data.mailboxes.filter((row) => !row.enabled).length;
  text($("stat-remaining"), data.capacity.checked ? String(data.capacity.remaining) : "—");
  text($("stat-remaining-detail"), data.capacity.checked
    ? `${data.capacity.checked} mailbox${data.capacity.checked === 1 ? "" : "es"} checked`
    : "Refresh a mailbox to read Google");
  text($("stat-enabled"), String(data.capacity.enabled));
  text($("stat-enabled-detail"), `${data.mailboxes.length} added · ${off} off`);
  const today = data.today || { sent: 0, failed: 0 };
  const sendingCount = data.campaigns.filter((row) => row.status === "running").length;
  const pausedCount = data.campaigns.filter((row) => row.status === "paused").length;
  text($("stat-queued"), String(queued));
  text($("stat-queued-detail"), queued ? "Waiting to send" : "Your queue is currently empty");
  text($("stat-sent"), String(today.sent));
  text($("stat-sent-detail"), `${sent} sent in total`);
  text($("stat-failed"), String(failed));
  $("failed-card").classList.toggle("attention", failed > 0);
  text($("stat-campaigns"), String(sendingCount + pausedCount));
  const people = data.audience ? data.audience.total : 0;
  const groups = (data.groups || []).length;
  text($("stat-campaigns-detail"), data.campaigns.length
    ? `${data.campaigns.length} campaigns · ${people} contacts · ${groups} groups`
    : `${people} contacts · ${groups} groups`);
  text($("system-state"), running ? "Sending" : paused ? "Paused" : "Idle");
  text($("system-mailboxes"), String(data.capacity.enabled));
  text($("system-running"), String(sendingCount));
  text($("system-queue"), queued ? `${queued} waiting` : "empty");
  text($("last-sync"), data.lastSync ? clockTime(data.lastSync) : "not yet");
  latestData = data;
  if ($("boot")) $("boot").hidden = true;
  paintActivity(data.series || []);
  paintAttention(data);

  const live = data.campaigns.find((row) => row.status === "running")
    || data.campaigns.find((row) => row.status === "paused");
  liveCampaignId = live ? live.id : null;
  $("run-bar").hidden = !live;
  if (live) {
    text($("run-name"), live.name);
    text($("run-phase"), live.phase || live.status);
    text($("run-pill"), live.status);
    $("run-pill").className = live.status === "paused" ? "pill warn" : "pill";
    $("run-open").href = `#/campaigns/${live.id}`;
  }
  const boardCampaign = data.campaign && live && data.campaign.id === live.id ? data.campaign : null;
  paintLiveBoard("overview", boardCampaign);
  paintDonut(
    data.campaigns.reduce((sum, row) => sum + Number(row.queued || 0), 0),
    data.campaigns.reduce((sum, row) => sum + Number(row.sent || 0), 0),
    data.campaigns.reduce((sum, row) => sum + Number(row.failed || 0), 0)
  );
  paintOverviewCampaignTable(data.campaigns);
  paintOverviewMailboxTable(data.mailboxes);
  paintQuotaBars(data.mailboxes);
  const overviewEvents = (data.recentEvents && data.recentEvents.length)
    ? data.recentEvents
    : (data.campaign && route.name === "overview" ? data.campaign.events : []);
  paintOverviewLog(overviewEvents);
  $("overview-log").hidden = overviewEvents.length === 0;
  $("overview-empty").hidden = overviewEvents.length > 0;
  text($("overview-empty"), "Activity will appear here when your campaigns start running.");

  text($("today-revenue"), Number(data.todayRevenue || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  paintMailboxes(data.mailboxes);
  paintServers(data.servers || []);
  paintOffers(data.offers || []);
  if ($("conversion-campaign")) fillNamedSelect($("conversion-campaign"), data.campaigns || [], "None");
  paintQueues(data);
  if (route.name === "settings" && data.settings && !settingsFilled) fillSettings(data.settings);
  paintCampaignList(data.campaigns);
  paintActivityChoices(data.campaigns);

  const onCampaign = route.name === "campaign";
  $("campaign-missing").hidden = !(onCampaign && !data.campaign);
  $("campaign-body").hidden = onCampaign && !data.campaign;
  if (!data.campaign) {
    if (onCampaign) text($("phase"), "Idle");
    $("people").replaceChildren();
    $("log").replaceChildren();
    text($("counts"), "");
    $("attachment-list").replaceChildren();
    $("activity-empty").hidden = false;
    return;
  }
  const justLoaded = onCampaign && data.campaign.id !== loadedId;
  if (justLoaded) {
    fillOffers(data.offers || [], data.campaign.offer_id);
    fillCampaign(data.campaign);
    loadedId = data.campaign.id;
    previewHtml();
    loadMedia();
  } else if (onCampaign) {
    fillOffers(data.offers || []);
  }
  if (onCampaign) {
    paintSendChoices(data.mailboxes, data.campaign, justLoaded);
    if (justLoaded) {
      const liveNow = data.campaign.status === "running" || data.campaign.status === "paused";
      showCampaignStep(liveNow ? "monitor" : "details");
    }
    paintPickSummary();
    paintEstimate();
    paintMailboxProgress(data.campaign);
  }
  text($("campaign-heading"), data.campaign.name || "Campaign");
  text($("campaign-status"), { draft: "Draft", running: "Running", paused: "Paused", done: "Completed" }[data.campaign.status] || data.campaign.status);
  text($("phase"), data.campaign.phase || data.campaign.status);
  $("campaign-body").classList.toggle(
    "live-first",
    data.campaign.status === "running" || data.campaign.status === "paused"
  );
  paintRunNote(data.campaign);
  paintLiveBoard("camp", data.campaign);
  paintCounts(data.campaign.counts);
  paintCampaignGroups(data.groups || [], data.segments || []);
  paintMailboxGroups(data.mailboxGroups || []);
  paintAttachments(data.campaign.attachments);
  paintPeople(data.campaign.recipients);
  paintPreviewChoices(data.campaign.recipients || []);
  paintTemplatePick(data.templates || []);
  paintLog(data.campaign.events);
  $("activity-empty").hidden = data.campaign.events.length > 0;
}

const STEP_LABELS = [
  ["checking", "Check"],
  ["sending", "Send"],
  ["waiting", "Wait"],
  ["next", "Next"],
  ["finished", "Finished"],
];

function paintLiveBoard(prefix, campaign) {
  const steps = $(`${prefix}-steps`);
  if (!steps) return;
  const counts = campaign ? campaign.counts || {} : {};
  const sent = Number(counts.sent || (campaign && campaign.sent) || 0);
  const failed = Number(counts.failed || (campaign && campaign.failed) || 0);
  const queued = Number(counts.queued || (campaign && campaign.queued) || 0) + Number(counts.sending || 0);
  const total = Number(counts.total || sent + failed + queued);
  const done = total ? Math.round(((sent + failed) / total) * 100) : 0;
  bumpNumber($(`${prefix}-sent`), sent);
  bumpNumber($(`${prefix}-failed`), failed);
  bumpNumber($(`${prefix}-queued`), queued);
  bumpNumber($(`${prefix}-percent`), `${done}%`);
  const bar = $(`${prefix}-progress`);
  if (bar) bar.style.width = `${done}%`;
  const step = campaign ? campaign.step || (campaign.status === "done" ? "finished" : "idle") : "idle";
  steps.replaceChildren();
  for (const [key, label] of STEP_LABELS) {
    const item = document.createElement("li");
    item.textContent = label;
    if (step === "paused" || step === "quota" || step === "error") {
      item.className = key === "waiting" ? (step === "error" ? "bad" : "warn") : "";
    } else if (key === step) item.className = "on";
    else if (step === "finished") item.className = "done";
    steps.appendChild(item);
  }
  const live = (campaign && campaign.live) || {};
  const pace = $(`${prefix}-pace`);
  if (pace) text(pace, campaign ? paceSentence(campaign, live) : "");
  const paceRows = live.pace || [];
  paintPace($(`${prefix}-chart`), paceRows);
  const chart = $(`${prefix}-chart`);
  if (chart) {
    chart.hidden = paceRows.length === 0;
    if (chart.parentElement) chart.parentElement.hidden = paceRows.length === 0;
  }
  const remain = $(`${prefix}-remaining`);
  if (remain) bumpNumber(remain, queued);
  paintMiniDonut($(`${prefix}-mini-donut`), queued, sent, failed);
  paintResults($(`${prefix}-results`), live.recent || []);
}

function paceSentence(campaign, live) {
  const amount = Number(campaign.delay_amount ?? 1);
  const unit = campaign.delay_unit === "minutes" ? "minute" : "second";
  const every = `${amount} ${unit}${amount === 1 ? "" : "s"}`;
  const per = Number(live.perMinute || 0);
  const eta = Number(live.etaSeconds || 0);
  const left = eta ? ` · about ${formatDuration(eta)} left` : "";
  return `1 email every ${every} · ${per} per minute${left}`;
}

function formatDuration(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function paintPace(svg, pace) {
  if (!svg) return;
  svg.replaceChildren();
  if (!pace.length) return;
  let sent = 0;
  let failed = 0;
  const points = pace.map((row) => {
    sent += Number(row.sent || 0);
    failed += Number(row.failed || 0);
    return { sent, failed };
  });
  const max = Math.max(1, ...points.map((row) => Math.max(row.sent, row.failed)));
  const width = 360;
  const height = 140;
  const step = points.length === 1 ? 0 : (width - 24) / (points.length - 1);
  const line = (key) => points.map((row, index) => {
    const x = 12 + index * step;
    const y = height - 16 - (row[key] / max) * (height - 32);
    return `${x},${y}`;
  }).join(" ");
  for (const [key, color] of [["sent", "#1f8a70"], ["failed", "#d4534a"]]) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    path.setAttribute("points", line(key));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-width", "3");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
  }
}

function paintMiniDonut(node, queued, sent, failed) {
  if (!node) return;
  const total = queued + sent + failed;
  if (!total) node.style.background = "conic-gradient(#e7e2d8 0 100%)";
  else {
    const sentEnd = (sent / total) * 100;
    const queuedEnd = sentEnd + (queued / total) * 100;
    node.style.background = `conic-gradient(#1f8a70 0 ${sentEnd}%, #e3a008 ${sentEnd}% ${queuedEnd}%, #d4534a ${queuedEnd}% 100%)`;
  }
  let center = node.querySelector(".donut-center");
  if (!center) {
    center = document.createElement("div");
    center.className = "donut-center";
    node.appendChild(center);
  }
  center.textContent = String(total);
}

function paintResults(list, rows) {
  if (!list) return;
  list.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    item.className = row.status === "failed" ? "failed" : "sent";
    const who = document.createElement("span");
    who.textContent = `${row.email} · ${row.mailbox || "mailbox"}`;
    const state = document.createElement("strong");
    state.textContent = row.status === "failed" ? "Failed" : "Sent";
    item.append(who, state);
    list.appendChild(item);
  }
}

function paintDonut(queued, sent, failed) {
  const total = queued + sent + failed;
  const donut = $("donut");
  text($("donut-center"), String(total));
  if (!total) {
    donut.style.background = "conic-gradient(#eef1f3 0 100%)";
  } else {
    const sentEnd = (sent / total) * 100;
    const queuedEnd = sentEnd + (queued / total) * 100;
    donut.style.background = `conic-gradient(#168C70 0 ${sentEnd}%, #D99A32 ${sentEnd}% ${queuedEnd}%, #D95B5B ${queuedEnd}% 100%)`;
  }
  const legend = $("donut-legend");
  legend.replaceChildren();
  for (const item of [
    ["#168C70", "Sent", sent],
    ["#D99A32", "Queued", queued],
    ["#D95B5B", "Failed", failed],
  ]) {
    const li = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = item[0];
    const label = document.createElement("span");
    label.textContent = `${item[1]}  ${item[2]}`;
    li.append(swatch, label);
    legend.appendChild(li);
  }
}

let overviewCampaigns = [];

function visibleCampaigns(rows) {
  const query = ($("campaign-filter").value || "").trim().toLowerCase();
  const status = $("campaign-status-filter").value;
  const sort = $("campaign-sort").value;
  let list = rows.filter((row) => {
    const name = campaignLabel(row).toLowerCase();
    if (query && !name.includes(query)) return false;
    if (status !== "all" && row.status !== status) return false;
    return true;
  });
  list.sort((a, b) => {
    if (sort === "name") return String(a.name || "").localeCompare(String(b.name || ""));
    if (sort === "sent") return Number(b.sent || 0) - Number(a.sent || 0);
    if (sort === "failed") return Number(b.failed || 0) - Number(a.failed || 0);
    return String(b.last_event || "").localeCompare(String(a.last_event || ""));
  });
  return list;
}

function paintOverviewCampaignTable(rows) {
  overviewCampaigns = rows;
  const body = $("overview-campaign-table");
  const shown = visibleCampaigns(rows);
  body.replaceChildren();
  if (!shown.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.textContent = rows.length ? "No campaigns match this filter." : "Your campaigns will appear here.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of shown) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = campaignLabel(row);
    const status = document.createElement("td");
    status.appendChild(statusBadge(row.status));
    const progress = document.createElement("td");
    const total = Number(row.queued || 0) + Number(row.sent || 0) + Number(row.failed || 0);
    const pct = total ? Math.round((Number(row.sent || 0) / total) * 100) : 0;
    const bar = document.createElement("div");
    bar.className = "bar progress mini-bar";
    const fill = document.createElement("span");
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    progress.appendChild(bar);
    tr.append(name, status, progress);
    for (const value of [row.sent, row.failed, row.queued]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    const when = document.createElement("td");
    when.textContent = row.last_event ? clockTime(row.last_event) : "—";
    tr.appendChild(when);
    const actions = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "row";
    const open = document.createElement("a");
    open.className = "text-link";
    open.href = `#/campaigns/${row.id}`;
    open.textContent = "Open";
    wrap.appendChild(open);
    if (row.status === "running") wrap.appendChild(miniButton("Pause", () => runAction("pause", row.id)));
    if (row.status === "paused") wrap.appendChild(miniButton("Resume", () => runAction("resume", row.id)));
    actions.appendChild(wrap);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function paintOverviewMailboxTable(rows) {
  const body = $("overview-mailbox-table");
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "No mailboxes yet.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const box of rows) {
    const tr = document.createElement("tr");
    const kind = box.accountKind === "workspace" ? "Workspace" : "Gmail";
    const cells = [
      box.label + (box.practice ? " (practice)" : ""),
      kind,
      box.enabled ? "Enabled" : "Off",
      box.quotaRemaining == null ? "—" : String(box.quotaRemaining),
      String(dailyCap(box)),
      box.lastError || "",
    ];
    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "row";
    wrap.append(
      miniButton(box.enabled ? "Disable" : "Enable", () => changeBox(box.id, { enabled: !box.enabled })),
      miniButton("Refresh", () => refreshBox(box.id)),
      miniButton("Remove", () => removeBox(box.id))
    );
    actions.appendChild(wrap);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function paintQuotaBars(rows) {
  const list = $("quota-bars");
  list.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No mailboxes yet. Add a Gmail or Workspace account.";
    list.appendChild(empty);
    return;
  }
  for (const box of rows) {
    const card = document.createElement("article");
    card.className = "mail-card";
    const kind = box.accountKind === "workspace" ? "Workspace" : "Gmail";
    const cap = dailyCap(box);
    const line = document.createElement("div");
    line.className = "cap-line";
    const name = document.createElement("strong");
    name.textContent = box.email || box.label;
    const leftLabel = document.createElement("span");
    leftLabel.className = "muted";
    const known = box.quotaRemaining != null;
    const left = known ? Number(box.quotaRemaining) : 0;
    const used = known ? Math.max(0, cap - left) : 0;
    const state = !box.enabled ? "Disabled" : box.lastError ? "Needs attention" : known ? "Connected" : "Not checked";
    leftLabel.textContent = `${kind}${box.practice ? " · practice" : ""} · ${state}`;
    const numbers = document.createElement("span");
    numbers.textContent = known
      ? `${left} remaining / ${cap} daily allowance`
      : "Quota has not been read from Google yet";
    if (box.quotaCheckedAt) {
      const checked = document.createElement("span");
      checked.className = "muted";
      checked.textContent = `Synced ${clockTime(box.quotaCheckedAt)}`;
      line.append(name, leftLabel, numbers, checked);
    } else {
      line.append(name, leftLabel, numbers);
    }
    const track = document.createElement("div");
    track.className = "bar";
    if (known && cap > 0) {
      const usedSpan = document.createElement("span");
      usedSpan.className = "used";
      usedSpan.style.width = `${(used / cap) * 100}%`;
      const leftSpan = document.createElement("span");
      leftSpan.className = "left";
      leftSpan.style.width = `${(left / cap) * 100}%`;
      track.append(usedSpan, leftSpan);
    }
    line.append(track);
    card.appendChild(line);
    if (box.lastError) {
      const error = document.createElement("div");
      error.className = "hint";
      error.textContent = box.lastError;
      card.appendChild(error);
    }
    list.appendChild(card);
  }
}

function describeEvent(message) {
  const text = message || "";
  const sent = text.match(/^Sent (.+) via (.+)\. (\d+) left on that box\.?$/);
  if (sent) return { kind: "sent", title: "Sent", detail: `${sent[1]} · ${sent[2]} · ${sent[3]} left` };
  if (text.startsWith("Test sent")) return { kind: "sent", title: "Test sent", detail: text.replace(/^Test sent:\s*/, "") };
  if (text.startsWith("Test skipped")) return { kind: "warn", title: "Test skipped", detail: text.replace(/^Test skipped:\s*/, "").replace(/\.\s*Invalid recipient$/, "") };
  if (text.startsWith("Test ")) return { kind: "bad", title: "Test failed", detail: text.replace(/^Test failed:\s*/, "") };
  if (text === "Paused") return { kind: "warn", title: "Paused", detail: "Waiting until you resume" };
  if (text === "Started" || text === "Resumed") return { kind: "run", title: text, detail: "" };
  if (text === "Queue finished") return { kind: "done", title: "Finished", detail: "Everyone in the queue was handled" };
  if (text.startsWith("Imported")) return { kind: "info", title: "Imported", detail: text.replace(/^Imported\s*/, "").replace(/\.$/, "") };
  if (text.includes("out of daily quota")) return { kind: "warn", title: "Quota used", detail: text };
  if (text.includes("asked to wait") || text.includes("Google")) return { kind: "warn", title: "Google wait", detail: text };
  if (text.startsWith("Returned")) return { kind: "info", title: "Retry queued", detail: text };
  return { kind: "info", title: text, detail: "" };
}

function paintFeed(container, rows, limit) {
  container.replaceChildren();
  const slice = typeof limit === "number" ? rows.slice(0, limit) : rows;
  let group = null;
  let stamp = "";
  for (const row of slice) {
    const when = clockTime(row.created_at);
    if (when !== stamp) {
      stamp = when;
      group = document.createElement("div");
      group.className = "feed-group";
      const time = document.createElement("time");
      time.textContent = when;
      time.dateTime = row.created_at || "";
      group.append(time, document.createElement("ol"));
      container.appendChild(group);
    }
    const described = describeEvent(row.message);
    const item = document.createElement("li");
    item.className = `feed-item ${described.kind}`;
    const dot = document.createElement("span");
    dot.className = "feed-dot";
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = described.title;
    body.appendChild(title);
    if (described.detail) {
      const detail = document.createElement("span");
      detail.textContent = described.detail;
      body.appendChild(detail);
    }
    item.append(dot, body);
    group.lastElementChild.appendChild(item);
  }
}

function campaignLabel(row) {
  const name = String(row.name || "").trim();
  if (name && name !== "Campaign") return name;
  if (row.subject) return row.subject;
  return name || "Untitled campaign";
}

function statusBadge(status) {
  const span = document.createElement("span");
  const key = String(status || "draft").toLowerCase();
  span.className = `badge ${key}`;
  const labels = {
    done: "Completed",
    draft: "Draft",
    running: "Running",
    paused: "Paused",
    failed: "Failed",
    sending: "Running",
  };
  span.textContent = labels[key] || status || "Draft";
  return span;
}

function paintActivity(series) {
  const svg = $("activity-chart");
  const empty = $("activity-empty");
  if (!svg) return;
  const slice = (series || []).slice(-activityDays);
  const total = slice.reduce((sum, row) => sum + row.sent + row.failed, 0);
  svg.hidden = total === 0;
  if (empty) empty.style.setProperty("display", total ? "none" : "block", "important");
  if (!total) {
    svg.replaceChildren();
    return;
  }
  const width = 640;
  const height = 220;
  const pad = { l: 36, r: 12, t: 16, b: 28 };
  const max = Math.max(1, ...slice.map((row) => Math.max(row.sent, row.failed)));
  const step = slice.length === 1 ? 0 : (width - pad.l - pad.r) / (slice.length - 1);
  const y = (value) => pad.t + (1 - value / max) * (height - pad.t - pad.b);
  const x = (index) => pad.l + index * step;
  const path = (key) => slice.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row[key])}`).join(" ");
  svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  const grid = document.createElementNS(ns, "path");
  grid.setAttribute("d", `M${pad.l},${pad.t} V${height - pad.b} H${width - pad.r}`);
  grid.setAttribute("fill", "none");
  grid.setAttribute("stroke", "#E5E9ED");
  svg.appendChild(grid);
  for (const [key, color] of [["sent", "#0866D9"], ["failed", "#D95B5B"]]) {
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", path(key));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "2");
    svg.appendChild(line);
  }
  slice.forEach((row, index) => {
    if (slice.length > 8 && index % Math.ceil(slice.length / 6) !== 0 && index !== slice.length - 1) return;
    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", x(index));
    label.setAttribute("y", height - 8);
    label.textContent = row.day.slice(5);
    svg.appendChild(label);
  });
  svg.onmousemove = (event) => {
    const bounds = svg.getBoundingClientRect();
    const rel = ((event.clientX - bounds.left) / bounds.width) * width;
    let nearest = 0;
    slice.forEach((_, index) => {
      if (Math.abs(x(index) - rel) < Math.abs(x(nearest) - rel)) nearest = index;
    });
    const row = slice[nearest];
    const tip = $("chart-tip");
    tip.hidden = false;
    tip.textContent = `${row.day}: ${row.sent} sent, ${row.failed} failed`;
    tip.style.left = `${event.clientX + 12}px`;
    tip.style.top = `${event.clientY + 12}px`;
  };
  svg.onmouseleave = () => { $("chart-tip").hidden = true; };
  if (empty) empty.style.setProperty("display", "none", "important");
}

function paintAttention(data) {
  const list = $("attention-list");
  const ok = $("attention-ok");
  if (!list) return;
  const items = [];
  for (const box of data.mailboxes) {
    if (box.lastError) items.push(`${box.email || box.label}: ${box.lastError}`);
    else if (box.enabled && box.quotaRemaining === 0) items.push(`${box.email || box.label} has no remaining quota.`);
  }
  for (const row of data.campaigns) {
    if (row.status === "paused") items.push(`${row.name} is paused.`);
    if (Number(row.failed) > 0) items.push(`${row.name} has ${row.failed} failed recipients.`);
  }
  list.replaceChildren();
  ok.hidden = items.length > 0;
  for (const message of items.slice(0, 8)) {
    const item = document.createElement("p");
    item.className = "attention-item";
    item.textContent = message;
    list.appendChild(item);
  }
}

function paintOverviewLog(rows) {
  paintFeed($("overview-log"), rows, 8);
}

let mailboxRows = [];
let detailId = null;
let mailboxView = localStorage.getItem("mailboxView") || "table";

function mailboxHealth(box) {
  if (!box.enabled) return { key: "disabled", label: "Disabled" };
  if (box.status === "Error") return { key: "bad", label: "Disconnected" };
  if (box.quotaRemaining === 0) return { key: "warn", label: "Quota used" };
  if (box.lastError) return { key: "warn", label: "Warning" };
  if (box.quotaRemaining == null) return { key: "warn", label: "Not checked" };
  return { key: "ok", label: "Connected" };
}

function paintMailboxKpis(rows) {
  const connected = rows.filter((box) => box.enabled && box.status !== "Error" && box.quotaRemaining != null).length;
  const attention = rows.filter((box) => box.status === "Error" || box.quotaRemaining === 0).length;
  const checked = rows.filter((box) => box.enabled && box.quotaRemaining != null);
  text($("box-total"), String(rows.length));
  text($("box-connected"), String(connected));
  text($("box-remaining"), checked.length ? String(checked.reduce((sum, box) => sum + Number(box.quotaRemaining), 0)) : "—");
  text($("box-remaining-detail"), checked.length ? `${checked.length} checked with Google` : "Not checked yet");
  text($("box-attention"), String(attention));
  $("box-attention-card").classList.toggle("attention", attention > 0);
}

function filteredMailboxes(rows) {
  const query = ($("mailbox-search").value || "").trim().toLowerCase();
  const server = $("mailbox-filter-server").value;
  const kind = $("mailbox-filter-kind").value;
  const health = $("mailbox-filter-health").value;
  const quota = $("mailbox-filter-quota").value;
  const sort = $("mailbox-sort").value;
  let list = rows.filter((box) => {
    const state = mailboxHealth(box);
    if (query && ![box.email, box.label, box.server, box.user].join(" ").toLowerCase().includes(query)) return false;
    if (server !== "all" && (box.server || "") !== server) return false;
    if (kind !== "all" && box.accountKind !== kind) return false;
    if (health === "connected" && state.label !== "Connected") return false;
    if (health === "warning" && state.label !== "Warning" && state.label !== "Not checked") return false;
    if (health === "disconnected" && state.label !== "Disconnected") return false;
    if (health === "quota" && state.label !== "Quota used") return false;
    if (health === "disabled" && state.label !== "Disabled") return false;
    if (quota === "remaining" && !(Number(box.quotaRemaining) > 0)) return false;
    if (quota === "exhausted" && box.quotaRemaining !== 0) return false;
    if (quota === "unknown" && box.quotaRemaining != null) return false;
    return true;
  });
  list.sort((a, b) => {
    if (sort === "quota") return Number(b.quotaRemaining || 0) - Number(a.quotaRemaining || 0);
    if (sort === "checked") return String(b.quotaCheckedAt || "").localeCompare(String(a.quotaCheckedAt || ""));
    return String(a.email || a.label).localeCompare(String(b.email || b.label));
  });
  return list;
}

function paintMailboxes(rows) {
  mailboxRows = rows;
  paintMailboxKpis(rows);
  const serverFilter = $("mailbox-filter-server");
  const currentServer = serverFilter.value;
  serverFilter.replaceChildren();
  const any = document.createElement("option");
  any.value = "all";
  any.textContent = "All servers";
  serverFilter.appendChild(any);
  for (const name of [...new Set(rows.map((box) => box.server).filter(Boolean))].sort()) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    serverFilter.appendChild(option);
  }
  if ([...serverFilter.options].some((option) => option.value === currentServer)) serverFilter.value = currentServer;
  const filtered = filteredMailboxes(rows);
  const pages = Math.max(1, Math.ceil(filtered.length / 50));
  if (mailboxPage >= pages) mailboxPage = 0;
  const slice = filtered.slice(mailboxPage * 50, mailboxPage * 50 + 50);
  const body = $("mailbox-list");
  const cards = $("mailbox-cards");
  body.replaceChildren();
  cards.replaceChildren();
  $("mailbox-empty").hidden = filtered.length > 0;
  text($("mailbox-page"), filtered.length ? `${mailboxPage + 1} / ${pages}` : "");
  const tableMode = mailboxView !== "card";
  $("mailbox-table-wrap").hidden = !tableMode;
  cards.hidden = tableMode;
  $("view-table").classList.toggle("on", tableMode);
  $("view-cards").classList.toggle("on", !tableMode);
  for (const box of slice) {
    if (tableMode) body.appendChild(mailboxRow(box));
    else cards.appendChild(mailboxCard(box));
  }
  const selected = selectedBoxes.size;
  $("bulk-bar").hidden = selected === 0;
  text($("bulk-count"), selected ? `${selected} selected` : "");
  if (detailId) {
    const open = rows.find((box) => box.id === detailId);
    if (open) fillDrawer(open);
  }
}

function mailboxRow(box) {
  const tr = document.createElement("tr");
  const check = document.createElement("td");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedBoxes.has(box.id);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selectedBoxes.add(box.id);
    else selectedBoxes.delete(box.id);
    $("bulk-bar").hidden = selectedBoxes.size === 0;
    text($("bulk-count"), selectedBoxes.size ? `${selectedBoxes.size} selected` : "");
  });
  check.appendChild(checkbox);
  const who = document.createElement("td");
  const email = document.createElement("strong");
  email.textContent = (box.email || box.label) + (box.practice ? " · practice" : "");
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.textContent = box.user || "Admin";
  who.append(email, meta);
  const kind = document.createElement("td");
  kind.textContent = box.accountKind === "workspace" ? "Workspace" : "Gmail";
  const server = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = box.server || "None";
  server.appendChild(badge);
  const status = document.createElement("td");
  const health = mailboxHealth(box);
  const pill = document.createElement("span");
  pill.className = `badge ${health.key === "ok" ? "ok" : health.key === "bad" ? "bad" : health.key === "disabled" ? "draft" : "warn"}`;
  pill.textContent = health.label;
  status.appendChild(pill);
  const quota = document.createElement("td");
  const cap = dailyCap(box);
  const known = box.quotaRemaining != null;
  const left = known ? Number(box.quotaRemaining) : 0;
  const used = known ? Math.max(0, cap - left) : 0;
  const track = document.createElement("div");
  track.className = "bar mini-bar";
  if (known && cap > 0) {
    const usedSpan = document.createElement("span");
    usedSpan.className = "used";
    usedSpan.style.width = `${(used / cap) * 100}%`;
    const leftSpan = document.createElement("span");
    leftSpan.className = "left";
    leftSpan.style.width = `${(left / cap) * 100}%`;
    track.append(usedSpan, leftSpan);
  }
  const numbers = document.createElement("div");
  numbers.className = "muted";
  numbers.textContent = known ? `${left} / ${cap} remaining` : "Not checked";
  quota.append(track, numbers);
  const checked = document.createElement("td");
  checked.textContent = box.quotaCheckedAt ? new Date(box.quotaCheckedAt).toLocaleString() : "—";
  const actions = document.createElement("td");
  const menu = document.createElement("details");
  menu.className = "row-menu";
  const summary = document.createElement("summary");
  summary.textContent = "Actions";
  const list = document.createElement("div");
  list.append(
    miniButton("Details", () => openMailbox(box)),
    miniButton("Refresh", () => refreshBox(box.id)),
    miniButton("Edit", () => editBox(box)),
    miniButton(box.enabled ? "Disable" : "Enable", () => changeBox(box.id, { enabled: !box.enabled })),
    miniButton("Delete", () => removeBox(box.id))
  );
  menu.append(summary, list);
  actions.appendChild(menu);
  tr.append(check, who, kind, server, status, quota, checked, actions);
  return tr;
}

function mailboxCard(box) {
  const card = document.createElement("article");
  card.className = `mail-card${box.status === "Error" ? " error" : ""}`;
  const top = document.createElement("div");
  top.className = "mail-top";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedBoxes.has(box.id);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selectedBoxes.add(box.id);
    else selectedBoxes.delete(box.id);
  });
  const pill = document.createElement("span");
  pill.className = box.status === "Error" ? "pill bad" : box.enabled ? "pill ok" : "pill muted-pill";
  pill.textContent = box.status;
  top.append(checkbox, pill);
  const email = document.createElement("strong");
  email.textContent = (box.email || box.label) + (box.practice ? " · practice" : "");
  const meta = document.createElement("p");
  meta.className = "muted";
  meta.textContent = box.user || "Admin";
  const ring = document.createElement("div");
  ring.className = "ring";
  const cap = dailyCap(box);
  const left = box.quotaRemaining == null ? 0 : Number(box.quotaRemaining);
  ring.style.setProperty("--p", String(Math.max(0, Math.min(100, Math.round((left / cap) * 100)))));
  const ringText = document.createElement("span");
  ringText.textContent = box.quotaRemaining == null ? "—" : String(box.quotaRemaining);
  ring.appendChild(ringText);
  const checked = document.createElement("p");
  checked.className = "muted";
  checked.textContent = box.quotaCheckedAt ? `Checked ${clockTime(box.quotaCheckedAt)}` : "Not checked yet";
  const error = document.createElement("p");
  error.className = "field-error";
  error.hidden = !box.lastError;
  error.textContent = box.lastError || "";
  const actions = document.createElement("div");
  actions.className = "row";
  actions.append(
    miniButton("Edit", () => editBox(box)),
    miniButton("Delete", () => removeBox(box.id))
  );
  card.append(top, email, meta, ring, checked, error, actions);
  return card;
}

function editBox(box) {
  const form = $("mailbox-form");
  form.id.value = String(box.id);
  form.owner_name.value = box.user || "";
  form.server_name.value = box.server || "";
  form.account_email.value = box.email || "";
  form.enabled.value = box.enabled ? "1" : "0";
  form.web_app_url.value = box.webAppUrl || "";
  form.account_kind.value = box.accountKind || "gmail";
  form.token.value = "";
  form.token.required = false;
  text($("mailbox-form-title"), "Edit mailbox");
  $("mailbox-cancel").hidden = false;
  showMailboxTab("add");
  showWizard(3);
}

function resetMailboxForm() {
  const form = $("mailbox-form");
  form.reset();
  form.id.value = "";
  form.token.required = true;
  text($("mailbox-form-title"), "Add mailbox");
  $("mailbox-cancel").hidden = true;
}

function paintServers(rows) {
  const list = $("server-list");
  const names = $("server-names");
  list.replaceChildren();
  names.replaceChildren();
  const boxes = (latestData && latestData.mailboxes) || [];
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = "No servers yet.";
    tr.appendChild(td);
    list.appendChild(tr);
  }
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = row.name;
    names.appendChild(option);
    const assigned = boxes.filter((box) => box.server === row.name);
    const connected = assigned.filter((box) => mailboxHealth(box).label === "Connected" || mailboxHealth(box).label === "Quota used").length;
    const tr = document.createElement("tr");
    for (const value of [row.name, String(assigned.length), String(connected)]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "row";
    wrap.append(
      miniButton("View", () => {
        $("mailbox-filter-server").value = row.name;
        showMailboxTab("overview");
        paintMailboxes(mailboxRows);
      }),
      miniButton("Delete", async () => {
        const note = assigned.length
          ? `${assigned.length} mailboxes will stay and lose this server name.`
          : "Delete this server?";
        if (!confirm(note)) return;
        try {
          await api(`/api/servers/${row.id}`, { method: "DELETE" });
          toast("Server deleted. Mailboxes were kept.", "ok");
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      })
    );
    actions.appendChild(wrap);
    tr.appendChild(actions);
    list.appendChild(tr);
  }
}

function paintOffers(rows) {
  const list = $("offer-list");
  list.replaceChildren();
  text($("offer-total"), String(rows.length));
  text($("offer-active"), String(rows.filter((row) => (row.status || "active") === "active").length));
  text($("offer-conversions"), String(rows.reduce((sum, row) => sum + Number(row.conversions || 0), 0)));
  text($("offer-revenue"), rows.reduce((sum, row) => sum + Number(row.revenue || 0), 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  $("offer-empty").hidden = rows.length > 0;
  for (const row of rows) {
    const tr = document.createElement("tr");
    const created = row.created_at ? new Date(row.created_at).toLocaleDateString() : "—";
    for (const value of [row.name, row.url, row.campaigns || 0, row.conversions || 0, Number(row.revenue || 0).toLocaleString(), row.status || "active", created]) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "row";
    wrap.append(
      miniButton("View", () => showOffer(row.id)),
      miniButton("Edit", () => editOffer(row)),
      miniButton("Duplicate", async () => {
        try {
          await api(`/api/offers/${row.id}/duplicate`, { method: "POST" });
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      }),
      miniButton("Delete", async () => {
        if (!confirm(`Delete ${row.name}?`)) return;
        try {
          await api(`/api/offers/${row.id}`, { method: "DELETE" });
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      })
    );
    actions.appendChild(wrap);
    tr.appendChild(actions);
    list.appendChild(tr);
  }
  const select = $("conversion-offer");
  const current = select.value;
  select.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "None";
  select.appendChild(blank);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.name;
    select.appendChild(option);
  }
  if (current) select.value = current;
}

function fillOffers(rows, selected) {
  const select = $("offer-select");
  const current = selected != null ? String(selected || "") : select.value;
  select.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "None";
  select.appendChild(blank);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.name;
    select.appendChild(option);
  }
  select.value = [...select.options].some((option) => option.value === current) ? current : "";
}

function paintQueues(data) {
  const modes = { drain: "Sequential", round_robin: "Rotation", wave: "Parallel waves" };
  text($("queue-running"), String(data.campaigns.filter((row) => row.status === "running").length));
  text($("queue-paused"), String(data.campaigns.filter((row) => row.status === "paused").length));
  text($("queue-done"), String(data.campaigns.filter((row) => row.status === "done").length));
  text($("queue-sent"), String(data.campaigns.reduce((sum, row) => sum + Number(row.sent || 0), 0)));
  text($("queue-failed"), String(data.campaigns.reduce((sum, row) => sum + Number(row.failed || 0), 0)));
  const history = $("queue-history");
  history.replaceChildren();
  if (!data.campaigns.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 9;
    td.textContent = "Campaigns appear here after you create one.";
    tr.appendChild(td);
    history.appendChild(tr);
  }
  for (const row of data.campaigns.slice(0, 20)) {
    const tr = document.createElement("tr");
    const recipients = Number(row.queued || 0) + Number(row.sent || 0) + Number(row.failed || 0);
    const values = [campaignLabel(row), row.status, recipients, row.queued, row.sent, row.failed, modes[row.rotation] || row.rotation || "—", row.created_at ? new Date(row.created_at).toLocaleDateString() : "—"];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    const action = document.createElement("td");
    const link = document.createElement("a");
    link.href = `#/campaigns/${row.id}`;
    link.className = "text-link";
    link.textContent = "Open";
    action.appendChild(link);
    tr.appendChild(action);
    history.appendChild(tr);
  }
  const live = data.campaigns.find((row) => row.status === "running")
    || data.campaigns.find((row) => row.status === "paused");
  $("queue-card").hidden = !live;
  $("queue-empty").hidden = Boolean(live);
  if (!live) {
    queueId = null;
    text($("queue-waiting"), "0");
    return;
  }
  queueId = live.id;
  const total = Number(live.queued || 0) + Number(live.sent || 0) + Number(live.failed || 0);
  const pct = total ? Math.round((Number(live.sent || 0) / total) * 100) : 0;
  text($("queue-name"), campaignLabel(live));
  text($("queue-phase"), live.phase || live.status);
  const boxes = String(live.mailbox_ids || "").split(",").filter(Boolean).length;
  text($("queue-waiting"), String(live.queued || 0));
  text($("queue-counts"), `${pct}% · ${modes[live.rotation] || "Sequential"} · ${boxes || "all eligible"} mailboxes · ${live.queued} still queued`);
  $("queue-open").href = `#/campaigns/${live.id}`;
}

function fillSettings(settings) {
  const form = $("settings-form");
  form.owner_name.value = settings.owner_name || "Admin";
  form.delay_amount.value = settings.delay_amount;
  form.delay_unit.value = settings.delay_unit;
  form.rotation.value = settings.rotation;
  form.auto_retries.value = settings.auto_retries;
  form.from_name.value = settings.from_name || "";
  form.reply_to.value = settings.reply_to || "";
  form.import_mode.value = settings.import_mode || "skip";
  form.public_base_url.value = settings.public_base_url || "";
  form.ai_base_url.value = settings.ai_base_url || "";
  form.ai_model.value = settings.ai_model || "";
  settingsFilled = true;
}

function previewHtml() {
  const frame = $("html-preview");
  const html = $("html-body").value || "";
  const doc = frame.contentDocument;
  doc.open();
  doc.write(html || "<p></p>");
  doc.close();
}

function paintSendChoices(mailboxes, campaign, justLoaded) {
  const key = `${campaign.id}:${campaign.mailbox_ids || ""}:${mailboxes.map((box) => box.id).join(",")}`;
  if (justLoaded || key !== pickKey) {
    pickKey = key;
    const chosen = new Set(String(campaign.mailbox_ids || "").split(",").filter(Boolean));
    const box = $("mailbox-picks");
    box.replaceChildren();
    const groups = new Map();
    for (const row of mailboxes) {
      const name = row.server || "No server";
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(row);
    }
    for (const [name, rows] of groups) {
      const group = document.createElement("section");
      group.className = "server-pick";
      const head = document.createElement("div");
      head.className = "row";
      const title = document.createElement("strong");
      const quota = rows.reduce((sum, row) => sum + (row.quotaRemaining == null ? 0 : Number(row.quotaRemaining)), 0);
      title.textContent = `${name} · ${rows.length} mailboxes · ${quota} remaining`;
      const selectServer = document.createElement("button");
      selectServer.type = "button";
      selectServer.className = "ghost";
      selectServer.textContent = "Select server";
      selectServer.addEventListener("click", () => {
        group.querySelectorAll("input[type=checkbox]").forEach((input) => {
          if (!input.disabled) input.checked = true;
        });
        paintPickSummary();
      });
      head.append(title, selectServer);
      group.appendChild(head);
      for (const row of rows) {
        const health = mailboxHealth(row);
        const blocked = health.label === "Disconnected";
        const label = document.createElement("label");
        label.className = "check";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = String(row.id);
        input.checked = !blocked && chosen.has(String(row.id));
        input.disabled = blocked;
        const kind = row.accountKind === "workspace" ? "Workspace" : "Gmail";
        const left = row.quotaRemaining == null ? "quota unread" : `${row.quotaRemaining} remaining`;
        label.append(input, document.createTextNode(` ${row.email || row.label} · ${kind} · ${left} · ${health.label}`));
        if (blocked) {
          const allow = document.createElement("button");
          allow.type = "button";
          allow.className = "ghost";
          allow.textContent = "Include anyway";
          allow.addEventListener("click", () => {
            input.disabled = false;
            input.checked = true;
            paintPickSummary();
          });
          label.appendChild(allow);
        }
        group.appendChild(label);
      }
      box.appendChild(group);
    }
  }
  const select = $("test-mailbox");
  const current = select.value;
  select.replaceChildren();
  for (const row of mailboxes) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.email || row.label;
    select.appendChild(option);
  }
  if (current && [...select.options].some((option) => option.value === current)) select.value = current;
  paintPlaceholders(campaign.placeholders || ["email", "first name", "offer url"]);
}

function syncRotationButtons() {
  const form = $("run-form");
  if (!form) return;
  document.querySelectorAll("[data-rotation]").forEach((button) => {
    button.classList.toggle("on", button.dataset.rotation === form.rotation.value);
  });
}

function selectedMailboxIds() {
  return [...$("mailbox-picks").querySelectorAll("input[type=checkbox]:checked")].map((input) => input.value);
}

function paintPickSummary() {
  const node = $("pick-summary");
  if (!node || !latestData) return;
  const ids = new Set(selectedMailboxIds());
  const chosen = latestData.mailboxes.filter((box) => ids.has(String(box.id)));
  const unavailable = latestData.mailboxes.filter((box) => mailboxHealth(box).label === "Disconnected").length;
  const quota = chosen.reduce((sum, box) => sum + (box.quotaRemaining == null ? 0 : Number(box.quotaRemaining)), 0);
  text(node, `${chosen.length} selected · ${quota} remaining on checked accounts · ${unavailable} disconnected`);
}

function paintEstimate() {
  const node = $("send-estimate");
  const form = $("run-form");
  if (!node || !form) return;
  const queued = Number(($("audience-queued") && $("audience-queued").textContent) || 0);
  const selected = Math.max(1, selectedMailboxIds().length);
  const delay = Number(form.delay_amount.value || 0);
  const seconds = form.delay_unit.value === "minutes" ? delay * 60 : delay;
  const steps = form.rotation.value === "wave" ? Math.ceil(queued / selected) : queued;
  const eta = steps * seconds;
  const mode = form.rotation.value === "wave" ? "each wave" : "each email";
  text(node, queued
    ? `About ${formatDuration(eta)} for ${queued} queued people, waiting ${delay || 0} ${form.delay_unit.value} after ${mode}. This is approximate.`
    : "Add people to see an estimate. The estimate is approximate.");
}

function paintMailboxProgress(campaign) {
  const node = $("mailbox-progress");
  if (!node || !campaign || !latestData) return;
  const stats = new Map((campaign.mailboxStats || []).map((row) => [String(row.mailbox_id), row]));
  const ids = new Set(selectedMailboxIds());
  let boxes = latestData.mailboxes.filter((box) => ids.has(String(box.id)));
  if (!boxes.length && $("run-form").send_with.value === "server") {
    const server = $("run-form").server_name.value;
    boxes = latestData.mailboxes.filter((box) => box.server === server);
  }
  node.replaceChildren();
  if (!boxes.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Selected mailboxes appear here after you choose them.";
    node.appendChild(empty);
    return;
  }
  for (const box of boxes) {
    const stat = stats.get(String(box.id)) || { sent: 0, failed: 0 };
    const line = document.createElement("p");
    const left = box.quotaRemaining == null ? "quota unread" : `${box.quotaRemaining} remaining`;
    line.textContent = `${box.email || box.label} · ${stat.sent} sent · ${stat.failed} failed · ${left} · ${mailboxHealth(box).label}`;
    node.appendChild(line);
  }
}

function showCampaignStep(name) {
  document.querySelectorAll("[data-campaign-step]").forEach((section) => {
    section.hidden = section.dataset.campaignStep !== name;
  });
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.classList.toggle("on", button.dataset.step === name);
  });
}

function launchSummary() {
  const form = $("campaign-form");
  const run = $("run-form");
  const modes = { drain: "Sequential", round_robin: "Rotation", wave: "Parallel waves" };
  const queued = ($("audience-queued") && $("audience-queued").textContent) || "0";
  return [
    `Start ${form.name.value || "this campaign"}?`,
    `${queued} people still queued.`,
    `${selectedMailboxIds().length} mailboxes selected.`,
    `${modes[run.rotation.value] || run.rotation.value}.`,
    `Wait ${run.delay_amount.value || 0} ${run.delay_unit.value}.`,
    "Nothing sends until you confirm.",
  ].join("\n");
}

function paintPlaceholders(names) {
  const list = $("placeholder-list");
  list.replaceChildren();
  for (const name of names) {
    const item = document.createElement("li");
    item.textContent = `{{${name}}}`;
    list.appendChild(item);
  }
}

function miniButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

let campaignListPage = 0;

function paintCampaignList(rows) {
  text($("list-total"), String(rows.length));
  text($("list-draft"), String(rows.filter((row) => row.status === "draft").length));
  text($("list-running"), String(rows.filter((row) => row.status === "running" || row.status === "paused").length));
  text($("list-done"), String(rows.filter((row) => row.status === "done").length));
  text($("list-failed"), String(rows.filter((row) => Number(row.failed) > 0).length));
  text($("list-sent"), String(rows.reduce((sum, row) => sum + Number(row.sent || 0), 0)));
  const query = ($("campaign-list-search").value || "").trim().toLowerCase();
  const status = $("campaign-list-status").value;
  const sort = $("campaign-list-sort").value;
  let list = rows.filter((row) => {
    if (query && !campaignLabel(row).toLowerCase().includes(query)) return false;
    if (status !== "all" && row.status !== status) return false;
    return true;
  });
  list.sort((a, b) => {
    if (sort === "name") return campaignLabel(a).localeCompare(campaignLabel(b));
    if (sort === "sent") return Number(b.sent || 0) - Number(a.sent || 0);
    return String(b.created_at || "").localeCompare(String(a.created_at || ""));
  });
  const pages = Math.max(1, Math.ceil(list.length / 20));
  if (campaignListPage >= pages) campaignListPage = 0;
  const slice = list.slice(campaignListPage * 20, campaignListPage * 20 + 20);
  const body = $("campaign-list");
  body.replaceChildren();
  $("campaign-empty").hidden = list.length > 0;
  text($("campaign-list-page"), list.length ? `${campaignListPage + 1} / ${pages}` : "");
  for (const row of slice) {
    const tr = document.createElement("tr");
    const recipients = Number(row.queued || 0) + Number(row.sent || 0) + Number(row.failed || 0);
    const created = row.created_at ? new Date(row.created_at).toLocaleDateString() : "—";
    const name = document.createElement("td");
    name.textContent = campaignLabel(row);
    const statusCell = document.createElement("td");
    statusCell.appendChild(statusBadge(row.status));
    tr.append(name, statusCell);
    for (const value of [recipients, row.sent, row.failed]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    const progress = document.createElement("td");
    const pct = recipients ? Math.round((Number(row.sent || 0) / recipients) * 100) : 0;
    const bar = document.createElement("div");
    bar.className = "bar progress mini-bar";
    const fill = document.createElement("span");
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    progress.appendChild(bar);
    const when = document.createElement("td");
    when.textContent = created;
    const activity = document.createElement("td");
    activity.textContent = row.last_event ? clockTime(row.last_event) : "—";
    tr.append(progress, when, activity);
    const actions = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "row";
    const open = document.createElement("a");
    open.className = "text-link";
    open.href = `#/campaigns/${row.id}`;
    open.textContent = "Open";
    wrap.append(
      open,
      miniButton("Duplicate", () => duplicateCampaign(row.id)),
      miniButton("Restart", () => restartCampaign(row.id)),
      miniButton("Delete", async () => {
        if (!confirm(`Delete ${campaignLabel(row)}?`)) return;
        try {
          await api(`/api/campaigns/${row.id}`, { method: "DELETE" });
          toast("Campaign deleted.", "ok");
          if (selectedId === row.id) location.hash = "#/campaigns";
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      })
    );
    actions.appendChild(wrap);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function paintActivityChoices(rows) {
  const select = $("activity-campaign");
  const current = select.value;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All campaigns";
  select.appendChild(all);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = campaignLabel(row);
    select.appendChild(option);
  }
  if (current && rows.some((row) => String(row.id) === current)) select.value = current;
}

function paintRunNote(campaign) {
  const note = $("run-note");
  if (!note) return;
  const counts = campaign.counts || {};
  const sent = Number(counts.sent || 0);
  const failed = Number(counts.failed || 0);
  const queued = Number(counts.queued || 0);
  let message = "";
  if (campaign.status === "running") {
    message = `Sending. ${sent} sent, ${queued} still waiting.`;
  } else if (campaign.status === "paused" && queued) {
    message = `${queued} waiting. Press Start.`;
  } else if (campaign.status === "paused") {
    message = `Paused. ${sent} sent, ${queued} still waiting.`;
  } else if (campaign.status === "done") {
    message = `Finished. ${sent} sent, ${failed} failed.`;
  }
  note.hidden = !message;
  text(note, message);
}

function paintCounts(counts) {
  text($("counts"), `${counts.total} people · ${counts.queued} eligible · ${counts.sent} sent · ${counts.failed} failed · ${counts.suppressed || 0} unsubscribed`);
  text($("audience-total"), String(counts.total || 0));
  text($("audience-queued"), String(counts.queued || 0));
  text($("audience-sent"), String(counts.sent || 0));
  text($("audience-failed"), String(counts.failed || 0));
  text($("audience-suppressed"), String(counts.suppressed || 0));
}

function paintAttachments(rows) {
  const list = $("attachment-list");
  list.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    item.textContent = `${row.filename} `;
    const remove = miniButton("Remove", async () => {
      await api(`/api/attachments/${row.id}`, { method: "DELETE" });
      await tick();
    });
    item.appendChild(remove);
    list.appendChild(item);
  }
}

function paintPeople(rows) {
  const body = $("people");
  body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of [row.email, row.status, row.attempts, row.mailbox || "", row.last_error || ""]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function paintLog(rows) {
  paintFeed($("log"), rows);
}

async function tick() {
  try {
    const me = await api("/api/me");
    const admin = me.user.role === "super_admin";
    $("company-nav").hidden = admin;
    $("admin-nav").hidden = !admin;
    $("header-create").hidden = admin;
    $("more-create").hidden = admin;
    $("header-search").hidden = admin;
    $("search-open").hidden = admin;
    $("user-settings").hidden = admin;
    paintAccount(me);
    if (admin) {
      document.querySelectorAll("[data-page]").forEach((section) => {
        section.hidden = section.dataset.page !== "admin";
      });
      setHeader("Administration", "Dashboard");
      paintNotices(null);
      await paintAdmin();
      schedule(8000);
      return;
    }
    const route = parseRoute();
    applyRoute(route);
    let campaignId = null;
    if (route.name === "campaign") campaignId = route.campaignId;
    else if (route.name === "activity") campaignId = selectedId;
    else if (route.name === "overview") campaignId = liveCampaignId;
    let data = await fetchState(campaignId);
    if (route.name === "overview") {
      const live = data.campaigns.find((row) => row.status === "running")
        || data.campaigns.find((row) => row.status === "paused");
      const focus = live || data.campaigns[0];
      if (focus && (!data.campaign || data.campaign.id !== focus.id)) data = await fetchState(focus.id);
    }
    if (route.name === "activity" && !campaignId && data.campaigns[0]) {
      selectedId = data.campaigns[0].id;
      data = await fetchState(selectedId);
    }
    paint(data, route);
    if (route.name === "subscribers" || route.name === "suppression") {
      await loadSubscribers();
      await loadSuppression();
    }
    if (route.name === "deliverability") await loadReports();
    if (route.name === "activity") await loadActivity();
    if (route.name === "media") await loadMedia();
    const running = data.campaigns.some((row) => row.status === "running");
    schedule(running ? 1000 : 4000);
  } catch (error) {
    text($("phase"), error.message);
    schedule(4000);
  }
}

function fetchState(campaignId) {
  const query = new URLSearchParams({ status: filter, offset: "0" });
  if (campaignId) query.set("campaign", String(campaignId));
  return api(`/api/state?${query}`);
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(tick, ms);
}

async function changeBox(id, fields) {
  await api(`/api/mailboxes/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
  await tick();
}

async function refreshBox(id) {
  await api(`/api/mailboxes/${id}/refresh`, { method: "POST" });
  await tick();
}

async function removeBox(id) {
  try {
    await api(`/api/mailboxes/${id}`, { method: "DELETE" });
    toast("Mailbox deleted.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
}

$("mailbox-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const id = String(form.get("id") || "");
  const payload = {
    label: form.get("account_email") || "Mailbox",
    web_app_url: form.get("web_app_url"),
    account_kind: form.get("account_kind"),
    account_email: form.get("account_email") || "",
    server_name: form.get("server_name") || "",
    owner_name: form.get("owner_name") || "",
    enabled: form.get("enabled") === "1",
  };
  const token = String(form.get("token") || "").trim();
  if (token) payload.token = token;
  const errorNote = $("mailbox-error");
  try {
    const saved = id
      ? await api(`/api/mailboxes/${id}`, { method: "PATCH", body: JSON.stringify(payload) })
      : await api("/api/mailboxes", { method: "POST", body: JSON.stringify({ ...payload, token }) });
    if (saved.lastError) {
      errorNote.hidden = false;
      text(errorNote, saved.lastError);
      toast(saved.lastError, "bad");
    } else {
      errorNote.hidden = true;
      const who = saved.email || saved.label;
      const left = saved.quotaRemaining == null ? "quota unread" : `${saved.quotaRemaining} left today`;
      toast(`${who} saved. ${left}.`, "ok");
      resetMailboxForm();
    }
    await tick();
  } catch (error) {
    errorNote.hidden = false;
    text(errorNote, error.message);
    toast(error.message, "bad");
  }
});

$("mailbox-cancel").addEventListener("click", resetMailboxForm);

$("mailbox-search").addEventListener("input", (event) => {
  mailboxQuery = event.target.value;
  mailboxPage = 0;
  paintMailboxes(mailboxRows);
});

$("mailbox-prev").addEventListener("click", () => {
  mailboxPage = Math.max(0, mailboxPage - 1);
  tick();
});

$("mailbox-next").addEventListener("click", () => {
  mailboxPage += 1;
  tick();
});

$("mailbox-all").addEventListener("change", (event) => {
  const root = mailboxView === "card" ? $("mailbox-cards") : $("mailbox-list");
  for (const box of root.querySelectorAll("input[type=checkbox]")) {
    box.checked = event.target.checked;
    box.dispatchEvent(new Event("change"));
  }
});

$("bulk-apply").addEventListener("click", async () => {
  const ids = [...selectedBoxes];
  if (!ids.length) {
    text($("bulk-note"), "Select at least one row.");
    return;
  }
  const action = $("bulk-action").value;
  const payload = { ids, action };
  if (action === "link") payload.server_name = $("bulk-server").value.trim();
  if (action === "status") payload.enabled = $("bulk-status").value === "1";
  if (action === "delete" && !confirm(`Delete ${ids.length} mailboxes? This cannot be undone.`)) return;
  try {
    const result = await api("/api/account-bulk", { method: "POST", body: JSON.stringify(payload) });
    text($("bulk-note"), result.message || "Bulk action completed successfully.");
    toast(result.message || "Bulk action completed successfully.", result.errors && result.errors.length ? "bad" : "ok");
    if (action === "delete") selectedBoxes.clear();
    await tick();
  } catch (error) {
    text($("bulk-note"), error.message);
    toast(error.message, "bad");
  }
});

$("server-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/servers", { method: "POST", body: JSON.stringify({ name: form.get("name") }) });
    event.target.reset();
    toast("Server created.", "ok");
    $("server-dialog").close();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("account-import").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const result = await api("/api/account-import", {
      method: "POST",
      body: JSON.stringify({ csv: form.get("csv") }),
    });
    const message = result.errors && result.errors.length
      ? `Added ${result.added}. ${result.errors.join("; ")}`
      : `Added ${result.added}.`;
    toast(message, result.errors && result.errors.length ? "bad" : "ok");
    event.target.reset();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

async function generateScript(button) {
  const status = $("script-status");
  button.disabled = true;
  text(status, "Generating…");
  try {
    const result = await api("/api/script", { method: "POST" });
    $("script-code").value = result.code;
    $("script-secret").value = result.secret;
    if ($("mailbox-form") && $("mailbox-form").token) {
      $("mailbox-form").token.value = result.secret;
    }
    text(status, "Script ready. Copy it into Apps Script.");
    toast("Script ready. Copy it into Apps Script.", "ok");
  } catch (error) {
    text(status, error.message);
    toast(error.message, "bad");
  } finally {
    button.disabled = false;
  }
}

async function copyField(id) {
  const value = $(id).value;
  if (!value) {
    toast("Nothing to copy yet.", "bad");
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    toast(id === "script-secret" ? "Secret copied." : "Code copied.", "ok");
  } catch (error) {
    toast(error.message || "Copy failed.", "bad");
  }
}

$("script-copy").addEventListener("click", () => copyField("script-code"));
$("script-copy-secret").addEventListener("click", () => copyField("script-secret"));

function showMailboxTab(name) {
  document.querySelectorAll("[data-mailbox-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.mailboxPanel !== name;
  });
  document.querySelectorAll("[data-mailbox-tab]").forEach((button) => {
    button.classList.toggle("on", button.dataset.mailboxTab === name);
  });
}

function showWizard(step) {
  document.querySelectorAll("[data-wizard-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.wizardPanel !== String(step);
  });
  document.querySelectorAll("[data-wizard]").forEach((button) => {
    button.classList.toggle("on", button.dataset.wizard === String(step));
  });
}

function fillDrawer(box) {
  detailId = box.id;
  $("mailbox-drawer").hidden = false;
  text($("drawer-email"), (box.email || box.label) + (box.practice ? " · practice" : ""));
  const health = mailboxHealth(box);
  text($("drawer-status"), health.label);
  text($("drawer-account"), `${box.accountKind === "workspace" ? "Workspace" : "Gmail"} · ${box.user || "Admin"} · ${box.server || "No server"}`);
  const cap = dailyCap(box);
  const known = box.quotaRemaining != null;
  const left = known ? Number(box.quotaRemaining) : 0;
  text($("drawer-quota"), known
    ? `${left} remaining of the ${cap} meter for this account type. ${Math.max(0, cap - left)} used on that meter.`
    : "Quota has not been read from Google yet.");
  const bar = $("drawer-bar");
  bar.replaceChildren();
  if (known && cap > 0) {
    const used = document.createElement("span");
    used.className = "used";
    used.style.width = `${(Math.max(0, cap - left) / cap) * 100}%`;
    const remain = document.createElement("span");
    remain.className = "left";
    remain.style.width = `${(left / cap) * 100}%`;
    bar.append(used, remain);
  }
  text($("drawer-connection"), box.lastError || (box.quotaCheckedAt ? `Last check ${new Date(box.quotaCheckedAt).toLocaleString()}` : "Not checked yet"));
  const events = ((latestData && latestData.recentEvents) || []).filter((row) => {
    const haystack = `${row.message || ""}`.toLowerCase();
    return (box.email && haystack.includes(box.email.toLowerCase())) || (box.label && haystack.includes(box.label.toLowerCase()));
  });
  text($("drawer-activity"), events.length ? events.slice(0, 3).map((row) => row.message).join(" · ") : "No stored events mention this mailbox yet.");
  text($("drawer-toggle"), box.enabled ? "Disable" : "Enable");
  $("drawer-refresh").onclick = () => refreshBox(box.id);
  $("drawer-edit").onclick = () => editBox(box);
  $("drawer-toggle").onclick = () => changeBox(box.id, { enabled: !box.enabled });
  $("drawer-delete").onclick = () => removeBox(box.id);
}

async function openMailbox(box) {
  fillDrawer(box);
  try {
    const report = await api("/api/reports");
    const row = (report.mailboxes || []).find((item) => item.id === box.id);
    if (row && detailId === box.id) {
      const current = $("drawer-activity").textContent;
      text($("drawer-activity"), `${row.sent} sent · ${row.failed} failed in stored results. ${current}`);
    }
  } catch (error) {
    text($("drawer-activity"), error.message);
  }
}

function previewImport(text) {
  const lines = text.replace(/^\uFEFF/, "").trim().split(/\r?\n/);
  const body = $("import-preview-body");
  body.replaceChildren();
  if (lines.length < 2) {
    text($("import-summary"), "Paste a header row and at least one mailbox.");
    return;
  }
  const headers = lines[0].split(",").map((item) => item.trim().toLowerCase());
  const index = (names) => headers.findIndex((header) => names.includes(header));
  const emailAt = index(["email"]);
  const urlAt = index(["web app url", "url"]);
  const secretAt = index(["secret", "token"]);
  const typeAt = index(["account type", "type"]);
  const serverAt = index(["server"]);
  let valid = 0;
  let invalid = 0;
  const seen = new Set();
  for (const line of lines.slice(1)) {
    if (!line.trim()) continue;
    const cells = line.split(",").map((item) => item.trim());
    const email = emailAt >= 0 ? cells[emailAt] : "";
    const url = urlAt >= 0 ? cells[urlAt] : "";
    const secret = secretAt >= 0 ? cells[secretAt] : "";
    const kind = (typeAt >= 0 ? cells[typeAt] : "gmail").toLowerCase();
    const server = serverAt >= 0 ? cells[serverAt] : "";
    const problems = [];
    if (!email) problems.push("missing email");
    if (!url || !/^https:\/\/script\.google\.com\/macros\//.test(url)) problems.push("invalid URL");
    if (!secret) problems.push("missing secret");
    if (kind && !["gmail", "workspace"].includes(kind)) problems.push("unsupported type");
    if (email && seen.has(email.toLowerCase())) problems.push("duplicate");
    if (email) seen.add(email.toLowerCase());
    if (problems.length) invalid += 1;
    else valid += 1;
    const tr = document.createElement("tr");
    for (const value of [email || "—", url ? "URL present" : "Missing", kind || "gmail", server || "—", problems.join(", ") || "Valid"]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  text($("import-summary"), `${valid + invalid} rows detected. ${valid} valid. ${invalid} invalid. Invalid rows are not added.`);
}

function exportSelected() {
  const rows = mailboxRows.filter((box) => selectedBoxes.has(box.id));
  const lines = ["email,account type,server,user,status,quota remaining"];
  for (const box of rows) {
    lines.push([box.email || box.label, box.accountKind, box.server, box.user, mailboxHealth(box).label, box.quotaRemaining == null ? "" : box.quotaRemaining].join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "mailboxes.csv";
  link.click();
}

$("mailbox-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-mailbox-tab]");
  if (button) showMailboxTab(button.dataset.mailboxTab);
});
$("open-add-mailbox").addEventListener("click", () => {
  resetMailboxForm();
  showMailboxTab("add");
  showWizard(1);
});
document.querySelectorAll("[data-wizard]").forEach((button) => {
  button.addEventListener("click", () => showWizard(button.dataset.wizard));
});
document.querySelectorAll("[data-wizard-go]").forEach((button) => {
  button.addEventListener("click", () => showWizard(button.dataset.wizardGo));
});
for (const id of ["mailbox-filter-server", "mailbox-filter-kind", "mailbox-filter-health", "mailbox-filter-quota", "mailbox-sort"]) {
  $(id).addEventListener("change", () => {
    mailboxPage = 0;
    paintMailboxes(mailboxRows);
  });
}
$("mailbox-clear").addEventListener("click", () => {
  $("mailbox-search").value = "";
  $("mailbox-filter-server").value = "all";
  $("mailbox-filter-kind").value = "all";
  $("mailbox-filter-health").value = "all";
  $("mailbox-filter-quota").value = "all";
  $("mailbox-sort").value = "email";
  mailboxPage = 0;
  paintMailboxes(mailboxRows);
});
$("view-table").addEventListener("click", () => {
  mailboxView = "table";
  localStorage.setItem("mailboxView", mailboxView);
  paintMailboxes(mailboxRows);
});
$("view-cards").addEventListener("click", () => {
  mailboxView = "card";
  localStorage.setItem("mailboxView", mailboxView);
  paintMailboxes(mailboxRows);
});
$("bulk-bar").addEventListener("click", (event) => {
  const button = event.target.closest("[data-bulk]");
  if (!button) return;
  const kind = button.dataset.bulk;
  if (kind === "export") {
    exportSelected();
    return;
  }
  if (kind === "enable" || kind === "disable") {
    $("bulk-action").value = "status";
    $("bulk-status").value = kind === "enable" ? "1" : "0";
  } else {
    $("bulk-action").value = kind;
  }
  $("bulk-apply").click();
});
$("connection-test").addEventListener("click", async () => {
  const form = $("mailbox-form");
  const button = $("connection-test");
  button.disabled = true;
  text($("connection-result"), "Checking the script…");
  try {
    const data = await api("/api/mailboxes/test", {
      method: "POST",
      body: JSON.stringify({ web_app_url: form.web_app_url.value, token: form.token.value }),
    });
    if (data.email) form.account_email.value = data.email;
    const left = data.quotaRemaining == null ? "quota unread" : `${data.quotaRemaining} remaining today`;
    text($("connection-result"), `Connected${data.email ? ` as ${data.email}` : ""}. ${left}. Save the mailbox when this looks right.`);
    toast("Connection succeeded.", "ok");
  } catch (error) {
    text($("connection-result"), error.message);
    toast(error.message, "bad");
  } finally {
    button.disabled = false;
  }
});
$("copy-deploy-steps").addEventListener("click", async () => {
  const steps = "Open script.google.com, paste the generated code, Deploy > New deployment > Web app, Execute as Me, Who has access: Anyone, copy the URL ending in /exec.";
  try {
    await navigator.clipboard.writeText(steps);
    toast("Deployment steps copied.", "ok");
  } catch (error) {
    toast(error.message || "Copy failed.", "bad");
  }
});
$("server-open").addEventListener("click", () => $("server-dialog").showModal());
$("server-close").addEventListener("click", () => $("server-dialog").close());
$("drawer-close").addEventListener("click", () => {
  detailId = null;
  $("mailbox-drawer").hidden = true;
});
$("import-preview").addEventListener("click", () => previewImport($("account-import").csv.value || ""));
$("import-file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  $("account-import").csv.value = await file.text();
  previewImport($("account-import").csv.value);
});

$("practice-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/practice-mailbox", {
      method: "POST",
      body: JSON.stringify({
        label: form.get("label") || "Practice",
        quota: Number(form.get("quota") || 100),
        fail_once: form.get("fail_once") === "on",
        account_kind: form.get("account_kind"),
        account_email: form.get("account_email") || "",
        server_name: form.get("server_name") || "",
        owner_name: form.get("owner_name") || "",
      }),
    });
    toast("Practice mailbox added.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("practice-kind").addEventListener("change", (event) => {
  $("practice-form").quota.value = event.target.value === "workspace" ? "1500" : "100";
});

$("refresh-all").addEventListener("click", async () => {
  try {
    const data = await api("/api/state");
    let failed = 0;
    for (const box of data.mailboxes) {
      const saved = await api(`/api/mailboxes/${box.id}/refresh`, { method: "POST" });
      if (saved.lastError) failed += 1;
    }
    toast(failed ? `${failed} mailbox${failed === 1 ? "" : "es"} could not be checked.` : "Quotas refreshed.", failed ? "bad" : "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("campaign-new").addEventListener("click", async () => {
  try {
    const created = await api("/api/campaigns", {
      method: "POST",
      body: JSON.stringify({ name: "Campaign" }),
    });
    selectedId = created.id;
    loadedId = null;
    toast("Campaign created.", "ok");
    location.hash = `#/campaigns/${created.id}`;
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("campaign-delete").addEventListener("click", async () => {
  if (!selectedId) return;
  if (!confirm("Delete this campaign?")) return;
  try {
    await api(`/api/campaigns/${selectedId}`, { method: "DELETE" });
    selectedId = null;
    loadedId = null;
    toast("Campaign deleted.", "ok");
    location.hash = "#/campaigns";
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("campaign-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveCampaign();
    text($("save-note"), "Saved");
    text($("campaign-heading"), $("campaign-form").name.value);
    toast("Campaign saved.", "ok");
    await tick();
  } catch (error) {
    text($("save-note"), error.message);
    toast(error.message, "bad");
  }
});

$("attachment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const input = event.target.file;
  try {
    for (const file of input.files) {
      const body = new FormData();
      body.append("file", file);
      await api(`/api/campaigns/${selectedId}/attachments`, { method: "POST", body });
    }
    input.value = "";
    toast("Attachment uploaded.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

async function uploadCsv(form, replace) {
  if (!selectedId) return;
  const file = form.file.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  const result = await api(`/api/campaigns/${selectedId}/csv?replace=${replace}`, { method: "POST", body });
  form.reset();
  text($("save-note"), `Added ${result.added}`);
  toast(`Added ${result.added}.`, "ok");
  await tick();
}

$("csv-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await uploadCsv(event.target, event.submitter?.dataset.replace === "true");
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("sheet-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const form = new FormData(event.target);
  try {
    await api(`/api/campaigns/${selectedId}/sheet`, {
      method: "POST",
      body: JSON.stringify({
        url: form.get("url"),
        replace: event.submitter?.dataset.replace === "true",
      }),
    });
    toast("Sheet rows added.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("paste-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const form = new FormData(event.target);
  try {
    const result = await api(`/api/campaigns/${selectedId}/paste`, {
      method: "POST",
      body: JSON.stringify({ csv: form.get("csv"), replace: false }),
    });
    const note = $("run-note");
    if (result.requeued) {
      note.hidden = false;
      text(note, `${result.requeued} queued again. Press Start.`);
      toast(`${result.requeued} queued again. Press Start.`, "ok");
    } else {
      text($("save-note"), `Added ${result.added}`);
      toast(`Added ${result.added}.`, "ok");
    }
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("sample-rows").addEventListener("click", () => {
  $("paste-form").csv.value = "email,first name\nada@example.com,Ada\nbounce@test.com,Bad\ngrace@example.com,Grace\n";
});

for (const button of $("filters").querySelectorAll("button")) {
  button.addEventListener("click", async () => {
    filter = button.dataset.status;
    for (const other of $("filters").querySelectorAll("button")) other.classList.remove("on");
    button.classList.add("on");
    await tick();
  });
}

$("activity-campaign").addEventListener("change", () => {
  activityPage = 0;
  loadActivity();
});

$("btn-start").addEventListener("click", () => runAction("start"));
$("btn-pause").addEventListener("click", () => runAction("pause"));
$("btn-resume").addEventListener("click", () => runAction("resume"));
$("btn-retry").addEventListener("click", () => runAction("retry-failed"));
$("btn-resend").addEventListener("click", () => runAction("resend"));
$("btn-restart").addEventListener("click", () => restartCampaign(selectedId));
$("launch-review").addEventListener("click", () => {
  showCampaignStep("monitor");
  $("btn-start").click();
});
$("campaign-steps").addEventListener("click", (event) => {
  const button = event.target.closest("[data-step]");
  if (button) showCampaignStep(button.dataset.step);
});
document.querySelectorAll("[data-step-go]").forEach((button) => {
  button.addEventListener("click", () => {
    const next = button.dataset.stepGo;
    const form = $("campaign-form");
    if ((next === "message" || next === "audience") && !form.name.value.trim()) {
      toast("Add a campaign name.", "bad");
      return;
    }
    if (next === "audience" && !form.subject.value.trim() && !form.html_body.value.trim() && !form.text_body.value.trim()) {
      toast("Add a subject or a message before continuing.", "bad");
      return;
    }
    if (next === "timing" && $("run-form").send_with.value !== "server" && !selectedMailboxIds().length) {
      toast("Select at least one mailbox, or send with one server.", "bad");
      return;
    }
    showCampaignStep(next);
  });
});
$("rotation-modes").addEventListener("click", (event) => {
  const button = event.target.closest("[data-rotation]");
  if (!button) return;
  $("run-form").rotation.value = button.dataset.rotation;
  syncRotationButtons();
  paintEstimate();
});
$("run-form").addEventListener("input", paintEstimate);
$("mailbox-picks").addEventListener("change", () => {
  paintPickSummary();
  paintEstimate();
});
$("pick-all").addEventListener("click", () => {
  $("mailbox-picks").querySelectorAll("input[type=checkbox]").forEach((input) => {
    if (!input.disabled) input.checked = true;
  });
  paintPickSummary();
});
$("pick-available").addEventListener("click", () => {
  $("mailbox-picks").querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = !input.disabled;
  });
  paintPickSummary();
});
$("pick-none").addEventListener("click", () => {
  $("mailbox-picks").querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = false;
  });
  paintPickSummary();
});
$("import-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-import]");
  if (!button) return;
  document.querySelectorAll("[data-import-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.importPanel !== button.dataset.import;
  });
  document.querySelectorAll("[data-import]").forEach((other) => other.classList.toggle("on", other === button));
});
for (const id of ["campaign-list-search", "campaign-list-status", "campaign-list-sort"]) {
  $(id).addEventListener("input", () => {
    campaignListPage = 0;
    if (latestData) paintCampaignList(latestData.campaigns);
  });
}
$("campaign-list-prev").addEventListener("click", () => {
  campaignListPage = Math.max(0, campaignListPage - 1);
  if (latestData) paintCampaignList(latestData.campaigns);
});
$("campaign-list-next").addEventListener("click", () => {
  campaignListPage += 1;
  if (latestData) paintCampaignList(latestData.campaigns);
});
document.querySelectorAll("[data-jump]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = $(button.dataset.jump);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    document.querySelectorAll("[data-jump]").forEach((other) => other.classList.toggle("on", other === button));
  });
});
$("overview-pause").addEventListener("click", () => runAction("pause", liveCampaignId));
$("overview-resume").addEventListener("click", () => runAction("resume", liveCampaignId));

async function runAction(action, campaignId) {
  const id = campaignId || selectedId;
  if (!id) return;
  try {
    if ((action === "start" || action === "resume") && parseRoute().name === "campaign") await saveCampaign();
    if (action === "start") {
      if (parseRoute().name === "campaign" && !confirm(launchSummary())) return;
      const report = await api(`/api/campaigns/${id}/preflight`, { method: "POST", body: "{}" });
      if (report.blockers && report.blockers.length) {
        toast(report.blockers[0], "bad");
        return;
      }
      if (report.warnings && report.warnings.length) {
        const proceed = window.confirm(`${report.warnings.join("\n")}\n\nStart anyway?`);
        if (!proceed) return;
      }
    }
    if (parseRoute().name === "campaign" && $("run-note")) {
      const labels = {
        start: "Starting…",
        resume: "Resuming…",
        pause: "Pausing…",
        "retry-failed": "Putting failed rows back in the queue…",
        resend: "Putting sent rows back in the queue…",
        restart: "Putting sent and failed rows back in the queue…",
      };
      $("run-note").hidden = false;
      text($("run-note"), labels[action] || "Working…");
    }
    await api(`/api/campaigns/${id}/${action}`, {
      method: "POST",
      body: action === "start" ? JSON.stringify(timingFields()) : "{}",
    });
    const done = {
      start: "Campaign started.",
      resume: "Campaign resumed.",
      pause: "Campaign paused.",
      "retry-failed": "Failed rows are back in the queue.",
      resend: "Sent rows are back in the queue. Press Start.",
    };
    if (done[action]) toast(done[action], "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function duplicateCampaign(id) {
  try {
    const created = await api(`/api/campaigns/${id}/duplicate`, { method: "POST", body: "{}" });
    toast(`Copied as ${created.name}.`, "ok");
    selectedId = created.id;
    loadedId = null;
    location.hash = `#/campaigns/${created.id}`;
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function restartCampaign(id) {
  if (!id) return;
  if (!window.confirm("Put every sent and failed address back in the queue? Sending starts only after you press Start.")) return;
  try {
    const result = await api(`/api/campaigns/${id}/restart`, { method: "POST", body: "{}" });
    const message = result.count
      ? `${result.count} back in the queue. Press Start.`
      : "Nothing to restart.";
    const note = $("run-note");
    if (note && parseRoute().name === "campaign") {
      note.hidden = false;
      text(note, message);
    }
    toast(message, result.count ? "ok" : "bad");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
}

$("html-body").addEventListener("input", previewHtml);

$("test-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const form = new FormData(event.target);
  try {
    await saveCampaign();
    const result = await api(`/api/campaigns/${selectedId}/test-send`, {
      method: "POST",
      body: JSON.stringify({ to: form.get("to"), mailbox_id: Number(form.get("mailbox_id")) }),
    });
    text($("test-result"), result.line);
    toast(result.line || "Test finished.", result.outcome === "failed" ? "bad" : "ok");
    await tick();
  } catch (error) {
    text($("test-result"), error.message);
    toast(error.message, "bad");
  }
});

$("subscriber-search").addEventListener("input", () => {
  audiencePage = 0;
  tick();
});
for (const id of ["audience-status", "audience-provider", "audience-group-filter", "audience-tag-filter", "audience-sort"]) {
  $(id).addEventListener("change", () => {
    audiencePage = 0;
    tick();
  });
}
$("audience-prev").addEventListener("click", () => {
  audiencePage = Math.max(0, audiencePage - 1);
  tick();
});
$("audience-next").addEventListener("click", () => {
  audiencePage += 1;
  tick();
});
$("import-open").addEventListener("click", () => {
  importReady = "";
  if (latestData && latestData.settings) $("import-mode").value = latestData.settings.import_mode || "skip";
  $("import-save").disabled = true;
  text($("import-preview"), "Choose a list, then preview before saving.");
  $("import-dialog").showModal();
});
$("import-cancel").addEventListener("click", () => $("import-dialog").close());
$("import-source").addEventListener("change", () => {
  const source = $("import-source").value;
  $("import-text-label").hidden = source !== "paste";
  $("import-file-label").hidden = source !== "csv" && source !== "xlsx";
  $("import-url-label").hidden = source !== "sheet";
  $("import-save").disabled = true;
});
$("import-preview-btn").addEventListener("click", async () => {
  try {
    await previewImport();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("import-dialog-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!importReady) {
    toast("Preview the list before saving.", "bad");
    return;
  }
  const source = $("import-source").value;
  const payload = {
    mode: $("import-mode").value,
    group_id: $("import-group").value ? Number($("import-group").value) : null,
    group_name: $("import-group-name").value,
    source,
    text: source === "sheet" ? "" : importReady,
    url: source === "sheet" ? importReady : "",
  };
  try {
    const result = await api("/api/audience/import", { method: "POST", body: JSON.stringify(payload) });
    toast(`${result.added} added, ${result.updated} updated, ${result.skipped} skipped.`, "ok");
    $("import-dialog").close();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("contact-open").addEventListener("click", () => openContact(null));
$("contact-cancel").addEventListener("click", () => $("contact-dialog").close());
$("contact-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = {
    email: form.get("email"),
    first_name: form.get("first_name"),
    last_name: form.get("last_name"),
    company: form.get("company"),
  };
  const id = form.get("id");
  try {
    await api(id ? `/api/audience/${id}` : "/api/audience", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    $("contact-dialog").close();
    toast("Contact saved.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("group-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/groups", { method: "POST", body: JSON.stringify({ name: new FormData(event.target).get("name") }) });
    event.target.reset();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("tag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/tags", { method: "POST", body: JSON.stringify({ name: new FormData(event.target).get("name") }) });
    event.target.reset();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("segment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/segments", { method: "POST", body: JSON.stringify({ name: form.get("name"), provider: form.get("provider"), status: form.get("status"), group_id: form.get("group_id") || "", tag_id: form.get("tag_id") || "" }) });
    event.target.reset();
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
async function assignSelected(kind) {
  const ids = selectedContactIds();
  if (!ids.length) {
    toast("Select at least one contact.", "bad");
    return;
  }
  const name = prompt(kind === "group" ? "Group name" : "Tag name");
  if (!name || !name.trim()) return;
  try {
    const created = await api(kind === "group" ? "/api/groups" : "/api/tags", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    await api("/api/audience/assign", { method: "POST", body: JSON.stringify(kind === "group" ? { ids, group_id: created.id } : { ids, tag_id: created.id }) });
    toast("Updated.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
}
$("bulk-group").addEventListener("click", () => assignSelected("group"));
$("bulk-tag").addEventListener("click", () => assignSelected("tag"));
$("bulk-delete").addEventListener("click", async () => {
  const ids = selectedContactIds();
  if (!ids.length || !confirm(`Delete ${ids.length} contacts from the audience? Campaign history stays.`)) return;
  try {
    await api("/api/audience/delete", { method: "POST", body: JSON.stringify({ ids }) });
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("bulk-export").addEventListener("click", () => {
  const ids = new Set(selectedContactIds());
  const lines = ["email,first name,last name,company,provider,groups,tags,status"];
  document.querySelectorAll("#subscriber-list tr").forEach((row) => {
    const box = row.querySelector("input");
    if (!box || (ids.size && !ids.has(Number(box.value)))) return;
    const cells = [...row.querySelectorAll("td")].slice(1, 8).map((cell) => `"${cell.textContent.replaceAll('"', '""')}"`);
    lines.push(cells.join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "audience.csv";
  link.click();
});
$("audience-use-add").addEventListener("click", async () => {
  const groupIds = [...$("campaign-group-picks").querySelectorAll("input:checked")].map((input) => Number(input.value));
  const segmentId = $("campaign-segment").value;
  if (!selectedId || (!groupIds.length && !segmentId)) {
    toast("Select a group or a segment.", "bad");
    return;
  }
  try {
    const result = await api(`/api/campaigns/${selectedId}/audience-group`, {
      method: "POST",
      body: JSON.stringify({ group_ids: groupIds, segment_id: segmentId ? Number(segmentId) : null }),
    });
    toast(`${result.added} added. ${result.duplicate} were already in this campaign.`, "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("mailbox-group-save").addEventListener("click", async () => {
  const name = prompt("Name this mailbox group");
  const mailboxIds = selectedMailboxIds().map(Number);
  if (!name || !name.trim() || !mailboxIds.length) {
    toast("Select mailboxes and name the group.", "bad");
    return;
  }
  try {
    await api("/api/mailbox-groups", { method: "POST", body: JSON.stringify({ name: name.trim(), mailbox_ids: mailboxIds }) });
    toast("Mailbox group saved.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
for (const id of ["report-campaign", "report-mailbox", "report-server", "report-domain"]) {
  $(id).addEventListener("change", paintAnalytics);
}
$("report-export").addEventListener("click", () => {
  if (!reportData) return;
  const lines = ["kind,name,sent,failed"];
  for (const row of reportData.mailboxes || []) lines.push(["mailbox", row.label, row.sent, row.failed].join(","));
  for (const row of reportData.campaigns || []) lines.push(["campaign", row.name, row.sent, row.failed].join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "analytics.csv";
  link.click();
});
for (const id of ["activity-search", "activity-kind", "activity-outcome"]) {
  $(id).addEventListener("input", () => {
    activityPage = 0;
    loadActivity();
  });
}
$("activity-prev").addEventListener("click", () => {
  activityPage = Math.max(0, activityPage - 1);
  loadActivity();
});
$("activity-next").addEventListener("click", () => {
  activityPage += 1;
  loadActivity();
});

$("report-range").addEventListener("submit", async (event) => {
  event.preventDefault();
  await loadReports();
});

async function uploadMedia(files) {
  const errors = [];
  for (const file of files) {
    const body = new FormData();
    body.append("file", file);
    try {
      await api("/api/media", { method: "POST", body });
    } catch (error) {
      errors.push(`${file.name}: ${error.message}`);
    }
  }
  text($("media-error"), errors.join(" "));
  await loadMedia();
  if (!errors.length) toast("Upload finished.", "ok");
}
$("media-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...$("media-file").files];
  if (!files.length) return;
  await uploadMedia(files);
  event.target.reset();
});
$("media-drop").addEventListener("dragover", (event) => event.preventDefault());
$("media-drop").addEventListener("drop", async (event) => {
  event.preventDefault();
  await uploadMedia([...event.dataTransfer.files]);
});
$("media-search").addEventListener("input", () => loadMedia());
$("media-filter").addEventListener("change", () => loadMedia());
$("media-view-grid").addEventListener("click", () => {
  localStorage.setItem("mediaView", "grid");
  loadMedia();
});
$("media-view-list").addEventListener("click", () => {
  localStorage.setItem("mediaView", "list");
  loadMedia();
});
$("media-preview-close").addEventListener("click", () => $("media-preview").close());
$("library-attach").addEventListener("click", async () => {
  if (!selectedId || !$("library-pick").value) {
    toast("Open a campaign and choose a file.", "bad");
    return;
  }
  try {
    await api(`/api/campaigns/${selectedId}/use-media/${$("library-pick").value}`, { method: "POST" });
    toast("Attached to this campaign.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("library-insert").addEventListener("click", () => {
  const id = $("library-pick").value;
  const row = mediaRows.find((item) => String(item.id) === id);
  if (!row || !String(row.mime_type).startsWith("image/")) {
    toast("Choose an image.", "bad");
    return;
  }
  $("html-body").value += `\n<img src="${location.origin}${row.url}" alt="${row.filename}">`;
  previewHtml();
  toast("Inserted. The image loads in this app, not in a recipient’s inbox.", "ok");
});
$("company-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/admin/companies", { method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())) });
    event.target.reset();
    toast("Company created.", "ok");
    await paintAdmin();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("user-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/admin/users", { method: "POST", body: JSON.stringify({ name: form.get("name"), email: form.get("email"), company_id: Number(form.get("company_id")), password: form.get("password") }) });
    event.target.reset();
    toast("User created.", "ok");
    await paintAdmin();
  } catch (error) {
    toast(error.message, "bad");
  }
});
$("offer-new").addEventListener("click", () => {
  $("offer-form").reset();
  $("offer-form").id.value = "";
  $("offer-form").hidden = false;
  text($("offer-form-title"), "New offer");
});
$("offer-cancel").addEventListener("click", () => {
  $("offer-form").hidden = true;
});
document.querySelectorAll("[data-settings]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-settings]").forEach((item) => item.classList.toggle("on", item === button));
    document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== button.dataset.settings;
    });
    const names = { general: "General", defaults: "Campaign defaults", audience: "Audience", security: "Security", data: "Data" };
    setHeader("Settings", names[button.dataset.settings] || "General");
  });
});

$("offer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const id = form.get("id");
    await api(id ? `/api/offers/${id}` : "/api/offers", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify({ name: form.get("name"), url: form.get("url"), note: form.get("note") || "", description: form.get("description") || "", status: form.get("status") || "active" }),
    });
    event.target.reset();
    $("offer-form").hidden = true;
    toast("Offer saved.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("conversion-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/conversions", {
      method: "POST",
      body: JSON.stringify({
        offer_id: form.get("offer_id") ? Number(form.get("offer_id")) : null,
        campaign_id: form.get("campaign_id") ? Number(form.get("campaign_id")) : null,
        email: form.get("email") || "",
        amount: Number(form.get("amount") || 0),
        occurred_at: form.get("occurred_at") || "",
        note: form.get("note") || "",
      }),
    });
    toast("Conversion saved.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        owner_name: form.get("owner_name") || "Admin",
        delay_amount: Number(form.get("delay_amount") || 0),
        delay_unit: form.get("delay_unit"),
        rotation: form.get("rotation"),
        auto_retries: Number(form.get("auto_retries") || 0),
        public_base_url: form.get("public_base_url") || "",
        ai_base_url: form.get("ai_base_url") || "",
        ai_model: form.get("ai_model") || "",
        ai_api_key: form.get("ai_api_key") || "",
        from_name: form.get("from_name") || "",
        reply_to: form.get("reply_to") || "",
        import_mode: form.get("import_mode") || "skip",
      }),
    });
    text($("settings-note"), "Saved.");
    settingsFilled = true;
    toast("Settings saved.", "ok");
  } catch (error) {
    text($("settings-note"), error.message);
    toast(error.message, "bad");
  }
});

$("queue-pause").addEventListener("click", () => runAction("pause", queueId));
$("queue-resume").addEventListener("click", () => runAction("resume", queueId));
$("queue-retry").addEventListener("click", () => runAction("retry-failed", queueId));

let audiencePage = 0;
let importReady = "";

function selectedContactIds() {
  return [...document.querySelectorAll("#subscriber-list input[type=checkbox]:checked")].map((input) => Number(input.value));
}

function paintCampaignGroups(groups, segments) {
  const box = $("campaign-group-picks");
  if (!box) return;
  const checked = new Set([...box.querySelectorAll("input:checked")].map((input) => input.value));
  box.replaceChildren();
  if (!groups.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Create a contact group in Audience, then select it here.";
    box.appendChild(empty);
  }
  for (const row of groups) {
    const label = document.createElement("label");
    label.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(row.id);
    input.checked = checked.has(input.value);
    label.append(input, document.createTextNode(` ${row.name} · ${row.contacts || 0}`));
    box.appendChild(label);
  }
  fillNamedSelect($("campaign-segment"), segments || [], "No segment");
}

function paintMailboxGroups(groups) {
  const node = $("mailbox-group-list");
  if (!node) return;
  node.replaceChildren();
  for (const group of groups) {
    node.appendChild(miniButton(`${group.name} · ${(group.mailbox_ids || []).length}`, () => {
      const ids = new Set((group.mailbox_ids || []).map(String));
      $("mailbox-picks").querySelectorAll("input[type=checkbox]").forEach((input) => {
        if (!input.disabled) input.checked = ids.has(input.value);
      });
      paintPickSummary();
      paintEstimate();
    }));
  }
}

function fillNamedSelect(select, rows, blank) {
  const current = select.value;
  select.replaceChildren();
  const first = document.createElement("option");
  first.value = "";
  first.textContent = blank;
  select.appendChild(first);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.name;
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

async function loadSubscribers() {
  const query = new URLSearchParams({
    q: $("subscriber-search").value.trim(),
    status: $("audience-status").value,
    provider: $("audience-provider").value,
    group: $("audience-group-filter").value,
    tag: $("audience-tag-filter").value,
    sort: $("audience-sort").value,
    page: String(audiencePage),
  });
  const data = await api(`/api/audience?${query}`);
  const summary = data.summary || {};
  text($("sub-total"), String(summary.total || 0));
  text($("sub-active"), String(summary.active || 0));
  text($("sub-unsub"), String(summary.unsubscribed || 0));
  text($("sub-invalid"), String(summary.invalid || 0));
  text($("sub-dupes"), String(summary.duplicates || 0));
  fillNamedSelect($("audience-group-filter"), data.groups || [], "Any group");
  fillNamedSelect($("audience-tag-filter"), data.tags || [], "Any tag");
  fillNamedSelect($("segment-group"), data.groups || [], "Any group");
  fillNamedSelect($("segment-tag"), data.tags || [], "Any tag");
  fillNamedSelect($("import-group"), data.groups || [], "No group");
  const body = $("subscriber-list");
  body.replaceChildren();
  $("subscriber-empty").hidden = data.rows.length > 0;
  text($("audience-page"), data.total ? `${data.page + 1} / ${data.pages}` : "");
  for (const row of data.rows) {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = String(row.id);
    pick.appendChild(box);
    tr.appendChild(pick);
    const added = row.created_at ? new Date(row.created_at).toLocaleDateString() : "—";
    for (const value of [row.email, `${row.first_name || ""} ${row.last_name || ""}`.trim() || "—", row.company || "—", row.provider, row.groups || "—", row.tags || "—", row.status, row.source || "—", added]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    const action = document.createElement("td");
    action.appendChild(miniButton("Edit", () => openContact(row)));
    action.appendChild(miniButton("Suppress", async () => {
      try {
        await api("/api/suppression", { method: "POST", body: JSON.stringify({ email: row.email }) });
        toast(`${row.email} will not be emailed again.`, "ok");
        await tick();
      } catch (error) {
        toast(error.message, "bad");
      }
    }));
    tr.appendChild(action);
    body.appendChild(tr);
  }
  paintDirectory("group-list", data.groups || [], "group");
  paintDirectory("tag-list", data.tags || [], "tag");
  paintDirectory("segment-list", data.segments || [], "segment");
}

function paintDirectory(id, rows, kind) {
  const node = $(id);
  node.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = kind === "group" ? "No groups yet." : kind === "tag" ? "No tags yet." : "No segments yet.";
    node.appendChild(empty);
    return;
  }
  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "row";
    const label = document.createElement("span");
    label.textContent = `${row.name} · ${row.contacts || 0}`;
    line.appendChild(label);
    if (kind === "group") {
      line.appendChild(miniButton("Merge", async () => {
        const target = prompt("Merge into which group name? This group will be removed. Contacts stay.");
        if (!target || !target.trim()) return;
        const match = (latestData.groups || []).find((item) => item.name.toLowerCase() === target.trim().toLowerCase());
        if (!match || match.id === row.id) {
          toast("Type the exact name of the group that should remain.", "bad");
          return;
        }
        try {
          await api("/api/groups/merge", { method: "POST", body: JSON.stringify({ ids: [row.id], group_id: match.id }) });
          toast("Groups merged.", "ok");
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      }));
      line.appendChild(miniButton("Rename", async () => {
        const name = prompt("Group name", row.name);
        if (!name || !name.trim()) return;
        try {
          await api(`/api/groups/${row.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
          await tick();
        } catch (error) {
          toast(error.message, "bad");
        }
      }));
    }
    if (kind === "segment") {
      line.appendChild(miniButton("Show", () => {
        $("audience-provider").value = row.provider || "";
        $("audience-status").value = row.status === "any" ? "" : row.status;
        audiencePage = 0;
        tick();
      }));
    }
    line.appendChild(miniButton("Delete", async () => {
      if (!confirm(`Delete ${row.name}? Contacts stay in the audience.`)) return;
      try {
        await api(`/api/${kind}s/${row.id}`, { method: "DELETE" });
        await tick();
      } catch (error) {
        toast(error.message, "bad");
      }
    }));
    node.appendChild(line);
  }
}

function openContact(row) {
  const form = $("contact-form");
  form.id.value = row ? row.id : "";
  form.email.value = row ? row.email : "";
  form.first_name.value = row ? row.first_name : "";
  form.last_name.value = row ? row.last_name : "";
  form.company.value = row ? row.company : "";
  text($("contact-title"), row ? "Edit contact" : "Add contact");
  $("contact-dialog").showModal();
}

async function previewImport() {
  const source = $("import-source").value;
  let data;
  if (source === "csv" || source === "xlsx") {
    const file = $("import-file").files[0];
    if (!file) throw new Error("Choose a file.");
    const body = new FormData();
    body.append("file", file);
    data = await api("/api/audience/preview-file", { method: "POST", body });
    importReady = data.text || "";
  } else {
    const payload = { text: $("import-text").value, url: $("import-url").value, source };
    data = await api("/api/audience/preview", { method: "POST", body: JSON.stringify(payload) });
    importReady = source === "sheet" ? $("import-url").value : $("import-text").value;
  }
  text($("import-preview"), `${data.total} rows · ${data.new} new · ${data.existing} already saved · ${data.duplicateInFile} duplicates in the file · ${data.invalid} invalid · ${data.suppressed} unsubscribed`);
  $("import-save").disabled = false;
}

let reportData = null;

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function rateOf(sent, failed) {
  const total = Number(sent || 0) + Number(failed || 0);
  return total ? `${Math.round((Number(sent) / total) * 100)}%` : "—";
}

async function loadReports() {
  const form = new FormData($("report-range"));
  const query = new URLSearchParams();
  if (form.get("start")) query.set("start", form.get("start"));
  if (form.get("end")) query.set("end", form.get("end"));
  reportData = await api(`/api/reports?${query}`);
  text($("range-sent"), `${reportData.sentInRange} accepted sends in this range`);
  const sent = (reportData.mailboxes || []).reduce((sum, row) => sum + Number(row.sent || 0), 0);
  const failed = (reportData.mailboxes || []).reduce((sum, row) => sum + Number(row.failed || 0), 0);
  text($("analytics-sent"), String(sent));
  text($("analytics-failed"), String(failed));
  text($("analytics-rate"), rateOf(sent, failed));
  text($("analytics-quota"), String(reportData.quotaStops || 0));
  text($("analytics-retries"), String(reportData.googleWaits || 0));
  text($("analytics-campaigns"), String(reportData.campaignCount || 0));
  text($("report-totals"), `${reportData.quotaStops} quota stops · ${reportData.googleWaits} automatic waits. A quota stop means Google reported no remaining sends for that mailbox.`);
  fillNamedSelect($("report-campaign"), reportData.campaigns || [], "All campaigns");
  fillNamedSelect($("report-mailbox"), (reportData.mailboxes || []).map((row) => ({ id: row.id, name: row.label || "Mailbox" })), "All mailboxes");
  const servers = [...new Set((reportData.mailboxes || []).map((row) => row.server).filter(Boolean))];
  const serverSelect = $("report-server");
  const currentServer = serverSelect.value;
  serverSelect.replaceChildren();
  serverSelect.appendChild(new Option("All servers", ""));
  servers.forEach((name) => serverSelect.appendChild(new Option(name, name)));
  if ([...serverSelect.options].some((option) => option.value === currentServer)) serverSelect.value = currentServer;
  const domainSelect = $("report-domain");
  const currentDomain = domainSelect.value;
  domainSelect.replaceChildren();
  domainSelect.appendChild(new Option("All recipient domains", ""));
  (reportData.domains || []).forEach((row) => domainSelect.appendChild(new Option(row.domain, row.domain)));
  if ([...domainSelect.options].some((option) => option.value === currentDomain)) domainSelect.value = currentDomain;
  paintAnalytics();
}

function paintAnalytics() {
  if (!reportData) return;
  const campaign = $("report-campaign").value;
  const mailbox = $("report-mailbox").value;
  const server = $("report-server").value;
  const domain = $("report-domain").value;
  const boxes = (reportData.mailboxes || []).filter((row) => (!mailbox || String(row.id) === mailbox) && (!server || row.server === server));
  const campaigns = (reportData.campaigns || []).filter((row) => !campaign || String(row.id) === campaign);
  const domains = (reportData.domains || []).filter((row) => !domain || row.domain === domain);
  paintReportRows($("report-mailboxes"), boxes, (row) => [row.label || "—", row.server || "—", row.accountKind || "—", row.sent, row.failed, rateOf(row.sent, row.failed), row.quotaStops]);
  paintReportRows($("report-campaigns"), campaigns, (row) => [row.name || "—", row.recipients, row.sent, row.failed, rateOf(row.sent, row.failed), row.created_at ? new Date(row.created_at).toLocaleDateString() : "—", row.status || "—"]);
  paintReportRows($("report-domains"), domains, (row) => [row.domain, row.sent, row.failed]);
  paintAnalyticsChart(reportData.series || []);
}

function paintAnalyticsChart(series) {
  const chart = $("analytics-chart");
  const days = series.filter((row) => Number(row.sent || 0) || Number(row.failed || 0)).slice(-16);
  text($("analytics-caption"), days.length ? "Days with a stored send. Empty days are left out." : "No sends are stored for this range.");
  chart.replaceChildren();
  if (!days.length) {
    const empty = document.createElement("p");
    empty.className = "analytics-empty";
    empty.textContent = "Nothing to chart yet.";
    chart.appendChild(empty);
    return;
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "analytics-chart");
  svg.setAttribute("viewBox", "0 0 640 220");
  svg.setAttribute("role", "img");
  const max = Math.max(1, ...days.map((row) => Number(row.sent || 0) + Number(row.failed || 0)));
  const left = 36;
  const plotW = 580;
  const base = 176;
  const plotH = 140;
  const gap = plotW / days.length;
  const barW = Math.min(28, gap * 0.46);
  const ns = "http://www.w3.org/2000/svg";
  const axis = document.createElementNS(ns, "line");
  axis.setAttribute("x1", String(left));
  axis.setAttribute("x2", "620");
  axis.setAttribute("y1", String(base));
  axis.setAttribute("y2", String(base));
  axis.setAttribute("stroke", "#d7e4f2");
  svg.appendChild(axis);
  const top = document.createElementNS(ns, "text");
  top.setAttribute("x", "0");
  top.setAttribute("y", "16");
  top.textContent = String(max);
  svg.appendChild(top);
  days.forEach((row, index) => {
    const sent = Number(row.sent || 0);
    const failed = Number(row.failed || 0);
    const x = left + index * gap + (gap - barW) / 2;
    const sentH = (sent / max) * plotH;
    const failedH = (failed / max) * plotH;
    const sentBar = document.createElementNS(ns, "rect");
    sentBar.setAttribute("x", String(x));
    sentBar.setAttribute("y", String(base - sentH));
    sentBar.setAttribute("width", String(barW));
    sentBar.setAttribute("height", String(Math.max(sent ? 2 : 0, sentH)));
    sentBar.setAttribute("rx", "3");
    sentBar.setAttribute("fill", "#0866D9");
    const tip = document.createElementNS(ns, "title");
    tip.textContent = `${row.day || "Day"}: ${sent} accepted, ${failed} failed`;
    sentBar.appendChild(tip);
    const failedBar = document.createElementNS(ns, "rect");
    failedBar.setAttribute("x", String(x));
    failedBar.setAttribute("y", String(base - sentH - failedH));
    failedBar.setAttribute("width", String(barW));
    failedBar.setAttribute("height", String(Math.max(failed ? 2 : 0, failedH)));
    failedBar.setAttribute("rx", "3");
    failedBar.setAttribute("fill", "#D95B5B");
    failedBar.appendChild(tip.cloneNode(true));
    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", String(x + barW / 2));
    label.setAttribute("y", "198");
    label.setAttribute("text-anchor", "middle");
    const day = String(row.day || row.date || "").slice(5);
    label.textContent = day ? day.replace("-", "/") : "";
    svg.append(sentBar, failedBar, label);
  });
  chart.appendChild(svg);
}

function paintReportRows(body, rows, cells) {
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "Nothing stored for this filter.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of cells(row)) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

let activityPage = 0;

async function loadActivity() {
  const query = new URLSearchParams({
    q: $("activity-search").value.trim(),
    campaign: $("activity-campaign").value,
    kind: $("activity-kind").value,
    outcome: $("activity-outcome").value,
    page: String(activityPage),
  });
  const data = await api(`/api/activity?${query}`);
  paintActivityList(data.rows);
  $("activity-none").hidden = data.rows.length > 0;
  $("activity-pager").hidden = data.pages <= 1;
  text($("activity-count"), data.total ? `${data.total} recorded events` : "No recorded events for this filter.");
  text($("activity-page"), data.total ? `${data.page + 1} / ${data.pages}` : "");
}

function paintActivityList(rows) {
  const log = $("log");
  log.replaceChildren();
  let day = "";
  for (const row of rows) {
    const date = new Date(row.created_at);
    const label = Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    if (label !== day) {
      day = label;
      const head = document.createElement("h3");
      head.className = "activity-day";
      head.textContent = label;
      log.appendChild(head);
    }
    const described = describeEvent(row.message);
    const item = document.createElement("article");
    item.className = `activity-row ${described.kind}`;
    const time = document.createElement("time");
    time.textContent = clockTime(row.created_at);
    time.dateTime = row.created_at || "";
    const dot = document.createElement("span");
    dot.className = "feed-dot";
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = described.title;
    body.appendChild(title);
    const extra = described.detail || (described.title === row.message ? "" : row.message);
    const campaign = row.campaign_name ? row.campaign_name : "";
    const line = [campaign, extra].filter(Boolean).join(" · ");
    if (line) {
      const detail = document.createElement("span");
      detail.textContent = line;
      body.appendChild(detail);
    }
    item.append(time, dot, body);
    item.addEventListener("click", () => {
      text($("activity-detail"), `${clockTime(row.created_at)} · ${row.message}`);
    });
    log.appendChild(item);
  }
}

let mediaRows = [];

function editOffer(row) {
  const form = $("offer-form");
  form.hidden = false;
  form.id.value = row.id;
  form.name.value = row.name;
  form.url.value = row.url;
  form.description.value = row.description || "";
  form.note.value = row.note || "";
  form.status.value = row.status || "active";
  text($("offer-form-title"), "Edit offer");
}

async function showOffer(id) {
  const row = await api(`/api/offers/${id}`);
  const node = $("offer-detail");
  node.hidden = false;
  node.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = row.name;
  const copy = document.createElement("p");
  copy.className = "hint";
  copy.textContent = `${row.url}. Clicks are not tracked. ${row.conversions.length} manual conversions, ${row.revenue} recorded.`;
  node.append(title, copy);
  for (const item of row.conversions) {
    const line = document.createElement("p");
    line.textContent = `${item.occurred_at || item.created_at} · ${item.amount} · ${item.email || "no contact"} · ${item.note || ""}`;
    node.appendChild(line);
  }
  if (!row.campaigns.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No campaign uses this offer yet. Select it in the campaign details step.";
    node.appendChild(empty);
  }
  for (const campaign of row.campaigns) {
    const link = document.createElement("a");
    link.href = `#/campaigns/${campaign.id}`;
    link.textContent = `${campaign.name} · ${campaign.status}`;
    node.appendChild(link);
  }
}

async function loadMedia() {
  const data = await api("/api/media");
  mediaRows = data.rows || [];
  const query = ($("media-search").value || "").trim().toLowerCase();
  const kind = $("media-filter").value;
  const rows = mediaRows.filter((row) => {
    if (query && !row.filename.toLowerCase().includes(query)) return false;
    if (kind === "image" && !String(row.mime_type).startsWith("image/")) return false;
    if (kind === "pdf" && row.mime_type !== "application/pdf") return false;
    return true;
  });
  const images = mediaRows.filter((row) => String(row.mime_type).startsWith("image/")).length;
  text($("media-total"), String(mediaRows.length));
  text($("media-images"), String(images));
  text($("media-docs"), String(mediaRows.length - images));
  const bytes = mediaRows.reduce((sum, row) => sum + Number(row.size || 0), 0);
  text($("media-size"), formatBytes(bytes));
  $("media-empty").hidden = rows.length > 0;
  const list = $("media-list");
  list.className = localStorage.getItem("mediaView") === "list" ? "media-list" : "media-grid";
  list.replaceChildren();
  const picker = $("library-pick");
  const current = picker.value;
  picker.replaceChildren();
  picker.appendChild(new Option("Choose a saved file", ""));
  for (const row of rows) {
    picker.appendChild(new Option(row.filename, String(row.id)));
    const card = document.createElement("article");
    card.className = "media-card";
    if (String(row.mime_type).startsWith("image/")) {
      const img = document.createElement("img");
      img.src = row.url;
      img.alt = "";
      img.addEventListener("error", () => {
        const mark = document.createElement("div");
        mark.className = "file-mark";
        mark.textContent = "Not a readable image";
        img.replaceWith(mark);
      });
      card.appendChild(img);
    } else {
      const mark = document.createElement("div");
      mark.className = "file-mark";
      mark.textContent = "PDF";
      card.appendChild(mark);
    }
    const name = document.createElement("strong");
    name.textContent = row.filename;
    const meta = document.createElement("p");
    meta.className = "hint";
    const when = row.created_at ? new Date(row.created_at).toLocaleDateString() : "";
    meta.textContent = row.used ? `${when} · used in ${row.used} campaign${row.used === 1 ? "" : "s"}` : `${when} · not used in a campaign`;
    const actions = document.createElement("div");
    actions.className = "media-actions";
    actions.append(
      miniButton("Preview", () => {
        if (String(row.mime_type).startsWith("image/")) {
          $("media-preview-img").src = row.url;
          text($("media-preview-name"), row.filename);
          $("media-preview").showModal();
        } else {
          window.open(row.url, "_blank");
        }
      }),
      miniButton("Copy link", async () => {
        await navigator.clipboard.writeText(location.origin + row.url);
        toast("Copied. This link only opens in the app.", "ok");
      }),
      miniButton("Rename", async () => {
        const name = prompt("File name", row.filename);
        if (!name || !name.trim()) return;
        await api(`/api/media/${row.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
        await loadMedia();
      }),
      miniButton("Delete", async () => {
        if (!confirm(`Delete ${row.filename}?`)) return;
        await api(`/api/media/${row.id}`, { method: "DELETE" });
        await loadMedia();
      })
    );
    card.append(name, meta, actions);
    list.appendChild(card);
  }
  if ([...picker.options].some((option) => option.value === current)) picker.value = current;
  for (const row of mediaRows) {
    if (![...picker.options].some((option) => option.value === String(row.id))) picker.appendChild(new Option(row.filename, String(row.id)));
  }
}

async function paintAdmin() {
  const summary = await api("/api/admin/summary");
  text($("admin-companies"), String(summary.companies));
  text($("admin-active-companies"), String(summary.activeCompanies));
  text($("admin-suspended-companies"), String(summary.suspendedCompanies));
  text($("admin-users"), String(summary.users));
  text($("admin-active-users"), String(summary.activeUsers));
  text($("admin-campaigns"), String(summary.campaigns));
  const companies = await api("/api/admin/companies");
  const users = await api("/api/admin/users");
  const pick = $("admin-company-pick");
  pick.replaceChildren();
  const companyBody = $("admin-company-list");
  companyBody.replaceChildren();
  for (const row of companies.rows) {
    pick.appendChild(new Option(row.name, String(row.id)));
    const tr = document.createElement("tr");
    for (const value of [row.name, row.email || "—", row.contact_name || "—", row.users, row.campaigns, row.status]) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    actions.appendChild(miniButton(row.status === "active" ? "Suspend" : "Activate", async () => {
      await api(`/api/admin/companies/${row.id}`, { method: "PATCH", body: JSON.stringify({ name: row.name, email: row.email, contact_name: row.contact_name, phone: row.phone, status: row.status === "active" ? "suspended" : "active" }) });
      await paintAdmin();
    }));
    actions.appendChild(miniButton("Delete", async () => {
      if (!confirm(`Delete ${row.name}? This is refused while it still has users or campaigns.`)) return;
      try {
        await api(`/api/admin/companies/${row.id}`, { method: "DELETE" });
        await paintAdmin();
      } catch (error) {
        toast(error.message, "bad");
      }
    }));
    tr.appendChild(actions);
    companyBody.appendChild(tr);
  }
  const userBody = $("admin-user-list");
  userBody.replaceChildren();
  for (const row of users.rows) {
    if (row.role === "super_admin") continue;
    const tr = document.createElement("tr");
    for (const value of [row.name, row.email, row.company_name || "—", row.status, row.last_login ? clockTime(row.last_login) : "—"]) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    actions.appendChild(miniButton(row.status === "active" ? "Suspend" : "Activate", async () => {
      await api(`/api/admin/users/${row.id}`, { method: "PATCH", body: JSON.stringify({ name: row.name, email: row.email, company_id: row.company_id, status: row.status === "active" ? "suspended" : "active" }) });
      await paintAdmin();
    }));
    actions.appendChild(miniButton("Reset password", async () => {
      const password = prompt("New password, at least 8 characters");
      if (!password) return;
      await api(`/api/admin/users/${row.id}`, { method: "PATCH", body: JSON.stringify({ name: row.name, email: row.email, company_id: row.company_id, status: row.status, password }) });
      toast("Password reset.", "ok");
    }));
    actions.appendChild(miniButton("Delete", async () => {
      if (!confirm(`Delete ${row.email}? Campaigns and mailboxes stay.`)) return;
      await api(`/api/admin/users/${row.id}`, { method: "DELETE" });
      await paintAdmin();
    }));
    tr.appendChild(actions);
    userBody.appendChild(tr);
  }
}

function paintPreviewChoices(rows) {
  const select = $("preview-person");
  if (!select) return;
  const signature = rows.map((row) => row.id).join(",");
  if (select.dataset.sig === signature) return;
  const current = select.value;
  select.dataset.sig = signature;
  select.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "Raw message";
  select.appendChild(blank);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.email;
    select.appendChild(option);
  }
  if (current && [...select.options].some((option) => option.value === current)) select.value = current;
}

function paintTemplatePick(rows) {
  const select = $("template-pick");
  if (!select) return;
  const signature = rows.map((row) => row.id).join(",");
  if (select.dataset.sig === signature) return;
  const current = select.value;
  select.dataset.sig = signature;
  select.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "Templates";
  select.appendChild(blank);
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.name;
    select.appendChild(option);
  }
  if (current && [...select.options].some((option) => option.value === current)) select.value = current;
}

$("preview-person").addEventListener("change", () => loadPreview());
$("preview-phone").addEventListener("click", () => {
  $("html-preview").classList.toggle("phone");
});

async function loadPreview() {
  const recipientId = $("preview-person").value;
  if (!recipientId || !selectedId) {
    previewHtml();
    text($("text-preview"), "");
    return;
  }
  try {
    const data = await api(`/api/campaigns/${selectedId}/preview`, {
      method: "POST",
      body: JSON.stringify({ recipient_id: Number(recipientId) }),
    });
    const frame = $("html-preview");
    const doc = frame.contentDocument;
    doc.open();
    doc.write(data.html || "<p></p>");
    doc.close();
    text($("text-preview"), data.text || "");
  } catch (error) {
    toast(error.message, "bad");
  }
}

$("template-apply").addEventListener("click", async () => {
  const id = $("template-pick").value;
  if (!id) {
    toast("Choose a template.", "bad");
    return;
  }
  try {
    const row = await api(`/api/templates/${id}`);
    const form = $("campaign-form");
    form.subject.value = row.subject || "";
    form.html_body.value = row.html_body || "";
    form.text_body.value = row.text_body || "";
    form.from_name.value = row.from_name || "";
    form.reply_to.value = row.reply_to || "";
    previewHtml();
    toast("Template applied. Save the campaign before you start.", "ok");
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("template-save").addEventListener("click", async () => {
  const name = window.prompt("Template name");
  if (!name) return;
  const form = new FormData($("campaign-form"));
  try {
    await api("/api/templates", {
      method: "POST",
      body: JSON.stringify({
        name,
        subject: form.get("subject") || "",
        html_body: form.get("html_body") || "",
        text_body: form.get("text_body") || "",
        from_name: form.get("from_name") || "",
        reply_to: form.get("reply_to") || "",
      }),
    });
    toast("Template saved.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("ai-draft").addEventListener("click", async () => {
  if (!selectedId) return;
  try {
    const data = await api(`/api/campaigns/${selectedId}/draft`, {
      method: "POST",
      body: JSON.stringify({ brief: $("ai-brief").value || "" }),
    });
    const form = $("campaign-form");
    if (data.subject) form.subject.value = data.subject;
    if (data.html) form.html_body.value = data.html;
    if (data.text) form.text_body.value = data.text;
    previewHtml();
    toast("Draft ready. Save the campaign before you start.", "ok");
  } catch (error) {
    toast(error.message, "bad");
  }
});

$("suppress-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/suppression", { method: "POST", body: JSON.stringify({ email: form.get("email") }) });
    event.target.reset();
    toast("Address suppressed.", "ok");
    await tick();
  } catch (error) {
    toast(error.message, "bad");
  }
});

async function loadSuppression() {
  const data = await api("/api/suppression");
  const list = $("suppression-list");
  list.replaceChildren();
  for (const row of data.rows) {
    const item = document.createElement("li");
    item.textContent = `${row.email} · ${row.reason || "unsubscribed"} `;
    item.appendChild(miniButton("Remove", async () => {
      try {
        await api(`/api/suppression?email=${encodeURIComponent(row.email)}`, { method: "DELETE" });
        toast("Removed from the suppression list.", "ok");
        await loadSuppression();
      } catch (error) {
        toast(error.message, "bad");
      }
    }));
    list.appendChild(item);
  }
}

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  location.href = "/login";
});

$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/password", {
      method: "POST",
      body: JSON.stringify({ current: form.get("current"), password: form.get("password") }),
    });
    event.target.reset();
    toast("Password changed.", "ok");
  } catch (error) {
    toast(error.message, "bad");
  }
});

const commands = [
  ["Create campaign", () => $("header-create").click()],
  ["Add mailbox", () => { location.hash = "#/mailboxes"; showMailboxTab("add"); showWizard(1); }],
  ["Open campaigns", () => { location.hash = "#/campaigns"; }],
  ["Open reports", () => { location.hash = "#/deliverability"; }],
  ["Open activity", () => { location.hash = "#/activity"; }],
  ["Open settings", () => { location.hash = "#/settings"; }],
  ["Open suppression", () => { location.hash = "#/suppression"; }],
  ["Sign out", () => $("logout").click()],
];

function paintCommands(query) {
  const list = $("command-list");
  const needle = query.trim().toLowerCase();
  list.replaceChildren();
  commands.filter(([label]) => label.toLowerCase().includes(needle)).forEach(([label, run], index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (index === 0) button.className = "active";
    button.addEventListener("click", () => {
      $("command").hidden = true;
      run();
    });
    list.appendChild(button);
  });
}

let searchSerial = 0;

function paintAccount(me) {
  const user = me.user || {};
  const name = user.name || user.email || "Account";
  text($("user-name"), name);
  text($("user-email"), user.email || "");
  const role = user.role === "super_admin" ? "Super admin" : "Company user";
  const company = me.company && me.company.name ? me.company.name : "";
  text($("user-meta"), company ? `${role} · ${company}` : role);
  const parts = name.trim().split(/\s+/).slice(0, 2);
  text($("user-initials"), parts.map((part) => part[0] || "").join("").toUpperCase() || "—");
}

function noticeItems(data) {
  if (!data) return [];
  const items = [];
  for (const box of data.mailboxes || []) {
    const label = box.email || box.label || "Mailbox";
    if (box.lastError) items.push({ text: `${label}: ${box.lastError}`, href: "#/mailboxes" });
    else if (box.enabled && box.quotaRemaining === 0) items.push({ text: `${label} has no remaining quota.`, href: "#/mailboxes" });
  }
  for (const row of data.campaigns || []) {
    const label = campaignLabel(row);
    if (row.status === "paused") items.push({ text: `${label} is paused.`, href: `#/campaigns/${row.id}` });
    if (Number(row.failed) > 0) items.push({ text: `${label} has ${row.failed} failed.`, href: `#/campaigns/${row.id}` });
  }
  return items.slice(0, 8);
}

function paintNotices(data) {
  const items = noticeItems(data);
  const list = $("notify-list");
  const badge = $("notify-count");
  list.replaceChildren();
  $("notify-empty").hidden = items.length > 0;
  badge.hidden = items.length === 0;
  text(badge, items.length ? String(items.length) : "");
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "notify-item";
    button.textContent = item.text;
    button.addEventListener("click", () => {
      $("notify-panel").hidden = true;
      location.hash = item.href;
    });
    list.appendChild(button);
  }
}

function toggleHeaderPanel(id) {
  for (const other of ["notify-panel", "user-panel", "more-panel"]) {
    if (other !== id) $(other).hidden = true;
  }
  $(id).hidden = !$(id).hidden;
}

function paintSearch(query) {
  const box = $("search-results");
  const needle = query.trim().toLowerCase();
  if (!needle || !latestData) {
    box.hidden = true;
    return;
  }
  const hits = [];
  for (const row of latestData.campaigns) {
    if ((row.name || "").toLowerCase().includes(needle)) hits.push({ label: campaignLabel(row), href: `#/campaigns/${row.id}`, kind: "Campaign" });
  }
  for (const row of latestData.mailboxes) {
    const label = row.email || row.label;
    if (label.toLowerCase().includes(needle)) hits.push({ label, href: "#/mailboxes", kind: "Mailbox" });
  }
  for (const row of latestData.offers || []) {
    if ((row.name || "").toLowerCase().includes(needle)) hits.push({ label: row.name, href: "#/offers", kind: "Offer" });
  }
  const serial = ++searchSerial;
  const draw = () => {
    box.replaceChildren();
    box.hidden = hits.length === 0;
    for (const hit of hits.slice(0, 8)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = hit.label;
    const kind = document.createElement("span");
    kind.textContent = hit.kind;
    button.appendChild(kind);
    button.addEventListener("click", () => {
      box.hidden = true;
      $("global-search").value = "";
      location.hash = hit.href;
    });
    box.appendChild(button);
    }
  };
  draw();
  if (needle.length < 3) return;
  api(`/api/subscribers?q=${encodeURIComponent(query)}`).then((data) => {
    if (serial !== searchSerial) return;
    for (const row of data.rows || []) {
      const label = row.email;
      if (label && !hits.some((hit) => hit.label === label)) hits.push({ label, href: "#/subscribers", kind: "Contact" });
    }
    draw();
  }).catch(() => {});
}

$("nav-toggle").addEventListener("click", () => document.body.classList.toggle("nav-open"));
$("header-refresh").addEventListener("click", () => tick());
$("more-refresh").addEventListener("click", () => { $("more-panel").hidden = true; tick(); });
$("header-create").addEventListener("click", () => $("campaign-new").click());
$("more-create").addEventListener("click", () => { $("more-panel").hidden = true; $("header-create").click(); });
$("menu-logout").addEventListener("click", () => $("logout").click());
$("notify-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleHeaderPanel("notify-panel"); });
$("user-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleHeaderPanel("user-panel"); });
$("header-more").addEventListener("click", (event) => { event.stopPropagation(); toggleHeaderPanel("more-panel"); });
$("search-open").addEventListener("click", () => {
  document.body.classList.toggle("search-open");
  if (document.body.classList.contains("search-open")) $("global-search").focus();
});
$("global-search").addEventListener("input", (event) => paintSearch(event.target.value));
document.addEventListener("click", (event) => {
  const search = $("header-search");
  if (search && !search.contains(event.target)) $("search-results").hidden = true;
  for (const id of ["notify-panel", "user-panel", "more-panel"]) {
    const panel = $(id);
    if (panel && !panel.parentElement.contains(event.target)) panel.hidden = true;
  }
});
for (const id of ["campaign-filter", "campaign-status-filter", "campaign-sort"]) {
  $(id).addEventListener("input", () => paintOverviewCampaignTable(overviewCampaigns));
  $(id).addEventListener("change", () => paintOverviewCampaignTable(overviewCampaigns));
}
$("activity-range").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  activityDays = Number(button.dataset.days);
  for (const other of $("activity-range").querySelectorAll("button")) other.classList.toggle("on", other === button);
  if (latestData) paintActivity(latestData.series || []);
});
$("search-kbd").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if ($("header-search").hidden) return;
    document.body.classList.add("search-open");
    $("global-search").focus();
    $("global-search").select();
  }
  if (event.key === "Escape") {
    $("command").hidden = true;
    $("notify-panel").hidden = true;
    $("user-panel").hidden = true;
    $("more-panel").hidden = true;
    $("search-results").hidden = true;
    document.body.classList.remove("search-open");
  }
});
$("command").addEventListener("click", (event) => {
  if (event.target.id === "command") $("command").hidden = true;
});
$("command-input").addEventListener("input", (event) => paintCommands(event.target.value));
$("command-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const first = $("command-list").querySelector("button");
    if (first) first.click();
  }
});

window.addEventListener("hashchange", () => {
  const route = parseRoute();
  if (!route.campaignId) loadedId = null;
  else if (route.campaignId !== loadedId) loadedId = null;
  if (route.name !== "settings") settingsFilled = false;
  clearTimeout(pollTimer);
  tick();
});

tick();
