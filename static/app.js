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

const $ = (id) => document.getElementById(id);

function text(node, value) {
  if (node) node.textContent = value == null ? "" : String(value);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function parseRoute() {
  const parts = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  const pages = ["mailboxes", "campaigns", "activity", "subscribers", "queues", "deliverability", "media", "offers", "settings"];
  if (parts[0] === "campaigns" && parts[1]) return { name: "campaign", campaignId: Number(parts[1]) };
  if (pages.includes(parts[0])) return { name: parts[0] };
  return { name: "overview" };
}

function applyRoute(route) {
  document.querySelectorAll("[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== route.name;
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    const onCampaigns = route.name === "campaign" && link.dataset.nav === "campaigns";
    link.classList.toggle("active", link.dataset.nav === route.name || onCampaigns);
  });
  if (route.campaignId) selectedId = route.campaignId;
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
  const side = $("side-status");
  side.dataset.state = running ? "sending" : paused ? "paused" : "idle";
  text(side, running ? "Sending" : paused ? "Paused" : "Idle");
  text($("overview-clock"), new Date().toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
  }));

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
  text($("stat-queued"), String(queued));
  text($("stat-sent"), String(sent));
  text($("stat-failed"), String(failed));
  text($("stat-campaigns"), String(data.campaigns.length));
  const sendingCount = data.campaigns.filter((row) => row.status === "running").length;
  const pausedCount = data.campaigns.filter((row) => row.status === "paused").length;
  text($("stat-campaigns-detail"), data.campaigns.length
    ? `${sendingCount} sending · ${pausedCount} paused`
    : "None yet");

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
  paintDonut(
    data.campaigns.reduce((sum, row) => sum + Number(row.queued || 0), 0),
    data.campaigns.reduce((sum, row) => sum + Number(row.sent || 0), 0),
    data.campaigns.reduce((sum, row) => sum + Number(row.failed || 0), 0)
  );
  paintOverviewCampaignTable(data.campaigns);
  paintOverviewMailboxTable(data.mailboxes);
  paintQuotaBars(data.mailboxes);
  const overviewEvents = data.campaign && route.name === "overview" ? data.campaign.events : [];
  paintOverviewLog(overviewEvents);
  $("overview-log").hidden = overviewEvents.length === 0;
  $("overview-empty").hidden = overviewEvents.length > 0;
  text(
    $("overview-empty"),
    !data.mailboxes.length && !data.campaigns.length
      ? "Add a mailbox or a campaign to see this fill in."
      : "No events yet."
  );

  text($("today-revenue"), Number(data.todayRevenue || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  paintMailboxes(data.mailboxes);
  paintServers(data.servers || []);
  paintOffers(data.offers || []);
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
  } else if (onCampaign) {
    fillOffers(data.offers || []);
  }
  if (onCampaign) paintSendChoices(data.mailboxes, data.campaign, justLoaded);
  text($("campaign-heading"), data.campaign.name || "Campaign");
  text($("campaign-status"), data.campaign.status);
  text($("phase"), data.campaign.phase || data.campaign.status);
  paintCounts(data.campaign.counts);
  paintAttachments(data.campaign.attachments);
  paintPeople(data.campaign.recipients);
  paintLog(data.campaign.events);
  $("activity-empty").hidden = data.campaign.events.length > 0;
}

