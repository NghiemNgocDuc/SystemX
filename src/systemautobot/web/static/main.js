"use strict";
const { api, el, cardView, when } = SAB;
const $ = (id) => document.getElementById(id);
let state = null;
let providers = [];
let hasKey = {};
let firstRunSetup = false;
let view = "home";

const ICON = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>',
  needs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 6v8M12 18h.01"/></svg>',
  paused: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
};

// ---- navigation ------------------------------------------------------------------------------

function show(next) {
  view = next;
  $("onboard").classList.add("hidden");
  $("shell").classList.remove("hidden");
  for (const v of ["home", "ask", "activity", "settings"]) $("view-" + v).classList.toggle("hidden", v !== next);
  for (const b of document.querySelectorAll(".nav button[data-view]")) {
    if (b.dataset.view === next) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  $("page").scrollTop = 0;
  if (next === "home") { loadGlance(); loadRecent(); }
  if (next === "activity") loadHistory();
  if (next === "settings") fillSettings();
  if (next === "ask") fillAsk();
}
document.querySelector(".nav").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) show(b.dataset.view);
});

// ---- state -------------------------------------------------------------------------------------

async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) {
    document.body.replaceChildren(el("p", { style: "padding:24px" }, "Open SystemAutoBot from the tray icon."));
    return;
  }
  const n = state.cards.length;
  $("badge").textContent = n;
  $("badge").classList.toggle("hidden", !n);
  $("fixes-on").checked = !state.paused;
  $("ai-foot").textContent = state.ai.configured ? `AI: ${state.ai.label}` : "No AI connected";
  renderHero();
  renderCards();
}

function renderHero() {
  const n = state.cards.length;
  const kind = state.paused ? "paused" : n ? "needs" : "ok";
  $("hero").className = "panel status-hero" + (kind === "ok" ? "" : " " + kind);
  $("hero-orb").innerHTML = ICON[kind];
  $("hero-title").textContent = state.paused ? "Fixes are paused"
    : n ? `${n} thing${n > 1 ? "s" : ""} need${n > 1 ? "" : "s"} you` : "Your PC is fine";
  const last = glance && glance.last_checked ? `Last checked ${when(glance.last_checked)}` : "Checking now";
  const watching = glance ? ` · Watching ${glance.watching} kinds of problems` : "";
  const pilot = state.settings.autopilot ? " · Autopilot on" : "";
  $("hero-sub").textContent = state.paused ? "I'm still checking, but I won't change anything." : last + watching + pilot;
}

// ---- home: what needs you (one line each), then recent history ----------------------------------------

let shownCards = "";
const finished = new Map(); // card id -> its row, kept after a fix so the result stays readable
const liveRows = new Map(); // card id -> row, so a refresh never rebuilds one someone has open
const rowLabels = new Map(); // card id -> updates that row's status word
function needRow(c) {
  const major = c.severity === "major";
  const chip = el("span", { class: "chip" });
  const label = (card) => {  // called on every refresh, so "Autopilot is on it" comes and goes
    if (finished.has(c.id)) return;
    chip.textContent = card.auto === "working" ? "Autopilot is on it" : major ? "Needs you now" : "Can wait";
    chip.className = "chip " + (card.auto === "working" ? "" : major ? "bad" : "warn");
  };
  label(c);
  rowLabels.set(c.id, label);
  const row = el("details", { class: "need" });
  const card = cardView(c, { compact: true, via: "fix_this", aiLabel: state.ai.label,
    onOpenRecord: (id) => { show("activity"); detail(id); },
    onDismiss: () => { finished.delete(c.id); liveRows.delete(c.id); shownCards = ""; renderCards(); },
    onConnectAI: () => openSetup(false),
    onChange: (_card, r) => {
      // Keep the row (and what happened) on screen after a fix, or after Check again found it fixed.
      const done = r.outcome ? !["refused", "not_now", "never"].includes(r.outcome) : r.verification === "fixed";
      if (done) {
        const worked = r.outcome ? r.outcome === "done" || r.outcome === "went_away" : true;
        chip.textContent = worked ? "Fixed" : "Didn't work";
        chip.className = "chip " + (worked ? "ok" : "bad");
        finished.set(c.id, row);
      }
      setTimeout(() => { refresh(); loadGlance(); loadRecent(); }, 1500);
    } });
  row.append(el("summary", {}, el("span", { class: "t" }, c.title), chip), card);
  return row;
}
function renderCards() {
  const live = state.cards.filter((c) => !finished.has(c.id));
  for (const c of live) if (rowLabels.has(c.id)) rowLabels.get(c.id)(c);
  const key = JSON.stringify([...finished.keys(), ...live.map((c) => c.id)]);
  if (key === shownCards) return; // never rebuild a row someone is using
  shownCards = key;
  for (const id of [...liveRows.keys()]) {
    if (!finished.has(id) && !live.some((c) => c.id === id)) { liveRows.delete(id); rowLabels.delete(id); }
  }
  const rows = live.map((c) => {
    if (!liveRows.has(c.id)) liveRows.set(c.id, needRow(c));
    return liveRows.get(c.id);
  });
  if (!rows.length && !finished.size) {
    $("cards").replaceChildren(el("p", { class: "empty" },
      "Nothing needs you. A critical problem shows up as a Windows notification. Small ones wait for the end-of-day list."));
    return;
  }
  $("cards").replaceChildren(...finished.values(), ...rows);
}

