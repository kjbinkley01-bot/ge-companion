"""Offline demo data source with the same interface as WikiClient.

Run `python run.py --demo` to try the dashboard without internet. Prices are
synthetic and only loosely based on real values.
"""
import math
import random
import time

# id, name, base price, buy limit, highalch, members, base hourly volume
ITEMS = [
    (4151, "Abyssal whip", 815000, 70, 72000, True, 60),
    (561, "Nature rune", 137, 18000, 108, False, 500000),
    (560, "Death rune", 191, 25000, 108, True, 700000),
    (565, "Blood rune", 340, 25000, 240, True, 1100000),
    (2434, "Prayer potion(4)", 9700, 2000, 90, True, 30000),
    (139, "Prayer potion(3)", 7170, 2000, 72, True, 2000),
    (141, "Prayer potion(2)", 4740, 2000, 54, True, 200),
    (143, "Prayer potion(1)", 2400, 2000, 36, True, 30),
    (3024, "Super restore(4)", 9990, 2000, 180, True, 17000),
    (3026, "Super restore(3)", 7265, 2000, 144, True, 13000),
    (3028, "Super restore(2)", 4849, 2000, 108, True, 550),
    (3030, "Super restore(1)", 2560, 2000, 72, True, 110),
    (6685, "Saradomin brew(4)", 7050, 2000, 120, True, 18000),
    (6687, "Saradomin brew(3)", 5080, 2000, 96, True, 6000),
    (6689, "Saradomin brew(2)", 3423, 2000, 72, True, 14),
    (6691, "Saradomin brew(1)", 1750, 2000, 48, True, 7),
    (1079, "Rune platelegs", 37800, 70, 38400, False, 2800),
    (1127, "Rune platebody", 38520, 70, 39000, False, 2600),
    (1201, "Rune kiteshield", 32000, 70, 32640, False, 3300),
    (1319, "Rune 2h sword", 37870, 70, 38400, False, 1000),
    (1163, "Rune full helm", 20540, 70, 21120, False, 2500),
    (1397, "Air battlestaff", 8770, 18000, 9300, True, 13000),
    (1391, "Battlestaff", 7968, 11000, 4650, True, 45000),
    (1213, "Rune dagger", 4440, 70, 4800, False, 290),
    (1373, "Rune battleaxe", 24540, 70, 24960, False, 630),
    (385, "Shark", 975, 13000, 180, True, 56000),
    (379, "Lobster", 108, 13000, 72, False, 21000),
    (453, "Coal", 140, 13000, 27, False, 650000),
    (440, "Iron ore", 74, 13000, 21, False, 66000),
    (1515, "Yew logs", 105, 15000, 96, False, 133000),
    (1513, "Magic logs", 730, 12000, 192, True, 78000),
    (5295, "Ranarr seed", 32780, 200, 30, True, 3600),
    (207, "Grimy ranarr weed", 5800, 13000, 18, True, 16000),
    (257, "Ranarr weed", 5964, 13000, 30, True, 6400),
    (2363, "Runite bar", 12200, 10000, 4800, False, 34000),
    (2353, "Steel bar", 590, 10000, 180, False, 115000),
    (13190, "Old school bond", 9800000, 100, 0, False, 400),
    (11802, "Armadyl godsword", 12400000, 8, 750000, True, 20),
    (11832, "Bandos chestplate", 21800000, 8, 159000, True, 15),
    (11834, "Bandos tassets", 27300000, 8, 162000, True, 14),
    (20997, "Twisted bow", 1450000000, 8, 720000, True, 2),
    (6585, "Amulet of fury", 2865000, 8, 60000, True, 58),
    (4587, "Dragon scimitar", 60000, 70, 60000, True, 88),
    (1305, "Dragon longsword", 59220, 70, 60000, True, 815),
    (4087, "Dragon platelegs", 161100, 70, 162000, True, 530),
    (4585, "Dragon plateskirt", 161200, 70, 162000, True, 457),
    (1215, "Dragon dagger", 17290, 70, 18000, True, 664),
    (892, "Rune arrow", 43, 11000, 240, False, 417000),
    (12934, "Zulrah's scales", 142, 30000, 0, True, 900000),
    (6737, "Berserker ring", 3830000, 8, 60000, True, 56),
    (6733, "Archers ring", 2245000, 8, 60000, True, 18),
    (227, "Vial of water", 5, 13000, 1, False, 200373),
    (99, "Ranarr potion (unf)", 6334, 10000, 15, True, 18967),
    (231, "Snape grass", 1170, 13000, 6, True, 77372),
    (2998, "Toadflax", 2268, 13000, 28, True, 16087),
    (3049, "Grimy toadflax", 2130, 13000, 11, True, 10514),
    (3002, "Toadflax potion (unf)", 2536, 10000, 28, True, 40349),
    (6693, "Crushed nest", 4072, 11000, 120, True, 19427),
    (4716, "Dharok's helm", 354613, 15, 61800, True, 12),
    (4720, "Dharok's platebody", 917000, 15, 168000, True, 9),
    (4722, "Dharok's platelegs", 895653, 15, 165000, True, 10),
    (4718, "Dharok's greataxe", 1799613, 15, 124800, True, 17),
    (12877, "Dharok's armour set", 4033642, 8, 240000, True, 56),
    (66, "Yew longbow (u)", 171, 10000, 384, True, 44762),
    (855, "Yew longbow", 543, 18000, 768, True, 31188),
    (1777, "Bow string", 199, 13000, 6, True, 67627),
    (536, "Dragon bones", 3699, 7500, 96, True, 99201),
    (1619, "Uncut ruby", 1012, 10000, 60, False, 58681),
    (1603, "Ruby", 710, 13000, 600, False, 119278),
    (383, "Raw shark", 689, 15000, 102, True, 81892),
    (6332, "Mahogany logs", 127, 11000, 30, True, 271965),
    (8782, "Mahogany plank", 2139, 13000, 900, True, 122841),
    (573, "Air orb", 1397, 11000, 180, True, 71975),
    (449, "Adamantite ore", 543, 4500, 240, False, 149858),
    (2361, "Adamantite bar", 1944, 10000, 384, False, 82311),
    (451, "Runite ore", 10172, 4500, 1920, False, 21211),
    (563, "Law rune", 122, 18000, 144, False, 253578),
    (562, "Chaos rune", 105, 18000, 54, False, 2494204),
    (1521, "Oak logs", 39, 15000, 12, False, 22696),
    (8778, "Oak plank", 531, 13000, 150, True, 125734),
    (1617, "Uncut diamond", 2452, 10000, 120, False, 61667),
    (1601, "Diamond", 1557, 11000, 1200, False, 64168),
    (22124, "Superior dragon bones", 19338, 7500, 96, True, 8054),
    (13439, "Raw anglerfish", 1326, 15000, 270, True, 97918),
    (13441, "Anglerfish", 1513, 10000, 270, True, 64053),
]


