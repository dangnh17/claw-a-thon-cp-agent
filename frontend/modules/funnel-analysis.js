/**
 * Funnel Analysis — Interactive, user-configurable conversion funnel.
 *
 * Flow:
 *   1. On load, fetch GET /preset to populate the step editor with defaults.
 *   2. User edits step names / event IDs / prefixes; add or remove steps.
 *   3. "Run Funnel" sends POST /conversion, renders bars, then AUTO-triggers AI Insight.
 *   4. AI Insight renders as formatted Markdown (headings, bold, lists).
 *   5. Event Browser lets users search available event_ids from the parquet.
 */

const API = "/api/funnel-analysis";
const STEP_COLORS = ["#C9B942", "#5B8A9A", "#2E8B57", "#E05555", "#00BCD4", "#9C5FB5", "#E08A2E"];

export default {
  id: "funnel-analysis",
  label: "Funnel Analysis",
  icon: "📊",

  _steps: [],
  _lastResult: null,

  render(container) {
    this._steps = [];
    this._lastResult = null;

    container.innerHTML = `
      <h2>${this.icon} ${this.label}</h2>

      <!-- ── Step editor ── -->
      <section id="fa-editor-section">
        <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.75rem">
          <strong>Funnel Steps</strong>
          <button id="fa-load-preset" class="btn-sm">↺ Load Preset</button>
          <button id="fa-add-step"    class="btn-sm btn-outline">+ Add Step</button>
          <button id="fa-run"         style="margin-left:auto">▶ Run Funnel</button>
        </div>
        <div id="fa-steps-list"></div>
        <div id="fa-run-status" class="status" style="margin-top:.4rem"></div>
      </section>

      <!-- ── Event browser ── -->
      <section style="margin-top:1rem">
        <details id="fa-event-browser">
          <summary style="cursor:pointer;font-weight:600;user-select:none">
            🔍 Event Browser <small style="font-weight:400;color:#888">(find event IDs to add)</small>
          </summary>
          <div style="margin-top:.5rem;display:flex;gap:.5rem;align-items:center">
            <input id="fa-event-search" type="text" placeholder="Filter by prefix e.g. 01.3160."
                   style="flex:1;max-width:280px" />
            <button id="fa-event-search-btn" class="btn-sm">Search</button>
          </div>
          <div id="fa-event-list" style="margin-top:.5rem;max-height:220px;overflow-y:auto;
               font-size:.8rem;border:1px solid #ddd;border-radius:4px;padding:.4rem"></div>
        </details>
      </section>

      <!-- ── Results ── -->
      <section id="fa-result-section" style="display:none;margin-top:1.25rem">
        <div style="display:flex;align-items:center;gap:1rem;margin-bottom:.6rem;flex-wrap:wrap">
          <strong>Results</strong>
          <span id="fa-summary-line" style="font-size:.88rem;color:#888"></span>
        </div>
        <div id="fa-funnel"></div>
      </section>

      <!-- ── AI Insight (auto-runs after funnel) ── -->
      <section id="fa-insight-section" style="display:none;margin-top:1.5rem">
        <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.5rem">
          <strong>🤖 AI Insight</strong>
          <span id="fa-insight-status" class="status"></span>
          <button id="fa-insight-btn" class="btn-sm btn-outline"
                  style="margin-left:auto;display:none">↺ Regenerate</button>
        </div>
        <div id="fa-insight-body" class="fa-md-body"></div>
      </section>
    `;

    this._injectStyles();
    this._bindEvents(container);
    this._loadPreset(container);
  },

  // ── Event binding ──────────────────────────────────────────────────────────

  _bindEvents(container) {
    container.querySelector("#fa-load-preset").onclick      = () => this._loadPreset(container);
    container.querySelector("#fa-add-step").onclick         = () => this._addStep(container);
    container.querySelector("#fa-run").onclick              = () => this._runFunnel(container);
    container.querySelector("#fa-event-search-btn").onclick = () => this._searchEvents(container);
    container.querySelector("#fa-insight-btn").onclick      = () => this._getInsight(container);

    container.querySelector("#fa-event-search").addEventListener("keydown", e => {
      if (e.key === "Enter") this._searchEvents(container);
    });
  },

  // ── Preset loader ──────────────────────────────────────────────────────────

  async _loadPreset(container) {
    const status = container.querySelector("#fa-run-status");
    status.textContent = "Loading preset…";
    try {
      const resp = await fetch(`${API}/preset`);
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      this._steps = data.steps.map(s => ({
        name:         s.name,
        event_ids:    (s.event_ids || []).join(", "),
        event_prefix: s.event_prefix || "",
      }));
      status.textContent = "";
      this._renderEditor(container);
    } catch (e) {
      status.textContent = `Could not load preset: ${e.message}`;
    }
  },

  // ── Step editor ────────────────────────────────────────────────────────────

  _renderEditor(container) {
    const list = container.querySelector("#fa-steps-list");
    list.innerHTML = this._steps.map((s, i) => this._stepCard(s, i)).join("");

    list.querySelectorAll(".fa-remove-step").forEach(btn => {
      btn.onclick = () => {
        this._syncStepsFromDOM(container);
        this._steps.splice(+btn.dataset.idx, 1);
        this._renderEditor(container);
      };
    });

    list.querySelectorAll("input, textarea").forEach(el => {
      el.addEventListener("change", () => this._syncStepsFromDOM(container));
    });
  },

  _stepCard(step, idx) {
    const color = STEP_COLORS[idx % STEP_COLORS.length];
    return `
      <div class="fa-step-card" data-idx="${idx}" style="--sc:${color}">
        <div class="fa-step-card-header">
          <span class="fa-step-badge" style="background:${color}">Step ${idx + 1}</span>
          <input class="fa-step-name" data-idx="${idx}" data-field="name"
                 type="text" value="${_esc(step.name)}" placeholder="Step name" />
          <button class="fa-remove-step btn-sm btn-danger" data-idx="${idx}"
                  title="Remove step" ${idx === 0 ? "disabled" : ""}>✕</button>
        </div>
        <div class="fa-step-card-body">
          <label class="fa-field-label">Event IDs <small style="color:#999">(comma-separated)</small></label>
          <textarea class="fa-step-ids" data-idx="${idx}" data-field="event_ids"
                    rows="2" placeholder="e.g. 01.3160.000, 01.3160.001">${_esc(step.event_ids)}</textarea>

          <label class="fa-field-label" style="margin-top:.35rem">
            Event prefix <small style="color:#999">(optional — matches any ID starting with)</small>
          </label>
          <input class="fa-step-prefix" data-idx="${idx}" data-field="event_prefix"
                 type="text" value="${_esc(step.event_prefix)}"
                 placeholder="e.g. 01.1008." style="width:100%;box-sizing:border-box" />
        </div>
      </div>
    `;
  },

  _syncStepsFromDOM(container) {
    container.querySelector("#fa-steps-list")
      .querySelectorAll(".fa-step-card").forEach(card => {
        const i = +card.dataset.idx;
        if (!this._steps[i]) return;
        this._steps[i] = {
          name:         card.querySelector(".fa-step-name").value,
          event_ids:    card.querySelector(".fa-step-ids").value,
          event_prefix: card.querySelector(".fa-step-prefix").value,
        };
      });
  },

  _addStep(container) {
    this._syncStepsFromDOM(container);
    this._steps.push({ name: `Step ${this._steps.length + 1}`, event_ids: "", event_prefix: "" });
    this._renderEditor(container);
    container.querySelectorAll(".fa-step-card").forEach(
      (c, i, arr) => i === arr.length - 1 && c.scrollIntoView({ behavior: "smooth", block: "nearest" })
    );
  },

  // ── Run funnel → then auto-trigger AI insight ──────────────────────────────

  async _runFunnel(container) {
    this._syncStepsFromDOM(container);
    const status  = container.querySelector("#fa-run-status");
    const section = container.querySelector("#fa-result-section");
    const insightSection = container.querySelector("#fa-insight-section");

    const steps = this._steps.map(s => ({
      name:         s.name.trim() || "Unnamed step",
      event_ids:    s.event_ids.split(",").map(e => e.trim()).filter(Boolean),
      event_prefix: s.event_prefix.trim(),
    }));

    const invalid = steps.filter(s => !s.event_ids.length && !s.event_prefix);
    if (invalid.length) {
      status.textContent = `⚠ Step "${invalid[0].name}" needs at least one Event ID or Prefix.`;
      return;
    }
    if (steps.length < 2) {
      status.textContent = "⚠ Add at least 2 steps.";
      return;
    }

    status.textContent = "Running…";
    section.style.display = "none";
    insightSection.style.display = "none";

    try {
      const resp = await fetch(`${API}/conversion`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ steps }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        status.textContent = `Error: ${data.detail || resp.statusText}`;
        return;
      }

      this._lastResult = { steps, funnel_result: data.funnel };
      status.textContent = "";
      section.style.display = "";

      this._renderSummary(container, data);
      this._renderFunnel(container, data);

      // ── Auto-trigger AI insight ──
      this._getInsight(container);

    } catch (e) {
      status.textContent = `Network error: ${e.message}`;
    }
  },

  // ── Funnel bar chart ───────────────────────────────────────────────────────

  _renderSummary(container, data) {
    container.querySelector("#fa-summary-line").innerHTML =
      `${(data.total_events ?? 0).toLocaleString()} events &nbsp;·&nbsp; ` +
      `${(data.baseline_users ?? 0).toLocaleString()} baseline users &nbsp;·&nbsp; ` +
      `Overall: <strong>${data.overall_conversion ?? "—"}</strong>`;
  },

  _renderFunnel(container, data) {
    const el    = container.querySelector("#fa-funnel");
    const steps = data.funnel ?? [];
    if (!steps.length) { el.textContent = "No data."; return; }

    // Grid tick marks: 0, 25, 50, 75, 100
    const ticks = [0, 25, 50, 75, 100];

    const rows = steps.map((s, i) => {
      const pct   = Math.max(0.5, s.pct_of_baseline);   // never fully invisible
      const color = STEP_COLORS[i % STEP_COLORS.length];
      const drop  = (i > 0 && s.drop_from_prev > 0)
        ? `<div class="fa-drop-tag">▼ −${s.drop_from_prev.toLocaleString()} (${s.drop_pct_formatted})</div>`
        : `<div class="fa-drop-tag"></div>`;

      return `
        <div class="fa-chart-row">
          <div class="fa-chart-label" title="${_esc(s.name)}">
            ${drop}
            <span class="fa-chart-step-name">${_esc(s.name)}</span>
            <span class="fa-chart-users">${s.users.toLocaleString()} users</span>
          </div>
          <div class="fa-chart-track">
            <div class="fa-chart-bar" style="width:${pct}%;background:${color}">
              <span class="fa-chart-pct-label">${s.pct_formatted}</span>
            </div>
          </div>
        </div>
      `;
    }).join("");

    const gridLines = ticks.map(t =>
      `<div class="fa-grid-line" style="left:${t}%">
        <span class="fa-grid-tick">${t}</span>
       </div>`
    ).join("");

    el.innerHTML = `
      <div class="fa-chart-wrap">
        <div class="fa-chart-grid">${gridLines}</div>
        <div class="fa-chart-rows">${rows}</div>
        <div class="fa-chart-axis-label">Conv. % +</div>
      </div>
    `;
  },

  // ── Event browser ──────────────────────────────────────────────────────────

  async _searchEvents(container) {
    const input  = container.querySelector("#fa-event-search");
    const listEl = container.querySelector("#fa-event-list");
    const prefix = input.value.trim();

    listEl.textContent = "Loading…";
    try {
      const url  = `${API}/events/sample?limit=200${prefix ? "&prefix=" + encodeURIComponent(prefix) : ""}`;
      const resp = await fetch(url);
      const data = await resp.json();

      if (!resp.ok) {
        listEl.textContent = `Error ${resp.status}: ${data.detail || resp.statusText}`;
        return;
      }
      if (!data.events?.length) {
        listEl.textContent = prefix
          ? `No events found with prefix "${prefix}". Try a shorter prefix or leave blank.`
          : "No events in dataset.";
        return;
      }

      listEl.innerHTML = data.events.map(e => `
        <div class="fa-event-row" title="${_esc(e.name || '')}">
          <code class="fa-event-id">${_esc(e.id)}</code>
          <span class="fa-event-name">${_esc(e.name)}</span>
          <button class="fa-copy-id btn-sm" data-id="${_esc(e.id)}" title="Copy ID">⎘</button>
        </div>
      `).join("");

      listEl.querySelectorAll(".fa-copy-id").forEach(btn => {
        btn.onclick = () => {
          navigator.clipboard?.writeText(btn.dataset.id).catch(() => {});
          btn.textContent = "✓";
          setTimeout(() => { btn.textContent = "⎘"; }, 1200);
        };
      });
    } catch (e) {
      listEl.textContent = `Error: ${e.message}`;
    }
  },

  // ── AI Insight — auto-run + Markdown render ────────────────────────────────

  async _getInsight(container) {
    if (!this._lastResult) return;

    const insightSection = container.querySelector("#fa-insight-section");
    const status  = container.querySelector("#fa-insight-status");
    const body    = container.querySelector("#fa-insight-body");
    const regenBtn = container.querySelector("#fa-insight-btn");

    insightSection.style.display = "";
    regenBtn.style.display = "none";
    status.textContent = "Generating AI insight…";
    body.innerHTML = `<div class="fa-insight-loading">
      <span class="fa-spinner"></span> Analysing funnel drop-offs with AI…
    </div>`;

    // Scroll insight into view
    setTimeout(() => insightSection.scrollIntoView({ behavior: "smooth", block: "nearest" }), 100);

    try {
      const resp = await fetch(`${API}/insight`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(this._lastResult),
      });
      const data = await resp.json();

      if (!resp.ok) {
        status.textContent = `Error: ${data.detail || resp.statusText}`;
        body.innerHTML = "";
        regenBtn.style.display = "";
        return;
      }

      status.textContent = "";
      regenBtn.style.display = "";
      body.innerHTML = _mdToHtml(data.insight ?? "_No insight returned._");

    } catch (e) {
      status.textContent = `Network error: ${e.message}`;
      body.innerHTML = "";
      regenBtn.style.display = "";
    }
  },

  // ── Styles ─────────────────────────────────────────────────────────────────

  _injectStyles() {
    if (document.getElementById("fa-styles")) return;
    const s = document.createElement("style");
    s.id = "fa-styles";
    s.textContent = `
      /* ── Step editor ── */
      .fa-step-card {
        border: 1px solid #ddd;
        border-left: 3px solid var(--sc, #4f8ef7);
        border-radius: 6px;
        margin-bottom: .6rem;
        background: #fff;
        overflow: hidden;
      }
      .fa-step-card-header {
        display: flex; align-items: center; gap: .5rem;
        padding: .45rem .6rem;
        background: #f8f9fa;
        border-bottom: 1px solid #eee;
      }
      .fa-step-badge {
        font-size: .72rem; font-weight: 700; color: #fff;
        padding: .15rem .45rem; border-radius: 99px; white-space: nowrap;
      }
      .fa-step-name {
        flex: 1; border: none; background: transparent;
        font-weight: 600; font-size: .92rem; outline: none; min-width: 0;
      }
      .fa-step-name:focus { background: #fff; border-radius: 3px; outline: 1px solid #4f8ef7; }
      .fa-step-card-body  { padding: .5rem .6rem; }
      .fa-field-label     { font-size: .78rem; color: #666; display: block; margin-bottom: .15rem; }
      .fa-step-ids        { width: 100%; box-sizing: border-box; resize: vertical;
                            font-family: monospace; font-size: .8rem; }
      .fa-step-prefix     { font-family: monospace; font-size: .8rem; }

      /* ── Buttons ── */
      .btn-sm      { font-size: .8rem; padding: .2rem .55rem; cursor: pointer; }
      .btn-outline { background: transparent; border: 1px solid #ccc; }
      .btn-danger  { background: transparent; border: 1px solid #e05c5c; color: #e05c5c; }
      .btn-danger:hover:not(:disabled) { background: #fee; }
      .btn-danger:disabled { opacity: .35; cursor: default; }

      /* ── Event browser ── */
      .fa-event-row {
        display: flex; align-items: center; gap: .4rem;
        padding: .18rem .3rem; border-bottom: 1px solid #f0f0f0;
      }
      .fa-event-row:last-child { border-bottom: none; }
      .fa-event-id   { min-width: 110px; color: #4f8ef7; }
      .fa-event-name { flex: 1; color: #555; font-size: .78rem;
                       white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

      /* ── Chart wrapper ── */
      .fa-chart-wrap {
        background: #f5f3ec;
        border-radius: 8px;
        padding: 1rem 1rem .5rem 1rem;
        position: relative;
        overflow: hidden;
      }

      /* Grid lines */
      .fa-chart-grid {
        position: absolute;
        inset: 0 1rem 2rem calc(1rem + 160px); /* align with track area */
        pointer-events: none;
      }
      .fa-grid-line {
        position: absolute; top: 0; bottom: 0;
        border-left: 1px solid #ddd9cc;
      }
      .fa-grid-tick {
        position: absolute; bottom: -1.2rem;
        transform: translateX(-50%);
        font-size: .72rem; color: #999;
      }

      /* Rows */
      .fa-chart-rows { position: relative; }
      .fa-chart-row  {
        display: flex; align-items: center;
        gap: .75rem; margin-bottom: .55rem;
      }

      /* Left label column */
      .fa-chart-label {
        min-width: 160px; max-width: 160px;
        display: flex; flex-direction: column;
        align-items: flex-end; gap: .05rem;
      }
      .fa-drop-tag {
        font-size: .7rem; color: #e05555; font-weight: 600;
        min-height: .9rem; line-height: 1;
      }
      .fa-chart-step-name {
        font-size: .82rem; font-weight: 700; color: #2c2c2c;
        text-align: right; line-height: 1.2;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        max-width: 160px;
      }
      .fa-chart-users {
        font-size: .7rem; color: #888; text-align: right;
      }

      /* Bar track */
      .fa-chart-track {
        flex: 1; height: 38px;
        display: flex; align-items: center;
      }
      .fa-chart-bar {
        height: 100%;
        border-radius: 3px;
        display: flex; align-items: center; justify-content: flex-end;
        min-width: 30px;
        transition: width .5s cubic-bezier(.4,0,.2,1);
        position: relative;
      }
      .fa-chart-pct-label {
        font-size: .82rem; font-weight: 700; color: #fff;
        padding-right: .5rem; white-space: nowrap;
        text-shadow: 0 1px 2px rgba(0,0,0,.25);
      }

      /* Axis label */
      .fa-chart-axis-label {
        text-align: center; font-size: .75rem; color: #999;
        margin-top: 1.6rem; letter-spacing: .03em;
      }

      /* ── AI Insight — Markdown output ── */
      .fa-insight-loading {
        display: flex; align-items: center; gap: .6rem;
        color: #888; font-size: .9rem; padding: .75rem 0;
      }
      .fa-spinner {
        display: inline-block; width: 16px; height: 16px;
        border: 2px solid #ddd; border-top-color: #4f8ef7;
        border-radius: 50%; animation: fa-spin .7s linear infinite;
      }
      @keyframes fa-spin { to { transform: rotate(360deg); } }

      .fa-md-body {
        background: #f8f9fb;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        font-size: .92rem;
        line-height: 1.65;
        color: #1f2937;
      }
      .fa-md-body h1 { font-size: 1.2rem; font-weight: 700; margin: .75rem 0 .35rem; }
      .fa-md-body h2 { font-size: 1.05rem; font-weight: 700; margin: .7rem 0 .3rem; border-bottom: 1px solid #e5e7eb; padding-bottom: .2rem; }
      .fa-md-body h3 { font-size: .95rem; font-weight: 700; margin: .6rem 0 .25rem; color: #374151; }
      .fa-md-body p  { margin: .4rem 0; }
      .fa-md-body ul, .fa-md-body ol { margin: .35rem 0 .35rem 1.4rem; padding: 0; }
      .fa-md-body li { margin: .2rem 0; }
      .fa-md-body strong { font-weight: 700; color: #111; }
      .fa-md-body em     { font-style: italic; }
      .fa-md-body code   { font-family: monospace; font-size: .85em;
                           background: #e5e7eb; padding: .1em .35em; border-radius: 3px; }
      .fa-md-body blockquote {
        border-left: 3px solid #4f8ef7; margin: .5rem 0;
        padding: .3rem .8rem; color: #555; background: #eff6ff; border-radius: 0 4px 4px 0;
      }
      .fa-md-body hr { border: none; border-top: 1px solid #e5e7eb; margin: .75rem 0; }
    `;
    document.head.appendChild(s);
  },
};