function historyRow(h) {
  return el("li", {},
    el("button", { class: "hrow", type: "button", onclick: () => { if (view !== "activity") show("activity"); detail(h.id); } },
      el("span", { class: "ic " + (h.outcome || ""), "aria-hidden": "true" }, MARK[h.outcome] || "…"),
      el("span", { class: "t" }, h.summary || h.title,
        el("div", { class: "caption" }, `${OUTCOME[h.outcome] || "In progress"} · ${h.approved_via === "auto" ? "Autopilot, no click" : BY[h.fixed_by] || h.fixed_by}`)),
      el("span", { class: "when" }, when(h.started_at))));
}
async function loadRecent() {
  let r;
  try { r = await api("/api/history?limit=50"); } catch (_) { return; }
  const weekAgo = Date.now() - 7 * 864e5;
  const fixed = r.items.filter((h) => h.outcome === "done" && new Date(h.started_at).getTime() >= weekAgo).length;
  $("recent-sum").textContent = fixed ? `${fixed} fixed this week` : "";
  $("recent").replaceChildren(...(r.items.length ? r.items.slice(0, 5).map(historyRow)
    : [el("li", { class: "empty-row" }, "No fixes yet. Everything I change will be listed here.")]));
}
$("see-activity").addEventListener("click", () => show("activity"));

// ---- at a glance ---------------------------------------------------------------------------------

let glance = null;
async function loadGlance() {
  try { glance = await api("/api/glance"); } catch (_) { return; }
  $("tiles").replaceChildren(...glance.tiles.map((t) => el("div", { class: "tile " + t.state },
    el("div", { class: "label" }, el("span", { class: "dot" }), t.label),
    el("div", { class: "value" }, t.value),
    el("div", { class: "detail", title: t.detail }, t.detail),
    typeof t.fill === "number" && el("div", { class: "bar", role: "img", "aria-label": t.detail },
      el("i", { style: `width:${Math.max(3, Math.min(100, t.fill))}%` })))));
  if (state) renderHero();
}
$("check-now").addEventListener("click", async () => {
  $("check-now").disabled = true;
  $("hero-sub").textContent = "Checking now…";
  await api("/api/check_now", {}).catch(() => {});
  setTimeout(async () => { await loadGlance(); await refresh(); $("check-now").disabled = false; }, 3500);
});
$("fixes-on").addEventListener("change", async () => {
  await api("/api/settings", { fixes_paused: !$("fixes-on").checked }).catch(() => {});
  refresh();
});

// ---- ask -------------------------------------------------------------------------------------------

function fillAsk() {
  $("ask-banner").classList.toggle("hidden", state.ai.configured);
  $("ask-ai").textContent = state.ai.configured ? `${state.ai.label} · ${state.ai.model}${state.ai.replay ? " · Replay" : ""}` : "";
  $("say").focus();
}
function say(who, text) {
  const m = el("div", { class: "msg " + who }, text);
  $("chat").append(m);
  m.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return m;
}
async function ask(text) {
  if (!text) return;
  say("me", text);
  const thinking = say("bot", "Looking at this PC…");
  try {
    const r = await api("/api/chat", { text });
    thinking.textContent = r.reply;
    if (r.card) $("chat").append(cardView(r.card, { aiLabel: state.ai.label, showNever: false,
      onChange: () => setTimeout(refresh, 1500) }));
    if (r.error === "no_key") $("ask-banner").classList.remove("hidden");
  } catch (e) {
    thinking.textContent = "Couldn't. " + e.message;
  }
}
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("say").value.trim();
  $("say").value = "";
  ask(t);
});
$("chips").addEventListener("click", (e) => { if (e.target.dataset.ask) ask(e.target.dataset.ask); });
$("ask-connect").addEventListener("click", () => openSetup(false));

