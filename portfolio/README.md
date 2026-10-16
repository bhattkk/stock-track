# Portfolio Service (Step 2)

Owns watchlists and the portfolio, exposed as a small FastAPI app, backed by
Postgres. Reads the instrument master and drives instrument subscriptions in
the Redis that [market-data](../market-data) owns.

## Architecture

```
docker-compose
├── redis                      (shared with market-data)
├── postgres                   watchlists, watchlist_items, portfolio_items, portfolio_tranches, instrument_fundamentals
└── portfolio (Python/FastAPI)
    ├── routers/
    │   ├── search.py          SearchStock
    │   ├── watchlists.py      AddToWatchlist / RemoveFromWatchlist / EditWatchlist
    │   └── portfolio.py       AddToPortfolio / RemoveFromPortfolio
    ├── crud.py                Postgres read/write functions
    ├── models.py               SQLAlchemy ORM: Watchlist, WatchlistItem, PortfolioItem, PortfolioTranche
    ├── redis_store.py         INSTRUMENTS search/lookup + SUBSCRIBED_INSTRUMENTS writes
    ├── backup.py               periodic pg_dump -> /backups
    └── main.py                 FastAPI app, startup, backup scheduler
```

### Instrument ids

Both services key everything on `instrument_id` (formerly `instrument_key`
in market-data) — the provider's own instrument identifier, e.g. Upstox's
`NSE_EQ|INE733E01010`. No separate id scheme.

### Subscription lifecycle

market-data polls whatever is in Redis' `SUBSCRIBED_INSTRUMENTS` set — it
doesn't decide what belongs there. This service does:

- Adding an instrument to a watchlist **or** the portfolio subscribes it
  (`SADD SUBSCRIBED_INSTRUMENTS instrument_id`).
- Removing it unsubscribes it (`SREM`) **only if** nothing else — another
  watchlist, or the portfolio — still references that `instrument_id`
  (`crud.is_instrument_referenced`). Postgres is the source of truth for
  this; there's no separate refcount table, it's a live query.

## API

| Method & path | Maps to | Notes |
|---|---|---|
| `GET /search?q=` | **SearchStock** | Scans Redis `INSTRUMENTS`, matches symbol/name substrings case-insensitively, symbol-prefix matches ranked first. Returns at most `SEARCH_RESULT_LIMIT` (default 5) `{instrument_id, symbol, name}`. |
| `GET /watchlists` | list all watchlists | Items include `symbol`/`name` from Redis, `ltp`/`prev_close`/`day_change_pct` from market-data's `INSTRUMENT_PRICES`, and cached `fundamentals` (same shape as the portfolio's). |
| `POST /watchlists` `{name}` | create watchlist | Creates an empty watchlist (name trimmed). 409 if the name is taken. |
| `GET /watchlists/{name}` | one watchlist | 404 if it doesn't exist. |
| `PATCH /watchlists/{name}` `{name: new_name}` | **Edit watchlist** | Renames; 409 if the new name is already taken. |
| `POST /watchlists/{name}/items` `{instrument_id}` | **AddToWatchlist** | Creates the watchlist if `name` doesn't exist yet. 404 if `instrument_id` isn't a known instrument. Subscribes it in Redis. |
| `DELETE /watchlists/{name}/items/{instrument_id}` | **RemoveFromWatchlist** | 404 if not present. Unsubscribes in Redis if nothing else references the instrument. |
| `GET /portfolio` | list portfolio | Each item includes tranches, position and P&L (see below). |
| `GET /portfolio/{instrument_id}` | one position | 404 if not in the portfolio. |
| `POST /portfolio` `{instrument_id, tranches?, notes?}` | **AddToPortfolio** | Same instrument validation + subscribe behavior as watchlists. A bare `{instrument_id}` re-add is a no-op; re-adding *with* tranches is a 409. |
| `PATCH /portfolio/{instrument_id}` `{notes?, sell_date?, sell_price?}` | edit notes / record exit | Only fields sent are changed. `sell_date` + `sell_price` go together; send both as `null` to undo a sell. Sell date can't precede the last buy. |
| `POST /portfolio/{instrument_id}/tranches` `{buy_date, quantity, price}` | add tranche | No limit on count. `buy_date` may be in the past, not the future. |
| `PUT /portfolio/{instrument_id}/tranches/{tranche_id}` | edit tranche | |
| `DELETE /portfolio/{instrument_id}/tranches/{tranche_id}` | delete tranche | Returns the updated position. |
| `POST /portfolio/fundamentals/refresh` | refresh fundamentals | Re-fetches every portfolio instrument's fundamentals from Upstox now; returns the portfolio. |
| `POST /portfolio/peaks/refresh` | refresh trailing-SL peaks | Re-fetches every open position's highest close from Upstox now; returns the portfolio. |
| `DELETE /portfolio/{instrument_id}` | **RemoveFromPortfolio** | Deletes its tranches too. Same unsubscribe-if-unreferenced behavior. |
| `GET /market/indices` | index ticker | NIFTY 50 `{name, ltp, prev_close, change, change_pct, as_of}`, fetched live from Upstox's LTP API (not market-data's 60 s sweep) behind a 1.5 s in-process cache; serves the last good quote for up to 60 s if Upstox fails, then 502. |
| `GET /health` | liveness | |

