// Claude Analytics — Tufte/Bloomberg dense dashboard.
// 5 tabs: Map (zoomable treemap) / Activity (small multiples) / Sessions / Health / Ledger.

const ETAG = {};
async function jget(path) {
  const headers = {};
  if (ETAG[path]) headers["If-None-Match"] = ETAG[path];
  const r = await fetch(path, { headers });
  if (r.status === 304) return undefined;
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  const tag = r.headers.get("ETag");
  if (tag) ETAG[path] = tag;
  return await r.json();
}

// ===== Sparkline (pure SVG) ==========================================
function sparkline(values, opts = {}) {
  const w = opts.w || 80, h = opts.h || 18, pad = 1;
  const v = values || [];
  if (!v.length) return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const max = Math.max(...v, 0.0001);
  const min = Math.min(...v, 0);
  const range = (max - min) || 1;
  const xs = v.map((_, i) => pad + (i * (w - 2*pad)) / Math.max(1, v.length - 1));
  const ys = v.map(d => h - pad - ((d - min) / range) * (h - 2*pad));
  let area = `M ${xs[0]} ${h-pad}`;
  let line = `M ${xs[0]} ${ys[0]}`;
  for (let i = 1; i < v.length; i++) {
    line += ` L ${xs[i]} ${ys[i]}`;
    area += ` L ${xs[i]} ${ys[i]}`;
  }
  area += ` L ${xs[xs.length-1]} ${h-pad} Z`;
  const lastX = xs[xs.length-1], lastY = ys[ys.length-1];
  return `<svg class="spark" width="${w}" height="${h}">
    <path class="area" d="${area}"></path>
    <path d="${line}"></path>
    <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="1.5"></circle>
  </svg>`;
}

// ===== Mini horizontal bar list ======================================
function miniBars(items, opts = {}) {
  // items: [{label, value, sub?}]
  if (!items || !items.length) return `<div class="muted">no data</div>`;
  const max = Math.max(...items.map(d => d.value), 0.0001);
  const maxW = opts.maxW || 110;
  return items.map(d => {
    const w = Math.max(2, (d.value / max) * maxW);
    const labelText = d.label || "";
    const subText = d.sub != null ? d.sub : fmtUsd(d.value);
    return `<div class="mbar" title="${escapeAttr(labelText)} — ${escapeAttr(String(subText))}">
      <div class="label-text">${escapeHtml(labelText)}</div>
      <div class="bar" style="width:${w}px"></div>
      <div class="num-text">${escapeHtml(String(subText))}</div>
    </div>`;
  }).join("");
}

// ===== Treemap (squarified, custom — no D3 dep) =======================
// Squarified treemap layout. Recursive within zoom level.
function squarify(items, x, y, w, h) {
  // items: array of {value, ...}; sums to area = w*h proportionally
  const total = items.reduce((s, d) => s + d.value, 0);
  if (total <= 0) return [];
  const out = [];
  const rects = items.map(d => ({ ...d, _v: d.value }));
  let i = 0;
  let curX = x, curY = y, curW = w, curH = h;
  while (i < rects.length) {
    const remaining = rects.slice(i);
    const rt = remaining.reduce((s, d) => s + d._v, 0);
    if (rt <= 0) break;
    const horiz = curW >= curH;
    const stripSize = horiz ? curH : curW;
    let row = [], rowSum = 0, bestScore = Infinity;
    let j = i;
    while (j < rects.length) {
      const next = rects[j]._v;
      const trial = [...row, rects[j]];
      const trialSum = rowSum + next;
      const stripLen = (trialSum / rt) * (horiz ? curW : curH);
      const ratios = trial.map(d => {
        const aw = horiz ? stripLen : stripSize;
        const ah = horiz ? stripSize * (d._v / trialSum) : stripLen * (d._v / trialSum);
        return Math.max(aw / ah, ah / aw);
      });
      const score = Math.max(...ratios);
      if (score > bestScore) break;
      bestScore = score;
      row = trial;
      rowSum = trialSum;
      j++;
    }
    if (!row.length) row = [rects[i]], rowSum = rects[i]._v, j = i + 1;
    const stripLen = (rowSum / rt) * (horiz ? curW : curH);
    let off = 0;
    for (const d of row) {
      const frac = d._v / rowSum;
      if (horiz) {
        out.push({ ...d, x: curX, y: curY + off, w: stripLen, h: stripSize * frac });
        off += stripSize * frac;
      } else {
        out.push({ ...d, x: curX + off, y: curY, w: stripSize * frac, h: stripLen });
        off += stripSize * frac;
      }
    }
    if (horiz) { curX += stripLen; curW -= stripLen; }
    else       { curY += stripLen; curH -= stripLen; }
    i = j;
  }
  return out;
}

