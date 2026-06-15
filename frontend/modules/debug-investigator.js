/**
 * Debug Investigator — analyze logs from a Jira ticket description
 *
 * Handles new backend contract:
 *   - ParseTicketResponse: zalopay_id, incident_time, timezone, missing_fields, warnings, confidence
 *   - InvestigateResponse: status, requested_time_range, actual_time_range, shift_days, query_summary,
 *                          events (normalized schema with id/title/subtitle/mapping/correlation/raw),
 *                          insight (structured: summary/user_flow/likely_root_cause/confidence/evidence[]/…)
 */

const API_PREFIX = "/api/debug";

const VIS_CSS = "https://cdnjs.cloudflare.com/ajax/libs/vis-timeline/7.7.2/vis-timeline-graph2d.min.css";
const VIS_JS  = "https://cdnjs.cloudflare.com/ajax/libs/vis-timeline/7.7.2/vis-timeline-graph2d.min.js";

function _loadVis() {
  return new Promise((resolve, reject) => {
    if (window.vis) { resolve(); return; }
    if (!document.querySelector(`link[href="${VIS_CSS}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet"; link.href = VIS_CSS;
      document.head.appendChild(link);
    }
    const script = document.createElement("script");
    script.src = VIS_JS;
    script.onload  = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

export default {
  id: "debug-investigator",
  label: "Debug",
  icon: "🔬",

  render(container) {
    container.innerHTML = `
      <style>
        .di { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1e293b; font-size: 14px; }
        .di-header { margin-bottom: 20px; }
        .di-header h2 { margin: 0; font-size: 20px; font-weight: 700; color: #0f172a; }

        /* ── Input card ── */
        .di-input-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
        .di-input-card > label { display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 8px; text-transform: uppercase; letter-spacing: .04em; }
        .di-textarea { width: 100%; box-sizing: border-box; padding: 12px 14px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 13px; font-family: "SF Mono","Fira Code",monospace; color: #1e293b; background: #f8fafc; resize: vertical; line-height: 1.6; outline: none; transition: border-color .15s; }
        .di-textarea:focus { border-color: #6366f1; background: #fff; box-shadow: 0 0 0 3px rgba(99,102,241,.1); }
        .di-btn-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
        .di-btn-primary { display: inline-flex; align-items: center; gap: 6px; padding: 9px 20px; background: #6366f1; color: #fff; border: none; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; transition: background .15s; }
        .di-btn-primary:hover { background: #4f46e5; }
        .di-btn-primary:disabled { background: #a5b4fc; cursor: not-allowed; }
        .di-btn-reset { display: inline-flex; align-items: center; gap: 6px; padding: 9px 16px; background: #fff; color: #64748b; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; transition: background .15s; }
        .di-btn-reset:hover { background: #f1f5f9; color: #334155; }
        .di-status { font-size: 12px; color: #64748b; }

        /* ── Parsed chips ── */
        .di-parsed { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
        .di-chip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 500; }
        .di-chip.uid   { background: #ede9fe; color: #5b21b6; }
        .di-chip.time  { background: #dbeafe; color: #1d4ed8; }
        .di-chip.dev   { background: #dcfce7; color: #15803d; }
        .di-chip.app   { background: #fef3c7; color: #b45309; }
        .di-chip.conf-high   { background: #d1fae5; color: #065f46; }
        .di-chip.conf-medium { background: #fef9c3; color: #854d0e; }
        .di-chip.conf-low    { background: #fee2e2; color: #991b1b; }
        .di-chip.warn  { background: #fef3c7; color: #92400e; }
        .di-chip.miss  { background: #fee2e2; color: #991b1b; }

        /* ── Banners ── */
        .di-banner { display: flex; align-items: flex-start; gap: 10px; padding: 12px 16px; border-radius: 10px; margin-bottom: 16px; }
        .di-banner .di-bi { font-size: 18px; flex-shrink: 0; line-height: 1.4; }
        .di-banner .di-bb { flex: 1; }
        .di-banner .di-bt { font-size: 13px; font-weight: 700; margin-bottom: 4px; }
        .di-banner .di-bx { font-size: 12px; line-height: 1.6; }
        .di-banner .di-br { font-family: "SF Mono","Fira Code",monospace; font-weight: 600; }
        .di-banner.warn  { background: #fffbeb; border: 1px solid #f59e0b; }
        .di-banner.warn .di-bt  { color: #92400e; }
        .di-banner.warn .di-bx  { color: #78350f; }
        .di-banner.warn .di-br  { color: #b45309; }
        .di-banner.error { background: #fff1f2; border: 1px solid #fecdd3; }
        .di-banner.error .di-bt { color: #9f1239; }
        .di-banner.error .di-bx { color: #be123c; }
        .di-banner.info  { background: #f0f9ff; border: 1px solid #7dd3fc; }
        .di-banner.info  .di-bt { color: #0c4a6e; }
        .di-banner.info  .di-bx { color: #075985; }

        /* ── Stats bar ── */
        .di-stats { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; margin-bottom: 20px; }
        .di-stats-ext { display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; margin-bottom: 12px; }
        .di-stat { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
        .di-stat .num { font-size: 26px; font-weight: 800; line-height: 1; }
        .di-stat .lbl { font-size: 11px; color: #64748b; margin-top: 5px; font-weight: 500; }
        .di-stat.tracking .num { color: #7c3aed; }
        .di-stat.access   .num { color: #2563eb; }
        .di-stat.vital    .num { color: #059669; }
        .di-stat.errors   .num { color: #dc2626; }
        .di-stat.attempts .num { color: #64748b; }
        .di-stat.mismatch .num { color: #f59e0b; }
        .di-stat.confidence .num { font-size: 16px; }
        .di-stat.confidence.conf-high  .num { color: #065f46; }
        .di-stat.confidence.conf-medium .num { color: #854d0e; }
        .di-stat.confidence.conf-low   .num { color: #991b1b; }

        /* ── Tabs ── */
        .di-tabs { display: flex; border-bottom: 2px solid #e2e8f0; margin-bottom: 16px; }
        .di-tab  { padding: 9px 18px; font-size: 13px; font-weight: 500; color: #64748b; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color .15s, border-color .15s; }
        .di-tab:hover { color: #334155; }
        .di-tab.active { color: #6366f1; border-bottom-color: #6366f1; font-weight: 600; }
        .di-tab-pane { display: none; }
        .di-tab-pane.active { display: block; }

        /* ── Filter bar ── */
        .di-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 12px; padding: 10px 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }
        .di-filters label { display: flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 500; color: #475569; cursor: pointer; }
        .di-filters input[type=checkbox] { cursor: pointer; accent-color: #6366f1; }
        .di-filter-search { flex: 1; min-width: 160px; padding: 5px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 12px; color: #1e293b; background: #fff; outline: none; }
        .di-filter-search:focus { border-color: #6366f1; }
        .di-tl-hint { font-size: 11px; color: #94a3b8; margin-left: auto; white-space: nowrap; }
        .di-tl-zoom { display: flex; gap: 4px; }
        .di-tl-zoom button { padding: 3px 10px; font-size: 12px; background: #fff; border: 1px solid #cbd5e1; border-radius: 6px; cursor: pointer; color: #475569; }
        .di-tl-zoom button:hover { background: #f1f5f9; }

        /* ── vis-timeline wrapper ── */
        .di-vis-wrap { border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: #fff; }
        #di-vis-timeline { width: 100%; min-height: 480px; }

        .di-vis-wrap .vis-timeline { border: none !important; background: #fff !important; }
        .di-vis-wrap .vis-panel.vis-bottom,.di-vis-wrap .vis-panel.vis-center,
        .di-vis-wrap .vis-panel.vis-top,.di-vis-wrap .vis-panel.vis-left,
        .di-vis-wrap .vis-panel.vis-right { border-color: #e2e8f0 !important; }
        .di-vis-wrap .vis-time-axis .vis-text { color: #94a3b8 !important; font-family: monospace !important; font-size: 11px !important; }
        .di-vis-wrap .vis-time-axis .vis-grid.vis-minor { border-color: #f1f5f9 !important; }
        .di-vis-wrap .vis-time-axis .vis-grid.vis-major { border-color: #e2e8f0 !important; }
        .di-vis-wrap .vis-labelset .vis-label { background: #f8fafc !important; color: #475569 !important; font-family: inherit !important; font-size: 12px !important; font-weight: 600 !important; border-bottom: 1px solid #e2e8f0 !important; }
        .di-vis-wrap .vis-foreground .vis-group { border-bottom: 1px solid #f1f5f9 !important; }
        .di-vis-wrap .vis-item { font-family: inherit !important; font-size: 11px !important; border-radius: 4px !important; cursor: pointer !important; border-width: 1px !important; padding: 1px 6px !important; }
        .di-vis-wrap .vis-item.vis-tracking     { background: #ede9fe !important; border-color: #7c3aed !important; color: #5b21b6 !important; }
        .di-vis-wrap .vis-item.vis-access       { background: #dbeafe !important; border-color: #2563eb !important; color: #1d4ed8 !important; }
        .di-vis-wrap .vis-item.vis-vital        { background: #d1fae5 !important; border-color: #059669 !important; color: #065f46 !important; }
        .di-vis-wrap .vis-item.vis-vital-error  { background: #fee2e2 !important; border-color: #dc2626 !important; color: #991b1b !important; }
        .di-vis-wrap .vis-item.vis-selected     { box-shadow: 0 0 0 2px rgba(99,102,241,.5) !important; z-index: 10 !important; }
        .di-vis-wrap .vis-panel.vis-background  { background: #fafafa !important; }

        /* ── Detail panel ── */
        .di-detail-scrim  { position: fixed; inset: 0; background: rgba(0,0,0,.25); z-index: 500; opacity: 0; pointer-events: none; transition: opacity .2s; }
        .di-detail-scrim.open { opacity: 1; pointer-events: auto; }
        .di-detail-panel  { position: fixed; top: 0; right: 0; bottom: 0; width: 460px; max-width: 90vw; background: #fff; border-left: 1px solid #e2e8f0; box-shadow: -8px 0 32px rgba(0,0,0,.1); z-index: 501; display: flex; flex-direction: column; transform: translateX(100%); transition: transform .25s cubic-bezier(.4,0,.2,1); }
        .di-detail-panel.open { transform: translateX(0); }
        .di-dp-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid #e2e8f0; flex-shrink: 0; }
        .di-dp-header h3 { font-size: 15px; font-weight: 700; color: #0f172a; margin: 0; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .di-dp-close { background: none; border: none; cursor: pointer; font-size: 18px; color: #94a3b8; padding: 2px 6px; border-radius: 4px; line-height: 1; }
        .di-dp-close:hover { color: #475569; background: #f1f5f9; }
        .di-dp-body { flex: 1; overflow-y: auto; padding: 20px; }

        /* detail fields */
        .di-dp-tag { display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 4px; margin-bottom: 14px; }
        .di-dp-tag.tracking    { background: #ede9fe; color: #5b21b6; }
        .di-dp-tag.access      { background: #dbeafe; color: #1d4ed8; }
        .di-dp-tag.vital       { background: #d1fae5; color: #065f46; }
        .di-dp-tag.vital-error { background: #fee2e2; color: #991b1b; }
        .di-dp-tag.sev-warn    { background: #fef3c7; color: #92400e; margin-left: 6px; }
        .di-dp-field { margin-bottom: 12px; }
        .di-dp-field-lbl { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: #94a3b8; margin-bottom: 2px; }
        .di-dp-field-val { font-size: 13px; color: #1e293b; word-break: break-all; }
        .di-dp-field-val.mono { font-family: "SF Mono","Fira Code",monospace; font-size: 12px; }
        .di-dp-field-val.large { font-size: 18px; font-weight: 700; font-family: inherit; color: #0f172a; }
        .di-dp-err { background: #fff1f2; border: 1px solid #fecdd3; border-radius: 6px; padding: 10px 12px; margin-bottom: 14px; font-size: 12px; color: #be123c; }
        .di-dp-divider { border: none; border-top: 1px solid #f1f5f9; margin: 16px 0; }
        .di-dp-json { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; font-size: 11px; font-family: "SF Mono","Fira Code",monospace; color: #475569; white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; margin-top: 4px; }
        .di-map-badge { display: inline-block; font-size: 10px; padding: 1px 7px; border-radius: 4px; font-weight: 600; margin-left: 6px; }
        .di-map-badge.exact    { background: #d1fae5; color: #065f46; }
        .di-map-badge.fallback { background: #dbeafe; color: #1d4ed8; }
        .di-map-badge.unknown  { background: #f1f5f9; color: #64748b; }

        /* ── Session journey ── */
        .di-journey-title  { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; margin-bottom: 10px; }
        .di-journey-wrap   { padding-bottom: 4px; }
        .di-journey        { display: flex; flex-direction: column; gap: 0; }
        .di-journey-step   { display: flex; flex-direction: column; cursor: pointer; }
        .di-journey-card   { width: 100%; padding: 8px 12px; border-radius: 8px; box-sizing: border-box; background: #f8fafc; border: 1px solid #e2e8f0; color: #334155; transition: background .15s, border-color .15s; }
        .di-journey-card:hover  { background: #ede9fe; border-color: #7c3aed; }
        .di-journey-card.active { background: #7c3aed; border-color: #5b21b6; color: #fff; box-shadow: 0 0 0 3px rgba(124,58,237,.2); }
        .di-journey-card-hd  { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
        .di-journey-card-id  { font-size: 12px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .di-journey-card-ts  { font-size: 10px; font-family: monospace; color: #94a3b8; flex-shrink: 0; }
        .di-journey-card.active .di-journey-card-ts  { color: rgba(255,255,255,.65); }
        .di-journey-card-name { font-size: 11px; color: #475569; margin-top: 3px; font-style: italic; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .di-journey-card.active .di-journey-card-name { color: rgba(255,255,255,.8); }
        .di-journey-card-meta { font-size: 10px; font-family: monospace; color: #64748b; margin-top: 4px; background: rgba(0,0,0,.04); border-radius: 4px; padding: 3px 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .di-journey-card.active .di-journey-card-meta { background: rgba(255,255,255,.15); color: rgba(255,255,255,.8); }
        .di-journey-card-err  { display: inline-block; margin-top: 4px; font-size: 10px; font-weight: 700; background: #fee2e2; color: #dc2626; border-radius: 4px; padding: 1px 6px; }
        .di-journey-card.active .di-journey-card-err { background: rgba(255,255,255,.25); color: #fff; }
        .di-journey-arrow  { font-size: 11px; color: #cbd5e1; padding: 3px 0 3px 14px; line-height: 1; }

        /* ── Insight ── */
        .di-insight-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
        .di-insight-summary { font-size: 15px; font-weight: 700; color: #0f172a; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #f1f5f9; }
        .di-insight-conf { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; padding: 2px 10px; border-radius: 20px; margin-left: 10px; vertical-align: middle; }
        .di-insight-conf.high   { background: #d1fae5; color: #065f46; }
        .di-insight-conf.medium { background: #fef9c3; color: #854d0e; }
        .di-insight-conf.low    { background: #fee2e2; color: #991b1b; }
        .di-insight-section { margin-bottom: 16px; }
        .di-insight-section h4 { margin: 0 0 6px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; }
        .di-insight-section p  { margin: 0; font-size: 13px; color: #334155; line-height: 1.65; }
        .di-insight-list { margin: 0; padding: 0 0 0 18px; font-size: 13px; color: #334155; line-height: 1.8; }
        .di-evidence-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px 12px; border-radius: 8px; border: 1px solid #e2e8f0; background: #f8fafc; margin-bottom: 8px; cursor: pointer; transition: background .15s, border-color .15s; }
        .di-evidence-item:hover { background: #ede9fe; border-color: #7c3aed; }
        .di-evidence-item .di-ev-id { font-size: 11px; font-weight: 700; font-family: monospace; color: #7c3aed; white-space: nowrap; }
        .di-evidence-item .di-ev-ts { font-size: 10px; font-family: monospace; color: #94a3b8; }
        .di-evidence-item .di-ev-reason { font-size: 12px; color: #334155; margin-top: 2px; }

        .di-empty { text-align: center; padding: 48px 20px; color: #94a3b8; font-size: 13px; }
        .di-btn-ghost { padding: 6px 14px; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 7px; font-size: 12px; font-weight: 500; color: #475569; cursor: pointer; }
        .di-btn-ghost:hover { background: #e2e8f0; }
      </style>

      <div class="di">
        <div class="di-header"><h2>🔬 Debug Investigator</h2></div>

        <div class="di-input-card">
          <label>Jira ticket description</label>
          <textarea class="di-textarea" id="di-desc" rows="7" placeholder="Paste nội dung ticket Jira vào đây…"></textarea>
          <div class="di-parsed" id="di-parsed" style="display:none"></div>
          <div class="di-btn-row">
            <button class="di-btn-primary" id="di-run">🔍 Điều tra</button>
            <button class="di-btn-reset"   id="di-reset">🔄 Làm mới</button>
          </div>
        </div>

        <div id="di-banners"></div>

        <div id="di-result" style="display:none">
          <div class="di-stats">
            <div class="di-stat tracking"><div class="num" id="st-track">–</div><div class="lbl">🟣 Tracking Events</div></div>
            <div class="di-stat access">  <div class="num" id="st-access">–</div><div class="lbl">🔵 Access Logs</div></div>
            <div class="di-stat vital">   <div class="num" id="st-vital">–</div><div class="lbl">🟢 Vital Events</div></div>
          </div>
          <div class="di-stats-ext" id="di-stats-ext" style="display:none"></div>

          <div class="di-tabs">
            <div class="di-tab active" data-pane="timeline">📊 Timeline</div>
            <div class="di-tab" data-pane="insight">💡 Insight</div>
          </div>

          <div class="di-tab-pane active" id="pane-timeline">
            <div class="di-filters">
              <label><input type="checkbox" id="f-track" checked> <span style="color:#7c3aed">Tracking</span></label>
              <label><input type="checkbox" id="f-access" checked> <span style="color:#2563eb">Access</span></label>
              <label><input type="checkbox" id="f-vital"  checked> <span style="color:#059669">Vital</span></label>
              <input class="di-filter-search" id="f-search" placeholder="Tìm event, page, endpoint…">
              <span class="di-tl-hint">Ctrl+scroll để zoom · Click event để xem chi tiết</span>
              <div class="di-tl-zoom">
                <button id="tl-zoom-in">＋</button>
                <button id="tl-zoom-out">－</button>
                <button id="tl-fit">Fit</button>
              </div>
            </div>
            <div class="di-vis-wrap">
              <div id="di-vis-timeline"></div>
            </div>
          </div>

          <div class="di-tab-pane" id="pane-insight">
            <div class="di-insight-card" id="di-insight">
              <div class="di-empty">Chạy điều tra để xem insight</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Detail panel -->
      <div class="di-detail-scrim" id="di-scrim"></div>
      <div class="di-detail-panel"  id="di-detail-panel">
        <div class="di-dp-header">
          <h3 id="di-dp-title">Event Detail</h3>
          <button class="di-dp-close" id="di-dp-close">✕</button>
        </div>
        <div class="di-dp-body" id="di-dp-body"></div>
      </div>
    `;

    this._events      = [];
    this._parsed      = null;
    this._insight     = null;
    this._visTimeline = null;
    this._visItems    = null;
    this._activeId    = null;
    this._bind(container);
  },

  // ── Bindings ──────────────────────────────────────────────────────────────

  _bind(c) {
    c.querySelector("#di-run").onclick   = () => this._run(c);
    c.querySelector("#di-reset").onclick = () => this._reset(c);

    c.querySelectorAll(".di-tab").forEach(tab => {
      tab.onclick = () => {
        c.querySelectorAll(".di-tab").forEach(t => t.classList.remove("active"));
        c.querySelectorAll(".di-tab-pane").forEach(p => p.classList.remove("active"));
        tab.classList.add("active");
        c.querySelector(`#pane-${tab.dataset.pane}`).classList.add("active");
        if (tab.dataset.pane === "timeline" && this._visTimeline)
          setTimeout(() => this._visTimeline.redraw(), 50);
      };
    });

    const re = () => this._applyFilters(c);
    ["#f-track","#f-access","#f-vital"].forEach(id => c.querySelector(id).onchange = re);
    c.querySelector("#f-search").oninput = re;

    c.querySelector("#tl-zoom-in").onclick  = () => this._visTimeline?.zoomIn(0.4);
    c.querySelector("#tl-zoom-out").onclick = () => this._visTimeline?.zoomOut(0.4);
    c.querySelector("#tl-fit").onclick      = () => this._visTimeline?.fit({ animation: { duration: 400 } });

    const closePanel = () => {
      document.getElementById("di-detail-panel")?.classList.remove("open");
      document.getElementById("di-scrim")?.classList.remove("open");
      this._activeId = null;
    };
    document.getElementById("di-dp-close").onclick = closePanel;
    document.getElementById("di-scrim").onclick     = closePanel;
  },

  // ── Run ───────────────────────────────────────────────────────────────────

  async _run(c) {
    const desc = c.querySelector("#di-desc").value.trim();
    if (!desc) return;
    const btn = c.querySelector("#di-run");
    btn.disabled = true;

    // Clear previous results
    c.querySelector("#di-banners").innerHTML = "";
    c.querySelector("#di-result").style.display = "none";
    c.querySelector("#di-parsed").innerHTML = "";
    c.querySelector("#di-parsed").style.display = "none";
    c.querySelector("#di-stats-ext").style.display = "none";
    c.querySelector("#di-insight").innerHTML = `<div class="di-empty">Chạy điều tra để xem insight</div>`;
    if (this._visTimeline) { this._visTimeline.destroy(); this._visTimeline = null; }
    this._visItems = null;
    this._events = [];
    document.getElementById("di-detail-panel")?.classList.remove("open");
    document.getElementById("di-scrim")?.classList.remove("open");

    this._addBanner(c, "info", "🤖", "Đang đọc ticket…", "AI đang phân tích nội dung ticket.");
    let parsed;
    try {
      const r = await fetch(`${API_PREFIX}/parse-ticket`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ ticket_text: desc }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      parsed = await r.json();
    } catch (e) {
      c.querySelector("#di-banners").innerHTML = "";
      this._addBanner(c, "error", "❌", "Parse thất bại", this._e(e.message));
      btn.disabled = false; return;
    }
    c.querySelector("#di-banners").innerHTML = "";

    this._parsed = parsed;
    this._showParsed(c, parsed);

    if (!parsed.zalopay_id || !parsed.incident_time) {
      const missing = [
        !parsed.zalopay_id    ? "zalopay_id" : null,
        !parsed.incident_time ? "incident_time (ngày giờ xảy ra sự cố)" : null,
      ].filter(Boolean);
      this._addBanner(c, "error",
        "❌", "Không thể điều tra — thiếu thông tin bắt buộc",
        `Vui lòng bổ sung vào ticket: <strong>${missing.join(", ")}</strong>.`
      );
      btn.disabled = false; return;
    }

    this._addBanner(c, "info", "🔍", "Đang điều tra…", "Agent đang lập kế hoạch và query log.");
    let data;
    try {
      const r = await fetch(`${API_PREFIX}/investigate`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({
          zalopay_id: parsed.zalopay_id,
          ticket_text: desc,
          incident_time: parsed.incident_time || "",
          window_minutes: 60,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      data = await r.json();
    } catch (e) {
      c.querySelector("#di-banners").innerHTML = "";
      this._addBanner(c, "error", "❌", "Điều tra thất bại", this._e(e.message));
      btn.disabled = false; return;
    }
    c.querySelector("#di-banners").innerHTML = "";

    this._events = data.events || [];

    // Stats bar
    c.querySelector("#st-track").textContent  = data.tracking_count ?? 0;
    c.querySelector("#st-access").textContent = data.access_count  ?? 0;
    c.querySelector("#st-vital").textContent  = data.vital_count   ?? 0;
    this._renderExtStats(c, data);

    // Banners
    this._renderBanners(c, data);

    c.querySelector("#di-result").style.display = "";
    if (this._events.length > 0) {
      await this._initVisTimeline(c);
    }
    this._renderInsight(c, data);

    btn.disabled = false;
  },

  // ── Banners ───────────────────────────────────────────────────────────────

  _addBanner(c, type, icon, title, text) {
    const div = document.createElement("div");
    div.className = `di-banner ${type}`;
    div.innerHTML = `
      <div class="di-bi">${icon}</div>
      <div class="di-bb">
        <div class="di-bt">${this._e(title)}</div>
        <div class="di-bx">${text}</div>
      </div>`;
    c.querySelector("#di-banners").appendChild(div);
  },

  _renderBanners(c, data) {
    const wrap = c.querySelector("#di-banners");
    wrap.innerHTML = "";

    const fmt = iso => iso ? iso.replace("T", " ").replace(/\.\d+/, "") : "–";

    if (data.status === "shifted_found" || data.time_mismatch) {
      const dir  = (data.shift_days < 0) ? "trước" : "sau";
      const days = Math.abs(data.shift_days || 0);
      const reqStart = fmt(data.requested_time_range?.start);
      const reqEnd   = fmt(data.requested_time_range?.end);
      const actStart = fmt(data.actual_time_range?.start);
      const actEnd   = fmt(data.actual_time_range?.end);
      this._addBanner(c, "warn", "⚠️",
        `Không có log tại thời gian báo cáo — hiển thị log từ ${days} ngày ${dir}`,
        `Đã yêu cầu: <span class="di-br">${this._e(reqStart)} → ${this._e(reqEnd)}</span><br>
         Dữ liệu thực tế: <span class="di-br">${this._e(actStart)} → ${this._e(actEnd)}</span>`
      );
    }

    if (data.status === "not_found") {
      this._addBanner(c, "error", "🔍",
        "Không tìm thấy log",
        "Không có log nào cho user này trong khoảng thời gian yêu cầu."
      );
    }

    if (data.status === "policy_rejected") {
      this._addBanner(c, "error", "🛡️",
        "Query bị từ chối bởi policy",
        "Agent đã đề xuất một query nằm ngoài policy cho phép. Không có query không an toàn nào được thực thi."
      );
    }

    if (data.status === "query_error") {
      this._addBanner(c, "error", "💥",
        "Lỗi thực thi query",
        (data.warnings || []).join(". ") || "Đã xảy ra lỗi không mong muốn khi thực thi query."
      );
    }

    if (data.status === "partial") {
      this._addBanner(c, "warn", "⚡",
        "Kết quả không đầy đủ",
        (data.warnings?.filter(w => w.includes("rejected")) || ["Một số bước query bị từ chối."]).join(". ")
      );
    }

    // Correlation warning
    const corrWarn = (data.warnings || []).find(w => w.includes("timestamp proximity"));
    if (corrWarn) {
      this._addBanner(c, "info", "ℹ️",
        "Tương quan dựa trên thời gian",
        corrWarn
      );
    }
  },

  // ── Extended stats ────────────────────────────────────────────────────────

  _renderExtStats(c, _data) {
    c.querySelector("#di-stats-ext").style.display = "none";
  },

  // ── vis-timeline ──────────────────────────────────────────────────────────

  async _initVisTimeline(c) {
    await _loadVis();
    const el = c.querySelector("#di-vis-timeline");

    const groups = new vis.DataSet([
      { id: "tracking", content: "🟣 Tracking" },
      { id: "access",   content: "🔵 Access"   },
      { id: "vital",    content: "🟢 Vital"     },
    ]);
    const items = new vis.DataSet(this._buildVisItems(this._events));

    const options = {
      stack: true,
      showMajorLabels: true,
      showMinorLabels: true,
      orientation: "top",
      zoomMin: 1000,
      zoomMax: 1000 * 60 * 60 * 6,
      zoomKey: "ctrlKey",
      selectable: true,
      margin: { item: { horizontal: 4, vertical: 4 }, axis: 8 },
      groupOrder: "id",
      type: "box",
      groupHeightMode: "auto",
    };

    if (this._visTimeline) this._visTimeline.destroy();
    this._visTimeline = new vis.Timeline(el, items, groups, options);
    this._visItems    = items;

    this._visTimeline.on("click", (props) => {
      if (props.item == null) return;
      const item = items.get(props.item);
      if (!item) return;
      this._openDetail(item._raw);
    });

    this._visTimeline.fit({ animation: false });
  },

  _buildVisItems(events) {
    return events.map((e) => {
      const dt    = new Date(e.ts);
      const isErr = e.severity === "error" || e.severity === "fatal";
      let cls, label;

      if (e.source === "tracking") {
        cls   = "vis-tracking";
        label = e.raw?.event_id || e.title || "–";
      } else if (e.source === "access") {
        cls   = "vis-access";
        label = e.subtitle || e.raw?.page || "–";
      } else {
        cls   = isErr ? "vis-vital-error" : "vis-vital";
        label = e.subtitle || e.raw?.endpoint || "–";
      }

      const short = label.length > 38 ? label.slice(0, 38) + "…" : label;
      return {
        id: e.id,          // use event id string as vis item id
        group: e.source,
        start: dt,
        content: this._e(short),
        className: cls,
        _raw: e,
      };
    });
  },

  _applyFilters(c) {
    if (!this._visItems) return;
    const showTrack  = c.querySelector("#f-track").checked;
    const showAccess = c.querySelector("#f-access").checked;
    const showVital  = c.querySelector("#f-vital").checked;
    const q          = c.querySelector("#f-search").value.toLowerCase();

    const filtered = this._events.filter(e => {
      if (!showTrack  && e.source === "tracking") return false;
      if (!showAccess && e.source === "access")   return false;
      if (!showVital  && e.source === "vital")    return false;
      if (q) {
        const hay = [
          e.id, e.title, e.subtitle,
          e.raw?.event_id, e.raw?.page, e.raw?.endpoint,
          e.raw?.error_message, e.raw?.metadata,
          e.mapping?.event_name, e.mapping?.screen_name,
        ].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    this._visItems.clear();
    this._visItems.add(this._buildVisItems(filtered));
    this._visTimeline.fit({ animation: { duration: 300 } });
  },

  // ── Detail panel ──────────────────────────────────────────────────────────

  _openDetail(e) {
    if (!e) return;
    this._activeId = e.id;
    const isErr = e.severity === "error" || e.severity === "fatal";
    const tagCls = e.source === "tracking" ? "tracking"
      : e.source === "access" ? "access"
      : isErr ? "vital-error" : "vital";
    const tagLabel = e.source === "tracking" ? "TRACKING"
      : e.source === "access" ? "ACCESS"
      : isErr ? "VITAL ⚠" : "VITAL";

    document.getElementById("di-dp-title").textContent =
      e.source === "tracking" ? (e.raw?.event_id || e.title || "Tracking Event")
      : e.source === "access"  ? (e.subtitle || e.raw?.page || "Access Log")
      : (e.subtitle || e.raw?.endpoint || "Vital Event");

    let html = `<span class="di-dp-tag ${tagCls}">${tagLabel}</span>`;
    if (isErr) html += `<span class="di-dp-tag sev-warn">⚠ ERROR</span>`;

    // Common fields
    html += this._dpField("Event ID", e.id);
    html += this._dpField("Timestamp", e.timestamp, true);
    html += this._dpField("Title", e.title);
    if (e.subtitle) html += this._dpField("Subtitle", e.subtitle);

    // Source-specific
    if (e.source === "tracking") {
      html += this._dpField("Event Code", e.raw?.event_id || "–", true);
      if (e.raw?.previous_event_id)
        html += this._dpField("Previous Event", e.raw.previous_event_id, true);

      // Mapping
      const m = e.mapping;
      if (m) {
        const badgeCls = m.mapping_status === "exact" ? "exact" : m.mapping_status === "screen_fallback" ? "fallback" : "unknown";
        const badgeLabel = m.mapping_status === "exact" ? "exact" : m.mapping_status === "screen_fallback" ? "screen" : "unknown";
        html += `<div class="di-dp-field">
          <div class="di-dp-field-lbl">Event Mapping <span class="di-map-badge ${badgeCls}">${badgeLabel}</span></div>
          <div class="di-dp-field-val">${this._e(m.event_name || "–")}</div>
        </div>`;
        if (m.screen_code)
          html += this._dpField("Screen Code", m.screen_code, true);
        if (m.screen_name)
          html += this._dpField("Screen Name", m.screen_name);
      }

    } else if (e.source === "access") {
      html += this._dpField("Page", e.raw?.page || "–", true);
      if (e.raw?.app)     html += this._dpField("App", e.raw.app);
      if (e.raw?.traceID) html += this._dpField("TraceID", e.raw.traceID, true);
      if (e.raw?.status_code) html += this._dpField("HTTP Status", String(e.raw.status_code));

    } else {
      html += this._dpField("Endpoint", e.raw?.endpoint || "–", true);
      if (e.raw?.network_type) html += this._dpField("Network", e.raw.network_type);
      if (e.raw?.error_message)
        html += `<div class="di-dp-err">⚠ ${this._e(e.raw.error_message)}</div>`;
    }

    // Correlation
    const corr = e.correlation;
    if (corr && (corr.trace_id || corr.session_id || corr.device_id)) {
      html += `<hr class="di-dp-divider">`;
      if (corr.trace_id)   html += this._dpField("Trace ID",   corr.trace_id, true);
      if (corr.session_id) html += this._dpField("Session ID", corr.session_id, true);
      if (corr.device_id)  html += this._dpField("Device ID",  corr.device_id, true);
    }

    // Raw JSON
    html += `<hr class="di-dp-divider">`;
    html += `<div class="di-dp-field">
      <div class="di-dp-field-lbl">Raw</div>
      <div class="di-dp-json">${this._e(JSON.stringify(e.raw, null, 2))}</div>
    </div>`;

    // Session journey (tracking only)
    if (e.source === "tracking") {
      html += `<hr class="di-dp-divider">`;
      html += this._buildJourneyHtml(e);
    }

    document.getElementById("di-dp-body").innerHTML = html;

    // Wire journey card clicks
    if (e.source === "tracking") {
      document.querySelectorAll(".di-journey-card[data-id]").forEach(card => {
        card.onclick = () => {
          const eid = card.dataset.id;
          const raw = this._events.find(ev => ev.id === eid);
          if (raw) this._openDetail(raw);
        };
      });
    }

    document.getElementById("di-detail-panel").classList.add("open");
    document.getElementById("di-scrim").classList.add("open");
  },

  _dpField(label, val, mono = false) {
    const cls = mono ? " mono" : "";
    return `<div class="di-dp-field">
      <div class="di-dp-field-lbl">${this._e(label)}</div>
      <div class="di-dp-field-val${cls}">${this._e(String(val ?? ""))}</div>
    </div>`;
  },

  _buildJourneyHtml(activeEvent) {
    const trackingEvts = this._events.filter(e => e.source === "tracking");
    if (trackingEvts.length === 0) return "";

    const chips = trackingEvts.map((e, pos) => {
      const isActive   = e.id === activeEvent.id;
      const eventCode  = e.raw?.event_id || "–";
      const eventName  = e.mapping?.event_name || e.title || "";
      const showName   = eventName && eventName !== eventCode;
      const isErr      = e.severity === "error" || e.severity === "fatal";
      const arrow      = pos < trackingEvts.length - 1 ? `<div class="di-journey-arrow">↓</div>` : "";
      return `
        <div class="di-journey-step">
          <div class="di-journey-card${isActive ? " active" : ""}" data-id="${this._e(e.id)}">
            <div class="di-journey-card-hd">
              <span class="di-journey-card-id">${this._e(eventCode)}</span>
              <span class="di-journey-card-ts">${this._e(e.ts_str || "")}</span>
            </div>
            ${showName  ? `<div class="di-journey-card-name">${this._e(eventName)}</div>` : ""}
            ${isErr     ? `<div class="di-journey-card-err">⚠ ERROR</div>` : ""}
          </div>
          ${arrow}
        </div>`;
    }).join("");

    return `
      <div class="di-journey-title">📍 Session Journey (${trackingEvts.length} events)</div>
      <div class="di-journey-wrap"><div class="di-journey">${chips}</div></div>`;
  },

  // ── Insight ───────────────────────────────────────────────────────────────

  _renderInsight(c, data) {
    const box = c.querySelector("#di-insight");
    const ins = data.insight;

    if (!ins || (!ins.summary && !ins.likely_root_cause)) {
      box.innerHTML = `<div class="di-empty">Không có insight</div>`;
      return;
    }

    const confCls   = ins.confidence === "high" ? "high" : ins.confidence === "medium" ? "medium" : "low";
    const confLabel = ins.confidence === "high" ? "High Confidence" : ins.confidence === "medium" ? "Medium Confidence" : "Low Confidence";

    let html = "";

    // Summary + confidence badge
    if (ins.summary) {
      html += `<div class="di-insight-summary">📝 ${this._e(ins.summary)}</div>`;
    }

    if (ins.user_flow) {
      html += `<div class="di-insight-section">
        <h4>User Flow</h4>
        <p>${this._e(ins.user_flow)}</p>
      </div>`;
    }

    // likely_root_cause: temporarily hidden from UI
    // if (ins.likely_root_cause) {
    //   html += `<div class="di-insight-section">
    //     <h4>Likely Root Cause</h4>
    //     <p>${this._e(ins.likely_root_cause)}</p>
    //   </div>`;
    // }

    // Evidence (clickable)
    if (ins.evidence && ins.evidence.length > 0) {
      const evCards = ins.evidence.map(ev => `
        <div class="di-evidence-item" data-eid="${this._e(ev.event_id)}">
          <div>
            <div class="di-ev-id">${this._e(ev.event_id)}</div>
            <div class="di-ev-ts">${this._e(ev.timestamp || "")}</div>
          </div>
          <div class="di-ev-reason">${this._e(ev.reason)}</div>
        </div>`).join("");
      html += `<div class="di-insight-section">
        <h4>Evidence (${ins.evidence.length}) — click để xem trên timeline</h4>
        ${evCards}
      </div>`;
    }

    if (ins.recommendations && ins.recommendations.length > 0) {
      const items = ins.recommendations.map(r => `<li>${this._e(r)}</li>`).join("");
      html += `<div class="di-insight-section">
        <h4>Recommendations</h4>
        <ul class="di-insight-list">${items}</ul>
      </div>`;
    }

    // unknowns: temporarily hidden
    // if (ins.unknowns && ins.unknowns.length > 0) {
    //   const items = ins.unknowns.map(u => `<li>${this._e(u)}</li>`).join("");
    //   html += `<div class="di-insight-section">
    //     <h4>Unknowns</h4>
    //     <ul class="di-insight-list">${items}</ul>
    //   </div>`;
    // }

    // Query summary: temporarily hidden
    // const qs = data.query_summary;
    // if (qs) {
    //   html += `<div class="di-insight-section" style="margin-top:20px;padding-top:16px;border-top:1px solid #f1f5f9">
    //     <h4>Query Summary</h4>
    //     <p>
    //       Attempts: ${qs.attempts_used} ·
    //       Tables: ${(qs.tables_scanned || []).join(", ") || "–"} ·
    //       Strategy: ${qs.strategy || "–"} ·
    //       Correlation: ${(qs.correlation_basis || []).join(", ") || "–"}
    //     </p>
    //   </div>`;
    // }

    box.innerHTML = html;

    // Wire evidence item clicks
    box.querySelectorAll(".di-evidence-item[data-eid]").forEach(el => {
      el.onclick = () => this._onEvidenceClick(c, el.dataset.eid);
    });
  },

  _onEvidenceClick(c, eventId) {
    const ev = this._events.find(e => e.id === eventId);
    if (!ev) return;

    // Switch to timeline tab
    c.querySelectorAll(".di-tab").forEach(t => t.classList.toggle("active", t.dataset.pane === "timeline"));
    c.querySelectorAll(".di-tab-pane").forEach(p => p.classList.toggle("active", p.id === "pane-timeline"));
    if (this._visTimeline) {
      setTimeout(() => {
        this._visTimeline.redraw();
        this._visTimeline.setSelection([eventId]);
        try { this._visTimeline.focus(eventId, { animation: { duration: 400 } }); } catch(_) {}
      }, 50);
    }

    this._openDetail(ev);
  },

  // ── Reset ─────────────────────────────────────────────────────────────────

  _reset(c) {
    c.querySelector("#di-desc").value = "";
    const parsed = c.querySelector("#di-parsed");
    parsed.innerHTML = ""; parsed.style.display = "none";
    c.querySelector("#di-banners").innerHTML = "";
    c.querySelector("#di-result").style.display = "none";
    c.querySelector("#di-stats-ext").style.display = "none";
    if (this._visTimeline) { this._visTimeline.destroy(); this._visTimeline = null; }
    this._visItems = null;
    this._events = []; this._parsed = null; this._insight = null; this._activeId = null;
    c.querySelectorAll(".di-tab").forEach(t => t.classList.toggle("active", t.dataset.pane === "timeline"));
    c.querySelectorAll(".di-tab-pane").forEach(p => p.classList.toggle("active", p.id === "pane-timeline"));
    document.getElementById("di-detail-panel")?.classList.remove("open");
    document.getElementById("di-scrim")?.classList.remove("open");
    c.querySelector("#di-desc").focus();
  },

  // ── Helpers ───────────────────────────────────────────────────────────────

  _showParsed(c, p) {
    const wrap = c.querySelector("#di-parsed");
    wrap.style.display = "flex";
    const confCls = { high: "conf-high", medium: "conf-medium", low: "conf-low" }[p.confidence] || "conf-low";

    const chips = [
      p.zalopay_id    ? `<span class="di-chip uid">👤 ${this._e(p.zalopay_id)}</span>` : "",
      p.incident_time ? `<span class="di-chip time">🕐 ${this._e(p.incident_time.replace("T"," ").replace("+07:00",""))}</span>` : "",
      p.device        ? `<span class="di-chip dev">📱 ${this._e(p.device)}</span>` : "",
      p.os_version    ? `<span class="di-chip dev">🖥 ${this._e(p.os_version)}</span>` : "",
      p.app_version   ? `<span class="di-chip app">📦 ZaloPay ${this._e(p.app_version)}</span>` : "",
      `<span class="di-chip ${confCls}">Confidence: ${this._e(p.confidence)}</span>`,
    ];

    // Missing fields as warning chips
    (p.missing_fields || []).forEach(f => {
      chips.push(`<span class="di-chip miss">⚠ Missing: ${this._e(f)}</span>`);
    });

    wrap.innerHTML = chips.join("");
  },

  _e(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  },
};
