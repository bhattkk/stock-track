// Stock Track front end — a single Vue 3 app (CDN build, no bundler).
// API calls go through api() in api.js; the Screener tab lives in screener.js
// and the Analysis tab in analysis.js.
const { createApp } = Vue;

// Search-as-you-type: wait for MIN_SEARCH_CHARS, debounce keystrokes, show
// at most SEARCH_LIMIT results. `seq` discards responses that arrive after
// a newer query was issued.
const MIN_SEARCH_CHARS = 2;
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_LIMIT = 5;

// An active holding whose unrealized loss reaches this many rupees is
// flagged "ready to sell" (light red row).
const SELL_ALERT_LOSS = 40000;

// Auto-refresh of the open tab's list while the browser tab is visible:
// Portfolio per view (Active / Old holdings), Watchlists flat. (Stock LTPs
// themselves change once per market-data sweep, POLL_INTERVAL_SECONDS.)
const PORTFOLIO_REFRESH_MS = { open: 2000, sold: 10000 };
const WATCHLIST_REFRESH_MS = 2000;
// Tabs whose list auto-refreshes (each named after its API path).
const LIST_TABS = ["portfolio", "watchlists"];

// Header index ticker (NIFTY 50) refresh; the API fetches it live from Upstox.
const INDEX_REFRESH_MS = 2000;

// The header's health pill re-checks GET /health this often (and on click).
const HEALTH_CHECK_MS = 30000;
// After adding a stock to a watchlist, the search box and results clear after
// this delay (long enough to see the row was added).
const WL_SEARCH_CLEAR_MS = 600;

const newSearch = () => ({ q: "", results: [], loading: false, searched: false, seq: 0, timer: null });

