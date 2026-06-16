/**
 * Journey Insight
 *
 * UI layout:
 *   - Window selector (8 checkboxes)
 *   - Run Pipeline + Generate Report buttons
 *   - Report panel (markdown rendered via marked.js)
 */

const API_PREFIX = "/api/journey-insight";
const INPUT_GLOB = "output/tracking_events_v2/*.parquet";
const WINDOWS = [
  "00:00-03:00", "03:00-06:00", "06:00-09:00", "09:00-12:00",
  "12:00-15:00", "15:00-18:00", "18:00-21:00", "21:00-24:00",
];

// ── Module definition ─────────────────────────────────────────────────────────
export default {
  id: "journey-insight",
  label: "Journey Insight",
  icon: "🗺️",

  _pollTimer: null,
  _pollingRunId: null,
  _latestSuccessfulRunId: null,
  _dataDate: null,
  _loadTimer: null,

  render(container) {
    if (!document.getElementById("ji-loading-styles")) {
      const s = document.createElement("style");
      s.id = "ji-loading-styles";
      s.textContent = `
        @keyframes ji-spin { to { transform: rotate(360deg); } }
        @keyframes ji-pop  { 0%,100% { transform: scale(1); } 50% { transform: scale(1.25); } }
        @keyframes ji-dot  { 0%,80%,100% { opacity:.2; transform:translateY(0); } 40% { opacity:1; transform:translateY(-4px); } }
        .ji-loading { display:flex; flex-direction:column; align-items:center; justify-content:center;
          padding:36px 20px; background:#fff; border:1px solid #e2e8f0; border-radius:12px;
          margin:16px 0; box-shadow:0 1px 3px rgba(0,0,0,.06); gap:14px; }
        .ji-loading-ring-wrap { position:relative; width:64px; height:64px; }
        .ji-loading-ring { position:absolute; inset:0; border-radius:50%;
          border:3px solid #e2e8f0; border-top-color:#6366f1;
          animation:ji-spin 0.9s linear infinite; }
        .ji-loading-icon { position:absolute; inset:0; display:flex; align-items:center;
          justify-content:center; font-size:26px; animation:ji-pop 1.8s ease-in-out infinite; }
        .ji-loading-title { font-size:14px; font-weight:700; color:#0f172a; }
        .ji-loading-sub   { font-size:12px; color:#64748b; }
        .ji-loading-dots  { display:flex; gap:5px; }
        .ji-loading-dots span { width:6px; height:6px; border-radius:50%; background:#6366f1;
          animation:ji-dot 1.2s ease-in-out infinite; }
        .ji-loading-dots span:nth-child(2) { animation-delay:.2s; }
        .ji-loading-dots span:nth-child(3) { animation-delay:.4s; }
      `;
      document.head.appendChild(s);
    }

    container.innerHTML = `
      <h2>${this.icon} ${this.label}</h2>

      <section>
        <!-- <h3>Run Pipeline</h3> -->
        <div style="margin-bottom:12px;display:flex;align-items:center;gap:8px">
          <label for="ji-date-select"><strong>Data date:</strong></label>
          <select id="ji-date-select" style="padding:4px 8px;border-radius:4px;border:1px solid #ccc">
            <option value="">Loading dates…</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button id="ji-btn-run" disabled>Run Pipeline</button>
          <button id="ji-btn-rerun" style="display:none">🔄 Re-run Pipeline</button>
        </div>
      </section>

      <section>
        <h3>Generate Report</h3>
        <div style="margin-bottom:8px">
          <strong>Select time windows (required):</strong><br>
          <div id="ji-windows" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:6px">
            ${WINDOWS.map(w => `
              <label class="ji-window-label" data-window="${w}" style="display:flex;align-items:center;gap:4px;cursor:pointer">
                <input type="checkbox" class="ji-window-cb" value="${w}"> <span class="ji-window-text">${w}</span>
              </label>
            `).join("")}
          </div>
        </div>
        <div>
          <button id="ji-btn-report" disabled>Generate Report</button>
        </div>
        <div id="ji-loading"></div>
        <div id="ji-status" style="display:none;margin-top:10px;padding:10px 14px;border-radius:6px;font-size:0.9em;background:#f0f4ff;border:1px solid #c7d4f0;color:#334"></div>
      </section>

      <section id="ji-report-section" style="display:none">
        <div id="ji-report-meta" style="color:#888;font-size:0.82em;margin-bottom:10px"></div>
        <div id="ji-summary-body"></div>
        <div id="ji-report-body" style="display:none"></div>
      </section>
    `;

    this._bindEvents(container);
    this._loadDates(container);
    this._loadLatest(container);
  },

  // ── Button / checkbox events ──────────────────────────────────────────────

  _bindEvents(container) {
    container.querySelectorAll(".ji-window-cb").forEach(cb => {
      cb.addEventListener("change", () => this._updateButtonStates(container));
    });
    const btnRun = container.querySelector("#ji-btn-run");
    if (btnRun) btnRun.onclick = () => this._startRun(container);
    const btnRerun = container.querySelector("#ji-btn-rerun");
    if (btnRerun) btnRerun.onclick = () => this._startRun(container, true);
    container.querySelector("#ji-btn-report").onclick = () => this._startReport(container);
    container.querySelector("#ji-date-select").addEventListener("change", () => {
      this._loadWindowCounts(container);
      this._loadLatest(container);
    });
  },

  _selectedWindows(container) {
    return Array.from(container.querySelectorAll(".ji-window-cb:checked")).map(cb => cb.value);
  },

  _selectedDate(container) {
    const sel = container.querySelector("#ji-date-select");
    return sel ? (sel.value || null) : null;
  },

  _isInProgress() {
    return this._pollingRunId !== null;
  },

  _updateButtonStates(container) {
    const windows = this._selectedWindows(container);
    const inProgress = this._isInProgress();
    const hasWindows = windows.length > 0;
    const hasSuccess = !!this._latestSuccessfulRunId;

    const btnRun2 = container.querySelector("#ji-btn-run");
    if (btnRun2) btnRun2.disabled = inProgress;
    const btnRerun2 = container.querySelector("#ji-btn-rerun");
    if (btnRerun2) btnRerun2.disabled = inProgress;
    container.querySelector("#ji-btn-report").disabled = !hasWindows || !hasSuccess || inProgress;
    const dateSelect = container.querySelector("#ji-date-select");
    if (dateSelect) dateSelect.disabled = inProgress;
  },

  // ── Init: load available dates + latest successful run ───────────────────

  async _loadLatest(container) {
    try {
      const date = this._selectedDate(container);
      const params = date ? `?date=${encodeURIComponent(date)}` : "";
      const resp = await fetch(`${API_PREFIX}/latest${params}`);
      const btnRun = container.querySelector("#ji-btn-run");
      const btnRerun = container.querySelector("#ji-btn-rerun");
      if (!resp.ok) {
        // No run for this date — show Run Pipeline
        this._latestSuccessfulRunId = null;
        if (btnRun) btnRun.style.display = "";
        if (btnRerun) btnRerun.style.display = "none";
        this._updateButtonStates(container);
        return;
      }
      const data = await resp.json();
      this._latestSuccessfulRunId = data.run_id;
      // Run exists for this date — show Re-run
      if (btnRun) btnRun.style.display = "none";
      if (btnRerun) btnRerun.style.display = "";
      this._updateButtonStates(container);
    } catch (e) {
      // ignore
    }
  },

  async _loadDates(container) {
    const sel = container.querySelector("#ji-date-select");
    if (!sel) return;
    try {
      const resp = await fetch(`${API_PREFIX}/dates`);
      if (!resp.ok) {
        sel.innerHTML = `<option value="">No dates found</option>`;
        this._updateButtonStates(container);
        return;
      }
      const data = await resp.json();
      const dates = data.dates || [];
      if (!dates.length) {
        sel.innerHTML = `<option value="">No dates found</option>`;
        this._updateButtonStates(container);
        return;
      }
      sel.innerHTML = dates
        .map(d => `<option value="${d}">${d}</option>`)
        .join("");
      // Default to latest date
      sel.value = dates[dates.length - 1];
      await this._loadWindowCounts(container);
    } catch (e) {
      sel.innerHTML = `<option value="">Error loading dates</option>`;
      this._updateButtonStates(container);
    }
  },

  async _loadWindowCounts(container) {
    const date = this._selectedDate(container);
    const params = new URLSearchParams({ input_glob: INPUT_GLOB });
    if (date) params.set("date_filter", date);
    try {
      const resp = await fetch(`${API_PREFIX}/windows?${params}`);
      if (!resp.ok) return;
      const data = await resp.json();
      const counts = data.windows || {};
      container.querySelectorAll(".ji-window-label").forEach(label => {
        const w = label.dataset.window;
        const count = counts[w] ?? 0;
        const cb = label.querySelector("input");
        const textEl = label.querySelector(".ji-window-text");
        if (count === 0) {
          label.style.opacity = "0.4";
          cb.disabled = true;
          cb.checked = false;
        } else {
          label.style.opacity = "1";
          cb.disabled = false;
        }
        textEl.textContent = w;
      });
      this._updateButtonStates(container);
    } catch (e) {
      // ignore, leave checkboxes as-is
    }
  },

  // ── Run Pipeline ──────────────────────────────────────────────────────────

  async _startRun(container, forceRerun = false) {
    const title = forceRerun ? "Re-running pipeline…" : "Starting pipeline…";
    this._showLoading(container, ["⚙️","📦","🔄","🛠️","📂"], title, "Đang xử lý dữ liệu tracking");
    try {
      const dateFilter = this._selectedDate(container);
      const resp = await fetch(`${API_PREFIX}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input_glob: INPUT_GLOB, date_filter: dateFilter, force_rerun: forceRerun }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        this._hideLoading(container);
        this._setStatus(container, `Error: ${data.detail || resp.status}`);
        return;
      }
      this._startPolling(container, data.run_id);
    } catch (e) {
      this._hideLoading(container);
      this._setStatus(container, `Network error: ${e.message}`);
    }
  },

  // ── Generate Report ───────────────────────────────────────────────────────

  async _startReport(container) {
    const windows = this._selectedWindows(container);
    if (!windows.length || !this._latestSuccessfulRunId) return;

    // Hide stale report while new one is generating
    const reportSection = container.querySelector("#ji-report-section");
    if (reportSection) reportSection.style.display = "none";

    this._showLoading(container, ["🧠","📊","✍️","💡","📈","🔍"], "Đang tạo report…", "AI đang phân tích journey theo time window");
    try {
      const resp = await fetch(`${API_PREFIX}/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: this._latestSuccessfulRunId, window_filters: windows }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        this._hideLoading(container);
        this._setStatus(container, `Error: ${data.detail || resp.status}`);
        return;
      }
      this._startPolling(container, data.run_id);
    } catch (e) {
      this._hideLoading(container);
      this._setStatus(container, `Network error: ${e.message}`);
    }
  },

  // ── Polling ───────────────────────────────────────────────────────────────

  _startPolling(container, runId) {
    this._pollingRunId = runId;
    this._updateButtonStates(container);
    if (this._pollTimer) clearInterval(this._pollTimer);
    this._pollTimer = setInterval(() => this._pollOnce(container, runId), 3000);
  },

  async _pollOnce(container, runId) {
    try {
      const resp = await fetch(`${API_PREFIX}/status/${runId}`);
      if (!resp.ok) return; // keep polling on transient errors
      const target = await resp.json();

      if (target.status === "ok") {
        this._stopPolling();
        this._hideLoading(container);
        this._latestSuccessfulRunId = runId;
        // Swap to Re-run button
        const btnRun = container.querySelector("#ji-btn-run");
        if (btnRun) btnRun.style.display = "none";
        const btnRerun = container.querySelector("#ji-btn-rerun");
        if (btnRerun) btnRerun.style.display = "";
        this._updateButtonStates(container);
        const steps = target.steps_completed || [];
        if (steps.includes("step4")) {
          this._setStatus(container, "Done. Loading report…");
          await this._loadReport(container, runId);
        } else {
          this._setStatus(container, "Pipeline ready. Select time windows and click Generate Report.");
        }
        // Refresh data date from latest endpoint
        this._loadLatest(container);
      } else if (target.status === "error") {
        this._stopPolling();
        this._hideLoading(container);
        const detail = target.error_detail || "Unknown error";
        this._setStatus(container, `Run failed at ${target.error_step || "unknown step"}: ${detail.slice(0, 120)}`);
        this._updateButtonStates(container);
      }
    } catch (e) {
      // keep polling on transient errors
    }
  },

  _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    this._pollingRunId = null;
  },

  // ── Load report ───────────────────────────────────────────────────────────

  async _loadReport(container, runId) {
    try {
      const [reportResp, summaryResp] = await Promise.all([
        fetch(`${API_PREFIX}/report/${runId}`),
        fetch(`${API_PREFIX}/summary/${runId}`),
      ]);
      if (!reportResp.ok) {
        this._setStatus(container, `Could not load report (${reportResp.status})`);
        return;
      }
      const reportData = await reportResp.json();
      const summaryData = summaryResp.ok ? await summaryResp.json() : null;
      this._renderReport(container, reportData, summaryData);
      this._setStatus(container, "Report loaded.");
    } catch (e) {
      this._setStatus(container, `Error loading report: ${e.message}`);
    }
  },

  _renderReport(container, reportData, summaryData) {
    const section = container.querySelector("#ji-report-section");
    section.style.display = "";

    const metaEl = container.querySelector("#ji-report-meta");
    const windows = Array.isArray(reportData.window_filters) ? reportData.window_filters.join(", ") : "";
    const genAt = reportData.generated_at ? new Date(reportData.generated_at).toLocaleString() : "—";
    metaEl.innerHTML = `
      <span><strong>Run:</strong> ${reportData.run_id?.slice(0, 8)}…</span> &nbsp;
      <span><strong>Date:</strong> ${reportData.data_date || "—"}</span> &nbsp;
      <span><strong>Windows:</strong> ${windows || "—"}</span> &nbsp;
      <span><strong>Generated:</strong> ${genAt}</span>
    `;

    // Render raw report — strip Method Guardrails section
    const rawEl = container.querySelector("#ji-report-body");
    this._renderMarkdown(rawEl, this._stripGuardrails(reportData.report_md || ""), false);

    // Render visual summary (with Mermaid)
    const summaryEl = container.querySelector("#ji-summary-body");
    if (summaryData && summaryData.summary_md) {
      this._renderMarkdown(summaryEl, summaryData.summary_md, true);
    } else {
      summaryEl.innerHTML = `<p style="color:#888;font-style:italic">Tóm tắt trực quan chưa có hoặc đang tạo…</p>`;
    }

    // Default to summary tab
    this._switchTab(container, "summary");
  },

  _renderMarkdown(el, md, withMermaid) {
    // Fix LaTeX arrow notation the LLM sometimes emits
    const cleaned = md
      .replace(/\$\\rightarrow\$/g, "→")
      .replace(/\$\\to\$/g, "→")
      .replace(/\\rightarrow/g, "→");

    // Inject table styles once
    if (!document.getElementById("ji-table-styles")) {
      const style = document.createElement("style");
      style.id = "ji-table-styles";
      style.textContent = `
        #ji-summary-body table, #ji-report-body table {
          border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.9em;
        }
        #ji-summary-body th, #ji-report-body th {
          background: #f4f6ff; text-align: left; padding: 8px 12px;
          border: 1px solid #d0d7e8; font-weight: 600;
        }
        #ji-summary-body td, #ji-report-body td {
          padding: 7px 12px; border: 1px solid #e4e8f0; vertical-align: top;
        }
        #ji-summary-body tr:nth-child(even) td, #ji-report-body tr:nth-child(even) td {
          background: #fafbff;
        }
      `;
      document.head.appendChild(style);
    }

    const doRender = () => {
      el.innerHTML = window.marked.parse(cleaned);
      if (withMermaid) this._initMermaid(el);
    };
    if (typeof window.marked !== "undefined") {
      doRender();
    } else {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/marked/marked.min.js";
      script.onload = doRender;
      script.onerror = () => {
        const pre = document.createElement("pre");
        pre.textContent = md;
        pre.style.cssText = "white-space:pre-wrap;word-break:break-word;margin:0";
        el.appendChild(pre);
      };
      document.head.appendChild(script);
    }
  },

  _initMermaid(container) {
    const loadAndRender = () => {
      if (typeof window.mermaid === "undefined") return;
      // Find all <code class="language-mermaid"> blocks and replace with mermaid divs
      container.querySelectorAll("code.language-mermaid").forEach((codeEl, i) => {
        const pre = codeEl.parentElement;
        const div = document.createElement("div");
        div.className = "mermaid";
        div.id = `mermaid-${Date.now()}-${i}`;
        div.textContent = codeEl.textContent;
        pre.replaceWith(div);
      });
      try {
        window.mermaid.init(undefined, container.querySelectorAll(".mermaid"));
      } catch (e) {
        // ignore mermaid render errors
      }
    };

    if (typeof window.mermaid !== "undefined") {
      loadAndRender();
    } else {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";
      script.onload = () => {
        window.mermaid.initialize({ startOnLoad: false, theme: "default" });
        loadAndRender();
      };
      document.head.appendChild(script);
    }
  },

  _switchTab(container, tab) {
    const summaryBtn = container.querySelector("#ji-tab-summary");
    const rawBtn = container.querySelector("#ji-tab-raw");
    const summaryEl = container.querySelector("#ji-summary-body");
    const rawEl = container.querySelector("#ji-report-body");
    if (!summaryBtn) return;

    if (tab === "summary") {
      summaryEl.style.display = "";
      rawEl.style.display = "none";
      summaryBtn.style.borderBottomColor = "#4a6cf7";
      summaryBtn.style.color = "#4a6cf7";
      summaryBtn.style.fontWeight = "600";
      rawBtn.style.borderBottomColor = "transparent";
      rawBtn.style.color = "#888";
      rawBtn.style.fontWeight = "normal";
    } else {
      summaryEl.style.display = "none";
      rawEl.style.display = "";
      rawBtn.style.borderBottomColor = "#4a6cf7";
      rawBtn.style.color = "#4a6cf7";
      rawBtn.style.fontWeight = "600";
      summaryBtn.style.borderBottomColor = "transparent";
      summaryBtn.style.color = "#888";
      summaryBtn.style.fontWeight = "normal";
    }
  },

  _stripGuardrails(md) {
    // Remove ## Method Guardrails section and everything after it
    return md.replace(/^##\s*Method Guardrails[\s\S]*/m, "").trimEnd();
  },

  // ── Loading ───────────────────────────────────────────────────────────────

  _showLoading(container, icons, title, subtitle) {
    this._stopLoadingTimer();
    const wrap = container.querySelector("#ji-loading");
    if (!wrap) return;
    let idx = 0;
    const render = () => {
      wrap.innerHTML = `
        <div class="ji-loading">
          <div class="ji-loading-ring-wrap">
            <div class="ji-loading-ring"></div>
            <div class="ji-loading-icon">${icons[idx]}</div>
          </div>
          <div class="ji-loading-title">${title}</div>
          <div class="ji-loading-sub">${subtitle}</div>
          <div class="ji-loading-dots"><span></span><span></span><span></span></div>
        </div>`;
    };
    render();
    this._loadTimer = setInterval(() => {
      idx = (idx + 1) % icons.length;
      const el = wrap.querySelector(".ji-loading-icon");
      if (el) el.textContent = icons[idx];
    }, 700);
  },

  _stopLoadingTimer() {
    if (this._loadTimer) { clearInterval(this._loadTimer); this._loadTimer = null; }
  },

  _hideLoading(container) {
    this._stopLoadingTimer();
    const wrap = container.querySelector("#ji-loading");
    if (wrap) wrap.innerHTML = "";
  },

  // ── Helpers ───────────────────────────────────────────────────────────────

  _setStatus(container, msg) {
    const el = container.querySelector("#ji-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    const isError = /error|fail|failed|missing/i.test(msg);
    const isDone  = /done|loaded|success/i.test(msg);
    el.style.display = "block";
    el.style.background = isError ? "#fff0f0" : isDone ? "#f0fff4" : "#f0f4ff";
    el.style.borderColor = isError ? "#f5c0c0" : isDone ? "#a8e6bf" : "#c7d4f0";
    el.style.color       = isError ? "#8b1a1a" : isDone ? "#1a5c35" : "#334";
    el.textContent = msg;
  },
};
