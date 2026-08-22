# Market Data Service (Step 1)

A standalone microservice that keeps an NSE equity instrument master in
Redis, polls a subscription list also in Redis, and continuously snapshots
LTP quotes back into Redis, via Upstox (read-only, year-long Analytics
Token).

## Architecture

```
docker-compose
├── redis                     instrument master, subscriptions in, prices out
└── market-data (Python)
    ├── providers/            base interface + upstox
    ├── instruments.py        fetch BOD dump -> filter equities -> Redis
    ├── ingest.py             poll subscriptions -> batched LTP fetch -> Redis
    ├── store.py              Redis key schema + helpers
    └── main.py               auth, scheduler
```

### Instrument master

Upstox's `instrument_key` (e.g. `NSE_EQ|INE733E01010`, ISIN-based) is used as
this system's `instrument_id` everywhere — subscriptions and prices are keyed
by it directly, no separate id scheme.

The instrument master is refreshed daily straight from Upstox's "BOD"
(Beginning-Of-Day) dump at
`https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz` —
fetched, gunzipped, and filtered down to equities (`instrument_type == "EQ"`)
— and cached in Redis for lookups/search. It is not required for the price
sweep itself, since `SUBSCRIBED_INSTRUMENTS` already holds instrument_ids
directly.

### Instrument subscriptions

This service does not decide what to subscribe to. Another service (the
`portfolio` service) maintains the `SUBSCRIBED_INSTRUMENTS` set in Redis —
instrument_ids to poll. Each
sweep reads that set, splits it into batches of `QUOTE_BATCH_SIZE`, and
fetches LTP quotes for each batch via Upstox's v3 LTP endpoint.

### Redis keys
| Key | Type | Contents |
|-----|------|----------|
| `INSTRUMENTS` | hash | `instrument_id -> json(instrument)` — equity master (symbol/name/exchange/segment/isin), refreshed daily |
| `SUBSCRIBED_INSTRUMENTS` | set | instrument_ids currently subscribed — written by another service |
| `INSTRUMENT_PRICES` | hash | `instrument_id -> json(quote)` — latest `ltp/cp/volume/ltq/ts` |

## Quick start

```bash
cp .env.example .env
# fill UPSTOX_ACCESS_TOKEN — generate at account.upstox.com/developer/apps
# -> Analytics tab -> Generate Token. Read-only (GET endpoints only, no order
# placement), no interactive login flow, valid for a year.
docker compose up --build

# find an instrument_id, then subscribe to it
docker exec -it stocktrack-redis redis-cli HGET INSTRUMENTS "NSE_EQ|INE733E01010"
docker exec -it stocktrack-redis redis-cli SADD SUBSCRIBED_INSTRUMENTS "NSE_EQ|INE733E01010"
```

Then:
```bash
docker exec -it stocktrack-redis redis-cli HGET INSTRUMENT_PRICES "NSE_EQ|INE733E01010"
```

## Config reference
See `.env.example` for every setting (poll interval, batch size, pacing,
instrument refresh hour).
