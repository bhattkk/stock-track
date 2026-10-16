# Stock Tracking & Portfolio System

A personal stock-tracking and portfolio-management system for monitoring
manually selected NSE stocks, running a tranche-based (pyramiding) strategy,
flagging exits by rule, and analysing performance over time. Market data,
history and fundamentals come from **Upstox** using a single read-only
Analytics token.

## Services

| Service | What it does | Details |
|---|---|---|
| [`market-data`](market-data/README.md) | Keeps the NSE equity instrument master in Redis (daily) and polls LTP quotes for every subscribed instrument (every `POLL_INTERVAL_SECONDS`, default 60 s). | Python, APScheduler |
| [`portfolio`](portfolio/README.md) | REST API for the portfolio (tranches, exits, P&L, trailing stop-loss), watchlists, stock search, fundamentals and the NIFTY 50 quote. Decides which instruments market-data polls. Backs up Postgres on a timer. | Python, FastAPI, SQLAlchemy |
| [`front-end`](front-end/README.md) | Single-page UI: Portfolio and Watchlists tabs, NIFTY 50 ticker and service-health pill. Served by nginx, which proxies `/api/*` to the portfolio service. | Vue 3 (CDN, no build step), nginx |
| `redis` | Shared state between services: instrument master, subscriptions, latest prices. | Redis 7 |
| `postgres` | Source of truth for portfolio, tranches, watchlists and cached fundamentals. | Postgres 16 |

## Architecture

```text
Browser ── http://localhost:8080
   │
   ▼
front-end (nginx) ── /api/* ──▶ portfolio (FastAPI :8001)
                                  │   ├── Postgres: portfolio_items, portfolio_tranches,
                                  │   │             watchlists, watchlist_items,
                                  │   │             instrument_fundamentals
                                  │   ├── Upstox: historical candles (peak close),
                                  │   │           fundamentals, NIFTY 50 LTP
                                  │   └── Redis: reads INSTRUMENTS / INSTRUMENT_PRICES,
                                  │              writes SUBSCRIBED_INSTRUMENTS
                                  ▼
                                Redis ◀── market-data ── Upstox: instrument master,
                                                         LTP quotes for subscriptions
```

- Every instrument is identified by Upstox's ISIN-based key, e.g.
  `NSE_EQ|INE733E01010`, across all services.
- Adding a stock to the portfolio or a watchlist subscribes it in Redis, and
  market-data starts polling it. Removing it unsubscribes it once nothing
  else references it.
- Order placement and broker execution are **not** built. Positions are
  entered manually.

## Strategy (as implemented)

Per stock, all derived on read from the stored tranches:

- **Entries:** any number of tranches (T1, T2, …, ordered by buy date), each
  with a buy date, quantity and price. Avg cost = Σ(qty × price) / Σqty.
- **Exit:** a full exit with a sell date and price. Sold positions move to
  *Old holdings* and show realized P&L; open ones show unrealized P&L
  against the live LTP.
- **Max holding date:** T1 + 9 months, shown with days left or overdue.
- **Loss alert:** an active stock whose unrealized loss reaches **₹40,000**
  is flagged *Ready to sell*.
- **Trailing stop-loss / profit exit:**
  - Peak = the highest daily **close** since T1, from Upstox history, or the
    live LTP if that's higher.
  - SL = peak − 11%.
  - *Exit — book profit* fires when **≥ 3 tranches** are deployed, the SL is
    above avg cost, and LTP ≤ SL.

The thresholds are single constants. The 11%, 3-tranche and 9-month rules
are in `portfolio/routers/portfolio.py`; the ₹40k alert is in
`front-end/public/app.js`.

## Front end

- **Portfolio:**
  - Active and Old holdings views, each sorted by P&L.
  - Columns: qty, avg price, amount, LTP, P&L, P&L % and holding period in
    weeks.
  - Add a position with any number of tranches.
  - **Show details:**
    - the entries & exits log
    - the max-hold and trailing-SL status
    - "why I bought it" notes
    - fundamentals
  - Auto-refresh: every 2 s on Active, 10 s on Old holdings.
- **Watchlists:**
  - Chips pick and create lists. Search as you type to add stocks.
  - Rows show LTP, day change and fundamentals.
  - Auto-refresh every 2 s.
- **Header:** NIFTY 50 ticker refreshed every 2 s, and a health pill that
  re-checks every 30 s.
- **Fundamentals** (Upstox Company Fundamentals API, cached daily):
  - sector
  - P/E, P/B, ROE, ROCE, ROA, EV/EBITDA, each vs its sector
  - four quarters of shareholding
  - the company profile

## Quick start

```bash
cp .env.example .env      # set UPSTOX_ACCESS_TOKEN (Upstox developer apps → Analytics tab)
docker compose up -d --build
open http://localhost:8080
```

| Port | Service |
|---|---|
| 8080 | front-end (the app) |
| 8001 | portfolio API (`/docs` for Swagger) |
| 5432 | Postgres |
| 6379 | Redis |

Configuration lives in `.env` (see `.env.example`). Main settings:

| Area | Variables |
|---|---|
| Upstox | `UPSTOX_ACCESS_TOKEN` |
| market-data | `POLL_INTERVAL_SECONDS`, `QUOTE_BATCH_SIZE`, `BATCH_PAUSE_SECONDS`, `INSTRUMENT_REFRESH_HOUR`, `VERBOSE` |
| Postgres | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| portfolio | `SEARCH_RESULT_LIMIT`, `BACKUP_INTERVAL_MINUTES`, `BACKUP_RETENTION` |

## Operations

- **Logs:**
  - `logs/market-data.log` and `logs/portfolio.log` at the repo root.
  - Also on stdout: `docker compose logs -f <service>`.
  - The portfolio service logs every API request, including the front end's
    auto-refresh polling.
- **Backups:** the portfolio service `pg_dump`s Postgres every 10 minutes into
  the `portfolio-backups` volume and keeps the last 12. Restore steps are in
  [`portfolio/README.md`](portfolio/README.md).
- **Schema changes:**
  - There's no migration tool. New tables are created on startup.
  - New columns are added by idempotent `ALTER TABLE … ADD COLUMN IF NOT
    EXISTS` statements in `portfolio/db.py`.
- **Scheduled jobs** (portfolio service, IST):
  - Peak closes: at startup and Mon–Fri 16:15.
  - Fundamentals: at startup (skipped if already fetched today) and daily
    at 06:00.
  - Backups: every 10 minutes.

## LLM Integration

Use historical portfolio and trading data to help an LLM analyse performance and identify potential improvements to the strategy.

Any strategy changes should first go through **backtesting and/or paper trading** before being considered for live execution.
