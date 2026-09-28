# GE Companion

A personal, all in one Old School RuneScape market and account tool that runs on your own PC.
Live Grand Exchange data comes from the OSRS Wiki's free real-time prices API, and stats come
from the official hiscores.

**Account safe by design:** it never touches the game client, never reads your screen, and
never sends input to the game. It only reads public websites, the same way your browser does.

## Start it

1. Install Python 3.10 or newer from python.org (tick "Add python.exe to PATH"). No other
   packages are needed.
2. Double-click `start.bat`. The dashboard opens at http://127.0.0.1:8765
3. Leave the black window open while you use it. Close it (or press Ctrl+C) to stop.

The first run downloads the item list and the last 24 hours of hourly prices (about 25 small
requests, spaced out). Volumes and movers fill in within a minute or so.

Want to look around offline? Run `start.bat --demo` for synthetic prices.

## What's in it

| Tab | What it does |
| --- | --- |
| Flip finder | Every item ranked by profit after the 2% GE tax, ROI, volume, buy limit and an estimated 4 hour profit. Presets for high volume, big ticket, steady margins, under 100k. Stale prices flagged. |
| Movers | Biggest gainers and losers over 1h, 6h, 24h, 7d, plus unusual volume spikes with pump and dump flags. |
| Watchlist | Starred items with 24h sparklines and live margins. |
| Alerts | Price below or above, flip profit, ROI, 1h move, volume spike. Pop-ups and a sound in the dashboard. |
| Flip log | Log real buys and sells, see realized profit after tax, open position value, win rate, profit by day, best items, CSV export. |
| High alch | Profit per cast after the nature rune, GP per XP, profit per buy limit. |
| Decanting | Cheapest dose per potion to buy and decant to 4-dose at Bob Barter, with profit per limit. |
| Account | Hiscores lookup (regular, iron, hardcore, ultimate), XP gains over time, bosses and clues, goals with pace, ETA and live supply cost. |
| Item panel | Click any item: instant buy and sell, tax, ROI, buy pressure, charts from 6 hours to 1 year, your own saved 5 minute history, links to the Wiki. |

## Good to know

* **Price terms.** "Instant buy" is the latest price someone paid to buy instantly, so it's
  roughly what you can sell for. "Instant sell" is roughly what you can buy for with a patient
  offer. Flip profit = instant buy minus tax minus instant sell.
* **GE tax.** 2%, rounded down, capped at 5m per item, nothing under 50 gp, and the Wiki's
  exempt list (bonds, some food, teleports, tools). Edit `data/config.json` if Jagex changes it.
* **Your own history.** The app saves every 5 minute window (kept 7 days) and every hourly
  window (kept 1 year) while it runs. That's the data paid trackers usually charge for.
  The 5 minute history levels off around 100 MB; hourly history adds roughly 2 MB a day.
  Change how long each is kept in Settings.
* **Estimates, not guarantees.** Prices come from trades seen by RuneLite users. Price check
  in game before big flips.
* **Be polite to the Wiki.** In Settings you can add your Discord name to the User-Agent so
  the Wiki team can reach you about API changes. Refresh can't go below 30 seconds.

## Files

* `run.py` starts everything. `geco/` is the engine (Python standard library only).
* `web/` is the dashboard. `data/` holds your settings, database and cached item list.
  Back up `data/ge_companion.sqlite3` to keep your flip log, alerts and history.

## Next up

Money making methods ranked by live GP/hr, a boss drop tracker with dry streaks,
and item set arbitrage.
