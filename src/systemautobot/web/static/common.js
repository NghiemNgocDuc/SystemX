// Shared by every SystemAutoBot window: token, API calls, and the card.
"use strict";

const SAB = (() => {
  // The token arrives in the URL fragment (never sent over HTTP), then leaves the URL.
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("t")) sessionStorage.setItem("sab_t", params.get("t"));
  params.delete("t");
  const token = sessionStorage.getItem("sab_t") || "";
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? "#" + rest : ""));

  // How long a request may take before the page stops waiting (the work may still finish; Activity shows it).
  const SLOW = { "/approve": 65 * 60e3, "/explain": 6 * 60e3, "/api/chat": 6 * 60e3, "/api/setup": 2 * 60e3,
                 "/api/models": 60e3, "/api/autopilot_admin": 6 * 60e3, "/check_again": 2 * 60e3, "/api/check_now": 2 * 60e3, "/api/doctor": 2 * 60e3 };
  async function api(path, body, timeoutMs) {
    const opts = { method: body === undefined ? "GET" : "POST", headers: { "X-SAB-Token": token } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const limit = timeoutMs || Object.entries(SLOW).find(([k]) => path.includes(k))?.[1] || 30e3;
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), limit);
    opts.signal = stop.signal;
    let r;
    try {
      r = await fetch(path, opts);
    } catch (e) {
      if (e.name === "AbortError") {
        throw new Error("This is taking much longer than it should. It may still finish: Activity will show it. You can try again.");
      }
      throw new Error("I couldn't reach the app. Is SystemAutoBot still running?");
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* not JSON */ }
    if (!r.ok) throw new Error((data && data.detail) || "Something went wrong.");
    return data;
  }

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }

  // The AI sometimes answers in Markdown; show it as plain words.
  const plain = (s) => String(s || "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/^#+\s*/gm, "").trim();

  // Long text starts short, with "more" to read the rest.
  function brief(text, limit = 220) {
    text = String(text || "");
    if (text.length <= limit) return document.createTextNode(text);
    const cut = text.lastIndexOf(" ", limit);
    const span = el("span", {}, text.slice(0, cut > 80 ? cut : limit) + "… ");
    span.append(el("button", { type: "button", class: "link", onclick: () => { span.textContent = text; } }, "more"));
    return span;
  }

  // After a fix, or after Check again found it fixed.
  function setOutcome(node, worked) {
    if (!node) return;
    node.classList.add("finished");
    const chip = node.querySelector(".card-head .chip");
    if (chip) {
      chip.textContent = worked ? "Fixed" : "Didn't work";
      chip.className = "chip " + (worked ? "ok" : "bad");
    }
  }

  // What a fix did, in a few lines: the result, what changed, and the steps one click away.
  const STEP_WORD = { couldnt: "didn't work", skipped: "skipped" };
  function fixReport(r, opts = {}) {
    const rep = r.report;
    const ok = r.outcome === "done" || r.outcome === "went_away";
    const buttons = el("div", { class: "actions" },
      rep && opts.onOpenRecord && el("button", { type: "button", onclick: () => opts.onOpenRecord(rep.fix_id) }, "Full record"),
      opts.onDismiss && el("button", { type: "button", class: "primary", onclick: opts.onDismiss }, "Got it"));
    const notFixed = !!rep && rep.verification === "not_fixed"; // it ran, but the problem is still there
    const cls = "report " + (ok && !notFixed ? "done" : "couldnt");
    if (!rep) return el("div", { class: cls }, el("p", { class: "rep-head" }, (ok ? "Done. " : "Didn't work. ") + (r.message || "")), buttons);
    const head = r.outcome === "went_away" ? "It went away on its own, so I changed nothing."
      : ok && rep.verification === "fixed" ? "Fixed. I checked again and it's gone."
      : notFixed ? (rep.detail || rep.verdict)
      : (ok ? "Done. " : "Didn't work. ") + (rep.detail || rep.verdict || r.message || "");
    const files = rep.files || { examples: [], more: 0 };
    const shown = files.examples.slice(0, 3);
    const rest = files.more + files.examples.length - shown.length;
    const extra = [rep.freed && `Freed ${rep.freed}`,
      shown.length && "Files: " + shown.join(", ") + (rest ? ` and ${rest.toLocaleString()} more` : "")].filter(Boolean).join(" · ");
    return el("div", { class: cls },
      el("p", { class: "rep-head" }, head),
      rep.changed.length ? el("ul", { class: "rep-changed" }, rep.changed.map((c) => el("li", {}, c)))
        : r.outcome !== "went_away" && el("p", {}, "Nothing on your PC was changed."),
      extra && el("p", { class: "caption" }, extra),
      rep.steps.length > 0 && el("details", {}, el("summary", {}, `Steps (${rep.steps.length})`),
        el("ol", { class: "rep-steps" }, rep.steps.map((s) => el("li", { class: s.status }, s.summary,
          STEP_WORD[s.status] ? ` (${STEP_WORD[s.status]})` : "", s.admin ? " · as administrator" : "")))),
      buttons);
  }

  // ---- think mode: "Fix for me", short ---------------------------------------------------------------
  // What matters is on top: what the fix does and Allow, or the AI's answer. Everything behind it
  // (what was sent, each check and what it saw, the AI's reasoning) is one click away, never hidden.
  // done: { onFixed(result), onCancel() }
  function thinkPanel(card, opts, done) {
    const via = opts.via || "fix_this";
    const main = el("div", { class: "think-main", "aria-live": "polite" });
    const status = el("p", { class: "think-status caption hidden", role: "status" });
    const log = el("ol", { class: "think-log" });
    const trailSum = el("summary", {}, "How it got here");
    const trail = el("details", { class: "think-trail hidden" }, trailSum, log);
    const bar = el("div", { class: "actions think-bar" });
    const box = el("section", { class: "think", "aria-label": "Fix for me" },
      el("p", { class: "think-head" }, el("b", {}, "Think mode"),
        el("span", { class: "caption" }, " · nothing changes until you press Allow")),
      main, status, trail, bar);
    let sid = null, seen = 0, ai = opts.aiLabel || "The AI", polling = false;
    let checks = 0, usage = "", touched = false, askBlock = null, sentText = "", answered = false;
    trailSum.addEventListener("click", () => { touched = true; });

    const block = (cls, ...kids) => {
      const d = el("div", { class: "t-block " + cls }, kids);
      main.append(d);
      return d;
    };
    const setStatus = (text) => {
      status.classList.toggle("hidden", !text);
      status.replaceChildren(...(text ? [el("span", { class: "spin", "aria-hidden": "true" }), text] : []));
    };
    const updateTrail = () => {
      trailSum.textContent = "How it got here" + (checks ? ` · ${checks} check${checks === 1 ? "" : "s"}` : "") + usage;
    };
    // One line in the trail. With `more`, the line opens to show the rest.
    const step = (tag, text, more) => {
      trail.classList.remove("hidden");
      const head = [el("span", { class: "t-tag" }, tag), el("span", { class: "t-line" }, text)];
      log.append(el("li", {}, more ? el("details", {}, el("summary", {}, head), more) : el("div", { class: "t-row" }, head)));
      updateTrail();
    };
    const buttons = (...bs) => bar.replaceChildren(...bs.filter(Boolean));
    const cancelBtn = (label) => el("button", { type: "button", onclick: () => done.onCancel() }, label);
    const stopBtn = () => el("button", { type: "button", onclick: async () => {
      if (sid) render(await api(`/api/think/${sid}/stop`, {}));
    } }, "Stop");
    const fail = (text) => { setStatus(""); block("warn", "Couldn't. " + text); buttons(cancelBtn("Close")); };

    function planView(e) {
      const more = [...new Set(e.steps.flatMap((s) => [...(s.detail || []), ...s.notes]))];
      return [
        el("p", {}, e.by_ai ? `${ai} suggests: ` : "", el("b", {}, e.title),
          e.admin ? el("span", { class: "chip-admin" }, "needs administrator") : null),
        el("p", {}, e.explain),
        e.by_ai && e.reason && !answered ? el("p", { class: "caption" }, "Why: " + plain(e.reason)) : null,
        e.warnings.map((w) => el("p", { class: "warn" }, w)),
        el("p", { class: "caption" }, e.undo),
        e.steps.length ? el("details", {}, el("summary", {}, `Exactly what runs (${e.steps.length} step${e.steps.length === 1 ? "" : "s"})`),
          el("ol", { class: "t-steps" }, e.steps.map((s) => el("li", {}, s.text, s.commands.map((c) => el("code", {}, c))))),
          more.length ? el("ul", { class: "t-notes" }, more.map((m) => el("li", {}, m))) : null)
          : el("p", { class: "warn" }, "I couldn't build the steps just now (the program may have closed)."),
      ];
    }

    async function allow(cardId) {
      buttons();
      setStatus("Working… Windows may ask for permission.");
      try {
        const r = await api(`/api/cards/${cardId}/approve`, { via });
        if (r.outcome === "refused") return fail(r.message);
        setStatus("");
        box.classList.add("done");
        main.replaceChildren(fixReport(r, { onOpenRecord: opts.onOpenRecord, onDismiss: opts.onDismiss }));
        done.onFixed(r);
      } catch (e) {
        fail(e.message);
      }
    }

    function add(e) {
      switch (e.kind) {
        case "consent":
          ai = e.provider;
          sentText = e.sent;
          askBlock = block("ask",
            el("p", {}, `There's no built-in fix for this. I can ask ${e.provider} to look into it. It only reads this PC and can't change anything.`),
            el("details", {}, el("summary", {}, "See exactly what I'll send"),
              el("pre", { class: "t-pre" }, e.sent),
              el("p", { class: "caption" }, "It may also check: " + e.checks.join(", ") + "."),
              el("details", {}, el("summary", {}, "The rules it must follow"), el("pre", { class: "t-pre" }, e.rules)),
              e.limit && el("p", { class: "caption" }, `AI questions today: ${e.limit.day.calls} of ${e.limit.limit || "no limit"}.`)));
          return;
        case "sent":
          if (askBlock) askBlock.remove();
          trail.open = true; // live while it works; it folds away when the answer is in
          return step("Sent", `The error summary went to ${ai}`, el("pre", { class: "t-pre" }, sentText));
        case "round": return setStatus(`Asking ${ai}… step ${e.n} of at most ${e.max}`);
        case "wait": setStatus(e.text); return step("Wait", e.text);
        case "say": { const t = plain(e.text); return step("AI", t, t.length > 70 ? el("p", { class: "t-more" }, t) : null); }
        case "reasoning": { const t = plain(e.text); return step("Thinking", t.split("\n")[0], el("p", { class: "t-more" }, t)); }
        case "tool": checks += 1; return step(e.ok ? "Check" : "Refused", e.text, el("pre", { class: "t-pre" }, e.seen));
        case "usage":
          usage = ` · ${e.tokens.toLocaleString()} tokens`;
          return updateTrail();
        case "answer": answered = true; return block("answer", el("b", {}, `${e.provider}: `), brief(plain(e.text), 280));
        case "cut_short": return block("note", brief(e.text, 160));
        case "held_back": return block("warn", e.text);
        case "manual": return block("steps", el("p", {}, el("b", {}, e.suggested ? `${ai}'s steps (I haven't checked them)` : "What you can do")),
          el("ol", {}, e.steps.map((s) => el("li", {}, s))));
        case "plan": return block("plan", planView(e));
        case "error": return block("warn", brief(e.text, 200));
        case "no_ai": case "stopped": return block("note", e.text);
        default: return null;
      }
    }

    function render(v) {
      sid = v.id;
      for (const e of v.events) if (e.seq >= seen) { add(e); seen = e.seq + 1; }
      if (v.status !== "thinking") {
        setStatus("");
        if (!touched) trail.open = false;
      }
      const plan = [...v.events].reverse().find((e) => e.kind === "plan");
      switch (v.status) {
        case "plan": case "proposal":
          buttons(el("button", { type: "button", class: "primary", onclick: () => allow(plan.card_id) }, "Allow"),
                  cancelBtn(opts.cancelLabel || "Don't"));
          break;
        case "consent":
          buttons(el("button", { type: "button", class: "primary", onclick: async () => {
            buttons(stopBtn());
            try { render(await api(`/api/think/${sid}/send`, {})); } catch (e) { fail(e.message); }
          } }, `Ask ${ai}`), cancelBtn(opts.cancelLabel || "Cancel"));
          break;
        case "thinking":
          buttons(stopBtn());
          if (!polling) poll();
          break;
        case "manual":
          buttons(opts.onConnectAI && v.events.some((e) => e.kind === "no_ai")
            ? el("button", { type: "button", class: "primary", onclick: opts.onConnectAI }, "Connect an AI") : null,
            cancelBtn(opts.cancelLabel || "Close"));
          break;
        default:
          buttons(cancelBtn("Close"));
      }
    }

    async function poll() {
      polling = true;
      try {
        while (box.isConnected) {
          await new Promise((r) => setTimeout(r, 800));
          const v = await api(`/api/think/${sid}?after=${seen}`);
          render(v);
          if (v.status !== "thinking") break;
        }
      } catch (e) {
        fail(e.message);
      }
      polling = false;
    }

    api("/api/think/start", { card_id: card.id }).then(render).catch((e) => fail(e.message));
    return box;
  }

  // One problem. opts: { via, aiLabel, compact, showNever, onChange, onDismiss, onOpenRecord, onConnectAI }
  // compact: the caller shows the title and status itself (the one-line rows on Home).
  function cardView(card, opts = {}) {
    let node = null, panel = null;
    const result = el("div", { class: "result", role: "status", "aria-live": "polite" });
    const steps = manualSteps(card, result, opts);
    const actions = el("div", { class: "actions" });
    const buttons = [];
    const busy = (on) => buttons.forEach((b) => (b.disabled = on));

    async function run(fn, workingText) {
      busy(true);
      result.className = "result working";
      result.textContent = workingText || "Working…";
      try {
        await fn();
      } catch (e) {
        result.className = "result couldnt";
        result.textContent = "Couldn't. " + e.message;
        busy(false);
      }
    }

    // Every problem gets "Fix for me": it opens think mode inside the card.
    buttons.push(el("button", {
      class: "primary", type: "button",
      onclick: () => {
        if (panel) return;
        actions.classList.add("hidden");
        node.classList.add("thinking"); // the panel says what the fix does; don't say it twice
        result.replaceChildren();
        result.className = "result";
        panel = thinkPanel(card, opts, {
          onFixed: (r) => {
            setOutcome(node, r.outcome === "done" || r.outcome === "went_away");
            opts.onChange && opts.onChange(card, r, node);
          },
          onCancel: () => {
            panel.remove();
            panel = null;
            node.classList.remove("thinking");
            actions.classList.remove("hidden");
          },
        });
        result.before(panel);
      },
    }, "Fix for me"));
    const stepsLabel = card.fix ? "I'll do it myself" : "Show me how";
    const stepsBtn = el("button", {
      type: "button", "aria-expanded": "false",
      onclick: () => {
        const open = steps.classList.toggle("hidden") === false;
        stepsBtn.textContent = open ? "Hide steps" : stepsLabel;
        stepsBtn.setAttribute("aria-expanded", String(open));
      },
    }, stepsLabel);
    buttons.push(stepsBtn);
    buttons.push(el("button", {
      type: "button",
      onclick: () => run(async () => {
        await api(`/api/cards/${card.id}/not_now`, {});
        result.className = "result";
        result.textContent = "Okay. I'll ask again at the end of the day.";
        busy(false);
        opts.onChange && opts.onChange(card, { outcome: "not_now" });
      }),
    }, card.fix ? "Not now" : "Remind me later"));
    if (opts.showNever !== false) {
      buttons.push(el("button", {
        class: "link", type: "button",
        onclick: () => run(async () => {
          await api(`/api/cards/${card.id}/never`, {});
          result.className = "result";
          result.textContent = "I won't bring this up again. You can change that in Settings.";
          opts.onChange && opts.onChange(card, { outcome: "never" });
        }),
      }, "Never for this"));
    }
    actions.append(...buttons);

    const fix = card.fix;
    const major = card.severity === "major";
    node = el("article", { class: "card", "aria-label": card.title },
      !opts.compact && el("div", { class: "card-head" }, el("h3", {}, card.title),
        el("span", { class: "chip " + (major ? "bad" : "warn") }, major ? "Needs you now" : "Can wait")),
      // one sentence: what the fix is meant to do
      fix ? el("p", { class: "what" }, el("b", {}, fix.title + ". "), fix.explain)
        : el("p", { class: "what" }, card.recurring ? "This keeps coming back, so a quick fix isn't enough."
          : "There's no built-in fix for this. I can look into it, or show you what to do."),
      fix && fix.warnings.map((w) => el("p", { class: "warn" }, w)),
      card.explanation && el("div", { class: "ai-says" }, el("b", {}, (opts.aiLabel || "AI") + ": "), brief(plain(card.explanation))),
      card.ai_note && !card.explanation && el("p", { class: "caption" }, brief(card.ai_note, 140)),
      card.auto_note && el("p", { class: "caption" }, "Autopilot didn't run this: " + card.auto_note),
      steps,
      actions,
      result);
    return node;
  }

  function manualSteps(card, result, opts) {
    const box = el("div", { class: "manual hidden" });
    if (!card.manual_steps.length) {
      box.append(el("p", { class: "muted" }, "No steps yet. Ask me about it in Ask."));
      return box;
    }
    if (card.manual_steps.some((s) => s.suggested)) {
      box.append(el("p", { class: "suggested" }, `${opts.aiLabel || "The AI"}'s suggestion. I haven't checked these.`));
    }
    box.append(el("ol", {}, card.manual_steps.map((s) => el("li", {}, s.text, " ",
      s.open && el("button", {
        class: "link", type: "button",
        onclick: () => api("/api/open", { target: s.open, card_id: card.id }).catch((e) => {
          result.className = "result couldnt";
          result.textContent = "Couldn't. " + e.message;
        }),
      }, "Open")))));
    const check = async (sorted) => {
      const node = box.closest(".card");
      result.className = "result working";
      result.textContent = sorted ? "Closing it…" : "Checking again…";
      try {
        const r = await api(`/api/cards/${card.id}/check_again`, sorted ? { sorted: true } : {});
        if (r.verification === "cant_check") {
          result.className = "result";
          result.replaceChildren(el("span", {}, r.message + " "),
            el("button", { type: "button", onclick: () => check(true) }, "Yes, it's sorted"));
          return;
        }
        const fixed = r.verification === "fixed";
        result.className = "result " + (fixed ? "done" : "couldnt");
        result.textContent = r.message || (fixed ? "It's fixed." : "Still happening.");
        if (fixed) {
          setOutcome(node, true);
          const acts = node && node.querySelector(".actions");
          if (acts) acts.classList.add("hidden");
          if (opts.onDismiss) result.append(" ", el("button", { type: "button", onclick: opts.onDismiss }, "Got it"));
        }
        opts.onChange && opts.onChange(card, r, node);
      } catch (e) {
        result.className = "result couldnt";
        result.textContent = "Couldn't. " + e.message;
      }
    };
    box.append(el("button", { type: "button", onclick: () => check(false) }, "Check again"));
    return box;
  }

  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const today = new Date();
    const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === today.toDateString() ? `Today, ${t}` : `${d.toLocaleDateString()}, ${t}`;
  }

  return { token, params: new URLSearchParams(rest), api, el, cardView, fixReport, thinkPanel, when };
})();
