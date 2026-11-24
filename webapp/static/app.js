// Small, dependency-free client. Every POST carries the per-launch app token.
(function () {
  "use strict";
  const token = document.querySelector('meta[name="app-token"]').content;

  async function post(url, body, isForm) {
    const opts = { method: "POST", headers: { "X-App-Token": token } };
    if (isForm) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body || {}); }
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) throw new Error(data.error || data.detail || ("Request failed (" + res.status + ")"));
    return data;
  }
  function showError(form, msg) {
    const el = form.querySelector(".error");
    if (el) { el.textContent = msg; el.hidden = !msg; }
  }

  // Command forms -> start a run, open its page.
  document.querySelectorAll("form.runform").forEach(function (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const values = {};
      form.querySelectorAll("input, textarea, select").forEach(function (el) {
        if (!el.name) return;
        values[el.name] = el.type === "checkbox" ? el.checked : el.value;
      });
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; showError(form, "");
      try {
        const data = await post("/api/run", { command: form.dataset.command, values: values });
        location.href = "/runs/" + data.run_id;
      } catch (e) { showError(form, e.message); btn.disabled = false; }
    });
  });

  // Document upload / delete.
  const upload = document.getElementById("upload");
  if (upload) {
    upload.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      try { await post("/api/documents", new FormData(upload), true); location.reload(); }
      catch (e) { showError(upload, e.message); }
    });
  }
  document.querySelectorAll("[data-delete]").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      if (!confirm("Delete this document from your documents folder?")) return;
      try { await post("/api/documents/delete", { path: btn.dataset.delete }); location.reload(); }
      catch (e) { alert(e.message); }
    });
  });

  // Run page: poll status, render turns and activity, reply / stop / undo.
  const runEl = document.getElementById("run");
  if (!runEl) return;
  const runId = runEl.dataset.run;
  const turnsEl = document.getElementById("turns");
  const eventsEl = document.getElementById("events");
  const statusEl = document.getElementById("status");
  const working = document.getElementById("working");
  const reply = document.getElementById("reply");
  const replyText = document.getElementById("reply-text");
  const replyLabel = document.getElementById("reply-label");
  const cancelBtn = document.getElementById("cancel");
  const undoBtn = document.getElementById("undo");
  const LABELS = { queued: "waiting to start", running: "working", waiting: "waiting for your reply",
                   finished: "finished", failed: "stopped with a problem", cancelled: "stopped", interrupted: "interrupted" };
  let seen = 0, lastTurns = "", timer = null;

  function li(cls, text) {
    const el = document.createElement("li"); el.className = cls; el.textContent = text; return el;
  }
  function renderTurns(turns) {
    const key = JSON.stringify(turns.map(function (t) { return [t.input.length, t.finished, t.output_html.length]; }));
    if (key === lastTurns) return;
    lastTurns = key;
    turnsEl.innerHTML = "";
    turns.forEach(function (t, i) {
      if (i > 0) {
        const you = document.createElement("div"); you.className = "card you";
        const p = document.createElement("p"); p.textContent = t.input; you.appendChild(p);
        turnsEl.appendChild(you);
      }
      if (t.finished) {
        const card = document.createElement("div"); card.className = "card prose";
        card.innerHTML = t.output_html || "<p class='muted'>(no message)</p>";  // server-rendered, HTML escaped
        if (t.denied && t.denied.length) {
          const d = document.createElement("p"); d.className = "warn small";
          d.textContent = "Not allowed from the app (use the Claude Code terminal if needed): " + t.denied.join("; ");
          card.appendChild(d);
        }
        turnsEl.appendChild(card);
      }
    });
  }
  async function poll() {
    let data;
    try { data = await (await fetch("/api/runs/" + runId + "?since=" + seen)).json(); }
    catch (e) { timer = setTimeout(poll, 3000); return; }
    data.events.forEach(function (e) {
      if (e.kind === "text") return;
      eventsEl.appendChild(li("ev " + e.kind, e.text));
    });
    seen = data.event_count;
    renderTurns(data.turns);
    statusEl.textContent = LABELS[data.status] || data.status;
    statusEl.className = "pill " + data.status;
    document.getElementById("cost").textContent = (data.cost_usd || 0).toFixed(2);
    const active = data.status === "queued" || data.status === "running";
    working.hidden = !active;
    cancelBtn.hidden = !active;
    reply.hidden = active || data.status === "cancelled" || data.turns.length === 0;
    replyLabel.textContent = data.status === "waiting" ? "Claude is waiting for your answer" : "Reply or ask for changes";
    if (undoBtn) undoBtn.disabled = active || data.undone;
    if (active) timer = setTimeout(poll, 1500);
  }
  reply.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    const text = replyText.value.trim();
    if (!text) return;
    try { await post("/api/runs/" + runId + "/reply", { text: text }); replyText.value = ""; showError(reply, ""); clearTimeout(timer); poll(); }
    catch (e) { showError(reply, e.message); }
  });
  reply.querySelectorAll("[data-quick]").forEach(function (b) {
    b.addEventListener("click", function () { replyText.value = b.dataset.quick; replyText.focus(); });
  });
  cancelBtn.addEventListener("click", async function () {
    if (!confirm("Stop this task? Changes it already made stay until you undo them.")) return;
    await post("/api/runs/" + runId + "/cancel"); setTimeout(poll, 800);
  });
  if (undoBtn) undoBtn.addEventListener("click", async function () {
    if (!confirm("Restore your files to how they were before this task?")) return;
    try { const r = await post("/api/runs/" + runId + "/undo"); alert("Done: " + r.restored + " files restored, " + r.removed + " removed."); location.reload(); }
    catch (e) { alert(e.message); }
  });
  poll();
})();