function renderTreemap(host, node, w, h, onClick, hooks = {}) {
  host.innerHTML = "";
  if (!node || !node.children || !node.children.length) {
    host.innerHTML = `<div class="muted" style="padding:24px">No children to drill into.</div>`;
    return;
  }
  const items = [...node.children].sort((a, b) => b.value - a.value);
  const rects = squarify(items, 2, 2, w - 4, h - 4);
  for (const r of rects) {
    const el = document.createElement("div");
    el.className = "tm-rect" + (r.is_error ? " err" : "");
    el.dataset.kind = r.kind || "tool";
    el.style.left = r.x + "px";
    el.style.top = r.y + "px";
    el.style.width = r.w + "px";
    el.style.height = r.h + "px";
    const fits = r.w > 60 && r.h > 24;
    if (fits) {
      let sub = "";
      if (r.calls) sub = ` · ${r.calls} calls`;
      else if (r.kind === "bash_call") sub = r.exit_code != null ? ` · exit ${r.exit_code}` : "";
      el.innerHTML = `<div class="tm-name">${escapeHtml(shortenName(r.name, r.kind))}</div>
        <div class="tm-val">${fmtUsd(r.value)}${sub}</div>`;
    }
    el.title = `${r.name}\n${fmtUsd(r.value)}`;
    // Lazy-loadable: Bash + Read/Write/Edit + residual "other"
    const FILE_TOOLS = ["Read","Write","Edit","MultiEdit"];
    const isLazyBash = (r.kind === "tool" && r.name === "Bash" && (!r.children || !r.children.length));
    const isLazyFile = (r.kind === "tool" && FILE_TOOLS.includes(r.name) && (!r.children || !r.children.length));
    const isLazyResidual = (r.kind === "other" && r.name === "(reasoning + cache)" && (!r.children || !r.children.length));
    // Also lazy: file_path can be drilled even though backend pre-fills calls
    const hasChildren = r.children && r.children.length;
    if (hasChildren || isLazyBash || isLazyFile || isLazyResidual) {
      el.style.cursor = "pointer";
      el.addEventListener("click", () => onClick(r));
    } else {
      el.style.cursor = "default";
    }
    // Tooltip on bash_call + residual_msg + file_call + file_path leaves
    if (["bash_call","residual_msg","file_call","file_path"].includes(r.kind) && hooks.onTooltip) {
      el.addEventListener("mousemove", (e) => hooks.onTooltip(e, r));
      el.addEventListener("mouseleave", () => hooks.onTooltip(null));
    }
    host.appendChild(el);
  }
}

