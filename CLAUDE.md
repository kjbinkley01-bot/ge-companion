# Bankstanding: notes for Claude Code

Personal Old School RuneScape Grand Exchange and account tool that runs locally on Windows.
A free alternative to GE Tracker premium, built only on public, read-only data.

## Hard rules
* Never automate the game: no input automation, screen reading, memory reading or macros.
  Jagex rules ban this. Market data comes only from public web APIs. Account data comes only
  from the listen-only RuneLite plugin (`runelite-plugin/`), which reads RuneLite's plugin API
  events and writes local files; it must never send input, click, or change game state.
* Keep the backend on the Python standard library (no pip installs). The owner starts it by
  double-clicking `start.bat`.
* Every request to the Wiki must send the configured User-Agent. Never loop `/latest?id=` over
  items; use the bulk endpoints. Keep polling at 30 seconds or slower.
* Do not use em dashes or en dashes anywhere (code comments, UI text, docs).

## Layout
* `run.py`: entry point (`--demo` for offline synthetic data, `--no-browser`, `--port`).
* `geco/wiki.py`: Wiki prices API (`/mapping`, `/latest`, `/5m`, `/1h`, `/timeseries`) and
  hiscores (`index_lite.json`, per account type).
* `geco/engine.py`: background poller. Backfills missing hourly windows (`backfill_hours`,
  max 720) newest first, then the 5 minute windows the stability score needs, one bulk
  request a second. Runs alerts, limit reset notices, hourly net worth snapshots, daily backups.
* `geco/market.py`: GE tax (2%, floor, 5m cap, exempt names in config), fill model, flip
  metrics, margin stability and trap flags, movers, volume spikes, decanting, alert conditions.
* `geco/db.py`: SQLite (`data/ge_companion.sqlite3`): `h1` and `m5` price history, watchlist,
  alerts, notifications, flips, holdings, networth, drops, chase, custom recipes, goals.
  Hourly and 5 minute stats aggregate in SQL. Column additions go in `MIGRATIONS`.
* `geco/features.py`: flip log math and analytics, buy limit windows, portfolio, account view,
  goals, boss drops and dry streaks, holdings paste parser.
* `geco/recipes.py`: recipe engine (processing methods, item sets, custom recipes), priced live.
  Recipe item names must match the Wiki mapping exactly.
* `geco/analytics.py`: seasonality, category indices, correlation, backtester. Reads only saved
  history, never the Wiki. `geco/categories.py`: name rules for index categories.
* `geco/forecast.py`: long term profit forecast. Daily series from `h1`, damped trend (Holt)
  on log price kept only if it beats a flat forecast on held out days, smoothed per side
  volume, live ROI fading to the historical ROI, Monte Carlo bands from resampled past days.
