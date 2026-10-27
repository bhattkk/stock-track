// Analysis tab — stage 1 technical filter, served by the analysis service via /api/analysis/*.
//
// Read-only by design: the rules live in analysis/criteria/technicals.yaml and
// are edited there, not here. This tab shows
//   - the latest run's summary (funnel counts) and a "Run now" button,
//   - the stocks that passed, best score first,
//   - for any stock (search, or click a row) every rule's outcome with the
//     numbers behind it and the rule's "why".

const ANALYSIS_POLL_MS = 3000;

// Labels for the layers and statuses the backend returns (see analysis/technicals/rules.py).
const LAYER_LABELS = {
  data: "Data",
  tradability: "A · Tradability",
  trend: "B · Trend",
  setup: "C · Entry setups",
  flag: "D · Red flags",
};
const STATUS_CLASS = {
  PASS: "gain", FIRED: "gain", CLEAR: "muted",
  FAIL: "loss", REJECT: "loss", NOT_FIRED: "muted",
  WARN: "warn", NA: "warn",
};
const STATUS_LABEL = {
  PASS: "✔ pass", FAIL: "✘ fail", NA: "? no data",
  FIRED: "✔ fired", NOT_FIRED: "· not fired",
  WARN: "! warning", REJECT: "✘ reject", CLEAR: "· clear",
};
const SETUP_LABELS = {
  breakout: "Breakout", accumulation: "Accumulation", tight_base: "Tight base",
  pullback_support: "Pullback", weekly_reversal: "Weekly reversal",
};
const FLAG_LABELS = {
  overextended: "Overextended", distribution: "Distribution",
  bearish_at_resistance: "Bearish at resistance", gap_down: "Gap down",
};

