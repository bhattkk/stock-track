# Front End

A minimal Vue 3 UI for the [portfolio](../portfolio) and
[screener](../screener) services. No build step: Vue is loaded from a CDN
and the app is a handful of static files in `public/`:
- `api.js`: shared fetch helper
- `app.js`: the app plus the Portfolio and Watchlists tabs
- `screener.js`: the Screener tab

## Run

```bash
docker compose up -d front-end     # also starts portfolio, postgres, redis
open http://localhost:8080
```

nginx serves `public/`, proxies `/api/screener/*` to `screener:8000` and
other `/api/*` calls to `portfolio:8000`. The browser only talks to one
origin, so the backends need no CORS config. The screener runs on demand.
While it's stopped, the Screener tab shows the command that starts it.
`public/` is bind-mounted — edit a file and refresh the browser.

## Pages

The app opens on **Portfolio**. The header shows a **NIFTY 50** ticker (LTP,
change and % vs previous close; hover for the time) refreshed every 2 s
(`INDEX_REFRESH_MS`) while the tab is visible. The **Service OK / Service down** pill in the
header calls `GET /health` on load, every 30 s (`HEALTH_CHECK_MS`) while the
tab is visible, and when clicked; hover it for the last result. If the
service is down at startup, data loads automatically once it's back.

| Page | What it does |
|---|---|
| **Watchlists** (`#watchlists`) | Search stocks as you type, pick the target list from the "Add to" chips (each shows its stock count; results already in it show "✓ In …" instead of Add), create an empty list with the **+ New watchlist** chip; the list below shows only the watchlist selected in the chips, remove items, rename watchlists. Each stock shows LTP and day change vs previous close; **Show details** opens the same Fundamentals panel as the portfolio. Auto-refreshes every 2 s (`WATCHLIST_REFRESH_MS`). |
| **Screener** (`#screener`) | **Data** card: coverage (stocks with results / listed), price date, last refresh and **Refresh data** (with live progress). **Screen** card: saved-screen picker (Save / Save as… / Delete) and "all of" conditions (indicator, operator, value, with each indicator's definition below it). The indicator list and the **Columns** chooser come from the screener's `GET /indicators`. **Results**: sortable columns, **Details** (yearly and quarterly statements plus every indicator; hover a value for its inputs) and **+ Watchlist** into the watchlist picked in "Add to". Doesn't auto-refresh. |
| **Portfolio** (`#portfolio`) | Search as you type, Select a stock, enter T1 (buy date, qty, price) and use **+ Add tranche** for T2, T3, … (no limit), plus optional notes; the form shows running totals. The list shows **Active** holdings by default and **Old holdings** (sold) via a toggle, each sorted by P&L descending, with total qty, avg price, amount, LTP, realized/unrealized P&L, P&L % and holding period in weeks (T1 to today, or to the sell date), plus portfolio totals. **Show details** opens the entries & exits log (every tranche, totals and the exit) with the max hold date for active holdings (T1 + 9 months, with days left / overdue), add/edit/delete tranche, an Exit panel to mark an active holding as sold (sold holdings edit or undo the exit from its row in the log instead), the "why I bought it" notes, fundamentals (sector, key ratios vs sector, 4 quarters of shareholding with the latest change, and a collapsible company profile — from Upstox, refreshed daily) and Remove from portfolio. |

The portfolio list auto-refreshes every **2 s** on Active and **10 s** on
Old holdings (`PORTFOLIO_REFRESH_MS` in `app.js`), only while the Portfolio
tab is open and the browser tab is visible. Background refreshes are silent
(a failure is shown once, not every tick), never overwrite a save that
happened meanwhile, and don't reset unsaved notes/sell inputs in an open
details panel.

Active rows are highlighted when a rule fires:

- **Ready to sell** (light red): unrealized loss ≥ `SELL_ALERT_LOSS` in
  `app.js` (₹40,000).
- **Exit — book profit** (light green): ≥ 3 tranches, the trailing SL
  (peak close since T1 − 11%) is above avg cost, and LTP has fallen to/below
  it. Details show the peak, SL and whether the rule is armed.

Search starts once 2+ characters are typed (debounced 250 ms) and shows at
most 5 matches.

## Errors

Every API call goes through `api()` in `app.js`. Any failure — network error,
nginx 502 when the portfolio container is down, or a FastAPI error such as
404 "Unknown instrument_id" / 409 "already exists" / 422 validation — is
shown in the red banner at the top with the method, path, HTTP status and
the service's `detail` message. The health pill in the header always shows the
last health-check result.
