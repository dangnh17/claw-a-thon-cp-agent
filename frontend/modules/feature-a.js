/**
 * Feature A — template for one team member's frontend module.
 *
 * Copy to feature-b.js / feature-c.js and change:
 *   - id, label, icon
 *   - API_PREFIX  → must match agent/modules/feature_b.py router prefix
 *   - render()    → build your UI inside `container`
 *
 * The module is fully self-contained. It owns its DOM, its API calls,
 * and its state. No shared globals needed.
 */

const API_PREFIX = "/api/feature-a";

// ── Module definition ─────────────────────────────────────────────────────────
export default {
  id: "feature-a",
  label: "Feature A",
  icon: "🔍",

  /**
   * Called once when the tab is first activated.
   * Build all DOM inside `container` — do not touch elements outside it.
   *
   * @param {HTMLElement} container
   */
  render(container) {
    container.innerHTML = `
      <h2>${this.icon} ${this.label}</h2>

      <section>
        <label>Input</label>
        <textarea id="fa-input" rows="5" placeholder="Enter text…" style="width:100%;box-sizing:border-box"></textarea>
        <button id="fa-run">Run</button>
        <div id="fa-status" class="status"></div>
      </section>

      <section id="fa-result-section" style="display:none">
        <h3>Result</h3>
        <pre id="fa-result" class="output"></pre>
      </section>

      <section>
        <h3>History</h3>
        <button id="fa-load-history">Load</button>
        <table id="fa-history" class="data-table">
          <thead><tr><th>saved_at</th><th>confidence</th><th>result</th></tr></thead>
          <tbody></tbody>
        </table>
      </section>
    `;

    this._bindEvents(container);
  },

  // ── Private ─────────────────────────────────────────────────────────────────

  _bindEvents(container) {
    container.querySelector("#fa-run").onclick = () => this._run(container);
    container.querySelector("#fa-load-history").onclick = () => this._loadHistory(container);
  },

  async _run(container) {
    const input = container.querySelector("#fa-input").value.trim();
    const status = container.querySelector("#fa-status");
    if (!input) { status.textContent = "Enter some text first."; return; }

    status.textContent = "Running…";

    const resp = await fetch(`${API_PREFIX}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_text: input }),
    });
    const data = await resp.json();
    status.textContent = resp.ok ? "Done." : `Error: ${data.detail}`;

    if (resp.ok) {
      container.querySelector("#fa-result-section").style.display = "";
      container.querySelector("#fa-result").textContent = JSON.stringify(data, null, 2);
    }
  },

  async _loadHistory(container) {
    const resp = await fetch(`${API_PREFIX}/history`);
    const rows = await resp.json();
    const tbody = container.querySelector("#fa-history tbody");
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${r.saved_at ?? ""}</td>
        <td>${(r.confidence ?? 0).toFixed(2)}</td>
        <td>${r.result ?? ""}</td>
      </tr>
    `).join("");
  },
};