// ---- first run: connect an AI, then choose what to watch -------------------------------------------

async function openSetup(firstRun) {
  firstRunSetup = firstRun;
  const r = await api("/api/providers");
  providers = r.providers;
  hasKey = r.has_key;
  $("provider").replaceChildren(...providers.map((p) => el("option", { value: p.id }, p.label)));
  $("provider").value = state.settings.provider;
  $("model").value = state.settings.model;
  $("base-url").value = state.settings.base_url || "";
  $("cancel-setup").classList.toggle("hidden", firstRun);
  $("skip-setup").classList.toggle("hidden", !firstRun);
  $("save-setup").textContent = firstRun ? "Test and continue" : "Test and save";
  onProvider(false);
  step(1);
  $("shell").classList.add("hidden");
  $("onboard").classList.remove("hidden");
}
function step(n) {
  $("step-1").classList.toggle("hidden", n !== 1);
  $("step-2").classList.toggle("hidden", n !== 2);
  $("step-1-label").className = n === 1 ? "on" : "done";
  $("step-2-label").className = n === 2 ? "on" : "";
  if (n === 2) renderWatch("watch-list-setup", "watch-row");
}
function onProvider(resetModel = true) {
  const p = providers.find((x) => x.id === $("provider").value);
  $("key-hint").textContent = p.key_hint ? `(${p.key_hint})` : "";
  $("get-key").classList.toggle("hidden", !p.key_page);
  $("base-url-box").classList.toggle("hidden", !p.needs_base_url);
  $("has-key").textContent = hasKey[p.id] ? "A key is saved. Leave the box empty to keep it." : "";
  if (resetModel) $("model").value = p.default_model || "";
  setModels([]);
  setupStatus("");
  $("save-anyway").classList.add("hidden");
}
$("provider").addEventListener("change", () => onProvider(true));
// A Groq key (gsk_) pasted while xAI Grok is picked is the easy mix-up: switch to the key's provider.
function keyOwner(key) {
  const known = providers.filter((p) => p.key_prefix).sort((a, b) => b.key_prefix.length - a.key_prefix.length);
  return known.find((p) => key.startsWith(p.key_prefix));
}
$("key").addEventListener("input", () => {
  const key = $("key").value.trim();
  const owner = keyOwner(key);
  if (!owner || owner.id === $("provider").value || $("provider").value === "custom") return;
  $("provider").value = owner.id;
  onProvider(true);
  setupStatus(`That's a ${owner.label} key, so I switched Provider to ${owner.label}.`, true);
});
$("show-key").addEventListener("click", () => {
  const k = $("key");
  k.type = k.type === "password" ? "text" : "password";
  $("show-key").textContent = k.type === "password" ? "Show" : "Hide";
});
$("get-key").addEventListener("click", () => api("/api/open_key_page", { provider: $("provider").value }));
function setupStatus(text, ok) {
  const s = $("setup-status");
  s.textContent = text;
  s.className = "status-line " + (ok === true ? "ok" : ok === false ? "bad" : "");
}
// ---- model picker: filterable list drawn in the page --------------------------------------------
let models = [];
let shown = [];
let active = -1;
function setModels(list) { models = list || []; closeModels(); }
function openModels(all) {
  const box = $("model");
  const q = all ? "" : box.value.trim().toLowerCase();
  shown = models.filter((m) => m.toLowerCase().includes(q));
  const ul = $("model-list");
  if (!models.length) return closeModels();
  active = shown.indexOf(box.value);
  ul.replaceChildren(...(shown.length ? shown.map((m, i) => el("li", {
    id: "model-opt-" + i, role: "option", "aria-selected": String(i === active), class: m === box.value ? "cur" : "",
  }, m)) : [el("li", { class: "none" }, "No model matches. You can still type a name.")]));
  ul.classList.remove("hidden");
  box.setAttribute("aria-expanded", "true");
  moveActive(active);
}
function closeModels() {
  $("model-list").classList.add("hidden");
  $("model").setAttribute("aria-expanded", "false");
  $("model").removeAttribute("aria-activedescendant");
  active = -1;
}
function moveActive(i) {
  const items = $("model-list").querySelectorAll("li[role=option]");
  if (!items.length) return;
  active = i < 0 ? -1 : Math.min(items.length - 1, i);
  items.forEach((li, n) => li.setAttribute("aria-selected", String(n === active)));
  if (active < 0) return $("model").removeAttribute("aria-activedescendant");
  $("model").setAttribute("aria-activedescendant", items[active].id);
  items[active].scrollIntoView({ block: "nearest" });
}
function pickModel(i) {
  if (shown[i] === undefined) return;
  $("model").value = shown[i];
  closeModels();
}
const isOpen = () => !$("model-list").classList.contains("hidden");
$("model").addEventListener("click", () => (isOpen() ? closeModels() : openModels(true)));
$("model").addEventListener("input", () => openModels(false));
$("model").addEventListener("blur", closeModels);
$("model").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!isOpen()) return openModels(true);
    moveActive(active + (e.key === "ArrowDown" ? 1 : -1));
  } else if (e.key === "Enter" && isOpen() && active >= 0) {
    e.preventDefault();
    pickModel(active);
  } else if (e.key === "Escape" && isOpen()) {
    e.preventDefault();
    closeModels();
  }
});
// mousedown, not click: picking must happen before the box loses focus and closes the list
$("model-list").addEventListener("mousedown", (e) => {
  e.preventDefault();
  const li = e.target.closest("li[role=option]");
  if (li) pickModel(Number(li.id.slice("model-opt-".length)));
});