Interactive docs at `/docs` once the service is running (FastAPI's built-in
Swagger UI).

### Portfolio positions

A position is bought in any number of **tranches** (T1, T2, …, ordered by buy date;
one `portfolio_tranches` row each;
`quantity` is number of shares, `price` is per share) and optionally sold in
full (`sell_date`, `sell_price`). `notes` holds the free-text buy thesis.
Derived fields are computed on every read, never stored:

| Field | Formula |
|---|---|
| `avg_cost` | Σ(qty × price) / Σqty |
| `ltp` | latest quote from market-data's `INSTRUMENT_PRICES` (null until polled) |
| `unrealized_pnl` | (ltp − avg_cost) × qty — open positions only |
| `realized_pnl` | (sell_price − avg_cost) × qty — sold positions only |
| `pnl_pct` | (exit-or-ltp / avg_cost − 1) × 100 |
| `max_holding_date` | T1 buy date + 9 calendar months |
| `peak_price` | max(highest daily **close** since T1, ltp) — open positions only |
| `trailing_sl` | peak_price × (1 − 11%) |
| `sl_locks_profit` | trailing_sl > avg_cost |
| `profit_exit` | ≥ 3 tranches **and** sl_locks_profit **and** ltp ≤ trailing_sl |

The highest close since T1 is cached on `portfolio_items` (`peak_close`,
`peak_close_date`, `peak_refreshed_on`) from Upstox's V3 historical daily
candles, using the same read-only Analytics token as market-data
(`UPSTOX_ACCESS_TOKEN`). It's refreshed by `peaks.py` at startup, Mon–Fri
16:15 IST, and immediately whenever a position's tranches change (or it's
un-sold). A failed fetch keeps the previous value. The 11% / 3-tranche
rules are `TRAILING_SL_PCT` / `PROFIT_EXIT_MIN_TRANCHES` in
`routers/portfolio.py`.

`fundamentals` comes from Upstox's Company Fundamentals API (keyed by ISIN,
same Analytics token) and is cached per instrument in the
`instrument_fundamentals` table (JSONB `data`, `fetched_on`):

| Upstox endpoint | Becomes |
|---|---|
| `/v2/fundamentals/{isin}/key-ratios` | `ratios`: `[{name, company, sector, unit}]` — P/E (TTM), P/B, ROA, ROE, ROCE, Quick Ratio, EV/EBITDA vs sector; `unit` is `%` or empty |
| `/v2/fundamentals/{isin}/profile` | `sector`, `profile` (company description) |
| `/v2/fundamentals/{isin}/share-holdings` | `shareholding`: `{periods: [newest first], rows: [{category, values}]}` — Promoters, FII, Mutual funds, Other DII, Retail & others |

`fundamentals.py` refreshes every portfolio instrument (active and sold) and
every watchlist instrument at
startup — skipping anything already fetched today — and daily at 06:00 IST
(after Upstox's 05:00 refresh), plus immediately when a new instrument is
added to the portfolio or a watchlist. A failed call keeps the previous data; a partial failure stores what
did load. Sector benchmark values move with market prices during the day,
so they're a once-a-day snapshot. All Upstox calls go through
`upstox_client.upstox_get`.

### Schema changes

There's no migration tool. `db.init_schema()` runs `create_all()` (new
tables) plus idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements
(new columns on existing tables) on every startup.

## Postgres backups

`backup.py` runs `pg_dump` on an APScheduler timer (`BACKUP_INTERVAL_MINUTES`,
default 10) and writes timestamped `.sql` files to `BACKUP_DIR` (default
`/backups`, a named Docker volume in compose so it survives container
recreation). Only the most recent `BACKUP_RETENTION` (default 12, i.e. 2
hours of history at the default interval) dumps are kept — older ones are
pruned after each successful backup. Set `BACKUP_INTERVAL_MINUTES=0` to
disable.

To restore on a new host:
```bash
docker cp stocktrack-portfolio:/backups/<latest>.sql ./restore.sql
docker exec -i stocktrack-postgres psql -U portfolio -d portfolio < ./restore.sql
```

## Quick start

```bash
cp .env.example .env   # fill in POSTGRES_* if you want non-default creds
docker compose up --build
# service listens on :8001 (mapped from the container's :8000)

curl "localhost:8001/search?q=NTPC"
curl -X POST localhost:8001/watchlists/core/items \
  -H 'content-type: application/json' -d '{"instrument_id": "NSE_EQ|INE733E01010"}'
curl localhost:8001/watchlists
```

## Logging

Logs go to stdout (visible via `docker compose logs -f portfolio`) and are
also appended to `logs/portfolio.log` at the repo root — one file, reused
across restarts, mounted into the container via docker-compose. Startup
(Postgres schema, Redis connection, backup scheduler) is logged at INFO, and
every API request is logged on the way in (method, path, query/body) and on
the way out (status code).

## Config reference
See `.env.example` at the repo root for every setting.
