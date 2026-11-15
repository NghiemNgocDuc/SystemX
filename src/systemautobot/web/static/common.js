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
                 "/api/models": 60e3, "/check_again": 2 * 60e3, "/api/check_now": 2 * 60e3, "/api/doctor": 2 * 60e3 };
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

  // What a fix did, right after the click: steps, what changed, and the re-check.
  const STEP_MARK = { done: "✓", couldnt: "!", skipped: "–" };
  function fixReport(r, opts = {}) {
    const rep = r.report;
    const ok = r.outcome === "done" || r.outcome === "went_away";
    const head = el("p", { class: "rep-head" }, (ok ? "Done. " : "Couldn't. ") + (r.message || ""));
    if (!rep) return el("div", { class: "report " + (ok ? "done" : "couldnt") }, head);
    const files = rep.files || { examples: [], more: 0 };
    return el("div", { class: "report " + (ok ? "done" : "couldnt") },
      head,
      rep.steps.length && el("h4", {}, "What I did"),
      rep.steps.length && el("ol", { class: "rep-steps" }, rep.steps.map((s) => el("li", { class: s.status },
        el("span", { class: "mark", "aria-label": s.status }, STEP_MARK[s.status] || "•"),
        el("span", {}, s.summary, s.admin ? el("span", { class: "caption" }, " (with administrator permission)") : null)))),
      el("h4", {}, "What changed"),
      rep.changed.length ? el("ul", { class: "rep-changed" }, rep.changed.map((c) => el("li", {}, c)))
        : el("p", { class: "small" }, ok ? "Nothing on your PC needed changing." : "Nothing was changed."),
      files.examples.length > 0 && el("p", { class: "caption" }, "Files: " + files.examples.join(", ")
        + (files.more ? ` and ${files.more.toLocaleString()} more` : "")),
      rep.freed && el("p", { class: "small" }, el("b", {}, `Freed ${rep.freed}.`)),
      rep.verdict && el("p", { class: "rep-verdict " + (rep.verification || "") }, rep.verdict),
      el("div", { class: "actions" },
        opts.onOpenRecord && el("button", { type: "button", onclick: () => opts.onOpenRecord(rep.fix_id) }, "Full record in Activity"),
        opts.onDismiss && el("button", { type: "button", class: "link", onclick: opts.onDismiss }, "Got it")));
  }

  // The AI sometimes answers in Markdown; show it as plain words.
  const plain = (s) => String(s || "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/^#+\s*/gm, "");

  // ---- think mode: "Fix for me", every step in the open ------------------------------------------
  // Built-in repairs show their exact plan and wait for Allow. Anything else goes to the AI only after
  // the person sees what will be sent; then every check it runs and everything it says shows up live.
  const TAG = { intro: "Plan", plan: "Plan", consent: "Ask", sent: "Sent", round: "AI", reasoning: "Thinking",
                say: "AI", tool: "Check", answer: "Answer", manual: "Steps", held_back: "Held back",
                no_ai: "No AI", error: "Problem", stopped: "Stopped", usage: "Used", result: "Done" };

  function thinkPanel(card, opts, onDone) {
    const via = opts.via || "fix_this";
    const box = el("section", { class: "think", "aria-label": "Fix for me, step by step" });
    const log = el("ol", { class: "think-log", "aria-live": "polite" });
    const bar = el("div", { class: "actions think-bar" });
    box.append(el("h4", {}, "Think mode"), el("p", { class: "caption" },
      "Everything I and the AI do is shown here. Nothing on your PC changes until you press Allow."), log, bar);
    let sid = null, seen = 0, ai = opts.aiLabel || "The AI", polling = false;

    const row = (kind, ...kids) => {
      const li = el("li", { class: "t-" + kind }, el("span", { class: "t-tag" }, TAG[kind] || kind), el("div", { class: "t-body" }, kids));
      log.querySelectorAll(".spin").forEach((s) => s.remove());
      log.append(li);
      li.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return li;
    };
    const buttons = (...bs) => bar.replaceChildren(...bs.filter(Boolean));
    const stopBtn = () => el("button", { type: "button", class: "link", onclick: async () => {
      if (sid) render(await api(`/api/think/${sid}/stop`, {}));
    } }, "Stop");
    const close = () => el("button", { type: "button", onclick: () => onDone(null) }, "Close");

    function planView(e) {
      const steps = el("ol", { class: "t-steps" }, e.steps.map((s) => el("li", {},
        el("div", {}, s.text, s.admin ? el("span", { class: "chip-admin" }, "admin") : null),
        s.commands.map((c) => el("code", {}, c)),
        s.notes.map((n) => el("div", { class: "caption" }, n)))));
      return [
        e.by_ai ? el("p", {}, el("b", {}, `${ai} suggests: `), e.title, e.reason ? ` — “${plain(e.reason)}”` : "")
          : el("p", {}, el("b", {}, e.title)),
        el("p", {}, e.explain),
        el("p", { class: "caption" }, "Exactly what will run, in order:"),
        e.steps.length ? steps : el("p", { class: "warn" }, "I couldn't build the steps just now (the program may have closed)."),
        e.warnings.map((w) => el("p", { class: "warn" }, w)),
        el("p", { class: "caption" }, e.undo),
      ];
    }

    async function allow(cardId) {
      buttons(el("span", { class: "caption" }, "Working… Windows may ask for permission."));
      try {
        const r = await api(`/api/cards/${cardId}/approve`, { via });
        if (r.outcome === "refused") {
          row("error", "Couldn't. " + r.message);
          return buttons(close());
        }
        row("result", fixReport(r, { onOpenRecord: opts.onOpenRecord }));
        buttons(el("button", { type: "button", class: "primary", onclick: () => onDone(r) }, "Got it"));
      } catch (e) {
        row("error", "Couldn't. " + e.message);
        buttons(close());
      }
    }

    function add(e) {
      switch (e.kind) {
        case "intro": case "sent": case "stopped": return row(e.kind, e.text);
        case "plan": return row("plan", planView(e));
        case "consent":
          ai = e.provider;
          return row("consent",
            el("p", {}, el("b", {}, `Before I send anything: this is exactly what goes to ${e.provider}`), ` (${e.model}).`),
            el("pre", { class: "t-pre" }, e.sent),
            el("details", {}, el("summary", {}, "The rules it must follow"), el("pre", { class: "t-pre" }, e.rules)),
            el("p", { class: "small" }, "It may also read: " + e.checks.join(", ") + ". Each one will show up here."),
            e.limit && el("p", { class: "caption" }, `AI questions today: ${e.limit.day.calls} of ${e.limit.limit || "no limit"}.`));
        case "round": return row("round", `Asking ${ai}… (step ${e.n} of at most ${e.max})`, el("span", { class: "spin", "aria-hidden": "true" }));
        case "reasoning": return row("reasoning", el("div", { class: "bubble" }, plain(e.text)));
        case "say": return row("say", el("div", { class: "bubble" }, el("b", {}, `${ai}: `), plain(e.text)));
        case "tool": return row("tool", el("div", {}, e.ok ? "" : "! ", e.text),
          el("details", {}, el("summary", {}, "What it saw"), el("pre", { class: "t-pre" }, e.seen)));
        case "answer": return row("answer", el("div", { class: "bubble" }, el("b", {}, `${e.provider}: `), plain(e.text)));
        case "held_back": return row("held_back", el("p", { class: "warn" }, e.text));
        case "manual": return row("manual", el("p", {}, e.suggested ? `${ai}'s steps (I haven't checked them):` : "Steps you can follow:"),
          el("ol", {}, e.steps.map((s) => el("li", {}, s))));
        case "no_ai": return row("no_ai", e.text);
        case "error": return row("error", e.text);
        case "usage": return row("usage", el("span", { class: "caption" },
          `${e.provider} · ${e.model} · ${e.rounds} step${e.rounds === 1 ? "" : "s"} · ${e.tokens.toLocaleString()} tokens${e.replay ? " · replay" : ""}`));
        default: return null;
      }
    }

    function render(v) {
      sid = v.id;
      for (const e of v.events) if (e.seq >= seen) { add(e); seen = e.seq + 1; }
      const plan = [...v.events].reverse().find((e) => e.kind === "plan");
      switch (v.status) {
        case "plan": case "proposal":
          buttons(el("button", { type: "button", class: "primary", onclick: () => allow(plan.card_id) }, "Allow"),
                  el("button", { type: "button", onclick: () => onDone(null) }, "Don't"));
          break;
        case "consent":
          buttons(el("button", { type: "button", class: "primary", onclick: async () => {
            buttons(stopBtn());
            render(await api(`/api/think/${sid}/send`, {}));
          } }, `Send to ${ai}`), el("button", { type: "button", onclick: () => onDone(null) }, "Cancel"));
          break;
        case "thinking":
          buttons(stopBtn());
          if (!polling) poll();
          break;
        case "manual":
          buttons(opts.onConnectAI && v.events.some((e) => e.kind === "no_ai")
            ? el("button", { type: "button", class: "primary", onclick: opts.onConnectAI }, "Connect an AI") : null, close());
          break;
        default:
          buttons(close());
      }
    }

    async function poll() {
      polling = true;
      try {
        while (box.isConnected || !sid) {
          await new Promise((r) => setTimeout(r, 800));
          const v = await api(`/api/think/${sid}?after=${seen}`);
          render(v);
          if (v.status !== "thinking") break;
        }
      } catch (e) {
        row("error", "Couldn't. " + e.message);
        buttons(close());
      }
      polling = false;
    }

    api("/api/think/start", { card_id: card.id }).then(render).catch((e) => {
      row("error", "Couldn't. " + e.message);
      buttons(close());
    });
    return box;
  }

  const FIX_LABEL = { fix_now: "Fix for me", fix_it_for_me: "Fix for me", fix_this: "Fix for me" };

  // One card. opts: { via, aiLabel, onChange, compact, showNever }
  function cardView(card, opts = {}) {
    const via = opts.via || "fix_this";
    let node = null;
    const result = el("div", { class: "result", role: "status", "aria-live": "polite" });
    const steps = manualSteps(card, result, opts);
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
    let panel = null;
    buttons.push(el("button", {
      class: "primary",
      onclick: () => {
        if (panel) return panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
        node.querySelector(".actions").classList.add("hidden");
        panel = thinkPanel(card, opts, (r) => {
          if (r && r.outcome && r.outcome !== "refused") {
            const worked = r.outcome === "done" || r.outcome === "went_away";
            node.classList.add("finished");
            node.querySelector(".chip").textContent = worked ? "Fixed" : "Didn't work";
            node.querySelector(".sev-icon").textContent = worked ? "✓" : "!";
            panel.querySelector(".think-bar").replaceChildren(opts.onDismiss
              ? el("button", { type: "button", class: "link", onclick: opts.onDismiss }, "Got it") : "");
            opts.onChange && opts.onChange(card, r, node);
            return;
          }
          panel.remove();
          panel = null;
          node.querySelector(".actions").classList.remove("hidden");
        });
        result.before(panel);
      },
    }, FIX_LABEL[via]));
    const stepsLabel = card.fix ? "I'll do it myself" : "Show me how";
    const stepsBtn = el("button", {
      type: "button", "aria-expanded": "false",
      onclick: () => {
        const open = steps.classList.toggle("hidden") === false;
        stepsBtn.textContent = open ? "Hide steps" : stepsLabel;
        stepsBtn.setAttribute("aria-expanded", String(open));
        if (open) steps.scrollIntoView({ block: "nearest", behavior: "smooth" });
      },
    }, stepsLabel);
    buttons.push(stepsBtn);
    buttons.push(el("button", {
      onclick: () => run(async () => {
        await api(`/api/cards/${card.id}/not_now`, {});
        result.className = "result";
        result.textContent = "Okay. I'll ask again at the end of the day. You can still fix it now.";
        busy(false);
        opts.onChange && opts.onChange(card, { outcome: "not_now" });
      }),
    }, card.fix ? "Not now" : "Remind me later"));
    if (opts.showNever !== false) {
      buttons.push(el("button", {
        class: "link",
        onclick: () => run(async () => {
          await api(`/api/cards/${card.id}/never`, {});
          result.className = "result";
          result.textContent = "I won't bring this up again. You can change that in Settings.";
          opts.onChange && opts.onChange(card, { outcome: "never" });
        }),
      }, "Never for this"));
    }

    const fix = card.fix;
    node = el("article", { class: "card" + (card.severity === "major" ? " major" : ""), "aria-label": card.title },
      el("span", { class: "sev-icon", "aria-hidden": "true" }, card.severity === "major" ? "!" : "i"),
      el("div", { class: "chip" }, card.severity === "major" ? "Needs you now" : "When you have a minute",
        card.replay ? " · Replay" : ""),
      el("h3", {}, card.title),
      fix && el("p", { class: "what" }, fix.explain),
      fix && fix.warnings.map((w) => el("p", { class: "warn" }, w)),
      fix && !fix.undo_possible && el("p", { class: "muted small" }, fix.no_undo_text),
      !fix && el("p", { class: "what" }, card.recurring
        ? "This keeps coming back, so a quick fix isn't enough. Here's how to get to the cause."
        : "I can't fix this one myself, but I can show you how."),
      card.explanation && el("div", { class: "ai-says" },
        el("b", {}, (opts.aiLabel || "AI") + " says: "), plain(card.explanation)),
      card.ai_note && el("p", { class: "muted small" }, card.ai_note),
      el("details", {},
        el("summary", {}, "More detail"),
        el("p", { class: "muted small" }, card.proposed_by === "grok"
          ? `${opts.aiLabel || "Grok"} chose this repair from my fixed list. I build the steps myself.`
          : "This is one of my built-in repairs.")),
      steps,
      el("div", { class: "actions" }, buttons),
      result);
    return node;
  }

  function manualSteps(card, result, opts) {
    const box = el("div", { class: "manual hidden" });
    if (!card.manual_steps.length) {
      box.append(el("p", { class: "muted" }, "No steps yet. Ask me about it in the chat."));
      return box;
    }
    if (card.manual_steps.some((s) => s.suggested)) {
      box.append(el("p", { class: "suggested" }, `${opts.aiLabel || "Grok"}'s suggestion. I haven't checked these.`));
    }
    box.append(el("ol", {}, card.manual_steps.map((s) => el("li", {}, s.text, " ",
      s.open && el("button", {
        class: "link",
        onclick: () => api("/api/open", { target: s.open, card_id: card.id }).catch((e) => alert(e.message)),
      }, "Open")))));
    const check = async (sorted) => {
      const nodeOf = () => box.closest(".card");
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
          const n = nodeOf();
          if (n) {
            n.classList.add("finished");
            n.querySelector(".chip").textContent = "Fixed";
            n.querySelector(".sev-icon").textContent = "✓";
            const acts = n.querySelector(".actions");
            if (acts) acts.classList.add("hidden");
            if (opts.onDismiss) result.append(" ", el("button", { type: "button", class: "link", onclick: opts.onDismiss }, "Got it"));
          }
        }
        opts.onChange && opts.onChange(card, r, nodeOf());
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
