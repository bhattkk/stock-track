# analysis — Stage 1: technical filter (daily + weekly charts)

Every week this service filters every NSE equity on its daily and weekly
charts and keeps the stocks that are **already in an uptrend and showing a
fresh entry setup**. The Analysis tab in the front end lists them, and you
can search any stock to see exactly which rule it passed or failed, with the
numbers behind each decision.

This is stage 1 of a planned pipeline: technicals, then fundamental rules,
then an LLM review of news and earnings calls. Stage 1 is plain Python with
no LLM and no token cost.

- **Runs:** weekly on Saturday at 07:00 IST (configurable), or with "Run now" in the tab
  or `python cli.py run`.
- **Rules:** [`criteria/technicals.yaml`](criteria/technicals.yaml). That file is
  the configuration; the UI only reads it. Each rule has a `why`.
- **Data:** daily candles from Upstox (stored incrementally in Postgres). Weekly
  bars are built from them. The universe is every company share (ISIN `INE…`; ETFs and fund units are excluded) in the `INSTRUMENTS` hash in Redis (owned by market-data), about 2,350 stocks.
- **Independence:** it never calls another service's API or imports its code. It
  reads Redis, and it writes only its own `analysis_*` tables.

## How a run works

```text
INSTRUMENTS (Redis, ~2,350 NSE company shares)
   │
   ▼  sync      Upstox daily candles → analysis_candles (only missing bars; ~1 request/stock,
   │            1.1 s apart because of the Upstox rate limit → ~45 min per run)
   ▼  evaluate  per stock: indicators → metrics → A gates → B gates → C setups → D flags
   ▼  rank      RS percentile across the universe → score
   ▼  store     analysis_results (one row per stock, every rule's outcome) + funnel counts on analysis_runs
```

Every layer is evaluated for every stock, even after it fails, so the
breakdown of a rejected stock is complete. `failed_at` records the first
layer it failed: `data`, `tradability`, `trend`, `setup` or `flag`.

## Pass logic

A stock **passes** when:

1. **A. Tradability:** every gate passes,
2. **B. Trend:** every gate passes,
3. **C. Entry setups:** at least `setups.min_fired` (default 1) setups fire,
4. **D. Red flags:** no flag with `action: reject` fires. A `warn` flag only lowers the score.

A rule whose data is missing is **NA**, and NA never counts as a pass. For
example, a stock with 120 days of history can't be judged on its 200-day average.

The idea in one line: **A** keeps stocks you can trade, **B** keeps stocks that
are going up, **C** keeps the ones where something is happening this week,
and **D** warns you about the ones that look risky.

---

## A. Tradability gates

