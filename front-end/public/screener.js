// Screener tab — talks to the on-demand screener service via /api/screener/*.
//
// The filter builder and column chooser are generated from GET /indicators,
// so indicators added on the backend show up here with no front-end change.
// Rules are edited as one flat "all of" list; saved screens keep them in the
// backend's rule-tree shape ({all: [...]}).

const SCREENER_REFRESH_POLL_MS = 2000;
const SCREENER_RESULT_LIMIT = 500;

const NUMBER_OPS = [
  { op: ">=", label: "≥" },
  { op: ">", label: ">" },
  { op: "<=", label: "≤" },
  { op: "<", label: "<" },
  { op: "between", label: "between" },
];
const BOOL_OPS = [
  { op: "is_true", label: "is yes" },
  { op: "is_false", label: "is no" },
];

// Render one indicator value by its unit/kind, e.g. "18.8%", "+3.1 pp", "0.42x", "12,345".
function fmtIndicator(value, ind) {
  if (value === null || value === undefined) return "—";
  if (ind && ind.kind === "bool") return value ? "Yes" : "No";
  if (ind && ind.kind === "enum") return value;
  const unit = ind ? ind.unit : "";
  const n = Number(value);
  const num = (digits) => n.toLocaleString("en-IN", { maximumFractionDigits: digits });
  switch (unit) {
    case "%": return `${num(1)}%`;
    case "pp": return `${n > 0 ? "+" : ""}${num(1)} pp`;
    case "sd": return `${num(1)} pp`; // standard deviation of a % series
    case "x": return `${num(2)}x`;
    case "₹ Cr": return num(0);
    case "₹": return num(2);
    case "yrs": return `${num(0)} yr${n === 1 ? "" : "s"}`;
    default: return num(2);
  }
}

// An indicator's `detail` (the numbers behind it) as one readable line for a tooltip.
function fmtDetail(detail) {
  if (!detail) return "";
  return Object.entries(detail)
    .map(([k, v]) => {
      const label = k.replace(/_cr$/, " (₹ Cr)").replace(/_/g, " ");
      if (v && typeof v === "object") {
        return `${label}: ${Object.entries(v).map(([p, x]) => `${p} ${x === null ? "—" : x.toLocaleString("en-IN")}`).join(", ")}`;
      }
      return `${label}: ${v === null ? "—" : typeof v === "number" ? v.toLocaleString("en-IN") : v}`;
    })
    .join("\n");
}

const blankCondition = (ind) => ({ ind: ind ? ind.key : "", op: ind && ind.kind === "bool" ? "is_true" : ">=", value: null, value2: null });