function paintDonut(queued, sent, failed) {
  const total = queued + sent + failed;
  const donut = $("donut");
  text($("donut-center"), String(total));
  if (!total) {
    donut.style.background = "conic-gradient(#e7e2d8 0 100%)";
  } else {
    const sentEnd = (sent / total) * 100;
    const queuedEnd = sentEnd + (queued / total) * 100;
    donut.style.background = `conic-gradient(#1f8a70 0 ${sentEnd}%, #e3a008 ${sentEnd}% ${queuedEnd}%, #d4534a ${queuedEnd}% 100%)`;
  }
  const legend = $("donut-legend");
  legend.replaceChildren();
  for (const item of [
    ["#1f8a70", "Sent", sent],
    ["#e3a008", "Queued", queued],
    ["#d4534a", "Failed", failed],
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

function paintOverviewCampaignTable(rows) {
  const body = $("overview-campaign-table");
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "No campaigns yet.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of [row.name, row.status, row.phase || "", row.queued, row.sent, row.failed]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
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
    name.textContent = `${box.label} · ${box.practice ? `${kind} practice` : kind}`;
    const leftLabel = document.createElement("span");
    leftLabel.className = "muted";
    const known = box.quotaRemaining != null;
    const left = known ? Number(box.quotaRemaining) : 0;
    const used = known ? Math.max(0, cap - left) : 0;
    leftLabel.textContent = known ? `${left} left · ${used} used` : "Not checked";
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
    line.append(name, leftLabel, track);
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

function paintOverviewLog(rows) {
  paintFeed($("overview-log"), rows, 8);
}

function paintMailboxes(rows) {
  const body = $("mailbox-list");
  const query = mailboxQuery.trim().toLowerCase();
  const filtered = rows.filter((box) => {
    if (!query) return true;
    return [box.email, box.label, box.status, box.server, box.user].join(" ").toLowerCase().includes(query);
  });
  const pages = Math.max(1, Math.ceil(filtered.length / 50));
  if (mailboxPage >= pages) mailboxPage = 0;
  const slice = filtered.slice(mailboxPage * 50, mailboxPage * 50 + 50);
  body.replaceChildren();
  $("mailbox-empty").hidden = filtered.length > 0;
  text($("mailbox-page"), filtered.length ? `${mailboxPage + 1} / ${pages}` : "");
  for (const box of slice) {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedBoxes.has(box.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedBoxes.add(box.id);
      else selectedBoxes.delete(box.id);
    });
    pick.appendChild(checkbox);
    tr.appendChild(pick);
    const email = box.email || box.label;
    const cells = [
      email + (box.practice ? " (practice)" : ""),
      box.server || "—",
      box.user || "—",
      box.quotaRemaining == null ? "—" : String(box.quotaRemaining),
      box.status,
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
      miniButton("Edit", () => editBox(box)),
      miniButton("Delete", () => removeBox(box.id))
    );
    actions.appendChild(wrap);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
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
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = row.name;
    names.appendChild(option);
    const item = document.createElement("li");
    item.textContent = `${row.name} `;
    item.appendChild(miniButton("Delete", async () => {
      await api(`/api/servers/${row.id}`, { method: "DELETE" });
      await tick();
    }));
    list.appendChild(item);
  }
}

function paintOffers(rows) {
  const list = $("offer-list");
  list.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    item.textContent = `${row.name} · ${row.url} `;
    item.appendChild(miniButton("Delete", async () => {
      await api(`/api/offers/${row.id}`, { method: "DELETE" });
      await tick();
    }));
    list.appendChild(item);
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
  const live = data.campaigns.find((row) => row.status === "running")
    || data.campaigns.find((row) => row.status === "paused");
  $("queue-card").hidden = !live;
  $("queue-empty").hidden = Boolean(live);
  if (!live) {
    queueId = null;
    return;
  }
  queueId = live.id;
  text($("queue-name"), live.name);
  text($("queue-phase"), live.phase || live.status);
  text($("queue-counts"), `${live.sent} sent · ${live.failed} failed · ${live.queued} queued`);
  $("queue-open").href = `#/campaigns/${live.id}`;
}

function fillSettings(settings) {
  const form = $("settings-form");
  form.owner_name.value = settings.owner_name || "Admin";
  form.delay_amount.value = settings.delay_amount;
  form.delay_unit.value = settings.delay_unit;
  form.rotation.value = settings.rotation;
  form.auto_retries.value = settings.auto_retries;
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
  const active = mailboxes.filter((box) => box.status === "Active" || box.enabled);
  const key = `${campaign.id}:${campaign.mailbox_ids || ""}:${active.map((box) => box.id).join(",")}`;
  if (justLoaded || key !== pickKey) {
    pickKey = key;
    const chosen = new Set(String(campaign.mailbox_ids || "").split(",").filter(Boolean));
    const box = $("mailbox-picks");
    box.replaceChildren();
    for (const row of active) {
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = String(row.id);
      input.checked = chosen.has(String(row.id));
      label.append(input, document.createTextNode(` ${row.email || row.label}`));
      box.appendChild(label);
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

function paintCampaignList(rows) {
  const body = $("campaign-list");
  body.replaceChildren();
  $("campaign-empty").hidden = rows.length > 0;
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of [row.name, row.status, row.queued, row.sent, row.failed]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    const actions = document.createElement("td");
    const open = document.createElement("a");
    open.className = "text-link";
    open.href = `#/campaigns/${row.id}`;
    open.textContent = "Open";
    actions.appendChild(open);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function paintActivityChoices(rows) {
  const select = $("activity-campaign");
  const current = selectedId ? String(selectedId) : "";
  select.replaceChildren();
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.name;
    select.appendChild(option);
  }
  if (current && rows.some((row) => String(row.id) === current)) select.value = current;
}

function paintCounts(counts) {
  text($("counts"), `${counts.total} people · ${counts.queued} queued · ${counts.sent} sent · ${counts.failed} failed`);
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
    if (route.name === "subscribers") await loadSubscribers();
    if (route.name === "deliverability") await loadReports();
    if (route.name === "media") await loadMedia();
    const running = data.campaigns.some((row) => row.status === "running");
    schedule(running ? 1000 : 4000);
  } catch (error) {
    text($("phase"), error.message);
    text($("side-status"), "Idle");
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
  await api(`/api/mailboxes/${id}`, { method: "DELETE" });
  await tick();
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
  try {
    if (id) {
      await api(`/api/mailboxes/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
    } else {
      await api("/api/mailboxes", { method: "POST", body: JSON.stringify({ ...payload, token }) });
    }
    resetMailboxForm();
    await tick();
  } catch (error) {
    alert(error.message);
  }
});

$("mailbox-cancel").addEventListener("click", resetMailboxForm);

$("mailbox-search").addEventListener("input", (event) => {
  mailboxQuery = event.target.value;
  mailboxPage = 0;
  tick();
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
  for (const box of $("mailbox-list").querySelectorAll("input[type=checkbox]")) {
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
  try {
    const result = await api("/api/account-bulk", { method: "POST", body: JSON.stringify(payload) });
    text($("bulk-note"), result.message || "Bulk action completed successfully.");
    if (action === "delete") selectedBoxes.clear();
    await tick();
  } catch (error) {
    text($("bulk-note"), error.message);
  }
});

$("server-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/servers", { method: "POST", body: JSON.stringify({ name: form.get("name") }) });
    event.target.reset();
    await tick();
  } catch (error) {
    alert(error.message);
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
    alert(result.errors && result.errors.length
      ? `Added ${result.added}. ${result.errors.join("; ")}`
      : `Added ${result.added}.`);
    event.target.reset();
    await tick();
  } catch (error) {
    alert(error.message);
  }
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
    await tick();
  } catch (error) {
    alert(error.message);
  }
});

$("practice-kind").addEventListener("change", (event) => {
  $("practice-form").quota.value = event.target.value === "workspace" ? "1500" : "100";
});

$("refresh-all").addEventListener("click", async () => {
  const data = await api("/api/state");
  for (const box of data.mailboxes) {
    await api(`/api/mailboxes/${box.id}/refresh`, { method: "POST" });
  }
  await tick();
});

$("campaign-new").addEventListener("click", async () => {
  const created = await api("/api/campaigns", {
    method: "POST",
    body: JSON.stringify({ name: "Campaign" }),
  });
  selectedId = created.id;
  loadedId = null;
  location.hash = `#/campaigns/${created.id}`;
});

$("campaign-delete").addEventListener("click", async () => {
  if (!selectedId) return;
  await api(`/api/campaigns/${selectedId}`, { method: "DELETE" });
  selectedId = null;
  loadedId = null;
  location.hash = "#/campaigns";
});

$("campaign-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveCampaign();
    text($("save-note"), "Saved");
    text($("campaign-heading"), $("campaign-form").name.value);
    await tick();
  } catch (error) {
    text($("save-note"), error.message);
  }
});

$("attachment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const input = event.target.file;
  for (const file of input.files) {
    const body = new FormData();
    body.append("file", file);
    await api(`/api/campaigns/${selectedId}/attachments`, { method: "POST", body });
  }
  input.value = "";
  await tick();
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
  await tick();
}

$("csv-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await uploadCsv(event.target, event.submitter?.dataset.replace === "true");
  } catch (error) {
    alert(error.message);
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
    await tick();
  } catch (error) {
    alert(error.message);
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
    text($("save-note"), `Added ${result.added}`);
    await tick();
  } catch (error) {
    alert(error.message);
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
  selectedId = Number($("activity-campaign").value);
  loadedId = null;
  tick();
});

$("btn-start").addEventListener("click", () => runAction("start"));
$("btn-pause").addEventListener("click", () => runAction("pause"));
$("btn-resume").addEventListener("click", () => runAction("resume"));
$("btn-retry").addEventListener("click", () => runAction("retry-failed"));
$("overview-pause").addEventListener("click", () => runAction("pause", liveCampaignId));
$("overview-resume").addEventListener("click", () => runAction("resume", liveCampaignId));

async function runAction(action, campaignId) {
  const id = campaignId || selectedId;
  if (!id) return;
  try {
    if ((action === "start" || action === "resume") && parseRoute().name === "campaign") await saveCampaign();
    await api(`/api/campaigns/${id}/${action}`, {
      method: "POST",
      body: action === "start" ? JSON.stringify(timingFields()) : "{}",
    });
    await tick();
  } catch (error) {
    alert(error.message);
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
    await tick();
  } catch (error) {
    text($("test-result"), error.message);
  }
});

$("subscriber-search").addEventListener("input", () => tick());

$("report-range").addEventListener("submit", async (event) => {
  event.preventDefault();
  await loadReports();
});

$("media-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/media", { method: "POST", body: form });
    event.target.reset();
    await loadMedia();
  } catch (error) {
    alert(error.message);
  }
});

$("offer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/offers", {
      method: "POST",
      body: JSON.stringify({ name: form.get("name"), url: form.get("url"), note: form.get("note") || "" }),
    });
    event.target.reset();
    await tick();
  } catch (error) {
    alert(error.message);
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
        amount: Number(form.get("amount") || 0),
        note: form.get("note") || "",
      }),
    });
    await tick();
  } catch (error) {
    alert(error.message);
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
      }),
    });
    text($("settings-note"), "Saved.");
    settingsFilled = true;
  } catch (error) {
    text($("settings-note"), error.message);
  }
});