// YYYY-MM-DD in the browser's local timezone (toISOString() would use UTC).
function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Whole days from one YYYY-MM-DD to another, calendar-based (DST-safe).
function daysBetween(fromYmd, toYmd) {
  const utc = (ymd) => {
    const [y, m, d] = ymd.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((utc(toYmd) - utc(fromYmd)) / 86400000);
}

const blankTranche = (today) => ({ buy_date: today, quantity: null, price: null });
const validTranche = (t) => Boolean(t.buy_date) && t.quantity > 0 && t.price > 0;

// "Show details" view of one portfolio position: entries (tranches) and
// exit, notes and (later) fundamentals. Keeps its own form drafts; every change is emitted as
// `save(method, path, body, notice)` and the root app performs the request.
// "2026-11-30" -> "30 Nov 2026" (parsed as a local date, not UTC midnight).
function fmtDay(ymd) {
  if (!ymd) return "—";
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

// Cached Upstox fundamentals for one instrument (`data` = the API's
// `fundamentals` field): sector, key ratios vs sector, shareholding by
// quarter and the company profile. Used by portfolio and watchlist details.
const FundamentalsPanel = {
  props: { data: Object },
  methods: {
    fmtDay,
    fmtRatio(v, unit) {
      return v === null || v === undefined ? "—" : `${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${unit}`;
    },
    // Latest-quarter change in a shareholding row (null when flat or unknown).
    shareChange(row) {
      const [latest, prev] = row.values;
      if (latest === null || latest === undefined || prev === null || prev === undefined) return null;
      const diff = +(latest - prev).toFixed(2);
      return diff === 0 ? null : diff;
    },
  },
  template: `
    <div>
      <template v-if="data">
        <p class="fund-meta">
          <template v-if="data.sector">Sector: <strong>{{ data.sector }}</strong> · </template><span class="muted small">Upstox, as of {{ fmtDay(data.fetched_on) }}</span>
        </p>
        <div class="fund-grid">
          <table v-if="data.ratios && data.ratios.length" class="compact">
            <thead><tr><th>Key ratio</th><th class="num">Company</th><th class="num">Sector</th></tr></thead>
            <tbody>
              <tr v-for="r in data.ratios" :key="r.name">
                <td>{{ r.name }}</td>
                <td class="num"><strong>{{ fmtRatio(r.company, r.unit) }}</strong></td>
                <td class="num muted">{{ fmtRatio(r.sector, r.unit) }}</td>
              </tr>
            </tbody>
          </table>
          <table v-if="data.shareholding" class="compact">
            <thead>
              <tr><th>Shareholding %</th><th v-for="p in data.shareholding.periods" :key="p" class="num">{{ p }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in data.shareholding.rows" :key="row.category">
                <td>{{ row.category }}</td>
                <td v-for="(v, idx) in row.values" :key="idx" class="num" :class="{ muted: idx > 0 }">
                  {{ v === null ? '—' : v.toFixed(2) }}<span v-if="idx === 0 && shareChange(row) !== null" class="small sh-delta" :class="shareChange(row) > 0 ? 'gain' : 'loss'">{{ shareChange(row) > 0 ? '▲' : '▼' }}{{ Math.abs(shareChange(row)).toFixed(2) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <details v-if="data.profile" class="fund-about">
          <summary>About the company</summary>
          <p>{{ data.profile }}</p>
        </details>
      </template>
      <p v-else class="muted">Not fetched yet — fundamentals load from Upstox at startup and daily at 06:00.</p>
    </div>`,
};

const PortfolioDetail = {
  props: { item: Object, busy: Boolean, today: String },
  emits: ["save", "remove"],
  data() {
    return {
      editing: null, // tranche id being edited
      draft: {},
      adding: false, // "+ Add tranche" form open
      editingSell: false, // exit row in edit mode (sold positions)
      newTranche: blankTranche(this.today),
      sell: { sell_date: this.item.sell_date || this.today, sell_price: this.item.sell_price },
      notes: this.item.notes || "",
    };
  },
  computed: {
    base() {
      return `/portfolio/${encodeURIComponent(this.item.instrument_id)}`;
    },
    canAddTranche() {
      return this.item.status === "open";
    },
  },
  // Re-sync drafts only when the saved values change. The list is re-fetched
  // every few seconds, so watching `item` itself would wipe in-progress edits.
  watch: {
    "item.notes"(notes) {
      this.notes = notes || "";
    },
    "item.sell_date"() {
      this.syncSell();
    },
    "item.sell_price"() {
      this.syncSell();
    },
  },
  methods: {
    valid: validTranche,
    syncSell() {
      this.sell = { sell_date: this.item.sell_date || this.today, sell_price: this.item.sell_price };
    },
    startEdit(t) {
      this.editing = t.id;
      this.draft = { buy_date: t.buy_date, quantity: t.quantity, price: t.price };
    },
    saveEdit(t) {
      this.$emit("save", "PUT", `${this.base}/tranches/${t.id}`, this.draft, `Updated ${t.label}.`);
      this.editing = null;
    },
    deleteTranche(t) {
      if (!confirm(`Delete ${t.label} (${t.quantity} @ ${t.price})?`)) return;
      this.$emit("save", "DELETE", `${this.base}/tranches/${t.id}`, null, `Deleted ${t.label}.`);
    },
    addTranche() {
      const label = `T${this.item.tranches.length + 1}`;
      this.$emit("save", "POST", `${this.base}/tranches`, { ...this.newTranche }, `${label} added.`);
      this.newTranche = blankTranche(this.today);
      this.adding = false;
    },
    saveSell() {
      const verb = this.item.status === "sold" ? "Updated the sell of" : "Marked";
      this.$emit("save", "PATCH", this.base, { ...this.sell }, `${verb} ${this.item.symbol}${this.item.status === "sold" ? "" : " as sold"}.`);
    },
    clearSell() {
      if (!confirm(`Undo the sell of ${this.item.symbol}? It moves back to active holdings.`)) return;
      this.$emit("save", "PATCH", this.base, { sell_date: null, sell_price: null }, `${this.item.symbol} re-opened.`);
    },
    saveNotes() {
      this.$emit("save", "PATCH", this.base, { notes: this.notes || null }, "Notes saved.");
    },
    money(v) {
      return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
    },

    fmtDay,

    // Percent move from `base` to `value`, e.g. "+15.12%".
    pctOf(value, base) {
      const p = (value / base - 1) * 100;
      return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`;
    },

    daysToMaxHold(i) {
      return daysBetween(this.today, i.max_holding_date);
    },

    holdLabel(i) {
      const days = this.daysToMaxHold(i);
      if (days < 0) return `${-days}d past max hold`;
      if (days === 0) return "Max hold is today";
      return `${days}d left`;
    },

    holdClass(i) {
      const days = this.daysToMaxHold(i);
      return days < 0 ? "loss" : days <= 30 ? "warn" : "muted";
    },
  },
  template: `
    <div class="detail">
      <div class="panel wide">
        <div class="card-head">
          <h4>Entries &amp; exits <span class="muted">({{ item.tranches.length }} tranche{{ item.tranches.length === 1 ? '' : 's' }})</span></h4>
          <button v-if="canAddTranche && !adding" @click="adding = true" :disabled="busy">+ Add tranche</button>
        </div>
        <table v-if="item.tranches.length || item.status === 'sold'" class="compact">
          <thead><tr><th></th><th>Type</th><th>Date</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Amount</th><th></th></tr></thead>
          <tbody>
            <tr v-for="t in item.tranches" :key="t.id">
              <td><strong>{{ t.label }}</strong></td>
              <td><span class="badge buy">Buy</span></td>
              <template v-if="editing === t.id">
                <td><input type="date" v-model="draft.buy_date" :max="today" /></td>
                <td class="num"><input type="number" min="1" step="1" v-model.number="draft.quantity" /></td>
                <td class="num"><input type="number" min="0" step="0.01" v-model.number="draft.price" /></td>
                <td></td>
                <td class="nowrap">
                  <button @click="saveEdit(t)" :disabled="busy || !valid(draft)">Save</button>
                  <button class="secondary" @click="editing = null">Cancel</button>
                </td>
              </template>
              <template v-else>
                <td>{{ t.buy_date }}</td>
                <td class="num">{{ t.quantity }}</td>
                <td class="num">{{ money(t.price) }}</td>
                <td class="num">{{ money(t.quantity * t.price) }}</td>
                <td class="nowrap">
                  <button class="secondary" @click="startEdit(t)" :disabled="busy">Edit</button>
                  <button class="danger" @click="deleteTranche(t)" :disabled="busy">Delete</button>
                </td>
              </template>
            </tr>
            <tr v-if="adding">
              <td><strong>T{{ item.tranches.length + 1 }}</strong></td>
              <td><span class="badge buy">Buy</span></td>
              <td><input type="date" v-model="newTranche.buy_date" :max="today" /></td>
              <td class="num"><input type="number" min="1" step="1" v-model.number="newTranche.quantity" placeholder="Qty" /></td>
              <td class="num"><input type="number" min="0" step="0.01" v-model.number="newTranche.price" placeholder="Price" /></td>
              <td class="num">{{ valid(newTranche) ? money(newTranche.quantity * newTranche.price) : '' }}</td>
              <td class="nowrap">
                <button @click="addTranche" :disabled="busy || !valid(newTranche)">Save</button>
                <button class="secondary" @click="adding = false">Cancel</button>
              </td>
            </tr>
            <tr class="total-row" v-if="item.tranches.length">
              <td></td><td>Total</td><td></td>
              <td class="num">{{ item.quantity }}</td>
              <td class="num" title="Average cost">{{ money(item.avg_cost) }}</td>
              <td class="num">{{ money(item.invested) }}</td>
              <td></td>
            </tr>
            <tr v-if="item.status === 'sold'">
              <td></td>
              <td><span class="badge sell">Exit</span></td>
              <template v-if="editingSell">
                <td><input type="date" v-model="sell.sell_date" :max="today" /></td>
                <td class="num">{{ item.quantity }}</td>
                <td class="num"><input type="number" min="0" step="0.01" v-model.number="sell.sell_price" /></td>
                <td class="num">{{ sell.sell_price > 0 ? money(item.quantity * sell.sell_price) : '' }}</td>
                <td class="nowrap">
                  <button @click="saveSell(); editingSell = false" :disabled="busy || !sell.sell_date || !(sell.sell_price > 0)">Save</button>
                  <button class="secondary" @click="editingSell = false">Cancel</button>
                </td>
              </template>
              <template v-else>
                <td>{{ item.sell_date }}</td>
                <td class="num">{{ item.quantity }}</td>
                <td class="num">{{ money(item.sell_price) }}</td>
                <td class="num">{{ money(item.quantity * item.sell_price) }}</td>
                <td class="nowrap">
                  <span :class="item.realized_pnl >= 0 ? 'gain' : 'loss'" title="Realized P&L">{{ money(item.realized_pnl) }}</span>
                  <button class="secondary" @click="syncSell(); editingSell = true" :disabled="busy">Edit</button>
                  <button class="danger" @click="clearSell" :disabled="busy">Undo sell</button>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
        <p v-if="!item.tranches.length && !adding" class="muted">No entries yet — use “+ Add tranche” to record T1.</p>
        <p v-if="item.max_holding_date && item.status === 'open'" class="hold-line">
          Max hold till <strong>{{ fmtDay(item.max_holding_date) }}</strong> <span :class="holdClass(item)">({{ holdLabel(item) }})</span> <span class="muted small">· T1 {{ fmtDay(item.first_buy_date) }} + 9 months</span>
        </p>
        <!-- Kept on single lines: Vue drops whitespace between elements across newlines. -->
        <p v-if="item.status === 'open' && item.tranches.length" class="hold-line">
          <template v-if="item.trailing_sl !== null">
            Peak close <strong>{{ money(item.peak_price) }}</strong> <span class="muted small">({{ fmtDay(item.peak_date) }})</span> · Trailing SL (−{{ item.trailing_sl_pct }}%) <strong>{{ money(item.trailing_sl) }}</strong> · <span :class="item.sl_locks_profit ? 'gain' : 'muted'">{{ item.sl_locks_profit ? 'locks in ' + pctOf(item.trailing_sl, item.avg_cost) + ' over avg' : 'below avg cost' }}</span> · <span v-if="item.profit_exit" class="gain"><strong>LTP at/below SL — exit</strong></span><span v-else-if="item.tranches.length >= item.profit_exit_min_tranches">Armed ({{ item.tranches.length }}/{{ item.profit_exit_min_tranches }} tranches)</span><span v-else class="muted">Needs {{ item.profit_exit_min_tranches }} tranches ({{ item.tranches.length }} deployed)</span>
          </template>
          <span v-else class="muted">Peak not available yet{{ item.peak_refreshed_on ? ' (last refreshed ' + fmtDay(item.peak_refreshed_on) + ')' : '' }}.</span>
        </p>
      </div>

      <!-- Sold positions show (and edit/undo) the exit in the entries & exits log instead. -->
      <div v-if="item.status === 'open'" class="panel">
        <h4>Exit</h4>
        <div class="row">
          <label>Sell date <input type="date" v-model="sell.sell_date" :max="today" /></label>
          <label>Sell price <input type="number" min="0" step="0.01" v-model.number="sell.sell_price" /></label>
        </div>
        <div class="row actions">
          <button @click="saveSell" :disabled="busy || !item.tranches.length || !sell.sell_date || !(sell.sell_price > 0)">Mark as sold</button>
        </div>
      </div>

      <div class="panel wide">
        <h4>Why I bought it</h4>
        <textarea rows="3" v-model="notes" placeholder="Thesis, triggers, targets…"></textarea>
        <div class="row actions">
          <button @click="saveNotes" :disabled="busy || notes === (item.notes || '')">Save notes</button>
        </div>
      </div>

      <div class="panel wide">
        <h4>Fundamentals</h4>
        <fundamentals-panel :data="item.fundamentals"></fundamentals-panel>
      </div>

      <div class="panel wide danger-zone">
        <span class="muted">Remove {{ item.symbol || item.instrument_id }} and all its entries from the portfolio.</span>
        <button class="danger" @click="$emit('remove')" :disabled="busy">Remove from portfolio</button>
      </div>
    </div>`,
};

createApp({
  data() {
    return {
      tabs: [
        { id: "portfolio", label: "Portfolio" },
        { id: "watchlists", label: "Watchlists" },
        { id: "screener", label: "Screener" },
        { id: "analysis", label: "Analysis" },
      ],
      tab: "portfolio",
      healthTimer: null,
      health: { state: "checking", message: "", checkedAt: null },
      error: null,
      notice: null,
      busy: false,
      loading: { watchlists: false, portfolio: false },
      watchlists: [],
      portfolio: [],
      wlChoice: null, // name of the watchlist "Add" targets; null when there are none
      creatingWl: false, // "+ New watchlist" name box open
      wlNewName: "",
      wlSearch: newSearch(),
      pfSearch: newSearch(),
      pfNew: { instrument: null, tranches: [], notes: "" },
      minSearchChars: MIN_SEARCH_CHARS,
      expanded: {},
      sellAlertLoss: SELL_ALERT_LOSS,
      pfView: "open", // "open" = active holdings, "sold" = old holdings
      refreshTimer: null,
      polling: false, // a background list refresh is in flight
      updatedAt: { portfolio: null, watchlists: null },
      dataVersion: 0, // bumped on every write/reload; stale background polls are dropped
      indices: [],
      indicesError: null,
      indexTimer: null,
      wlExpanded: {}, // "watchlist|instrument_id" -> details open
      today: localToday(),
      renaming: null,
      renameTo: "",
    };
  },

  computed: {
    // The Watchlists card shows only the list selected in the "Add to" chips.
    shownWatchlists() {
      return this.watchlists.filter((w) => w.name === this.wlChoice);
    },

    healthTitle() {
      const { message, checkedAt } = this.health;
      const when = checkedAt ? `Last checked ${checkedAt.toLocaleTimeString()}` : "Not checked yet";
      return `${message ? message + " · " : ""}${when}. Click to re-check (auto every ${HEALTH_CHECK_MS / 1000}s).`;
    },

    // Running totals for the "new position" form.
    pfNewTotals() {
      const valid = this.pfNew.tranches.filter(validTranche);
      const qty = valid.reduce((n, t) => n + t.quantity, 0);
      const amount = valid.reduce((n, t) => n + t.quantity * t.price, 0);
      return { qty, amount, avg: qty ? amount / qty : null };
    },

    pfNewValid() {
      return this.pfNew.tranches.length > 0 && this.pfNew.tranches.every(validTranche);
    },

    openCount() {
      return this.portfolio.filter((i) => i.status === "open").length;
    },

    // Rows for the current view, P&L (realized or unrealized) descending;
    // positions without a P&L yet (no tranches / no LTP) sink to the bottom.
    pfRows() {
      return this.portfolio
        .filter((i) => i.status === this.pfView)
        .sort((a, b) => (this.pnlOf(b) ?? -Infinity) - (this.pnlOf(a) ?? -Infinity));
    },

    totals() {
      const t = { invested: 0, value: 0, unrealized: 0, realized: 0 };
      for (const i of this.portfolio) {
        if (i.status === "sold") {
          t.realized += i.realized_pnl || 0;
        } else if (i.invested !== null) {
          t.invested += i.invested;
          t.value += i.ltp !== null ? i.ltp * i.quantity : i.invested;
          t.unrealized += i.unrealized_pnl || 0;
        }
      }
      return t;
    },
  },

  async mounted() {
    this.syncTab();
    window.addEventListener("hashchange", this.syncTab);
    await this.checkHealth(); // loads the data once the service is up
    this.healthTimer = setInterval(() => {
      if (!document.hidden) this.checkHealth();
    }, HEALTH_CHECK_MS);
    this.scheduleRefresh();
    this.pollIndices();
  },

  unmounted() {
    clearTimeout(this.refreshTimer);
    clearTimeout(this.indexTimer);
    clearInterval(this.healthTimer);
  },

  methods: {
    syncTab() {
      const id = location.hash.replace("#", "");
      this.tab = this.tabs.some((t) => t.id === id) ? id : "portfolio";
    },

    showError(e) {
      this.notice = null;
      this.error = e instanceof ApiError ? { title: e.title, message: e.message } : { title: "Error", message: String(e) };
    },

    showNotice(msg) {
      this.error = null;
      this.notice = msg;
    },

    // Runs an API action with the shared busy flag and error handling.
    async run(fn) {
      this.busy = true;
      this.dataVersion++;
      try {
        return await fn();
      } catch (e) {
        this.showError(e);
      } finally {
        this.busy = false;
      }
    },

    fmtDate(iso) {
      return iso ? new Date(iso).toLocaleString() : "";
    },

    // `digits` = max decimals (rounded); the list's Amount and P&L use 0.
    money(v, digits = 2) {
      return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits });
    },

    pct(v) {
      return v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
    },

    pnlClass(v) {
      return v > 0 ? "gain" : v < 0 ? "loss" : "";
    },

    readyToSell(i) {
      return i.status === "open" && i.unrealized_pnl !== null && i.unrealized_pnl <= -SELL_ALERT_LOSS;
    },

    pnlOf(i) {
      return i.status === "sold" ? i.realized_pnl : i.unrealized_pnl;
    },

    // T1 -> sell date (sold) or today (open), in weeks to one decimal.
    holdingWeeks(i) {
      if (!i.first_buy_date) return "—";
      const days = daysBetween(i.first_buy_date, i.sell_date || this.today);
      return `${(days / 7).toFixed(1)} wk`;
    },

    toggle(id) {
      this.expanded[id] = !this.expanded[id];
    },

    // Runs at startup, every HEALTH_CHECK_MS and when the pill is clicked.
    // Only the first check and manual ones show "Checking…"; the error
    // banner appears when the service goes down, not on every failed tick;
    // and data (re)loads whenever the service comes (back) up.
    async checkHealth({ manual = false } = {}) {
      const prev = this.health.state;
      const first = this.health.checkedAt === null;
      if (manual || first) this.health = { ...this.health, state: "checking" };
      let ok = false;
      try {
        const data = await api("GET", "/health");
        ok = Boolean(data && data.status === "ok");
        this.health = {
          state: ok ? "ok" : "down",
          message: ok ? "Portfolio service responded: status ok" : `Unexpected response: ${JSON.stringify(data)}`,
          checkedAt: new Date(),
        };
        if (!ok && (manual || prev !== "down")) this.showError(new ApiError("GET /health failed", this.health.message));
      } catch (e) {
        this.health = { state: "down", message: e.message, checkedAt: new Date() };
        if (manual || prev !== "down") this.showError(e);
      }
      if (ok && this.error && this.error.title.startsWith("GET /health")) this.error = null;
      if (ok && (first || prev === "down")) {
        this.loadWatchlists();
        this.loadPortfolio();
      }
    },

    async loadWatchlists() {
      this.loading.watchlists = true;
      try {
        this.dataVersion++;
        this.watchlists = await api("GET", "/watchlists");
        this.updatedAt.watchlists = new Date();
      } catch (e) {
        this.showError(e);
      } finally {
        this.loading.watchlists = false;
      }
    },

    async loadPortfolio() {
      this.loading.portfolio = true;
      try {
        this.dataVersion++;
        this.portfolio = await api("GET", "/portfolio");
        this.updatedAt.portfolio = new Date();
      } catch (e) {
        this.showError(e);
      } finally {
        this.loading.portfolio = false;
      }
    },

    // Auto-refresh loop for the open tab's list: a chain of timeouts (not
    // setInterval) so requests never overlap, and a tab/view switch picks up
    // its own interval on the next tick.
    scheduleRefresh() {
      clearTimeout(this.refreshTimer);
      if (!LIST_TABS.includes(this.tab)) return; // the Screener tab doesn't auto-refresh
      const ms = this.tab === "portfolio" ? PORTFOLIO_REFRESH_MS[this.pfView] : WATCHLIST_REFRESH_MS;
      this.refreshTimer = setTimeout(async () => {
        await this.pollList();
        this.scheduleRefresh();
      }, ms);
    },

    // Silent refresh of `this.portfolio` or `this.watchlists` (the field is
    // named after the tab and its API path). Skipped while the browser tab is
    // hidden or a user action is saving; dropped if a write/reload happened
    // meanwhile. Doesn't toggle the Refresh button, and shows a failure once
    // instead of every tick.
    async pollList() {
      const kind = this.tab;
      if (document.hidden || this.busy || this.polling || this.loading[kind]) return;
      this.polling = true;
      const version = this.dataVersion;
      try {
        const fresh = await api("GET", `/${kind}`);
        if (version !== this.dataVersion || kind !== this.tab) return;
        this[kind] = fresh;
        this.updatedAt[kind] = new Date();
        if (this.error && this.error.title.startsWith(`GET /${kind}`)) this.error = null;
      } catch (e) {
        if (!(this.error && this.error.title === e.title && this.error.message === e.message)) this.showError(e);
      } finally {
        this.polling = false;
      }
    },

    // Header ticker. Failures stay quiet (the ticker shows "—" with the
    // reason on hover) so a flaky quote never spams the error banner.
    async pollIndices() {
      clearTimeout(this.indexTimer);
      if (!document.hidden) {
        try {
          this.indices = await api("GET", "/market/indices");
          this.indicesError = null;
        } catch (e) {
          this.indicesError = e.message;
        }
      }
      this.indexTimer = setTimeout(this.pollIndices, INDEX_REFRESH_MS);
    },

    wlKey(w, i) {
      return `${w.name}|${i.instrument_id}`;
    },

    queueSearch(s) {
      clearTimeout(s.timer);
      s.seq++; // invalidate any in-flight request for an older query
      if (s.q.length < MIN_SEARCH_CHARS) {
        Object.assign(s, { results: [], loading: false, searched: false });
        return;
      }
      s.loading = true;
      s.timer = setTimeout(() => this.runSearch(s), SEARCH_DEBOUNCE_MS);
    },

    async runSearch(s) {
      const seq = s.seq;
      try {
        const results = await api("GET", `/search?q=${enc(s.q)}`);
        if (seq !== s.seq) return;
        s.results = results.slice(0, SEARCH_LIMIT);
        s.searched = true;
      } catch (e) {
        if (seq !== s.seq) return;
        s.results = [];
        this.showError(e);
      } finally {
        if (seq === s.seq) s.loading = false;
      }
    },

    // From the Screener tab, which picks its own target watchlist.
    addToNamedWatchlist(name, instrumentId) {
      return this.run(async () => {
        await api("POST", `/watchlists/${enc(name)}/items`, { instrument_id: instrumentId });
        await this.loadWatchlists();
        this.showNotice(`Added ${instrumentId} to watchlist "${name}".`);
      });
    },

    addToWatchlist(instrumentId) {
      const name = this.wlChoice;
      return this.run(async () => {
        await api("POST", `/watchlists/${enc(name)}/items`, { instrument_id: instrumentId });
        await this.loadWatchlists();
        this.showNotice(`Added ${instrumentId} to watchlist "${name}".`);

        // Close the results and clear the box — unless a new search was typed meanwhile.
        const q = this.wlSearch.q;
        setTimeout(() => {
          if (this.wlSearch.q === q) this.wlSearch = newSearch();
        }, WL_SEARCH_CLEAR_MS);
      });
    },

    createWatchlist() {
      const name = this.wlNewName.trim();
      if (!name) return;
      return this.run(async () => {
        const created = await api("POST", "/watchlists", { name });
        await this.loadWatchlists();
        this.wlChoice = created.name; // ready to add stocks to it
        this.cancelCreateWatchlist();
        this.showNotice(`Created watchlist "${created.name}".`);
      });
    },

    cancelCreateWatchlist() {
      this.creatingWl = false;
      this.wlNewName = "";
    },

    // Is the stock already in the watchlist selected in the "Add to" chips?
    inSelectedWatchlist(instrumentId) {
      const w = this.watchlists.find((x) => x.name === this.wlChoice);
      return Boolean(w && w.items.some((i) => i.instrument_id === instrumentId));
    },

    removeFromWatchlist(name, instrumentId) {
      return this.run(async () => {
        await api("DELETE", `/watchlists/${enc(name)}/items/${enc(instrumentId)}`);
        await this.loadWatchlists();
        this.showNotice(`Removed ${instrumentId} from "${name}".`);
      });
    },

    renameWatchlist(oldName) {
      const newName = this.renameTo;
      if (newName === oldName) {
        this.renaming = null;
        return;
      }
      return this.run(async () => {
        await api("PATCH", `/watchlists/${enc(oldName)}`, { name: newName });
        this.renaming = null;
        if (this.wlChoice === oldName) this.wlChoice = newName;
        await this.loadWatchlists();
        this.showNotice(`Renamed "${oldName}" to "${newName}".`);
      });
    },

    selectForPortfolio(instrument) {
      this.pfNew = { instrument, tranches: [blankTranche(this.today)], notes: "" };
    },

    addNewTranche() {
      this.pfNew.tranches.push(blankTranche(this.today));
    },

    addToPortfolio() {
      const { instrument, tranches, notes } = this.pfNew;
      return this.run(async () => {
        await api("POST", "/portfolio", {
          instrument_id: instrument.instrument_id,
          notes: notes || null,
          tranches,
        });
        this.pfNew = { instrument: null, tranches: [], notes: "" };
        this.pfSearch = newSearch();
        await this.loadPortfolio();
        this.showNotice(`Added ${instrument.symbol} to portfolio.`);
      });
    },

    // Handler for <portfolio-detail>'s save event: every detail endpoint
    // returns the updated item, so swap it into the list in place.
    savePortfolio(method, path, body, notice) {
      return this.run(async () => {
        const updated = await api(method, path, body);
        const idx = this.portfolio.findIndex((p) => p.instrument_id === updated.instrument_id);
        if (idx >= 0) this.portfolio[idx] = updated;
        this.showNotice(notice);
      });
    },

    removeFromPortfolio(item) {
      if (!confirm(`Remove ${item.symbol || item.instrument_id} and all its tranches from the portfolio?`)) return;
      return this.run(async () => {
        await api("DELETE", `/portfolio/${enc(item.instrument_id)}`);
        delete this.expanded[item.instrument_id];
        await this.loadPortfolio();
        this.showNotice(`Removed ${item.symbol || item.instrument_id} from portfolio.`);
      });
    },
  },

  watch: {
    // Keep the watchlist picker valid as lists load, get renamed in another
    // tab, or disappear: fall back to the first list (null when none).
    watchlists(lists) {
      const names = lists.map((w) => w.name);
      if (!names.includes(this.wlChoice)) this.wlChoice = names[0] ?? null;
    },

    "wlSearch.q"() {
      this.queueSearch(this.wlSearch);
    },
    "pfSearch.q"() {
      this.queueSearch(this.pfSearch);
    },

    pfView() {
      this.scheduleRefresh();
    },

    // Refresh data when switching tabs so it never looks stale.
    tab(t) {
      this.scheduleRefresh();
      if (t === "watchlists") this.loadWatchlists();
      if (t === "portfolio") this.loadPortfolio();
    },
  },
})
  .component("portfolio-detail", PortfolioDetail)
  .component("fundamentals-panel", FundamentalsPanel)
  .component("screener-tab", ScreenerTab)
  .component("analysis-tab", AnalysisTab)
  .mount("#app");
