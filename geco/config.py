"""Settings for Bankstanding. Stored in data/config.json and editable by hand or in the app."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
WEB_DIR = os.path.join(ROOT, "web")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    # The Wiki asks every tool to send a descriptive User-Agent. Adding a Discord
    # name here is optional but lets the Wiki team reach you about API changes.
    "user_agent": "Bankstanding - personal local price dashboard",
    "port": 8765,
    "open_browser": True,
    "latest_poll_seconds": 60,
    "backfill_hours": 24,
    "keep_5m_days": 7,
    "keep_1h_days": 365,
    "stale_minutes": 20,
    "alert_cooldown_minutes": 30,
    # Share of an item's instant-sell / instant-buy volume you can expect to win as a
    # flipper. Other flippers compete for the same trades, so 100% is never realistic.
    "fill_share": 0.2,
    # A margin whose two prices traded this far apart in time is flagged as a possible trap.
    "trap_gap_minutes": 15,
    # How many hours of 5 minute history the margin stability score looks at.
    "stability_hours": 6,
    "notify_limit_reset": True,
    # Days of daily market history to import from the Wiki's bulk /24h endpoint (one request
    # per day of history, once). Feeds the forecast and its backtest. 0 turns it off.
    "history_import_days": 730,
    # RuneLite plugin folder (blank: ~/.runelite/bankstanding) and net worth valuation.
    "runelite_folder": "",
    "networth_value": "sell",
    "networth_include_manual": True,
    # Game updates, blogs and polls from the OSRS Wiki and Jagex's news feed.
    "news_enabled": True,
    # Daily copy of the database in data/backups, keeping this many (0 turns it off).
    "auto_backup_days": 7,
    # GE convenience fee (tax). Current rules: 2%, rounded down, 5m cap per item,
    # items under 50 gp pay nothing. Exempt items are matched by exact name.
    "tax_rate": 0.02,
    "tax_cap": 5_000_000,
    "tax_exempt_names": [
        "Old school bond",
        "Energy potion(1)", "Energy potion(2)", "Energy potion(3)", "Energy potion(4)",
        "Bronze arrow", "Bronze dart", "Iron arrow", "Iron dart", "Mind rune",
        "Steel arrow", "Steel dart",
        "Bass", "Bread", "Cake", "Cooked chicken", "Cooked meat", "Herring", "Lobster",
        "Mackerel", "Meat pie", "Pike", "Salmon", "Shrimps", "Tuna",
        "Ardougne teleport", "Camelot teleport", "Civitas illa fortis teleport",
        "Falador teleport", "Games necklace(8)", "Kourend castle teleport",
        "Lumbridge teleport", "Ring of dueling(8)", "Teleport to house", "Varrock teleport",
        "Chisel", "Gardening trowel", "Glassblowing pipe", "Hammer", "Needle",
        "Pestle and mortar", "Rake", "Saw", "Secateurs", "Seed dibber", "Shears",
        "Spade", "Watering can",
    ],
}


def load():
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    # v3: the holding outlook needs two years of daily history; lift the old 365 default.
    if cfg.get("config_version", 1) < 3:
        if cfg.get("user_agent") == "GE Companion - personal local price dashboard":
            cfg["user_agent"] = DEFAULTS["user_agent"]  # the app was renamed
        if cfg.get("history_import_days") == 365:
            cfg["history_import_days"] = 730
        cfg["config_version"] = 3
    save(cfg)  # writes any new default keys back to the file
    return cfg


def save(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