$("queue-pause").addEventListener("click", () => runAction("pause", queueId));
$("queue-resume").addEventListener("click", () => runAction("resume", queueId));
$("queue-retry").addEventListener("click", () => runAction("retry-failed", queueId));

async function loadSubscribers() {
  const q = $("subscriber-search").value.trim();
  const data = await api(`/api/subscribers?q=${encodeURIComponent(q)}`);
  const body = $("subscriber-list");
  body.replaceChildren();
  $("subscriber-empty").hidden = data.rows.length > 0;
  for (const row of data.rows) {
    const tr = document.createElement("tr");
    for (const value of [row.email, row.campaign_name, row.status, row.attempts, row.last_error || ""]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : String(value);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

async function loadReports() {
  const form = new FormData($("report-range"));
  const query = new URLSearchParams();
  if (form.get("start")) query.set("start", form.get("start"));
  if (form.get("end")) query.set("end", form.get("end"));
  const data = await api(`/api/reports?${query}`);
  text($("range-sent"), `${data.sentInRange} sent in this range`);
  text($("report-totals"), `${data.quotaStops} quota stops · ${data.googleWaits} Google waits`);
  fillReport($("report-mailboxes"), data.mailboxes, "label");
  fillReport($("report-campaigns"), data.campaigns, "name");
}

function fillReport(body, rows, nameKey) {
  body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of [row[nameKey] || "—", row.sent, row.failed]) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

async function loadMedia() {
  const data = await api("/api/media");
  const list = $("media-list");
  list.replaceChildren();
  for (const row of data.rows) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = row.url;
    link.textContent = row.filename;
    link.target = "_blank";
    item.append(link, document.createTextNode(" "));
    item.appendChild(miniButton("Copy link", async () => {
      await navigator.clipboard.writeText(location.origin + row.url);
    }));
    if (selectedId) {
      item.appendChild(miniButton("Attach to campaign", async () => {
        await api(`/api/campaigns/${selectedId}/use-media/${row.id}`, { method: "POST" });
        await tick();
      }));
    }
    list.appendChild(item);
  }
}

window.addEventListener("hashchange", () => {
  const route = parseRoute();
  if (!route.campaignId) loadedId = null;
  else if (route.campaignId !== loadedId) loadedId = null;
  if (route.name !== "settings") settingsFilled = false;
  clearTimeout(pollTimer);
  tick();
});

tick();