* `geco/forecast_eval.py`: walk-forward backtest and tuner for the forecast on imported daily
  history (`d1` table, filled from the Wiki's bulk `/24h` windows by `Engine.import_daily`).
  Tunes on older dates, adopts new settings only if they also win on held out newer dates.
  The shipped `DEFAULT_PARAMS` and `geco/forecast_baseline.json` come from a run on a year of
  real data; re-run and update both together if the model changes.
* `geco/hold.py`: holding outlook. Trend features from daily grids, ridge regression per
  horizon (7/30/90 days), walk-forward tested; a horizon's signal is only used if it beat
  "no change" on held out months, otherwise the outlook centers on no change with odds and
  ranges from real moves. Ships `geco/hold_model.json` (two years, Sep 2026: no signal passed).
* `runelite-plugin/`: RuneLite plugin (targets Java 11, builds with Gradle 9.1 on Java 17 to 25, `run-plugin.bat` or `gradlew run`
  sideloads it). Writes JSON lines to `~/.runelite/bankstanding/events-YYYY-MM.jsonl`:
  `{v, t (ms), type, acct (rsprofile key), name, ...}` with types login, logout, offer (slot,
  state, item, price, total, done, spent: cumulative), container (bank, inventory, equipment,
  looting_bag, seed_vault, rune_pouch, death_storage, ge_collect_0..7; items as [[id, qty]],
  noted ids canonicalized), loot, xp. JUnit tests: `gradle test` in that folder.
  Side panel (display only): polls `/api/plugin/summary` on the app (config `appUrl`) every 30
  seconds and when the GE offer item (varp TRADINGPOST_SEARCH) changes.
* `geco/account.py`: ingests plugin files incrementally (byte offsets, whole lines only), keeps
  latest GE offers, reconstructs fills from cumulative offer changes, FIFO matches them into
  automatic flip log rows (`flips.source='auto'`, rebuilt each time; `flip_ignore` opts out).
* `geco/networth.py`: account valuation (sell price after tax or mid), GE escrow (reserved
  coins, listed items, collection boxes), cost basis from open lots plus starting costs
  (`cost_seed`: items first seen with no cost get that day's value, topped up the first time a
  new storage is seen), portfolio categories,
  history in `account_worth` (5 minute buckets, per account and `*` combined), backcast of
  today's holdings at past prices.
* `geco/advice.py`: recommendations from what an account holds, each with a confidence label.
* Attribution (`networth.attribute`, run on every `record`): each account and `*` keep a kv
  snapshot; the change since the last recording splits into `mkt` (price moves on what was
  held), `trade` (GE fills vs item value), `loot` and `other`, stored cumulatively in
  `account_worth` with `twr`, a time weighted return index that ignores income.
  `networth.performance` reads a period from it.
* `geco/risk.py`: daily market index (top 150 by gp traded, 5% weight cap), benchmark series,
  account risk (daily swing, historical VaR, drawdown, beta, risk share, days to sell).
* `geco/news.py`: game updates, dev blogs, polls, future updates from the Wiki's Update
  namespace (MediaWiki API, bulk: category lists, then 20 posts' text per request) and the
  Jagex news RSS. Tags tradeable items (Wiki links, or full names of two words or more).
  First start imports 800 days, then RSS every 30 minutes and the Wiki every 6 hours.
  Demo mode writes synthetic posts. `news_enabled` in config turns it off.
* `geco/events.py`: event study. Abnormal move (item minus market index) around each post and
  mentioned item for pre7, d1, d7, d30 windows, with a random ordinary day control and t stats.
* `geco/indicators.py`: SMA, EMA, RSI, Bollinger, and a report replaying 8 daily signals over
  the history (7 and 30 day moves after tax, beyond the market and a random day control,
  split into older and newer halves). The hourly backtester also has rsi, ma_cross, bollinger.
* `geco/fills.py`: your real fill share. `account` logs every offer from open to finish in
  `ge_offer_log`; share = filled / market volume on that side while open (m5, else h1), only
  offers within 2% of the market. Median per item (3+ offers) or overall (8+) feeds
  `market.build_market(share_fn=...)` when `fill_share_measured`. Buy limits are per account.
* `geco/coach.py`: stale buys and sells (suggested price, fill time), offers through the
  market, positions below break-even (with the item's own recovery odds), pump warnings.
* `geco/brief.py`: the Bank statement (changes since last visit: attribution, fills, coach,
  movers, news about holdings, limit resets, alerts, ideas) and its phone text version.
* `geco/push.py`: optional Discord webhook and ntfy notifications, per event type. Engine
  `notify()` stores in-app notifications and pushes; cooldown keys persist in kv.
* `geco/wealth.py`: wealth goals (projection from TWR plus income, cautious and hopeful dates)
  and gp sources per hour played (`sessions` table from login, logout and 20 minute gaps).
* `geco/edge.py`: edge report by strategy tag, account, item, price band, hold time, and
  execution vs the hour's average prices. Tags for auto rows live in `trade_tags` keyed by
  (acct, item, buy_ts) so they survive `sync_flips`. Paper rules = backtest restricted to
  entries after the rule was saved.
* `geco/targets.py`: price targets and sell ladders, checked after each price refresh.
* `geco/guard.py`: manipulation score (sharp rise vs class, volume burst, thin market, spread,
  lopsided flow, above 90 day range).
* `geco/extras.py`: official guide prices (Jagex itemdb graph, per item on demand, cached 6h),
  clue casket values from loot, custom index categories (also in `analytics.indices`).
* Server security: `_allowed` accepts only loopback with a local Host header, or LAN clients
  with the Basic auth password when `lan_enabled` (then it binds 0.0.0.0). Non GET requests
  must not come from another Origin.
* `geco/demo_feed.py`: writes plugin format events in demo mode (`data/demo-runelite`).
* `geco/server.py`: JSON API plus static files, bound to 127.0.0.1.
* `web/`: vanilla JS dashboard (`app.js`), no build step. Styled after Robinhood Legend using
  colors measured from a Legend screenshot: blue black canvas, flat borderless panels with 8px
  gutters, a navy header with page tabs (open, close, and a "+" menu of every page grouped by
  section, like Legend's layouts), a centered live status pill, a global account switcher,
  normal case type (Inter, bundled in `web/fonts`, OFL), text only timeframes, outlined pills,
  neon lime for up and actions, coral red for down, and triangles (not signs) on changes via
  `arrow()`, `signed()` and `sgnPct()`. The app opens on Net worth. Charts are hand-rolled SVG
  (`priceChart`, `lineChart`, `barChart`, `heatmap`, `tradingChart`, `treemap`), colors are
  CSS tokens in `style.css`; categorical series colors pass the dataviz palette validator in
  both themes, and up and down always carry a second cue (triangles; candles are filled like Legend's, and
  lime and coral also differ strongly in lightness).
  Layouts (Legend's workspaces): header tabs keyed `L:<id>` hold a 24 column grid of widgets
  (`WIDGETS` registry, rows stretch so 22 rows fill the window) saved in localStorage
  `layouts`; drag by the header, resize by the corner (`settle` pushes down and compacts,
  `refill` swaps a displaced widget into the freed span). Link colors (`LINKS`, localStorage
  `links`) make widgets follow one item, synced across windows with a BroadcastChannel; blue
  also drives the Terminal. `TEMPLATES` feed the "Start from a template" page; the first run
  creates "Flipping desk" and "Monitoring". `mountChartView` is the chart widget and the
  Terminal's chart: ranges 1D to All, intervals, indicators menu, drawings (per item in
  localStorage `draw.<id>`), levels for GE offers, cost and targets, Buy/Sell and the axis "+"
  open `planTicket`, which only saves a price target (never an offer). Endpoints for widgets:
  `/api/account/offers` (working and finished offers) and `/api/sparks` (24h mids).
  The Terminal tab is a linked workspace (panels marked with Legend's link square): list,
  chart, quote/position and news panels all follow `TM.id`, with keyboard shortcuts. Candles
  are approximate (built from window averages: close = mid, open = previous close, wicks =
  avg instant buy and sell).
* `tests/`: `python -m unittest discover -s tests` (standard library only).
* `data/`: runtime files (config, database, backups, cached mapping). Git ignored.

## Price terms
`high` = latest instant-buy price (what a seller receives), `low` = latest instant-sell price
(what a patient buyer pays). Flip profit = high minus tax(high) minus low.

## Fill model
A patient buy at `low` fills against instant sells (`lv` volume); a sell at `high` fills against
instant buys (`hv`). Estimates use `fill_share` (default 20%) of the slower side. Stability is the
share of recent 5 minute windows where avg high minus tax beat avg low.

## Testing
`python -m unittest discover -s tests`, then `python run.py --demo --no-browser --port 8799` and
hit `/api/status`, `/api/market`, `/api/indices`, `/api/recipes`, etc. Demo data lives in
`geco/demo.py` and uses a separate database and mapping cache; demo mode backfills 14 days
instantly so every analytics view has data.

## Status
v2: everything in v1 plus a realistic fill model, margin stability and trap flags, buy limit
tracking, slot planner, market indices, portfolio and net worth, money making by GP/hr, item set
arbitrage, backtester, seasonality heatmap, correlations, composite and watchlist alerts, desktop
notifications, KC gains, drop log with dry streaks, flip analytics, backups and export,
and a Forecast tab (expected profit with likely range over 7/30/90 days).
v3: RuneLite plugin for live account data, Net worth tab (portfolio style valuation, history,
allocation, live GE slots, recommendations), automatic flip log from real trades.
v4: Terminal (linked workspace, candles, indicators, compare, price lines, news flags), News
tab with an event study, performance vs a market index with return attribution, risk panel,
holdings and market heatmaps, indicator report, Legend style theme.
v5: renamed Bankstanding; Legend layout (page tabs, measured palette, Inter); Bank statement; offer coach; measured fill rates and per
account limits; phone notifications and LAN mode; goals and income; edge report, strategy
tags, paper trading; price targets and ladders; manipulation guard; guide prices; clue
values; custom categories; RuneLite side panel.
v6: Legend workspaces (layouts of widgets with drag, resize, add and remove, color linking
across layouts and windows, 8 templates), Legend chart (ranges and intervals, drawings,
offers, cost and targets on the chart, Buy/Sell plan tickets), palette refined to the product
images (#CCFF00 lime, #1a1b1e panels, teal header glow).
Open questions to verify in game: whether a sell offer's `spent` is before or after tax
(`account.sell_split` handles both), and how promptly collection box containers update.
Hiscores response format verified against the live endpoint (Sep 2026).
Next: verify the open questions in game, then consider Plugin Hub submission.
