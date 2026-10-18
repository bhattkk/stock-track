# Screener Service

Screens every NSE-listed stock on fundamentals computed from the companies'
own XBRL results filings. It's a separate, **on-demand** service: it isn't
started by a plain `docker compose up`, has no scheduler, and only fetches
data when you trigger a refresh. It needs Postgres only (no Redis, no
market-data); its tables are prefixed `screener_` and live in the same
database as the portfolio.

```bash
docker compose --profile screener up -d screener   # start (API on :8002, UI at #screener)
docker compose stop screener                        # stop when done; data stays in Postgres
```

## Data flow

```
NSE EQUITY_L.csv ──────────────▶ screener_stocks        universe (~2,600 EQ/BE stocks) + latest close
NSE bhavcopy ──────────────────▶   (close, close_date)
NSE results listings ──────────▶ screener_filings       one row per XBRL filing (quarter × basis)
  integrated-filing-results        (Dec 2024 →)
  corporates-financial-results     (→ Dec 2024)
XBRL files (cached on disk) ───▶ screener_facts         long format: stock × basis × period × metric
                                        │
                        indicators/ (registered functions)
                                        ▼
                                 screener_indicator_values   one row per stock × indicator
                                        │
               screen_engine.py (rule tree → SQL EXISTS per condition)
                                        ▼
                                 POST /run, saved screener_screens
```

- **Universe:** NSE's `EQUITY_L.csv`. `instrument_id = "NSE_EQ|<ISIN>"`, the
  same key the portfolio and market-data use, so "+ Watchlist" in the UI
  calls the portfolio API directly.
- **Filings kept per stock:** the latest 8 quarters plus each March quarter
  of the last 6 years (Q4 filings carry the full year). Consolidated is used
  when filed, else standalone; the newest revision wins.
- **Balance sheets** exist in XBRL only from FY23 (older filings carry P&L
  and cash flow only), so balance-sheet history is 3–4 years deep. That's
  enough for 3-year ROE/ROCE averages and the 3-year debt change.
- **Filing quirks handled in `providers/xbrl.py`:**
  - Legacy filings stamp year-to-date contexts with the quarter's dates.
    The parser uses each context's reported dates instead.
  - Pre-2022 filings reference contexts that are never defined.
  - Banking/NBFC formats are flagged.
- **Typos in filings:** reported PAT is checked against PBT and tax
  (`Period._d_net_profit`).

## Refreshing

| How | Command |
|---|---|
| UI | Screener tab → **Refresh data** (background thread; progress shown) |
| CLI, everything that may have new results | `docker compose --profile screener run --rm screener python cli.py refresh` |
| CLI, specific stocks | `… python cli.py refresh --symbols ASIANPAINT,TCS` |
| CLI, ignore what's stored | `… python cli.py refresh --full` |
| Recompute indicators only | `… python cli.py compute` |
| Re-parse cached XBRL after a `TAG_MAP` change | `… python cli.py reparse` |

- **First refresh:** a back-fill of about 12 filings per stock, throttled
  to `NSE_REQUEST_DELAY` (0.4 s), so it takes a few hours.
- **Later refreshes are incremental:**
  - a stock's filing list is only fetched once a newer quarter's results
    could be out (10+ days after the quarter ends, re-checked every 3 days
    for late filers);
  - parsed filings are never downloaded again.
- **One refresh at a time:** UI and CLI runs share a Postgres advisory lock.
- **Interrupted runs:** a run interrupted by a restart is marked
  `interrupted`. The next refresh resumes, because finished stocks aren't
  due again.

## Indicators

Each indicator is a function decorated with `@indicator` in `indicators/`
(see `indicators/base.py`). Stacking the decorator registers parameter
variants (e.g. `cfo_to_pat_3y` / `cfo_to_pat_5y`). `GET /indicators` serves
the registry, and the UI builds its filter list and column chooser from it.
So **adding an indicator needs no schema, API or UI change**: add the
function, then run `python cli.py compute`.

| Your criterion | Indicators (v1) |
|---|---|
| OPM improvement | `opm_latest`, `opm_ttm`, `opm_change_3y/5y`, `opm_ttm_change`, `opm_rising_years` |
| Backward integration / pricing power (proxies) | `gross_margin_latest`, `gross_margin_change_3y`, `gross_margin_stability_5y`, `opm_stability_5y` |
| Debt / equity | `debt_to_equity`, `net_debt_to_equity`, `interest_coverage`, `current_ratio` |
| Capex not funded by debt | `capex_3y`, `capex_to_cfo_3y`, `debt_change_3y`, `capex_self_funded_3y` (debt rose ≤ 25% of capex) |
| Good ROE / ROCE | `roe_latest`, `roe_3y_avg`, `roce_latest`, `roce_3y_avg` |
| CFO ≥ 70% of PAT | `cfo_to_pat_3y`, `cfo_to_pat_5y` |
| Common | `price`, `market_cap`, `pe`, `pb`, `sales_cagr_3y/5y`, `profit_cagr_3y/5y`, `sales_growth_ttm`, `fcf_3y` |

Definitions follow Screener.in:
- **OPM:** (revenue − expenses excl. interest & depreciation) / revenue,
  before other income.
- **ROCE:** EBIT (excl. exceptional items) / average (equity + borrowings).

Banks, NBFCs and insurers only get the price-based indicators for now.

**Not built yet** (next phases in the plan):
- shareholding and pledge (promoter quality);
- BRSR employee counts;
- manual / LLM-suggested tags for B2B vs B2C, backward integration and
  capex purpose.

All three plug in as new providers plus indicators. Technical indicators
(MACD, candlestick patterns) plug in the same way once a candles input is
loaded into `StockContext`.

## API

| Endpoint | |
|---|---|
| `GET /indicators` | registry metadata (key, name, category, kind, unit, description) |
| `POST /run` | `{rules, columns, sort, limit}` → matching stocks with the requested values |
| `GET/POST /screens`, `PUT/DELETE /screens/{id}` | saved screens (a "Quality compounders" screen is seeded on first start) |
| `GET /stocks/{instrument_id}` | yearly and quarterly statements, every indicator with the numbers behind it |
| `POST /refresh` | `{symbols?, full?, compute_only?}` → starts a background refresh (409 if one is running) |
| `GET /refresh` | latest run's progress plus coverage |
| `GET /health` | |

Rules are nested `all` / `any` groups of `{ind, op, value[, value2]}`.
- Ops: `> >= < <= eq neq between` for numbers, `is_true` / `is_false` for
  yes/no indicators, `is_null` / `not_null` for any indicator.
- A stock without a value (not enough history, not applicable) never matches
  a comparison.

## Tests

```bash
docker compose --profile screener run --rm --no-deps screener python -m pytest -q
```

The fixtures are real Asian Paints XBRL filings (public NSE data), one in
each of the three formats.

## Configuration

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | set by compose | the shared Postgres |
| `NSE_REQUEST_DELAY` | `0.4` | seconds between NSE requests |
| `HISTORY_YEARS` / `HISTORY_QUARTERS` | `6` / `8` | filings kept per stock |
| `SERIES` | `EQ,BE` | NSE series in the universe |
| `RAW_DIR` | `/data/raw` | XBRL cache (`screener-raw` volume) |