$("load-models").addEventListener("click", async () => {
  setupStatus("Asking for the models your key can use…");
  try {
    const r = await api("/api/models", { provider: $("provider").value, key: $("key").value, base_url: $("base-url").value });
    if (r.error) return setupStatus(r.error, false);
    setModels(r.models);
    setupStatus(r.models.length ? `${r.models.length} models found. Pick one in the list.`
      : "This provider didn't list its models. Type the model name.", r.models.length ? true : null);
    if (r.models.length && !r.models.includes($("model").value)) {
      // Prefer a model known to handle tools; the first one alphabetically often can't.
      const p = providers.find((x) => x.id === $("provider").value) || {};
      const good = [...(p.prefer || []), p.default_model].find((m) => m && r.models.includes(m));
      $("model").value = good || "";
    }
    if (r.models.length) { $("model").focus(); openModels(true); }
  } catch (e) { setupStatus(e.message, false); }
});
async function saveSetup(anyway) {
  setupStatus("Testing…");
  $("save-setup").disabled = true;
  try {
    const r = await api("/api/setup", { provider: $("provider").value, model: $("model").value.trim(),
      key: $("key").value.trim(), base_url: $("base-url").value.trim(), save_anyway: !!anyway });
    setupStatus(r.message, r.ok);
    $("save-anyway").classList.toggle("hidden", r.ok);
    if (r.ok) {
      $("key").value = "";
      await refresh();
      setTimeout(() => (firstRunSetup ? step(2) : show("settings")), 700);
    }
  } catch (e) { setupStatus(e.message, false); }
  $("save-setup").disabled = false;
}
$("save-setup").addEventListener("click", () => saveSetup(false));
$("save-anyway").addEventListener("click", () => saveSetup(true));
$("skip-setup").addEventListener("click", async () => { await api("/api/setup/skip", {}); await refresh(); step(2); });
$("cancel-setup").addEventListener("click", () => show("settings"));
$("back-step-1").addEventListener("click", () => step(1));
$("start-watching").addEventListener("click", () => show("home"));

// ---- which problems to handle (toggle rows) ----------------------------------------------------------

function renderWatch(boxId, rowClass) {
  const s = state.settings;
  $(boxId).replaceChildren(...s.problem_kinds.map((k) => {
    const input = el("input", { type: "checkbox", id: `${boxId}-${k.id}`, "aria-label": k.label });
    input.checked = !s.off.includes(k.id);
    input.addEventListener("change", async () => {
      const off = s.problem_kinds.filter((x) => !$(`${boxId}-${x.id}`).checked).map((x) => x.id);
      try {
        state.settings = await api("/api/settings", { off });
        saved();
      } catch (e) {
        input.checked = !input.checked;
        saved(e.message);
      }
    });
    return el("div", { class: rowClass },
      el("div", { class: "grow" }, el("div", { class: "title", style: "font-weight:700" }, k.label),
        el("div", { class: "sub small muted" }, k.detail)),
      el("label", { class: "switch" }, input, el("span")));
  }));
}

// ---- activity (fix history) ------------------------------------------------------------------------------