def _noise(seed):
    return random.Random(repr(seed)).uniform(-1, 1)


def _price(item, t):
    iid, _, base = item[0], item[1], item[2]
    hours = t / 3600.0
    drift = 0.04 * math.sin(hours / 30 + iid) + 0.02 * math.sin(hours / 5 + iid * 0.3)
    # A daily and weekly rhythm so the best time to trade view has a pattern to find.
    drift += 0.012 * math.sin(2 * math.pi * (hours % 24) / 24 + iid % 7)
    drift += 0.006 * math.sin(2 * math.pi * t / (7 * 86400) + iid % 5)
    jitter = 0.006 * _noise((iid, int(t // 300)))
    # A couple of demo items get a sharp move so the movers and spike flags have something to show.
    if iid in (1515, 6737):
        drift += 0.12 * max(0.0, 1 - abs((time.time() - t) / 3600.0) / 3)
    mid = base * (1 + drift + jitter)
    spread = max(1, int(mid * (0.018 + 0.014 * abs(_noise((iid, "s", int(t // 3600)))))))
    return int(mid + spread / 2), int(mid - spread / 2)


def _vols(item, t, window_seconds):
    base = item[6] * window_seconds / 3600.0
    f = 1 + 0.4 * _noise((item[0], "v", int(t // window_seconds)))
    if item[0] in (1515, 6737) and time.time() - t < 3 * 3600:
        f *= 6
    total = max(0, int(base * f))
    buy_share = 0.5 + 0.2 * _noise((item[0], "b", int(t // window_seconds)))
    hv = int(total * buy_share)
    return hv, total - hv


class DemoClient:
    is_demo = True

    def __init__(self, user_agent=""):
        pass

    def mapping(self):
        out = []
        for iid, name, base, limit, ha, mem, _ in ITEMS:
            out.append({
                "id": iid, "name": name, "limit": limit, "highalch": ha,
                "lowalch": int(ha * 2 / 3), "value": int(ha / 0.6) if ha else 1,
                "members": mem, "icon": name + ".png", "examine": "Demo item.",
            })
        return out

    def latest(self):
        now = time.time()
        data = {}
        for it in ITEMS:
            hi, lo = _price(it, now)
            data[str(it[0])] = {"high": hi, "highTime": int(now - 40), "low": lo,
                                "lowTime": int(now - 95)}
        return data

    def _window(self, ts, size):
        data = {}
        for it in ITEMS:
            hi, lo = _price(it, ts + size / 2)
            hv, lv = _vols(it, ts, size)
            data[str(it[0])] = {"avgHighPrice": hi if hv else None, "highPriceVolume": hv,
                                "avgLowPrice": lo if lv else None, "lowPriceVolume": lv}
        return {"data": data, "timestamp": int(ts)}

    def five_min(self, timestamp=None):
        ts = timestamp or (int(time.time()) // 300 * 300 - 300)
        return self._window(ts, 300)

    def one_hour(self, timestamp=None):
        ts = timestamp or (int(time.time()) // 3600 * 3600 - 3600)
        return self._window(ts, 3600)

    def one_day(self, timestamp):
        return self._window(int(timestamp), 86400)

    def timeseries(self, item_id, lookback):
        step = {"6h": 300, "24h": 300, "7d": 3600, "30d": 21600, "6m": 86400, "1y": 86400}[lookback]
        span = {"6h": 6, "24h": 24, "7d": 168, "30d": 720, "6m": 4380, "1y": 8760}[lookback] * 3600
        item = next((i for i in ITEMS if i[0] == int(item_id)), None)
        if not item:
            return {"data": [], "itemId": item_id}
        end = int(time.time()) // step * step
        rows = []
        for ts in range(end - span, end, step):
            hi, lo = _price(item, ts)
            hv, lv = _vols(item, ts, step)
            rows.append({"timestamp": ts, "avgHighPrice": hi, "avgLowPrice": lo,
                         "highPriceVolume": hv, "lowPriceVolume": lv})
        return {"data": rows, "itemId": int(item_id), "timestep": step}

    def hiscores(self, player, mode="normal"):
        rng = random.Random(player.lower())
        names = ["Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer", "Magic",
                 "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting",
                 "Smithing", "Mining", "Herblore", "Agility", "Thieving", "Slayer", "Farming",
                 "Runecraft", "Hunter", "Construction", "Sailing"]
        from .skills import level_for_xp
        grow = (time.time() / 86400.0) % 1000
        skills = []
        total_xp = total_lvl = 0
        for i, n in enumerate(names):
            xp = int(rng.uniform(200_000, 13_000_000) + grow * rng.uniform(500, 4000))
            lvl = level_for_xp(xp)
            total_xp += xp
            total_lvl += lvl
            skills.append({"id": i + 1, "name": n, "rank": rng.randint(10_000, 900_000),
                           "level": lvl, "xp": xp})
        skills.insert(0, {"id": 0, "name": "Overall", "rank": 123456, "level": total_lvl,
                          "xp": total_xp})
        # Kill counts creep up over time so KC gains have something to show.
        kc_grow = time.time() / 3600.0
        acts = []
        for i, (n, lo, hi, rate) in enumerate([("Clue Scrolls (all)", 100, 400, 0.02),
                                               ("Zulrah", 50, 900, 0.9), ("Vorkath", 50, 900, 0.7),
                                               ("General Graardor", 20, 400, 0.3),
                                               ("Chambers of Xeric", 5, 120, 0.05)]):
            base = rng.randint(lo, hi)
            acts.append({"id": i, "name": n, "rank": rng.randint(5_000, 90_000),
                         "score": int(base + (kc_grow % 5000) * rate * rng.uniform(0.5, 1.0))})
        return {"name": player, "skills": skills, "activities": acts}