| id | Metric | Default | What & why |
|---|---|---|---|
| `history` | `daily_bars` | ≥ 220 | **Enough history.** The 200-day average, the 52-week range and 6-month relative strength need about a year of data. Recent IPOs fail here rather than being judged on guesses. *Tune:* lowering it lets young stocks through, but their trend gates will be NA and fail anyway. |
| `min_price` | `close` | ≥ ₹50 | **No penny stocks.** Very cheap stocks attract operators, hit circuit limits and have wide spreads. *Tune:* raise it to ₹100 for a cleaner list. |
| `liquidity` | `median_traded_value_cr_20d` | ≥ ₹5 Cr | **Can you get in and out?** This is the median (not the mean, so one block deal doesn't inflate it) of close × volume over 20 sessions, in ₹ crore. With ₹5 Cr a day, a few-lakh position is a tiny part of the volume. *Tune:* raise it if your position size grows. A rough rule is to keep your position under 1–2% of daily turnover. |

## B. Trend gates

Your strategy pyramids into winners and trails an 11% stop from the peak
close. That only pays when you're riding an existing uptrend, so all of these must hold.

| id | Metric | Default | What & why |
|---|---|---|---|
| `w_above_30w` | `w_close_vs_sma30_pct` | > 0 | **Weekly close above the 30-week average.** This is Stan Weinstein's *Stage 2*. The 30-week average (about 7 months) is close to your 9-month holding horizon, so above it, buyers have controlled your timeframe. |
| `w_30w_rising` | `w_sma30_slope_4w_pct` | > 0 | **30-week average rising** (vs 4 weeks ago). Price above a *flat or falling* average is often just a bounce in a downtrend; the average itself has to turn up. |
| `d_above_50` | `d_close_vs_sma50_pct` | > 0 | **Daily close above the 50-day average.** The intermediate trend that most funds watch. Below it, the current swing is down. |
| `d_50_above_200` | `d_sma50_vs_sma200_pct` | > 0 | **50-day above 200-day** (the "golden cross" state). With close > 50 > 200, short, medium and long trends all agree. |
| `d_200_rising` | `d_sma200_slope_20d_pct` | > 0 | **200-day average rising** (vs 20 sessions ago). A falling 200-day average is a long-term downtrend, where rallies tend to fade. |
| `near_high` | `pct_below_52w_high` | ≤ 25 | **Within 25% of the 52-week high.** Leaders trade near their highs. Far below the high, holders waiting to break even sell into every rally (overhead supply). *Tune:* 15 is stricter (true leaders only). |
| `off_low` | `pct_above_52w_low` | ≥ 30 | **At least 30% above the 52-week low.** Proof that a real advance has happened. Stocks hugging their lows haven't shown demand. |
| `rs_vs_nifty` | `rs_6m_vs_benchmark_pct` | > 0 | **Beating the NIFTY 50 over 6 months** (stock's 6-month return minus the index's). Relative-strength leaders tend to keep leading (momentum), and in a weak market they are where money is going. |

*Failure modes:* in a broad bear market very few stocks pass B, and that is
working as intended. A stock that just recovered from a crash can show close >
50-day but a falling 200-day, so it fails `d_200_rising` until the long trend turns.

## C. Entry setups (at least one must fire)

A stock that passes A and B is a **candidate**; a setup makes it **actionable
this week**. The detectors are in [`technicals/setups.py`](technicals/setups.py),
and their thresholds are the `params` in the YAML.

### `breakout`: weekly breakout above resistance on volume
- **Checks:** in the last `within_weeks` (2) weeks, a weekly close above the nearest
  resistance level, on volume ≥ `volume_multiple` (1.5×) the 10-week average, and the latest
  close is still above that level.
- **Resistance:** the nearest support/resistance level above the previous week's
  close, built only from swing points *before* the breakout week (no look-ahead).
  If no level is overhead, the highest weekly close of the prior 26 weeks is used.
- **Why:** resistance is where sellers kept appearing. Closing above it on heavy
  volume means that supply was absorbed, and good breakouts often start the next
  leg up. Volume is what separates real breakouts from false ones.
- **Tune:** `volume_multiple: 2` for fewer, stronger breakouts; `within_weeks: 1` for this week only.
- **Fails when:** the breakout fades back below the level, so it stops firing next week.

### `accumulation`: up week on very heavy volume
- **Checks:** last week closed up, on volume ≥ 2× the 10-week average, and closed in the upper
  half of its range (`min_close_position: 0.5`).
- **Why:** institutions can't hide their size. Heavy buying that holds near the
  week's high suggests funds are building positions, often before a breakout.
- **Fails when:** the heavy volume comes from a one-off event (index inclusion, a block
  deal) rather than steady buying.

### `tight_base`: volatility contraction just below resistance
- **Checks:** all three of these hold:
  - the last 3 weekly closes are within 8% of each other;
  - daily ATR/close is in the quietest 20% of the last 60 sessions;
  - price is within 5% below the nearest resistance. No resistance overhead
    (above all old swing points) also counts.
- **Why:** when an uptrending stock moves less and less under resistance, sellers
  are drying up (Minervini's VCP idea). Breakouts from tight bases tend to be clean,
  and the stop can sit just under the base, so the risk is small.
- **Tune:** `max_range_pct: 6` for tighter bases only.

### `pullback_support`: daily dip to support with a bullish reversal
- **Checks:** within the last 3 sessions, all three of these (and the reversal bar must close above the support it touched):
  - a low within 2% of the 50-day average or a support level (a broken
    resistance counts, see *Levels*);
  - a bullish reversal candle (hammer, bullish engulfing, piercing or morning star);
  - volume in the 5 days before the reversal below the 20-day average.
- **Why:** buying a dip to support inside an uptrend is the classic low-risk entry,
  because the stop goes just under support. Quiet volume means holders aren't
  dumping, and the candle shows buyers stepping back in.
- **Fails when:** the support breaks the following week. The candle was a pause,
  not a turn.

### `weekly_reversal`: weekly bullish candle at support
- **Checks:** the latest weekly bar is a bullish engulfing, hammer or morning star,
  with its low within 3% of the 30-week average or of a weekly support level.
- **Why:** the same idea as the daily pullback on a bigger timeframe. A whole
  week of buyers defending a key level carries more weight.

**About candlestick patterns:** on their own they are close to noise. That's why
every pattern rule here also requires price to be at a level that matters
(support, resistance or a key average). The definitions are in
[`technicals/patterns.py`](technicals/patterns.py), with the thresholds as constants
at the top:

| Pattern | Definition |
|---|---|
| hammer | lower shadow ≥ 2× body, upper shadow ≤ 15% of the range, body ≥ 5% of the range, after a 3-bar decline |
| shooting star | the mirror of the hammer, after a 3-bar advance |
| bullish / bearish engulfing | the body fully covers the previous, opposite-coloured body, and is bigger |
| piercing | after a strong bearish bar, opens at or below its close and closes above the midpoint of its body |
| morning / evening star | strong bar 1, small bar 2 (≤ 30% of bar 1's body), bar 3 closes past bar 1's midpoint |
| doji | body ≤ 10% of the range |
| inside bar | the range sits inside the previous bar's range |

## D. Red flags

`warn` (default): shown on the stock and subtracts 1 from the score.
`reject`: fails the stock at the `flag` layer. Switch any flag to `reject` in the YAML.

| id | Default | What & why |
|---|---|---|
| `overextended` | > 25% above the 50-day average, or daily RSI > 80 | **Stretched.** Pullbacks are likely. Entering here puts your 11% trailing stop in the path of a normal shakeout. |
| `distribution` | ≥ 3 down weeks on above-average volume in the last 8 | **Large holders selling.** The mirror of accumulation. Clusters of these weeks often come before a trend breaks. |
| `bearish_at_resistance` | bearish engulfing, shooting star or evening star on the latest week, with its high within 3% of a level | **Sellers defended the ceiling** on the weekly chart. Upside looks capped until the level is cleared. |
| `gap_down` | a gap down ≥ 5% in the last 10 sessions, not filled | **Bad news still being priced.** Price often struggles to climb back through the gap. |

## Score (ranking only)

```
score = 2 × setups fired
      + RS percentile / 25          (0–4; RS percentile = rank of rs_6m_vs_benchmark_pct across the universe)
      + 1 if weekly RSI is 50–75    (healthy momentum, not stretched)
      − 1 × warnings
```
The weights are in the `score:` block of the YAML. The score orders the passing
list and never filters it.

## Metrics

The gates compare these by name. They are stored for every stock and shown in the
breakdown. `_pct` = percent, `d_` = daily bars, `w_` = weekly bars, `null` = not enough data.

| Metric | Meaning |
|---|---|
| `daily_bars`, `weekly_bars` | Number of bars available |
| `close` | Latest daily close (₹) |
| `median_traded_value_cr_20d` | Median of close × volume over 20 sessions, ₹ crore |
| `d_sma50`, `d_sma200`, `w_sma30` | 50-day, 200-day and 30-week simple moving averages |
| `w_close_vs_sma30_pct` | Latest weekly close vs the 30-week average |
| `w_sma30_slope_4w_pct` | 30-week average now vs 4 weeks ago |
| `d_close_vs_sma50_pct` | Close vs the 50-day average |
| `d_sma50_vs_sma200_pct` | 50-day average vs the 200-day average |
| `d_sma200_slope_20d_pct` | 200-day average now vs 20 sessions ago |
| `high_52w`, `low_52w` | Highest high / lowest low of the last 250 sessions |
| `pct_below_52w_high`, `pct_above_52w_low` | Distance from those |
| `return_6m_pct`, `benchmark_return_6m_pct` | 126-session return of the stock and of the NIFTY 50 |
| `rs_6m_vs_benchmark_pct` | Their difference (relative strength) |
| `rs_percentile` | Rank of the above across the universe (0 = weakest, 100 = strongest) |
| `d_rsi14`, `w_rsi14` | 14-bar RSI (Wilder), daily and weekly |
| `atr_pct` | 14-day ATR as % of close (typical daily move) |
| `w_volume_ratio_10w` | Last week's volume ÷ the average of the 10 weeks before |
| `resistance`, `resistance_touches`, `pct_below_resistance` | Nearest weekly level above the close |
| `support`, `support_touches`, `pct_above_support` | Nearest weekly level at or below the close |

### Levels (support / resistance)

Defined in [`technicals/indicators.py`](technicals/indicators.py):
1. **Swing points:** a bar whose high (or low) is the extreme of the 2 bars on
   each side on weekly charts, or 3 bars on daily. The newest bars can't be
   swing points until the bars after them exist.
2. **Clustering:** swing prices within 1.5% of each other merge into one level,
   because traders defend zones, not exact rupees. `touches` = how many swing points
   formed the level. More touches = more significant.
3. **Highs and lows are pooled.** A broken resistance often becomes support
   ("polarity"), so a level counts as resistance or support only by where it sits
   relative to today's price.

## Configuration

Rules: `criteria/technicals.yaml`. In docker it is bind-mounted, so edits
apply on the next run without a rebuild. Check a change before waiting for a run:

```bash
cd analysis
python cli.py validate            # is the YAML valid? (unknown ids, patterns, operators, missing `why`)
python cli.py check TATASTEEL     # every rule's outcome for one stock, right now (stores nothing)
```

Environment (`.env`):

| Variable | Default | Meaning |
|---|---|---|
| `UPSTOX_ACCESS_TOKEN` | — | The same read-only Analytics token the other services use |
| `UPSTOX_REQUEST_DELAY` | 1.1 | Seconds between candle requests. Upstox allows about 2,000 per 30 minutes per user, shared with market-data and portfolio |
| `HISTORY_DAYS` | 420 | Calendar days of daily candles kept per stock |
| `SCHEDULE_DAY_OF_WEEK` / `SCHEDULE_HOUR` | sat / 7 | Weekly run time (IST) |
| `SCHEDULE_ENABLED` | true | Turn the scheduler off (manual runs only) |

## API (via nginx at `/api/analysis/*`)

| Method & path | Returns |
|---|---|
| `GET /results` | Latest finished run + its passing stocks, best score first |
| `GET /stocks/{instrument_id}` | One stock's full breakdown: every rule's status, note and why, plus all metrics |
| `GET /search?q=` | Stocks in the latest run by symbol or name |
| `GET /runs`, `GET /runs/{id}` | Run history / live progress |
| `POST /runs` | Start a run (409 if one is running, 422 if the YAML is invalid) |
| `GET /criteria` | The parsed rules file |
| `GET /health` | Liveness |

## Tables (Postgres, owned by this service)

| Table | Contents |
|---|---|
| `analysis_candles` | Daily OHLCV per instrument (incremental; pruned to `HISTORY_DAYS`) |
| `analysis_runs` | One row per run: status, stage, progress, heartbeat, funnel counts, rules hash |
| `analysis_results` | One row per (run, stock): passed, score, failed_at, every rule outcome, metrics |

## Development

```bash
cd analysis
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q        # pure-Python tests: no Postgres, Redis or Upstox needed
.venv/bin/uvicorn main:app --port 8003
```

Code map: `technicals/indicators.py` (averages, RSI, ATR, weekly bars, swing
points, levels) → `technicals/patterns.py` (candles) → `technicals/metrics.py`
(named metrics) → `technicals/setups.py` (setups and flags) →
`technicals/rules.py` (pass logic, score) → `pipeline.py` (a run) →
`routers/` (API).