function shortenName(n, kind) {
  if (!n) return "";
  if (kind === "project") return n.replace(/^\/Users\/[^/]+\//, "~/");
  return n;
}

// ===== Helpers =======================================================
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
function shortPath(p) { return p ? p.replace(/^\/Users\/[^/]+\//, "~/") : ""; }
function fmtUsd(v) { return v == null ? "—" : "$" + Number(v).toLocaleString(undefined,{maximumFractionDigits:2}); }
function fmtPct(v) { return v == null ? "—" : (Number(v)*100).toFixed(1) + "%"; }
function fmtNum(v) { return v == null ? "—" : Number(v).toLocaleString(); }
function fmtBytes(v) {
  if (v == null) return "—";
  const u = ["B","KB","MB","GB"]; let i = 0;
  while (v >= 1024 && i < u.length-1) { v /= 1024; i++; }
  return v.toFixed(1) + u[i];
}
function fmtDur(ms) {
  if (ms == null) return "—";
  const s = ms/1000;
  if (s < 60) return s.toFixed(1)+"s";
  if (s < 3600) return (s/60).toFixed(1)+"m";
  return (s/3600).toFixed(1)+"h";
}
function fmtDate(ms) {
  if (ms == null) return "—";
  return new Date(ms).toLocaleString(undefined, { month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" });
}

// ===== Alpine factory ================================================
function dashboard() {
  return {
    tabs: ["Map","Activity","Sessions","Health","Ledger"],
    active: "Map",

    // Filters
    f: { project: "", session_id: "", task_id: "", tool: "", since: "", until: "" },
    options: { projects: [], sessions: [], tools: [], tasks: [] },
    sessionSearch: "",
    taskSearch: "",
    showSessionPicker: false,
    showTaskPicker: false,

    // Treemap state
    treemapData: null,
    zoomPath: [],   // array of nodes leading to current view
    zoomNode: null,
    treemapLoading: false,

    // Activity data
    activity: null,
    bashPanel: null,    // currently selected program
    bashPanelData: null,

    // Sessions data
    sessionsData: null,
    sessionPanel: null,
    sessionPanelData: null,

    // Health
    healthData: null,
    heatmapData: null,

    // Ledger
    ledgerData: null,
    ledgerRange: "30d",

    // Overview KPIs
    overview: null,
    lastUpdate: "",

    async init() {
      await this.loadOptions();
      await this.loadAll();
      this.$watch("active", () => this.onTabChange());
      this.$watch("f", () => { this.invalidate(); this.loadAll(); }, { deep: true });
      window.addEventListener("resize", () => this.maybeRedrawTreemap());
      setInterval(() => this.loadAll(), 60_000);
    },

    qs(extra = {}) {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(this.f)) if (v) p.set(k, v);
      for (const [k, v] of Object.entries(extra)) if (v) p.set(k, v);
      const s = p.toString();
      return s ? "?" + s : "";
    },

    invalidate() { for (const k of Object.keys(ETAG)) delete ETAG[k]; },

    async loadOptions() {
      const o = await jget("/api/filter-options");
      if (o) this.options = o;
    },

    activeFilters() {
      const out = [];
      if (this.f.project) out.push({ k: "project", label: shortPath(this.f.project) });
      if (this.f.session_id) out.push({ k: "session_id", label: "session " + this.f.session_id.slice(0,8) });
      if (this.f.task_id) {
        const t = this.options.tasks.find(t => t.id === this.f.task_id);
        out.push({ k: "task_id", label: "task: " + (t ? (t.description || "").slice(0,30) : this.f.task_id.slice(0,12)) });
      }
      if (this.f.tool) out.push({ k: "tool", label: "tool=" + this.f.tool });
      if (this.f.since) out.push({ k: "since", label: "≥ " + this.f.since });
      if (this.f.until) out.push({ k: "until", label: "≤ " + this.f.until });
      return out;
    },
    clearFilter(k) { this.f[k] = ""; },
    clearAll() { for (const k of Object.keys(this.f)) this.f[k] = ""; this.sessionSearch = ""; this.taskSearch = ""; },
    pickSession(id) { this.f.session_id = id; this.showSessionPicker = false; },
    pickTask(id) { this.f.task_id = id; this.showTaskPicker = false; },
    filteredSessions() {
      const q = this.sessionSearch.toLowerCase();
      let list = this.options.sessions;
      if (this.f.project) list = list.filter(s => s.project === this.f.project);
      if (q) list = list.filter(s => s.id.toLowerCase().includes(q) || (s.project||"").toLowerCase().includes(q));
      return list.slice(0, 100);
    },
    filteredTasks() {
      const q = this.taskSearch.toLowerCase();
      let list = this.options.tasks;
      if (this.f.project) list = list.filter(t => t.project === this.f.project);
      if (q) list = list.filter(t => (t.description||"").toLowerCase().includes(q) || (t.agent_type||"").toLowerCase().includes(q));
      return list.slice(0, 100);
    },

    async loadAll() {
      const q = this.qs();
      try {
        const ov = await jget("/api/overview" + q);
        if (ov) this.overview = ov;
        this.lastUpdate = "updated " + new Date().toLocaleTimeString();
        this.onTabChange();
      } catch (e) {
        this.lastUpdate = "error: " + e.message;
        console.error(e);
      }
    },

    onTabChange() {
      switch (this.active) {
        case "Map": this.loadTreemap(); break;
        case "Activity": this.loadActivity(); break;
        case "Sessions": this.loadSessionsView(); break;
        case "Health": this.loadHealthView(); break;
        case "Ledger": this.loadLedgerView(); break;
      }
    },

    // ---------- Map (treemap) ----------
    async loadTreemap() {
      this.treemapLoading = true;
      const q = this.qs();
      const j = await jget("/api/treemap" + q);
      this.treemapLoading = false;
      if (j) {
        this.treemapData = j;
        this.zoomPath = [{ name: "All", node: j.root }];
        this.zoomNode = j.root;
        this.$nextTick(() => this.drawTreemap());
      } else {
        this.$nextTick(() => this.drawTreemap());
      }
    },
    async zoomTo(rect) {
      // Lazy-load Bash sub-tree on first click
      if (rect.kind === "tool" && rect.name === "Bash" && (!rect.children || !rect.children.length)) {
        const scope = this.currentDrillScope();
        if (scope) {
          const params = new URLSearchParams({ scope_kind: scope.kind, scope_id: scope.id });
          if (this.f.project) params.set("project", this.f.project);
          if (this.f.since) params.set("since", this.f.since);
          if (this.f.until) params.set("until", this.f.until);
          try {
            const j = await jget("/api/treemap/bash?" + params.toString());
            if (j?.root?.children?.length) rect.children = j.root.children;
          } catch (e) { console.error("bash drill failed", e); }
        }
      }
      // Lazy-load Read/Write/Edit/MultiEdit sub-tree on first click
      if (rect.kind === "tool" && ["Read","Write","Edit","MultiEdit"].includes(rect.name) && (!rect.children || !rect.children.length)) {
        const scope = this.currentDrillScope();
        if (scope) {
          const params = new URLSearchParams({ tool: rect.name, scope_kind: scope.kind, scope_id: scope.id });
          if (this.f.project) params.set("project", this.f.project);
          try {
            const j = await jget("/api/treemap/file_tool?" + params.toString());
            if (j?.root?.children?.length) rect.children = j.root.children;
          } catch (e) { console.error("file tool drill failed", e); }
        }
      }
      // Lazy-load (reasoning + cache) residual breakdown
      if (rect.kind === "other" && (!rect.children || !rect.children.length)) {
        const scope = this.currentDrillScope();
        if (scope) {
          const params = new URLSearchParams({ scope_kind: scope.kind, scope_id: scope.id });
          if (this.f.project) params.set("project", this.f.project);
          try {
            const j = await jget("/api/treemap/residual?" + params.toString());
            if (j?.root?.children?.length) {
              rect.children = j.root.children;
              rect._breakdown = j.root._breakdown;
            }
          } catch (e) { console.error("residual drill failed", e); }
        }
      }
      this.zoomPath.push({ name: rect.name, node: rect });
      this.zoomNode = rect;
      this.$nextTick(() => this.drawTreemap());
    },
    currentDrillScope() {
      // Walk zoomPath from deepest to shallowest, prefer task over session
      for (let i = this.zoomPath.length - 1; i >= 0; i--) {
        const n = this.zoomPath[i].node;
        if (n.kind === "task" && n.id) return { kind: "task", id: n.id };
        if (n.kind === "session" && n.full_id) return { kind: "session", id: n.full_id };
      }
      return null;
    },
    zoomCrumb(idx) {
      this.zoomPath = this.zoomPath.slice(0, idx + 1);
      this.zoomNode = this.zoomPath[idx].node;
      this.$nextTick(() => this.drawTreemap());
    },
    maybeRedrawTreemap() {
      if (this.active === "Map" && this.zoomNode) this.drawTreemap();
    },
    drawTreemap() {
      const host = document.getElementById("treemap-root");
      if (!host || !this.zoomNode) return;
      const w = host.clientWidth, h = host.clientHeight;
      const FILE_TOOLS = ["Read","Write","Edit","MultiEdit"];
      const onClick = (rect) => {
        const isLazyBash = (rect.kind === "tool" && rect.name === "Bash" && (!rect.children || !rect.children.length));
        const isLazyFile = (rect.kind === "tool" && FILE_TOOLS.includes(rect.name) && (!rect.children || !rect.children.length));
        const isLazyResidual = (rect.kind === "other" && rect.name === "(reasoning + cache)" && (!rect.children || !rect.children.length));
        if ((rect.children && rect.children.length) || isLazyBash || isLazyFile || isLazyResidual) {
          this.zoomTo(rect);
        } else {
          this.applyLeafFilter(rect);
        }
      };
      const onTooltip = (e, r) => this.showTooltip(e, r);
      renderTreemap(host, this.zoomNode, w, h, onClick, { onTooltip });
    },
    showTooltip(e, r) {
      const tt = document.getElementById("tm-tooltip");
      if (!tt) return;
      if (!e || !r) { tt.style.display = "none"; return; }
      let title, lines = [];
      if (r.kind === "bash_call") {
        title = r.full_command || r.name || "";
        if (r.exit_code != null) lines.push(`exit ${r.exit_code}`);
        if (r.duration_ms) lines.push(fmtDur(r.duration_ms));
        if (r.result_bytes) lines.push(fmtBytes(r.result_bytes));
      } else if (r.kind === "file_call") {
        title = r.full_path || r.name;
        if (r.result_lines) lines.push(`${r.result_lines} lines`);
        if (r.result_bytes) lines.push(fmtBytes(r.result_bytes));
        if (r.duration_ms) lines.push(fmtDur(r.duration_ms));
        if (r.user_modified) lines.push("user modified");
        if (r.truncated) lines.push("truncated");
        if (r.is_error) lines.push("ERROR");
      } else if (r.kind === "file_path") {
        title = r.full_path || r.name;
        if (r.calls) lines.push(`${r.calls} calls`);
        if (r.errors) lines.push(`${r.errors} errors`);
        if (r.user_modified) lines.push(`${r.user_modified} user-mod`);
        if (r.total_lines) lines.push(`${fmtNum(r.total_lines)} lines total`);
        if (r.total_bytes) lines.push(fmtBytes(r.total_bytes));
      } else if (r.kind === "residual_msg") {
        title = r.name;
        if (r.subtype === "cache_read") {
          lines.push(`${fmtNum(r.cache_read_tokens)} cache-read tokens`);
          lines.push(`${fmtNum(r.input_tokens)} fresh input`);
          lines.push(`hit ${(r.hit_ratio*100).toFixed(1)}%`);
        } else if (r.subtype === "cache_creation") {
          lines.push(`${fmtNum(r.cache_creation_tokens)} cache-creation tokens`);
        } else if (r.subtype === "thinking") {
          lines.push(`${fmtNum(r.thinking_tokens)} thinking tokens`);
          lines.push(`${fmtNum(r.output_tokens)} total output`);
          lines.push(`${(r.ratio*100).toFixed(0)}% of output is thinking`);
        } else if (r.subtype === "text_only") {
          lines.push(`${fmtNum(r.output_tokens)} output tokens`);
          if (r.thinking_tokens) lines.push(`${fmtNum(r.thinking_tokens)} thinking`);
        }
        if (r.model) lines.push(r.model);
      } else {
        title = r.name;
      }
      lines.push(fmtUsd(r.value));
      tt.innerHTML = `<div class="mono" style="max-width:480px;word-break:break-all">${escapeHtml(title)}</div>
        <div class="subtle" style="margin-top:4px;font-size:11px">${escapeHtml(lines.join(" · "))}</div>`;
      tt.style.display = "block";
      const pad = 12;
      const x = Math.min(e.clientX + pad, window.innerWidth - 500);
      const y = Math.min(e.clientY + pad, window.innerHeight - 60);
      tt.style.left = x + "px";
      tt.style.top = y + "px";
    },
    applyLeafFilter(rect) {
      // Leaf click → set most-specific filter
      if (rect.kind === "tool") {
        this.f.tool = rect.name;
      } else if (rect.kind === "task") {
        // Find task_id by description match
        const t = this.options.tasks.find(t => rect.name.includes((t.description||"").slice(0,30)));
        if (t) this.f.task_id = t.id;
      } else if (rect.kind === "session" && rect.full_id) {
        this.f.session_id = rect.full_id;
      } else if (rect.kind === "project") {
        this.f.project = rect.name;
      }
    },

    // ---------- Activity (small multiples) ----------
    async loadActivity() {
      if (this.activity) {
        // refresh on filter change
      }
      const q = this.qs();
      const [tools, programs, categories, files, search, workflow, bash] = await Promise.all([
        jget("/api/tools" + q),
        jget("/api/bash/programs" + q),
        jget("/api/bash/categories" + q),
        jget("/api/files" + q),
        jget("/api/search" + q),
        jget("/api/workflow" + q),
        jget("/api/bash" + q),
      ]);
      const upd = (cur, next) => next === undefined ? cur : next;
      this.activity = {
        tools: upd(this.activity?.tools, tools),
        programs: upd(this.activity?.programs, programs),
        categories: upd(this.activity?.categories, categories),
        files: upd(this.activity?.files, files),
        search: upd(this.activity?.search, search),
        workflow: upd(this.activity?.workflow, workflow),
        bash: upd(this.activity?.bash, bash),
      };
      this.$nextTick(() => this.renderActivity());
    },
    renderActivity() {
      const a = this.activity;
      if (!a) return;
      // Tile: top tools
      this.$el.querySelector("#tile-tools").innerHTML = miniBars(
        (a.tools?.tools || []).slice(0, 6).map(t => ({ label: t.tool_name, value: t.cost, sub: fmtUsd(t.cost) }))
      );
      // Tile: top bash programs
      this.$el.querySelector("#tile-programs").innerHTML = miniBars(
        (a.programs?.programs || []).slice(0, 8).map(p => ({ label: p.program, value: p.calls, sub: p.calls + (p.errors?` ✗${p.errors}`:'') }))
      );
      // Tile: bash categories (stacked one-row)
      this.$el.querySelector("#tile-categories").innerHTML = this.renderCategoryStrip(a.categories?.categories || []);
      // Tile: top git subcommands (drill into top program if it's git, else show top program subcommands)
      this.renderTopSubcommandsTile(a.programs?.programs || []);
      // Tile: top files
      this.$el.querySelector("#tile-files").innerHTML = miniBars(
        (a.files?.hotspots || []).slice(0, 6).map(f => ({
          label: (f.file_path || "").split("/").slice(-2).join("/"),
          value: (f.reads||0) + (f.edits||0) + (f.writes||0),
          sub: `${f.reads}r ${f.edits}e ${f.writes}w`,
        }))
      );
      // Tile: bigrams
      this.$el.querySelector("#tile-bigrams").innerHTML = miniBars(
        (a.workflow?.bigrams || []).slice(0, 6).map(b => ({ label: `${b.prev_tool}→${b.next_tool}`, value: b.n, sub: b.n }))
      );
      // Tile: errors per tool
      const errTools = (a.tools?.tools || []).filter(t => t.errors > 0).slice(0, 6).map(t => ({ label: t.tool_name, value: t.errors, sub: t.errors }));
      this.$el.querySelector("#tile-errors").innerHTML = miniBars(errTools);
      // Tile: web search top queries
      this.$el.querySelector("#tile-websearch").innerHTML = miniBars(
        (a.search?.web_search || []).slice(0, 5).map(q => ({ label: q.query, value: q.n, sub: q.n }))
      );
      // Tile: web fetch domains
      this.$el.querySelector("#tile-webfetch").innerHTML = miniBars(
        (a.search?.web_fetch || []).slice(0, 5).map(u => {
          let host = "";
          try { host = new URL(u.url).host; } catch {}
          return { label: host || u.url.slice(0, 30), value: u.n, sub: u.n };
        })
      );
      // Tile: permission modes
      const pm = a.workflow?.permission_modes || [];
      const totalPm = pm.reduce((s, x) => s + x.n, 0) || 1;
      this.$el.querySelector("#tile-perm").innerHTML = pm.map(x =>
        `<div class="mbar"><div class="label-text">${escapeHtml(x.mode)}</div>
          <div class="bar" style="width:${(x.n/totalPm)*110}px"></div>
          <div class="num-text">${fmtPct(x.n/totalPm)}</div></div>`
      ).join("") || `<div class="muted">no data</div>`;
      // Tile: bash sandbox-disabled / sudo / pipes — health indicators
      const summary = a.bash?.summary || {};
      this.$el.querySelector("#tile-bash-health").innerHTML = `
        <div class="mbar"><div class="label-text">errors</div><div class="bar err" style="width:${Math.min((summary.errors||0)/30, 110)}px;background:var(--error)"></div><div class="num-text">${summary.errors||0}</div></div>
        <div class="mbar"><div class="label-text">backgrounded</div><div class="bar muted" style="width:${Math.min((summary.backgrounds||0)/2, 110)}px"></div><div class="num-text">${summary.backgrounds||0}</div></div>
        <div class="mbar"><div class="label-text">sandbox-off</div><div class="bar" style="width:${Math.min((summary.sandbox_off||0)*10, 110)}px;background:var(--error)"></div><div class="num-text">${summary.sandbox_off||0}</div></div>`;
      // Tile: grep patterns
      this.$el.querySelector("#tile-grep").innerHTML = miniBars(
        (a.search?.grep || []).slice(0, 5).map(g => ({ label: g.pattern, value: g.n, sub: g.n }))
      );
      // Tile: tools by avg latency
      const lat = (a.tools?.tools || []).filter(t => t.avg_ms).sort((a,b) => b.avg_ms - a.avg_ms).slice(0, 6);
      this.$el.querySelector("#tile-latency").innerHTML = miniBars(
        lat.map(t => ({ label: t.tool_name, value: t.avg_ms, sub: fmtDur(t.avg_ms) }))
      );
    },
    renderCategoryStrip(cats) {
      const total = cats.reduce((s, c) => s + c.calls, 0) || 1;
      const colors = ["#fbbf24","#b88819","#7a5e0f","#3a2c08","#737373","#525252","#a1a1aa","#262626"];
      const segs = cats.map((c, i) => `<div title="${escapeAttr(c.category)}: ${c.calls}" style="background:${colors[i%colors.length]};flex:${c.calls};height:14px"></div>`).join("");
      const labels = cats.slice(0, 5).map((c, i) => `<div style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-2)"><span style="width:8px;height:8px;background:${colors[i%colors.length]};display:inline-block"></span>${escapeHtml(c.category)} ${c.calls}</div>`).join("");
      return `<div style="display:flex;width:100%;border-radius:2px;overflow:hidden;margin-bottom:8px">${segs}</div>
              <div style="display:flex;flex-wrap:wrap;gap:6px">${labels}</div>`;
    },
    renderTopSubcommandsTile(programs) {
      const top = programs.find(p => p.program === "git") || programs[0];
      if (!top) return;
      this.$el.querySelector("#tile-subs-title").textContent = `Top ${top.program} subcommands`;
      jget(`/api/bash/program/${top.program}` + this.qs()).then(j => {
        if (!j) return;
        const subs = j.subcommands.filter(s => s.subcommand !== "(none)").slice(0, 6);
        this.$el.querySelector("#tile-subs").innerHTML = miniBars(
          subs.map(s => ({ label: s.subcommand, value: s.calls, sub: s.calls + (s.errors?` ✗${s.errors}`:'') }))
        );
      });
    },

    // Bash drill-down side panel
    async openBashPanel(program) {
      this.bashPanel = program;
      this.bashPanelData = null;
      const j = await jget(`/api/bash/program/${program}` + this.qs());
      this.bashPanelData = j;
    },
    closeBashPanel() { this.bashPanel = null; this.bashPanelData = null; },

    // ---------- Sessions ----------
    async loadSessionsView() {
      const q = this.qs();
      const j = await jget("/api/sessions" + q);
      if (j) this.sessionsData = j;
    },
    async openSessionPanel(sid) {
      this.sessionPanel = sid;
      this.sessionPanelData = null;
      const j = await jget(`/api/sessions/${sid}`);
      this.sessionPanelData = j;
    },
    closeSessionPanel() { this.sessionPanel = null; this.sessionPanelData = null; },

    // ---------- Health ----------
    async loadHealthView() {
      const q = this.qs();
      const [h, hm] = await Promise.all([jget("/api/health" + q), jget("/api/heatmap" + q)]);
      if (h) this.healthData = h;
      if (hm) this.heatmapData = hm;
      this.$nextTick(() => this.renderHealth());
    },
    renderHealth() {
      this.renderHeatmap();
      const tl = this.healthData?.api_errors_timeline || [];
      const host = this.$el.querySelector("#health-errors-spark");
      if (host) {
        const vals = tl.slice().reverse().map(r => r.errors);
        host.innerHTML = sparkline(vals, { w: 600, h: 40 });
      }
    },
    renderHeatmap() {
      const cells = this.heatmapData?.cells || [];
      const grid = this.$el.querySelector("#heatmap-grid");
      if (!grid) return;
      grid.innerHTML = "";
      const max = Math.max(0.0001, ...cells.map(c => c.cost));
      const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
      const e = (tag, text="", cls="") => { const x = document.createElement(tag); if (text) x.textContent = text; if (cls) x.className = cls; return x; };
      grid.appendChild(e("div"));
      for (let h = 0; h < 24; h++) grid.appendChild(e("div", String(h), "subtle"));
      for (let d = 0; d < 7; d++) {
        grid.appendChild(e("div", days[d], "subtle"));
        for (let h = 0; h < 24; h++) {
          const c = cells.find(x => x.dow === d && x.hour === h);
          const v = c ? c.cost : 0;
          const cell = e("div", v ? "$"+v.toFixed(0) : "", "heat-cell");
          cell.style.background = `rgba(251,191,36,${0.04 + (v/max)*0.85})`;
          cell.title = `${days[d]} ${h}:00 — $${v.toFixed(2)}`;
          grid.appendChild(cell);
        }
      }
    },

    // ---------- Ledger ----------
    async loadLedgerView() {
      const path = "/api/ledger?range=" + this.ledgerRange + (this.qs() ? "&" + this.qs().slice(1) : "");
      delete ETAG[path];
      const j = await jget(path);
      if (j) this.ledgerData = j;
      this.$nextTick(() => this.renderLedger());
    },
    setLedgerRange(r) { this.ledgerRange = r; this.loadLedgerView(); },
    renderLedger() {
      const dl = this.ledgerData?.daily || [];
      // Big sparkline of cost
      const host = this.$el.querySelector("#ledger-spark");
      if (host) {
        const vals = dl.map(r => r.cost);
        host.innerHTML = sparkline(vals, { w: host.clientWidth - 8 || 800, h: 80 });
      }
      // Model bars
      const bm = this.ledgerData?.by_model || [];
      this.$el.querySelector("#ledger-models").innerHTML = miniBars(
        bm.map(r => ({ label: r.model || "(unknown)", value: r.cost, sub: fmtUsd(r.cost) })),
        { maxW: 240 }
      );
    },

    // ---------- Helpers exposed to template ----------
    sparkline, miniBars, escapeHtml, shortPath, fmtUsd, fmtPct, fmtNum, fmtBytes, fmtDur, fmtDate,
  };
}