const ScreenerTab = {
  props: { watchlists: Array, busy: Boolean },
  emits: ["error", "notice", "add-to-watchlist"],
  data() {
    return {
      state: "loading", // loading | down | ok
      categories: [],
      indicators: [],
      screens: [],
      screenId: null, // saved screen being edited; null = unsaved
      screenName: "",
      conditions: [],
      columns: [],
      sort: { key: "market_cap", dir: "desc" },
      results: null,
      running: false,
      refresh: null, // GET /refresh payload
      refreshTimer: null,
      expanded: null, // instrument_id whose details are open
      stock: null, // GET /stocks/{id} payload for `expanded`
      wlTarget: null,
    };
  },
  computed: {
    byKey() {
      return Object.fromEntries(this.indicators.map((i) => [i.key, i]));
    },
    grouped() {
      return this.categories
        .map((c) => ({ ...c, items: this.indicators.filter((i) => i.category === c.key) }))
        .filter((c) => c.items.length);
    },
    rules() {
      return { all: this.conditions.filter((c) => this.conditionValid(c)).map((c) => this.toRule(c)) };
    },
    refreshRunning() {
      return this.refresh && this.refresh.run && this.refresh.run.status === "running";
    },
    refreshProgress() {
      const run = this.refresh && this.refresh.run;
      if (!run) return "";
      const stage = { universe: "Updating stock list & prices", results: "Fetching results", indicators: "Computing indicators" }[run.stage] || run.stage;
      return run.total ? `${stage} — ${run.done}/${run.total}${run.failed ? ` (${run.failed} failed)` : ""}` : stage;
    },
    dirty() {
      const saved = this.screens.find((s) => s.id === this.screenId);
      if (!saved) return this.conditions.length > 0;
      // Postgres JSONB reorders object keys, so compare normalised copies.
      const rules = (r) => JSON.stringify(((r && r.all) || []).filter((x) => x.ind).map((x) => this.toRule(this.fromRule(x))));
      const sort = (s) => (s ? `${s.key}:${s.dir}` : "");
      return rules(saved.rules) !== rules(this.rules) || JSON.stringify(saved.columns) !== JSON.stringify(this.columns) ||
        sort(saved.sort) !== sort(this.sort) || saved.name !== this.screenName;
    },
  },
  async mounted() {
    await this.init();
  },
  unmounted() {
    clearTimeout(this.refreshTimer);
  },
  watch: {
    watchlists: {
      immediate: true,
      handler(lists) {
        const names = (lists || []).map((w) => w.name);
        if (!names.includes(this.wlTarget)) this.wlTarget = names[0] ?? null;
      },
    },
  },
  methods: {
    fmtIndicator,
    fmtDetail,
    fmtDate(iso) {
      return iso ? new Date(iso).toLocaleString() : "never";
    },
    fail(e) {
      this.$emit("error", e);
    },

    async init() {
      this.state = "loading";
      try {
        await api("GET", "/screener/health");
      } catch {
        this.state = "down";
        return;
      }
      try {
        const [meta, screens, refresh] = await Promise.all([
          api("GET", "/screener/indicators"),
          api("GET", "/screener/screens"),
          api("GET", "/screener/refresh"),
        ]);
        this.categories = meta.categories;
        this.indicators = meta.indicators;
        this.screens = screens;
        this.refresh = refresh;
        this.state = "ok";
        if (screens.length) this.loadScreen(screens[0]);
        else this.newScreen();
        if (this.refreshRunning) this.pollRefresh();
      } catch (e) {
        this.state = "down";
        this.fail(e);
      }
    },

    // -- filters ------------------------------------------------------------
    opsFor(key) {
      const ind = this.byKey[key];
      return ind && ind.kind === "bool" ? BOOL_OPS : NUMBER_OPS;
    },
    onIndicatorChange(c) {
      const ops = this.opsFor(c.ind).map((o) => o.op);
      if (!ops.includes(c.op)) c.op = ops[0];
    },
    conditionValid(c) {
      if (!this.byKey[c.ind]) return false;
      if (c.op === "is_true" || c.op === "is_false") return true;
      const ok = (v) => v !== null && v !== "" && !Number.isNaN(Number(v));
      return ok(c.value) && (c.op !== "between" || ok(c.value2));
    },
    toRule(c) {
      const r = { ind: c.ind, op: c.op };
      if (c.op !== "is_true" && c.op !== "is_false") r.value = Number(c.value);
      if (c.op === "between") r.value2 = Number(c.value2);
      return r;
    },
    fromRule(r) {
      return { ind: r.ind, op: r.op, value: r.value ?? null, value2: r.value2 ?? null };
    },
    addCondition() {
      this.conditions.push(blankCondition(this.indicators[0]));
    },
    removeCondition(idx) {
      this.conditions.splice(idx, 1);
      this.run();
    },
    toggleColumn(key) {
      const idx = this.columns.indexOf(key);
      if (idx >= 0) this.columns.splice(idx, 1);
      else this.columns.push(key);
      this.run();
    },
    sortBy(key) {
      this.sort = this.sort.key === key ? { key, dir: this.sort.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" };
      this.run();
    },
    sortMark(key) {
      return this.sort.key === key ? (this.sort.dir === "desc" ? " ▼" : " ▲") : "";
    },

    // -- screens ------------------------------------------------------------
    loadScreen(s) {
      this.screenId = s.id;
      this.screenName = s.name;
      this.conditions = ((s.rules && s.rules.all) || []).filter((r) => r.ind).map(this.fromRule);
      this.columns = [...(s.columns || [])];
      this.sort = s.sort ? { ...s.sort } : { key: "market_cap", dir: "desc" };
      this.run();
    },
    selectScreen(id) {
      const s = this.screens.find((x) => x.id === Number(id));
      if (s) this.loadScreen(s);
      else this.newScreen();
    },
    newScreen() {
      this.screenId = null;
      this.screenName = "";
      this.conditions = [];
      this.columns = this.columns.length ? this.columns : ["market_cap", "pe", "roce_3y_avg", "opm_latest", "debt_to_equity"];
      this.run();
    },
    async saveScreen(asNew = false) {
      let name = this.screenName.trim();
      if (asNew || !this.screenId) {
        name = (prompt("Name for this screen:", asNew ? `${name} (copy)` : name) || "").trim();
        if (!name) return;
      }
      const body = { name, rules: this.rules, columns: this.columns, sort: this.sort };
      try {
        const saved = asNew || !this.screenId
          ? await api("POST", "/screener/screens", body)
          : await api("PUT", `/screener/screens/${this.screenId}`, body);
        this.screens = await api("GET", "/screener/screens");
        this.screenId = saved.id;
        this.screenName = saved.name;
        this.$emit("notice", `Saved screen "${saved.name}".`);
      } catch (e) {
        this.fail(e);
      }
    },
    async deleteScreen() {
      if (!this.screenId || !confirm(`Delete the screen "${this.screenName}"?`)) return;
      try {
        await api("DELETE", `/screener/screens/${this.screenId}`);
        this.screens = await api("GET", "/screener/screens");
        this.$emit("notice", `Deleted screen "${this.screenName}".`);
        if (this.screens.length) this.loadScreen(this.screens[0]);
        else this.newScreen();
      } catch (e) {
        this.fail(e);
      }
    },

    // -- results ------------------------------------------------------------
    async run() {
      if (this.state !== "ok") return;
      this.running = true;
      try {
        this.results = await api("POST", "/screener/run", {
          rules: this.rules, columns: this.columns, sort: this.sort, limit: SCREENER_RESULT_LIMIT,
        });
      } catch (e) {
        this.fail(e);
      } finally {
        this.running = false;
      }
    },
    async toggleStock(row) {
      if (this.expanded === row.instrument_id) {
        this.expanded = null;
        return;
      }
      this.expanded = row.instrument_id;
      this.stock = null;
      try {
        this.stock = await api("GET", `/screener/stocks/${enc(row.instrument_id)}`);
      } catch (e) {
        this.fail(e);
      }
    },
    stockIndicators(category) {
      return this.stock ? this.stock.indicators.filter((i) => i.category === category && i.value !== null) : [];
    },
    fmtCell(v, unit) {
      if (v === null || v === undefined) return "—";
      if (unit === "%") return `${v.toFixed(1)}%`;
      return v.toLocaleString("en-IN", { maximumFractionDigits: unit === "cr" ? 0 : 2 });
    },
    inWatchlist(instrumentId) {
      const w = (this.watchlists || []).find((x) => x.name === this.wlTarget);
      return Boolean(w && w.items.some((i) => i.instrument_id === instrumentId));
    },

    // -- refresh ------------------------------------------------------------
    async startRefresh() {
      try {
        await api("POST", "/screener/refresh", {});
        this.$emit("notice", "Refresh started — the first one fetches every stock's history and takes a few hours.");
        this.pollRefresh();
      } catch (e) {
        this.fail(e);
      }
    },
    async pollRefresh() {
      clearTimeout(this.refreshTimer);
      try {
        this.refresh = await api("GET", "/screener/refresh");
      } catch (e) {
        this.fail(e);
        return;
      }
      if (this.refreshRunning) {
        this.refreshTimer = setTimeout(this.pollRefresh, SCREENER_REFRESH_POLL_MS);
      } else {
        this.run();
      }
    },
  },
  template: `
    <div>
      <div v-if="state === 'loading'" class="card"><p class="muted">Connecting to the screener…</p></div>

      <div v-else-if="state === 'down'" class="card">
        <h2>Screener isn't running</h2>
        <p>The screener runs on demand. Start it with</p>
        <p><code>docker compose --profile screener up -d screener</code></p>
        <p class="muted small">Stop it again with <code>docker compose stop screener</code> — its data stays in Postgres.</p>
        <button @click="init">Retry</button>
      </div>

      <template v-else>
        <div class="card">
          <div class="card-head">
            <h2>Data</h2>
            <div class="nowrap head-actions">
              <button class="secondary refresh-btn" @click="startRefresh" :disabled="refreshRunning">{{ refreshRunning ? 'Refreshing…' : 'Refresh data' }}</button>
            </div>
          </div>
          <p class="small" v-if="refresh">
            <template v-if="refreshRunning"><strong>{{ refreshProgress }}</strong><span v-if="refresh.run.message && refresh.run.stage === 'results'" class="muted"> · {{ refresh.run.message }}</span></template>
            <template v-else>
              Results for <strong>{{ refresh.coverage.with_results }}</strong> of {{ refresh.coverage.listed }} NSE stocks ·
              prices as of {{ refresh.coverage.close_date || '—' }} · last refresh {{ fmtDate(refresh.last_completed_at) }}
              <span v-if="refresh.run && refresh.run.status === 'failed'" class="loss"> · last run failed: {{ refresh.run.message }}</span>
              <span v-if="refresh.run && refresh.run.status === 'interrupted'" class="warn"> · last run was interrupted — refresh again to resume</span>
            </template>
          </p>
          <p class="muted small" v-if="refresh && !refreshRunning && refresh.coverage.with_results < refresh.coverage.listed / 2">
            Most stocks have no results yet. "Refresh data" back-fills them (a few hours the first time; later refreshes only fetch new quarters).
          </p>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Screen</h2>
            <div class="nowrap head-actions screen-actions">
              <select :value="screenId ?? ''" @change="selectScreen($event.target.value)" aria-label="Saved screens">
                <option v-for="s in screens" :key="s.id" :value="s.id">{{ s.name }}</option>
                <option value="">+ New screen</option>
              </select>
              <button class="secondary refresh-btn" @click="saveScreen(false)" :disabled="!dirty">{{ screenId ? 'Save' : 'Save as…' }}</button>
              <button v-if="screenId" class="secondary refresh-btn" @click="saveScreen(true)">Save as…</button>
              <button v-if="screenId" class="danger refresh-btn" @click="deleteScreen">Delete</button>
            </div>
          </div>

          <p class="muted small">Stocks matching <strong>all</strong> of these:</p>
          <div v-for="(c, idx) in conditions" :key="idx" class="condition">
            <select v-model="c.ind" @change="onIndicatorChange(c); run()" aria-label="Indicator">
              <optgroup v-for="g in grouped" :key="g.key" :label="g.label">
                <option v-for="i in g.items" :key="i.key" :value="i.key">{{ i.name }}</option>
              </optgroup>
            </select>
            <select v-model="c.op" @change="run" aria-label="Condition">
              <option v-for="o in opsFor(c.ind)" :key="o.op" :value="o.op">{{ o.label }}</option>
            </select>
            <template v-if="c.op !== 'is_true' && c.op !== 'is_false'">
              <input type="number" step="any" v-model="c.value" @change="run" placeholder="value" aria-label="Value" />
              <template v-if="c.op === 'between'"><span class="muted">and</span><input type="number" step="any" v-model="c.value2" @change="run" placeholder="value" aria-label="Upper value" /></template>
              <span class="muted small">{{ byKey[c.ind] ? byKey[c.ind].unit : '' }}</span>
            </template>
            <button class="link remove-cond" @click="removeCondition(idx)" title="Remove condition">✕</button>
            <div class="muted small cond-help" v-if="byKey[c.ind] && byKey[c.ind].description">{{ byKey[c.ind].description }}</div>
          </div>
          <div class="row actions">
            <button class="secondary" @click="addCondition">+ Add condition</button>
            <details class="column-picker">
              <summary>Columns ({{ columns.length }})</summary>
              <div class="column-grid">
                <div v-for="g in grouped" :key="g.key">
                  <strong class="small">{{ g.label }}</strong>
                  <label v-for="i in g.items" :key="i.key" class="check">
                    <input type="checkbox" :checked="columns.includes(i.key)" @change="toggleColumn(i.key)" /> {{ i.name }}
                  </label>
                </div>
              </div>
            </details>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Results <span class="muted" v-if="results">({{ results.total }}{{ results.total > results.rows.length ? ', showing ' + results.rows.length : '' }})</span></h2>
            <div class="nowrap head-actions" v-if="watchlists && watchlists.length">
              <label class="inline-label">Add to
                <select v-model="wlTarget" aria-label="Watchlist to add to">
                  <option v-for="w in watchlists" :key="w.id" :value="w.name">{{ w.name }}</option>
                </select>
              </label>
            </div>
          </div>
          <p v-if="running && !results" class="muted">Running…</p>
          <p v-else-if="results && !results.rows.length" class="muted">No stocks match. Loosen a condition, or refresh data if few stocks have results yet.</p>
          <div v-else-if="results" class="table-wrap">
            <table class="screener-table">
              <thead>
                <tr>
                  <th class="sortable" @click="sortBy('symbol')">Stock{{ sortMark('symbol') }}</th>
                  <th v-for="k in results.columns" :key="k" class="num sortable" :title="byKey[k] && byKey[k].description" @click="sortBy(k)">
                    {{ byKey[k] ? byKey[k].name : k }}<span class="muted" v-if="byKey[k] && byKey[k].unit === '₹ Cr'"> ₹Cr</span>{{ sortMark(k) }}
                  </th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <template v-for="r in results.rows" :key="r.instrument_id">
                  <tr :class="{ open: expanded === r.instrument_id }">
                    <td><strong>{{ r.symbol }}</strong><div class="muted small">{{ r.name }}</div></td>
                    <td v-for="k in results.columns" :key="k" class="num">{{ fmtIndicator(r.values[k], byKey[k]) }}</td>
                    <td class="nowrap">
                      <button class="secondary" @click="toggleStock(r)">{{ expanded === r.instrument_id ? 'Hide' : 'Details' }}</button>
                      <template v-if="wlTarget">
                        <span v-if="inWatchlist(r.instrument_id)" class="muted small">✓ {{ wlTarget }}</span>
                        <button v-else class="secondary" @click="$emit('add-to-watchlist', wlTarget, r.instrument_id)" :disabled="busy">+ Watchlist</button>
                      </template>
                    </td>
                  </tr>
                  <tr v-if="expanded === r.instrument_id" class="detail-row">
                    <td :colspan="results.columns.length + 2">
                      <p v-if="!stock" class="muted">Loading…</p>
                      <div v-else class="detail">
                        <div class="panel wide">
                          <h4>{{ stock.name }} <span class="muted small">· {{ stock.basis || 'no results yet' }}{{ stock.is_financial ? ' · bank/NBFC format (most ratios n/a)' : '' }} · close ₹{{ stock.close ?? '—' }} ({{ stock.close_date || '—' }})</span></h4>
                          <div class="table-wrap" v-if="stock.yearly.periods.length">
                            <table class="compact">
                              <thead><tr><th>Yearly (₹ Cr)</th><th v-for="p in stock.yearly.periods" :key="p" class="num">{{ p }}</th></tr></thead>
                              <tbody>
                                <tr v-for="row in stock.yearly.rows" :key="row.metric">
                                  <td>{{ row.label }}</td>
                                  <td v-for="(v, idx) in row.values" :key="idx" class="num">{{ fmtCell(v, row.unit) }}</td>
                                </tr>
                              </tbody>
                            </table>
                          </div>
                          <p v-else class="muted">No results filed yet — refresh data.</p>
                        </div>
                        <div class="panel wide" v-if="stock.quarterly.periods.length">
                          <div class="table-wrap">
                            <table class="compact">
                              <thead><tr><th>Quarterly (₹ Cr)</th><th v-for="p in stock.quarterly.periods" :key="p" class="num">{{ p }}</th></tr></thead>
                              <tbody>
                                <tr v-for="row in stock.quarterly.rows" :key="row.metric">
                                  <td>{{ row.label }}</td>
                                  <td v-for="(v, idx) in row.values" :key="idx" class="num">{{ fmtCell(v, row.unit) }}</td>
                                </tr>
                              </tbody>
                            </table>
                          </div>
                        </div>
                        <div class="panel wide">
                          <h4>Indicators <span class="muted small">· hover a value for the numbers behind it</span></h4>
                          <div class="indicator-grid">
                            <div v-for="g in grouped" :key="g.key" v-show="stockIndicators(g.key).length">
                              <strong class="small">{{ g.label }}</strong>
                              <table class="compact">
                                <tr v-for="i in stockIndicators(g.key)" :key="i.key">
                                  <td :title="i.description">{{ i.name }}</td>
                                  <td class="num" :title="fmtDetail(i.detail)"><strong>{{ fmtIndicator(i.value, i) }}</strong></td>
                                </tr>
                              </table>
                            </div>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>`,
};