const AnalysisTab = {
  props: { watchlists: Array, busy: Boolean },
  emits: ["error", "notice", "add-to-watchlist"],
  data() {
    return {
      state: "loading", // loading | down | ok
      run: null, // latest finished run (from GET /results)
      active: null, // a run in progress, if any (from GET /runs)
      stocks: [], // passing stocks of `run`
      pollTimer: null,
      query: "",
      matches: [], // search results
      searchTimer: null,
      stock: null, // GET /stocks/{id} payload shown in the breakdown panel
      wlTarget: null,
    };
  },
  computed: {
    // The stock's rules grouped by layer, in pipeline order.
    stockLayers() {
      if (!this.stock) return [];
      const order = ["data", "tradability", "trend", "setup", "flag"];
      return order
        .map((layer) => ({ layer, label: LAYER_LABELS[layer], rules: this.stock.rules.filter((r) => r.layer === layer) }))
        .filter((g) => g.rules.length);
    },
    // "Syncing candles — 1,200 / 2,709" while a run is going.
    progress() {
      const r = this.active;
      if (!r) return "";
      const stage = { starting: "Starting", syncing: "Syncing candles", evaluating: "Evaluating" }[r.stage] || r.stage;
      return r.total ? `${stage} — ${r.done.toLocaleString("en-IN")} / ${r.total.toLocaleString("en-IN")}` : stage;
    },
    progressPct() {
      const r = this.active;
      return r && r.total ? Math.round((r.done / r.total) * 100) : 0;
    },
    // Funnel as [label, count] pairs, e.g. [["Universe", 2708], ["Tradable", 1500], ...].
    funnel() {
      const c = this.run && this.run.counts;
      if (!c) return [];
      return [["Universe", c.universe], ["With data", c.with_data], ["Tradable", c.tradability],
        ["In uptrend", c.trend], ["Setup", c.setup], ["Passed", c.passed]];
    },
  },
  async mounted() {
    await this.init();
  },
  unmounted() {
    clearTimeout(this.pollTimer);
    clearTimeout(this.searchTimer);
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
    fail(e) {
      this.$emit("error", e);
    },
    fmtDate(iso) {
      return iso ? new Date(iso).toLocaleString() : "—";
    },
    num(v, digits = 1) {
      return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits });
    },
    pctText(v) {
      return v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${this.num(v)}%`;
    },
    setupLabel(id) {
      return SETUP_LABELS[id] || id;
    },
    flagLabel(id) {
      return FLAG_LABELS[id] || id;
    },
    statusClass(s) {
      return STATUS_CLASS[s] || "";
    },
    statusLabel(s) {
      return STATUS_LABEL[s] || s;
    },
    // A gate's value vs threshold, e.g. "-8.25 (needs > 0)". Setups/flags use their note instead.
    ruleValue(r) {
      if (r.op === undefined) return "";
      return `${this.num(r.value, 2)} (needs ${r.op} ${r.threshold})`;
    },
    inWatchlist(instrumentId) {
      const w = (this.watchlists || []).find((x) => x.name === this.wlTarget);
      return Boolean(w && w.items.some((i) => i.instrument_id === instrumentId));
    },

    // Load the latest results and check whether a run is going; "down" if the service is unreachable.
    async init() {
      this.state = "loading";
      try {
        await api("GET", "/analysis/health");
      } catch {
        this.state = "down";
        return;
      }
      try {
        await Promise.all([this.loadResults(), this.loadActive()]);
        this.state = "ok";
      } catch (e) {
        this.state = "down";
        this.fail(e);
      }
    },
    async loadResults() {
      const data = await api("GET", "/analysis/results");
      this.run = data.run;
      this.stocks = data.stocks;
    },
    // Find a running run (if any) and keep polling it until it finishes.
    async loadActive() {
      const runs = await api("GET", "/analysis/runs?limit=1");
      const latest = runs[0];
      this.active = latest && latest.status === "running" ? latest : null;
      if (latest && latest.status === "failed" && (!this.run || latest.id > this.run.id)) {
        this.fail(new ApiError("Last analysis run failed", latest.error || "unknown error"));
      }
      clearTimeout(this.pollTimer);
      if (this.active) this.pollTimer = setTimeout(() => this.poll(), ANALYSIS_POLL_MS);
    },
    async poll() {
      try {
        const r = await api("GET", `/analysis/runs/${this.active.id}`);
        if (r.status === "running") {
          this.active = r;
          this.pollTimer = setTimeout(() => this.poll(), ANALYSIS_POLL_MS);
          return;
        }
        this.active = null;
        await this.loadResults();
        if (r.status === "done") this.$emit("notice", `Analysis finished: ${r.counts ? r.counts.passed : 0} stocks passed.`);
        else this.fail(new ApiError("Analysis run " + r.status, r.error || "The run did not finish."));
      } catch (e) {
        this.fail(e);
      }
    },
    async runNow() {
      try {
        this.active = await api("POST", "/analysis/runs");
        this.pollTimer = setTimeout(() => this.poll(), ANALYSIS_POLL_MS);
      } catch (e) {
        this.fail(e);
      }
    },

    // Debounced search over the latest run by symbol or name.
    onSearch() {
      clearTimeout(this.searchTimer);
      const q = this.query.trim();
      if (!q) {
        this.matches = [];
        return;
      }
      this.searchTimer = setTimeout(async () => {
        try {
          this.matches = await api("GET", `/analysis/search?q=${enc(q)}`);
        } catch (e) {
          this.fail(e);
        }
      }, 250);
    },
    async openStock(instrumentId) {
      this.matches = [];
      try {
        this.stock = await api("GET", `/analysis/stocks/${enc(instrumentId)}`);
        this.$nextTick(() => this.$refs.breakdown && this.$refs.breakdown.scrollIntoView({ behavior: "smooth", block: "start" }));
      } catch (e) {
        this.fail(e);
      }
    },
  },
  template: `
    <div>
      <div v-if="state === 'loading'" class="card"><p class="muted">Connecting to the analysis service…</p></div>

      <div v-else-if="state === 'down'" class="card">
        <h2>Analysis service isn't running</h2>
        <p>Start it with <code>docker compose up -d analysis</code></p>
        <button @click="init">Retry</button>
      </div>

      <template v-else>
        <div class="card">
          <div class="card-head">
            <h2>Stage 1 · Technical filter</h2>
            <div class="nowrap head-actions">
              <button class="secondary refresh-btn" @click="runNow" :disabled="Boolean(active)">{{ active ? 'Running…' : 'Run now' }}</button>
            </div>
          </div>
          <template v-if="active">
            <p class="small"><strong>{{ progress }}</strong> <span class="muted">· a full run takes about 50 minutes (Upstox rate limit)</span></p>
            <div class="progress"><div class="progress-bar" :style="{ width: progressPct + '%' }"></div></div>
          </template>
          <p v-if="run" class="small">
            Last run {{ fmtDate(run.finished_at) }} · rules <code>{{ (run.criteria_hash || '').slice(0, 8) }}</code>
            <span class="muted"> · weekly on Saturday morning · rules are edited in <code>analysis/criteria/technicals.yaml</code></span>
          </p>
          <p v-else-if="!active" class="muted">No finished run yet. Click "Run now" (about 50 minutes the first time).</p>
          <div v-if="funnel.length" class="funnel">
            <div v-for="([label, n], idx) in funnel" :key="label" class="funnel-step">
              <span class="muted small">{{ label }}</span>
              <strong>{{ num(n, 0) }}</strong>
              <span v-if="idx < funnel.length - 1" class="muted funnel-arrow">→</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Why did a stock pass or fail?</h2>
          </div>
          <div class="search-box">
            <input v-model="query" @input="onSearch" placeholder="Search any stock by symbol or name…" aria-label="Search stock" />
            <ul v-if="matches.length" class="search-results">
              <li v-for="m in matches" :key="m.instrument_id" @click="openStock(m.instrument_id)">
                <strong>{{ m.symbol }}</strong> <span class="muted small">{{ m.name }}</span>
                <span v-if="m.passed" class="badge buy">passed</span>
                <span v-else class="badge sell">failed at {{ m.failed_at }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="card" v-if="stock" ref="breakdown">
          <div class="card-head">
            <h2>{{ stock.symbol }} <span class="muted small">{{ stock.name }}</span></h2>
            <div class="nowrap head-actions">
              <span v-if="stock.passed" class="badge buy">passed · score {{ stock.score }}</span>
              <span v-else class="badge sell">rejected at {{ stock.failed_at }}</span>
              <button class="link" @click="stock = null">Close</button>
            </div>
          </div>
          <p class="small muted">
            Candles as of {{ stock.as_of || '—' }} · close ₹{{ num(stock.metrics.close, 2) }}
            · support {{ stock.metrics.support ? '₹' + num(stock.metrics.support, 2) : '—' }}
            · resistance {{ stock.metrics.resistance ? '₹' + num(stock.metrics.resistance, 2) : 'none overhead' }}
            · RS percentile {{ num(stock.metrics.rs_percentile, 0) }}
            <span v-if="stock.metrics.sync_error" class="warn"> · candle sync failed this run, older data used</span>
          </p>
          <div v-for="g in stockLayers" :key="g.layer" class="rule-group">
            <h4>{{ g.label }}</h4>
            <table class="compact rule-table">
              <tr v-for="r in g.rules" :key="r.id">
                <td class="nowrap" :class="statusClass(r.status)">{{ statusLabel(r.status) }}</td>
                <td>
                  <strong>{{ r.title }}</strong>
                  <div class="small">{{ r.op !== undefined ? ruleValue(r) : r.note }}</div>
                  <details class="small muted"><summary>Why this rule</summary>{{ r.why }}</details>
                </td>
              </tr>
            </table>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Passed <span class="muted" v-if="run">({{ stocks.length }})</span></h2>
            <div class="nowrap head-actions" v-if="watchlists && watchlists.length">
              <label class="inline-label">Add to
                <select v-model="wlTarget" aria-label="Watchlist to add to">
                  <option v-for="w in watchlists" :key="w.id" :value="w.name">{{ w.name }}</option>
                </select>
              </label>
            </div>
          </div>
          <p v-if="run && !stocks.length" class="muted">No stock passed every layer in the last run.</p>
          <div v-else-if="stocks.length" class="table-wrap">
            <table class="screener-table">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th class="num" title="Ranking score: 2 per setup + RS percentile/25 + 1 for healthy weekly RSI − 1 per warning">Score</th>
                  <th>Setups</th>
                  <th class="num">Close ₹</th>
                  <th class="num" title="How far the next resistance level is above the close">To resistance</th>
                  <th class="num" title="Last week's volume vs the 10-week average">Week vol ×</th>
                  <th class="num" title="6-month return minus NIFTY 50's">RS 6M</th>
                  <th class="num" title="How far below the 52-week high">From 52W high</th>
                  <th class="num">Weekly RSI</th>
                  <th>Warnings</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in stocks" :key="s.instrument_id">
                  <td><a href="#analysis" @click.prevent="openStock(s.instrument_id)"><strong>{{ s.symbol }}</strong></a><div class="muted small">{{ s.name }}</div></td>
                  <td class="num"><strong>{{ num(s.score, 1) }}</strong></td>
                  <td><span v-for="id in s.setups_fired" :key="id" class="badge buy">{{ setupLabel(id) }}</span></td>
                  <td class="num">{{ num(s.close, 2) }}</td>
                  <td class="num">{{ s.pct_below_resistance === null ? 'clear' : num(s.pct_below_resistance) + '%' }}</td>
                  <td class="num">{{ num(s.w_volume_ratio_10w, 2) }}</td>
                  <td class="num" :class="s.rs_6m_vs_benchmark_pct > 0 ? 'gain' : 'loss'">{{ pctText(s.rs_6m_vs_benchmark_pct) }}</td>
                  <td class="num">{{ num(s.pct_below_52w_high) }}%</td>
                  <td class="num">{{ num(s.w_rsi14, 0) }}</td>
                  <td><span v-for="id in s.warns" :key="id" class="badge sell">{{ flagLabel(id) }}</span></td>
                  <td class="nowrap">
                    <template v-if="wlTarget">
                      <span v-if="inWatchlist(s.instrument_id)" class="muted small">✓ {{ wlTarget }}</span>
                      <button v-else class="secondary" @click="$emit('add-to-watchlist', wlTarget, s.instrument_id)" :disabled="busy">+ Watchlist</button>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>`,
};