// ── Utilities ─────────────────────────────────────────────────────────────────

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * Lightweight Markdown → HTML converter.
 * Handles: headings, bold, italic, inline code, blockquote,
 *          unordered + ordered lists, horizontal rules, paragraphs.
 */
function _mdToHtml(md) {
  const lines  = md.replace(/\r\n/g, "\n").split("\n");
  const out    = [];
  let listTag  = null;   // "ul" | "ol" | null
  let inBlock  = false;

  const closeList = () => {
    if (listTag) { out.push(`</${listTag}>`); listTag = null; }
  };

  const inline = (text) => text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g,  "<strong>$1</strong>")
    .replace(/__(.+?)__/g,       "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g,       "<em>$1</em>")
    .replace(/_(.+?)_/g,         "<em>$1</em>")
    .replace(/`(.+?)`/g,         "<code>$1</code>");

  for (const raw of lines) {
    const line = raw;

    // Heading
    const hm = line.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      closeList();
      const lvl = hm[1].length;
      out.push(`<h${lvl}>${inline(hm[2])}</h${lvl}>`);
      continue;
    }

    // Horizontal rule
    if (/^[-*_]{3,}\s*$/.test(line)) {
      closeList(); out.push("<hr>"); continue;
    }

    // Blockquote
    const bq = line.match(/^>\s*(.*)/);
    if (bq) {
      closeList();
      out.push(`<blockquote>${inline(bq[1])}</blockquote>`);
      continue;
    }

    // Unordered list
    const ul = line.match(/^[-*+]\s+(.*)/);
    if (ul) {
      if (listTag !== "ul") { closeList(); out.push("<ul>"); listTag = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }

    // Ordered list
    const ol = line.match(/^\d+\.\s+(.*)/);
    if (ol) {
      if (listTag !== "ol") { closeList(); out.push("<ol>"); listTag = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }

    // Blank line
    if (!line.trim()) {
      closeList();
      out.push("");
      continue;
    }

    // Normal paragraph line
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }

  closeList();
  return out.join("\n");
}
