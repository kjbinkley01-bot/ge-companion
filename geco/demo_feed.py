"""Offline stand in for the RuneLite plugin (demo mode only).

Writes events in exactly the plugin's JSON lines format so that ingest, automatic flips,
net worth and the dashboard can be tried without the game: two weeks of past trades, a
bank, inventory and gear, a rune pouch, loot, and GE offers that keep filling while the
demo runs.
"""
import json
import os
import random
import time

from . import demo

ACCT = "rsprofile--demo-main"
NAME = "Zezima"


def _id(name):
    for it in demo.ITEMS:
        if it[1] == name:
            return it[0]
    raise KeyError(name)


class DemoFeed:
    def __init__(self, folder, mapping, latest):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.rng = random.Random(7)
        self.state = {}
        fresh = not any(f.endswith(".jsonl") for f in os.listdir(folder))
        if fresh:
            self._history(latest)
        if not os.path.exists(os.path.join(folder, "progress.done")):
            self._progress()
            open(os.path.join(folder, "progress.done"), "w").close()

    # Writing --------------------------------------------------------------------------
    def _write(self, t, etype, **fields):
        e = {"v": 1, "t": int(t * 1000), "type": etype, "acct": ACCT, "name": NAME}
        e.update(fields)
        path = os.path.join(self.folder, time.strftime("events-%Y-%m.jsonl", time.gmtime(t)))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e) + "\n")

    def _offer(self, t, slot, state, item, price, total, done, spent):
        self._write(t, "offer", slot=slot, state=state, item=item, price=price, total=total, done=done, spent=spent)

    def _price(self, item, t):
        it = next(i for i in demo.ITEMS if i[0] == item)
        return demo._price(it, t)

    # Starting data ------------------------------------------------------------------------
    def _history(self, latest):
        now = time.time()
        start = now - 14 * 86400
        self._write(start, "login", world=302)
        flips = [("Abyssal whip", 20), ("Shark", 3000), ("Prayer potion(4)", 400), ("Dragon bones", 1500),
                 ("Rune platebody", 60), ("Amulet of fury", 4), ("Yew logs", 5000), ("Blood rune", 8000)]
        t = start + 3600
        slot = 0
        holding = {}
        while t < now - 6 * 3600:
            name, qty = self.rng.choice(flips)
            item = _id(name)
            hi, lo = self._price(item, t)
            buy = lo
            self._offer(t, slot, "BUYING", item, buy, qty, 0, 0)
            half = qty // 2
            self._offer(t + 1200, slot, "BUYING", item, buy, qty, half, half * buy)
            self._offer(t + 2400, slot, "BOUGHT", item, buy, qty, qty, qty * buy)
            self._offer(t + 2500, slot, "EMPTY", 0, 0, 0, 0, 0)
            t2 = t + 3 * 3600 + self.rng.randint(0, 6 * 3600)
            hi2, _ = self._price(item, t2)
            sell = max(hi2, int(buy * 1.035)) if self.rng.random() < 0.8 else int(buy * 0.99)
            if t2 + 1900 > now - 1800:
                holding[item] = holding.get(item, 0) + qty
                t +=  self.rng.randint(4, 10) * 3600
                continue  # still holding this one
            self._offer(t2, slot + 1, "SELLING", item, sell, qty, 0, 0)
            self._offer(t2 + 1800, slot + 1, "SOLD", item, sell, qty, qty, qty * sell)
            self._offer(t2 + 1900, slot + 1, "EMPTY", 0, 0, 0, 0, 0)
            t += self.rng.randint(4, 10) * 3600
        bank = [("Coins", 0), ("Twisted bow", 1), ("Bandos chestplate", 1), ("Bandos tassets", 1),
                ("Dharok's helm", 1), ("Dharok's platebody", 1), ("Dharok's platelegs", 1), ("Dharok's greataxe", 1),
                ("Prayer potion(3)", 120), ("Prayer potion(2)", 60), ("Saradomin brew(3)", 90),
                ("Shark", 1200), ("Dragon bones", 800), ("Grimy ranarr weed", 600), ("Runite bar", 40),
                ("Magic logs", 3000), ("Nature rune", 5000), ("Death rune", 12000), ("Rune platebody", 3),
                ("Old school bond", 2)]
        items = [[995, 187_000_000]] + [[_id(n), q] for n, q in bank if n != "Coins"]
        for iid, q in holding.items():  # recent buys not sold yet sit in the bank
            for it in items:
                if it[0] == iid:
                    it[1] += q
                    break
            else:
                items.append([iid, q])
        self._write(now - 3 * 86400, "container", container="bank", items=items)
        self._write(now - 3600, "container", container="inventory",
                    items=[[995, 12_450_000], [_id("Shark"), 20], [_id("Prayer potion(4)"), 6]])
        self._write(now - 3600, "container", container="equipment",
                    items=[[_id("Abyssal whip"), 1], [_id("Amulet of fury"), 1], [_id("Berserker ring"), 1],
                           [_id("Rune full helm"), 1], [_id("Rune kiteshield"), 1]])
        self._write(now - 3600, "container", container="rune_pouch",
                    items=[[_id("Death rune"), 2000], [_id("Blood rune"), 1500], [_id("Nature rune"), 800]])
        self._write(now - 86400, "loot", source="Zulrah", kind="NPC",
                    items=[[_id("Zulrah's scales"), 500], [_id("Magic logs"), 100]])
        self._write(now - 2 * 86400, "loot", source="General Graardor", kind="NPC",
                    items=[[_id("Bandos tassets"), 1]])
        # Clue caskets, as RuneLite's Loot Tracker names them.
        for k, (tier, items) in enumerate([("Hard", [[_id("Rune platebody"), 1], [_id("Nature rune"), 60]]),
                                          ("Hard", [[_id("Amulet of fury"), 1]]),
                                          ("Elite", [[_id("Dragon bones"), 40], [_id("Runite bar"), 8]]),
                                          ("Medium", [[_id("Rune full helm"), 1], [_id("Shark"), 12]])]):
            self._write(now - (5 - k) * 86400, "loot", source=f"Clue Scroll ({tier})", kind="EVENT", items=items)
        # A play session yesterday.
        self._write(now - 30 * 3600, "login", world=330)
        self._write(now - 27 * 3600, "logout")
        self._write(now - 3 * 3600, "login", world=302)
        # Current offers: a buy filling now, a sell waiting, and a buy priced under the market.
        whip = _id("Abyssal whip")
        hi, lo = self._price(whip, now)
        self.state["buy"] = {"slot": 0, "item": _id("Shark"), "price": self._price(_id("Shark"), now)[1],
                             "total": 5000, "done": 0}
        self.state["sell"] = {"slot": 1, "item": whip, "price": int(hi * 1.04), "total": 2, "done": 0}
        low_item = _id("Dragon bones")
        self._offer(now - 7200, 2, "BUYING", low_item, int(self._price(low_item, now)[1] * 0.9), 2000, 0, 0)
        b, s_ = self.state["buy"], self.state["sell"]
        self._offer(now - 600, b["slot"], "BUYING", b["item"], b["price"], b["total"], 0, 0)
        self._offer(now - 600, s_["slot"], "SELLING", s_["item"], s_["price"], s_["total"], 0, 0)
        for slot in (0, 1, 2):  # the plugin also reports each slot's collection box
            self._write(now - 590, "container", container=f"ge_collect_{slot}", items=[])
        self.last_tick = now

    # Quests and levels ----------------------------------------------------------------------
    # A mid to late game main working toward the grandmaster quests: most quests done, a few
    # of While Guthix Sleeps' prerequisites left, and some skills short of its levels.
    TODO = {"While Guthix Sleeps", "Dream Mentor", "Tears of Guthix", "Temple of the Eye", "The Path of Glouphrie",
            "Dragon Slayer II", "Song of the Elves", "Monkey Madness II", "Desert Treasure II - The Fallen Empire",
            "Sins of the Father", "Mourning's End Part II", "Lunar Diplomacy", "A Night at the Theatre",
            "Secrets of the North", "The Garden of Death", "Defender of Varrock", "Making Friends with My Arm",
            "Beneath Cursed Sands", "Kingdom Divided", "A Kingdom Divided", "Children of the Sun",
            "Twilight's Promise", "At First Light", "Perilous Moons", "The Ribbiting Tale of a Lily Pad Labour Dispute",
            "Meat and Greet", "The Heart of Darkness", "Death on the Isle", "Ethically Acquired Antiquities",
            "The Curse of Arrav", "The Final Dawn", "Shadows of Custodia", "Scrambled!", "Pandemonium",
            "Troubled Tortugans", "Prying Times", "Current Affairs", "The Red Reef", "Ruins of Unkah",
            "Recipe for Disaster", "Legends' Quest", "Underground Pass", "Regicide", "Roving Elves",
            "Mourning's End Part I", "Making History", "The Eyes of Glouphrie", "Swan Song", "Grim Tales",
            "Desert Treasure I", "Enakhra's Lament", "Contact!", "The Fremennik Exiles", "Royal Trouble",
            "Throne of Miscellania", "Bone Voyage", "Client of Kourend", "Ghosts Ahoy", "Animal Magnetism"}
    TIMED = [("The Hand in the Sand", 5.5, 52), ("Fight Arena", 9.2, 24), ("Nature Spirit", 12.1, 31),
             ("A Tail of Two Cats", 8.0, 38), ("Wanted!", 6.3, 47)]
    LEVELS = {"Attack": 78, "Strength": 80, "Defence": 75, "Ranged": 82, "Prayer": 70, "Magic": 80,
              "Runecraft": 58, "Construction": 68, "Hitpoints": 82, "Agility": 63, "Herblore": 66,
              "Thieving": 68, "Crafting": 71, "Fletching": 75, "Slayer": 72, "Hunter": 58, "Mining": 70,
              "Smithing": 66, "Fishing": 76, "Cooking": 80, "Firemaking": 75, "Woodcutting": 74,
              "Farming": 67, "Sailing": 1}

    def _progress(self):
        from . import quests as qmod
        names = list(qmod.load_snapshot().keys())
        now = time.time()
        timed = {n: (now - d * 86400, mins) for n, d, mins in self.TIMED}
        start = {n: (0 if n in self.TODO else 1 if n in timed else 2) for n in names}
        self._write(now - 13 * 86400, "quests", states=start, qp=172)
        st = dict(start)
        for n, (when, mins) in sorted(timed.items(), key=lambda kv: kv[1][0]):
            st[n] = 1
            self._write(when, "quests", states=dict(st), qp=172)
            st[n] = 2
            self._write(when + mins * 60, "quests", states=dict(st), qp=172)
        from .skills import xp_for_level
        lv = {k: [v, xp_for_level(v) + 1000] for k, v in self.LEVELS.items()}
        self._write(now - 2 * 3600, "levels", levels=lv, combat=104)

    # Live changes ---------------------------------------------------------------------------
    def tick(self, latest):
        """Called every few seconds by the engine: the open buy offer keeps filling."""
        if "buy" not in self.state:
            return
        now = time.time()
        if now - getattr(self, "last_tick", 0) < 20:
            return
        self.last_tick = now
        b = self.state["buy"]
        if b["done"] >= b["total"]:
            return
        b["done"] = min(b["total"], b["done"] + self.rng.randint(100, 400))
        state = "BOUGHT" if b["done"] >= b["total"] else "BUYING"
        self._offer(now, b["slot"], state, b["item"], b["price"], b["total"], b["done"], b["done"] * b["price"])
        self._write(now, "container", container=f"ge_collect_{b['slot']}", items=[[b["item"], b["done"]]])
