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

        /* ── Loading card ── */
        @keyframes di-spin  { to { transform: rotate(360deg); } }
        @keyframes di-pop   { 0%,100% { transform: scale(1); } 50% { transform: scale(1.25); } }
        @keyframes di-dot   { 0%,80%,100% { opacity: .2; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-4px); } }
        .di-loading { display: flex; flex-direction: column; align-items: center; justify-content: center;
          padding: 36px 20px; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
          margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); gap: 14px; }
        .di-loading-ring-wrap { position: relative; width: 64px; height: 64px; }
        .di-loading-ring { position: absolute; inset: 0; border-radius: 50%;
          border: 3px solid #e2e8f0;
          border-top-color: #6366f1;
          animation: di-spin 0.9s linear infinite; }
        .di-loading-icon { position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: center; font-size: 26px;
          animation: di-pop 1.8s ease-in-out infinite; }
        .di-loading-title { font-size: 14px; font-weight: 700; color: #0f172a; }
        .di-loading-sub   { font-size: 12px; color: #64748b; }
        .di-loading-dots  { display: flex; gap: 5px; }
        .di-loading-dots span { width: 6px; height: 6px; border-radius: 50%; background: #6366f1;
          animation: di-dot 1.2s ease-in-out infinite; }
        .di-loading-dots span:nth-child(2) { animation-delay: .2s; }
        .di-loading-dots span:nth-child(3) { animation-delay: .4s; }

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
        .di-evidence-item { padding: 8px 12px; border-radius: 8px; border: 1px solid #e2e8f0; background: #f8fafc; margin-bottom: 8px; }
        .di-evidence-item .di-ev-reason { font-size: 12px; color: #334155; line-height: 1.6; }

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

        <div id="di-loading" style="display:none"></div>
        <div id="di-banners"></div>

        <div id="di-result" style="display:none">
          <div class="di-stats">
            <div class="di-stat tracking"><div class="num" id="st-track">–</div><div class="lbl">🟣 Tracking Events</div></div>
            <div class="di-stat access">  <div class="num" id="st-access">–</div><div class="lbl">🔵 Access Logs</div></div>
            <div class="di-stat vital">   <div class="num" id="st-vital">–</div><div class="lbl">🟢 Vital Events</div></div>
          </div>
          <div class="di-stats-ext" id="di-stats-ext" style="display:none"></div>

          <div class="di-insight-card" id="di-insight">
            <div class="di-empty">Chạy điều tra để xem insight</div>
          </div>
        </div>
      </div>
    `;

    this._events    = [];
    this._parsed    = null;
    this._insight   = null;
    this._loadTimer = null;
    this._bind(container);
  },

  // ── Bindings ──────────────────────────────────────────────────────────────

  _DEMO_TICKET: `Mô tả lỗi: Truy cập app bị trắng màn hình
Thời gian xảy ra: 16:25 13/06/2026
UserID: 240919000001944`,

  _bind(c) {
    c.querySelector("#di-run").onclick   = () => this._run(c);
    c.querySelector("#di-reset").onclick = () => this._reset(c);
    c.querySelector("#di-desc").value    = this._DEMO_TICKET;
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
    this._events = [];

    this._showLoading(c, ["🤖","📄","🔎","💭"], "Đang đọc ticket…", "AI đang phân tích nội dung ticket");
    let parsed;
    try {
      const r = await fetch(`${API_PREFIX}/parse-ticket`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ ticket_text: desc }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      parsed = await r.json();
    } catch (e) {
      this._hideLoading(c);
      this._addBanner(c, "error", "❌", "Parse thất bại", this._e(e.message));
      btn.disabled = false; return;
    }
    this._hideLoading(c);

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

    this._showLoading(c, ["🔍","🧠","📊","⚡","🗂️","💡"], "Đang điều tra…", "Agent đang lập kế hoạch và query log");
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
      this._hideLoading(c);
      this._addBanner(c, "error", "❌", "Điều tra thất bại", this._e(e.message));
      btn.disabled = false; return;
    }
    this._hideLoading(c);

    this._events = data.events || [];

    // Stats bar
    c.querySelector("#st-track").textContent  = data.tracking_count ?? 0;
    c.querySelector("#st-access").textContent = data.access_count  ?? 0;
    c.querySelector("#st-vital").textContent  = data.vital_count   ?? 0;
    this._renderExtStats(c, data);

    // Banners
    this._renderBanners(c, data);

    c.querySelector("#di-result").style.display = "";
    this._renderInsight(c, data);

    btn.disabled = false;
  },

  // ── Loading ───────────────────────────────────────────────────────────────

  _showLoading(c, icons, title, subtitle) {
    this._stopLoading();
    const wrap = c.querySelector("#di-loading");
    let idx = 0;
    const render = () => {
      wrap.innerHTML = `
        <div class="di-loading">
          <div class="di-loading-ring-wrap">
            <div class="di-loading-ring"></div>
            <div class="di-loading-icon">${icons[idx]}</div>
          </div>
          <div class="di-loading-title">${this._e(title)}</div>
          <div class="di-loading-sub">${this._e(subtitle)}</div>
          <div class="di-loading-dots"><span></span><span></span><span></span></div>
        </div>`;
    };
    render();
    wrap.style.display = "";
    this._loadTimer = setInterval(() => {
      idx = (idx + 1) % icons.length;
      const el = wrap.querySelector(".di-loading-icon");
      if (el) el.textContent = icons[idx];
    }, 700);
  },

  _stopLoading() {
    if (this._loadTimer) { clearInterval(this._loadTimer); this._loadTimer = null; }
  },

  _hideLoading(c) {
    this._stopLoading();
    const wrap = c.querySelector("#di-loading");
    wrap.style.display = "none";
    wrap.innerHTML = "";
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

    // Evidence (read-only)
    if (ins.evidence && ins.evidence.length > 0) {
      const evCards = ins.evidence.map(ev => `
        <div class="di-evidence-item">
          <div class="di-ev-reason">${this._e(ev.reason)}</div>
        </div>`).join("");
      html += `<div class="di-insight-section">
        <h4>Evidence (${ins.evidence.length})</h4>
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
  },

  // ── Reset ─────────────────────────────────────────────────────────────────

  _reset(c) {
    this._hideLoading(c);
    c.querySelector("#di-desc").value = "";
    const parsed = c.querySelector("#di-parsed");
    parsed.innerHTML = ""; parsed.style.display = "none";
    c.querySelector("#di-banners").innerHTML = "";
    c.querySelector("#di-result").style.display = "none";
    c.querySelector("#di-stats-ext").style.display = "none";
    this._events = []; this._parsed = null; this._insight = null;
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