const OUTCOME = { done: "Done", couldnt: "Couldn't", went_away: "Went away on its own", interrupted: "Interrupted", refused: "Refused" };
const MARK = { done: "✓", couldnt: "!", went_away: "–", interrupted: "!", refused: "×" };
const BY = { playbook: "I fixed it", grok: "AI-chosen repair", person: "You fixed it", nobody: "Nobody needed to" };

async function loadHistory() {
  const q = $("search").value.trim();
  $("history-detail").classList.add("hidden"); // before the wait, so it can't hide a record opened meanwhile
  const r = await api("/api/history" + (q ? "?q=" + encodeURIComponent(q) : ""));
  $("history").replaceChildren(...(r.items.length ? r.items.map(historyRow)
    : [el("li", { class: "empty-row" }, q ? "Nothing in the history touched that." : "No fixes yet. They'll show up here, step by step.")]));
}
let searchTimer;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadHistory, 300); });

async function detail(id) {
  const h = await api("/api/history/" + id);
  const box = $("history-detail");
  const files = h.files.map((f) => el("tr", {}, el("td", {}, f.action), el("td", {}, f.path)));
  box.replaceChildren(
    el("h2", {}, h.title),
    el("p", { class: "muted", style: "margin-top:4px" }, h.summary || ""),
    el("dl", { class: "kv" },
      el("dt", {}, "When"), el("dd", {}, when(h.started_at)),
      el("dt", {}, "Who"), el("dd", {}, BY[h.fixed_by] || h.fixed_by),
      el("dt", {}, "Approved with"), el("dd", {}, h.approved_via === "auto" ? "Autopilot (no click; you switched it on in Settings)"
        : (h.approved_via || "no approval needed").replaceAll("_", " ")),
      el("dt", {}, "Checked again"), el("dd", {}, ((h.verifications[0] && h.verifications[0].result) || "not yet").replaceAll("_", " ")),
      h.grok_model && el("dt", {}, "AI"), h.grok_model && el("dd", {}, `${h.grok_model}, ${h.grok_rounds} rounds, ${h.grok_tokens} tokens`)),
    el("h3", { style: "margin-top:16px" }, "Steps"),
    h.steps.length ? el("ol", {}, h.steps.map((s) => el("li", {}, `${s.summary} `,
      el("span", { class: "caption" }, `(${s.tool}${s.command ? ": " + s.command : ""}, ${s.status})`))))
      : el("p", { class: "muted" }, h.manual.length ? "You followed the steps yourself." : "No steps ran."),
    el("h3", { style: "margin-top:16px" }, "Files and settings touched"),
    h.folder_totals.map((f) => el("p", { class: "small" }, `${f.folder}: ${f.count} files, ${(f.bytes / 1048576).toFixed(1)} MB`)),
    h.file_lists.map((f) => el("p", { class: "caption" }, `Full list of ${f.total} files is saved in the history folder.`)),
    files.length ? el("table", { class: "files" }, files) : null,
    h.settings.map((s) => el("p", { class: "small" }, `${s.kind} ${s.name}: ${s.old ?? "none"} → ${s.new ?? "none"}`)),
    !files.length && !h.settings.length && !h.folder_totals.length && el("p", { class: "muted small" },
      h.fixed_by === "person" ? "Not tracked: you made these changes outside the app." : "Nothing was touched."));
  box.classList.remove("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}
async function download(fmt) {
  const r = await fetch("/api/history/export?fmt=" + fmt, { headers: { "X-SAB-Token": SAB.token } });
  const a = el("a", { href: URL.createObjectURL(await r.blob()), download: "fix-history." + fmt });
  document.body.append(a);
  a.click();
  a.remove();
}
$("export-json").addEventListener("click", () => download("json"));
$("export-csv").addEventListener("click", () => download("csv"));
$("report").addEventListener("click", async () => {
  const r = await fetch("/api/report", { headers: { "X-SAB-Token": SAB.token } });
  const name = (r.headers.get("content-disposition") || "").split("filename=")[1] || "SystemAutoBot-report.html";
  const a = el("a", { href: URL.createObjectURL(await r.blob()), download: name });
  document.body.append(a);
  a.click();
  a.remove();
});

// ---- settings: every change saves on its own ---------------------------------------------------------------

function saved(err) {
  const s = $("settings-status");
  s.textContent = err || "Saved";
  s.className = "status-line " + (err ? "bad" : "ok");
  clearTimeout(saved.t);
  saved.t = setTimeout(() => (s.textContent = ""), 2500);
}
async function setSetting(body) {
  try { state.settings = await api("/api/settings", body); saved(); refresh(); } catch (e) { saved(e.message); fillSettings(); }
}
function fillSettings() {
  const s = state.settings;
  $("ai-line").textContent = state.ai.configured ? `${state.ai.label} · ${state.ai.model}` : "No AI connected";
  renderWatch("watch-list", "srow");
  $("warn").value = String(s.warn_free_gb);
  $("eod").value = s.end_of_day;
  $("keep").value = s.history_days;
  $("demo-row").classList.toggle("hidden", !s.demo);
  $("ai-limit").value = String(s.ai_daily_limit);
  $("autopilot").checked = !!s.autopilot;
  $("autopilot-frozen").checked = !!s.autopilot_close_frozen;
  $("autopilot-frozen").disabled = !s.autopilot;
  $("autopilot-admin").checked = !!s.autopilot_admin;
  $("autopilot-admin").disabled = !s.autopilot && !s.autopilot_admin;
  api("/api/ai_usage").then((u) => {
    const lim = u.limit ? ` of ${u.limit}` : "";
    $("ai-usage").textContent = `Today: ${u.day.calls}${lim} questions, ${u.day.tokens.toLocaleString()} tokens. ` +
      `This week: ${u.week.calls} questions, ${u.week.tokens.toLocaleString()} tokens.`;
  }).catch(() => ($("ai-usage").textContent = "Not available"));
  $("never").replaceChildren(...(s.never.length ? s.never.map((fp) => el("li", { class: "srow" },
    el("div", { class: "grow" }, el("div", { class: "title" }, fp), el("div", { class: "sub" }, "I won't bring this up again.")),
    el("button", { onclick: () => setSetting({ forget_never: fp }) }, "Remove")))
    : [el("li", { class: "srow muted" }, "Nothing remembered. Choosing Never for this on a card adds it here.")]));
}
$("autopilot").addEventListener("change", () => setSetting({ autopilot: $("autopilot").checked }));
$("autopilot-frozen").addEventListener("change", () => setSetting({ autopilot_close_frozen: $("autopilot-frozen").checked }));
$("autopilot-admin").addEventListener("change", async () => {
  const box = $("autopilot-admin"), line = $("autopilot-admin-status");
  const on = box.checked;
  box.disabled = true;
  line.className = "status-line";
  line.textContent = on ? "Windows will ask for permission once…" : "Removing the setup. Windows will ask once…";
  try {
    const r = await api("/api/autopilot_admin", { on });
    line.className = "status-line " + (r.ok ? "ok" : "bad");
    line.textContent = r.message;
  } catch (e) {
    line.className = "status-line bad";
    line.textContent = e.message;
  }
  await refresh();
  fillSettings();
});
$("warn").addEventListener("change", () => setSetting({ warn_free_gb: Number($("warn").value) }));
$("eod").addEventListener("change", () => setSetting({ end_of_day: $("eod").value }));
$("keep").addEventListener("change", () => setSetting({ history_days: Number($("keep").value) }));
$("change-ai").addEventListener("click", () => openSetup(false));
$("forget-key").addEventListener("click", async () => {
  await api("/api/forget_key", { provider: state.settings.provider });
  saved();
  $("ai-line").textContent = "Key forgotten. Connect an AI to use Ask again.";
});
$("digest-now").addEventListener("click", () => api("/api/digest/now", {}));
$("ai-limit").addEventListener("change", () => setSetting({ ai_daily_limit: Number($("ai-limit").value) }));
$("run-doctor").addEventListener("click", async () => {
  $("run-doctor").disabled = true;
  try {
    const r = await api("/api/doctor");
    $("doctor").replaceChildren(...r.checks.map((c) => el("li", { class: "doc " + c.state },
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("span", { class: "grow" }, el("b", {}, c.name), el("div", { class: "caption" }, c.detail)),
      el("span", { class: "caption" }, c.state === "ok" ? "OK" : c.state === "warn" ? "Check" : "Problem"))));
  } catch (e) { saved(e.message); }
  $("run-doctor").disabled = false;
});

// ---- start ---------------------------------------------------------------------------------------------------

(async () => {
  await refresh();
  if (!state) return;
  if (state.needs_setup) openSetup(true);
  else show(SAB.params.get("view") || "home");
  setInterval(refresh, 4000);
  setInterval(() => { if (view === "home") { loadGlance(); loadRecent(); } }, 15000);
})();
window.sabShow = (v) => (v === "setup" ? openSetup(false) : show(v)); // called by the tray
