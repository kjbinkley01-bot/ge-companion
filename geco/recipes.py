"""Recipe engine: inputs to outputs with XP, priced live.

One table of recipes powers three features:
  * money making by GP/hr (processing, skilling)
  * item set arbitrage (combine parts into a set, or split a set, free at the GE clerk)
  * cheapest GP per XP for skill goals

Recipes are matched to items by exact name at runtime, so a name the Wiki does not
know is simply skipped. Actions per hour are rough typical rates for a focused player
and can be overridden in the dashboard. Burn rates, failure chances and travel are
not modelled.
"""
import json
import time

# Recipe fields: name, skill, level, xp per action, inputs [(item, qty)], outputs
# [(item, qty)], actions per hour, coins spent per action, group, note.


def R(name, skill, level, xp, inputs, outputs, per_hour, coins=0, group=None, note=""):
    return {"name": name, "skill": skill, "level": level, "xp": xp, "inputs": inputs,
            "outputs": outputs, "per_hour": per_hour, "coins": coins,
            "group": group or skill, "note": note}


# name clean, grimy, unfinished potion, cleaning level, cleaning xp, unf level
HERBS = [
    ("Guam leaf", "Grimy guam leaf", "Guam potion (unf)", 3, 2.5, 3),
    ("Marrentill", "Grimy marrentill", "Marrentill potion (unf)", 5, 3.8, 5),
    ("Tarromin", "Grimy tarromin", "Tarromin potion (unf)", 11, 5, 12),
    ("Harralander", "Grimy harralander", "Harralander potion (unf)", 20, 6.3, 22),
    ("Ranarr weed", "Grimy ranarr weed", "Ranarr potion (unf)", 25, 7.5, 30),
    ("Toadflax", "Grimy toadflax", "Toadflax potion (unf)", 30, 8, 34),
    ("Irit leaf", "Grimy irit leaf", "Irit potion (unf)", 40, 8.8, 45),
    ("Avantoe", "Grimy avantoe", "Avantoe potion (unf)", 48, 10, 50),
    ("Kwuarm", "Grimy kwuarm", "Kwuarm potion (unf)", 54, 11.3, 55),
    ("Snapdragon", "Grimy snapdragon", "Snapdragon potion (unf)", 59, 11.8, 63),
    ("Cadantine", "Grimy cadantine", "Cadantine potion (unf)", 65, 12.5, 66),
    ("Lantadyme", "Grimy lantadyme", "Lantadyme potion (unf)", 67, 13.1, 69),
    ("Dwarf weed", "Grimy dwarf weed", "Dwarf weed potion (unf)", 70, 13.8, 72),
    ("Torstol", "Grimy torstol", "Torstol potion (unf)", 75, 15, 78),
]

# output (3 dose), unfinished potion, secondary, level, xp
POTIONS = [
    ("Attack potion(3)", "Guam potion (unf)", "Eye of newt", 3, 25),
    ("Strength potion(3)", "Tarromin potion (unf)", "Limpwurt root", 12, 50),
    ("Restore potion(3)", "Harralander potion (unf)", "Red spiders' eggs", 22, 62.5),
    ("Energy potion(3)", "Harralander potion (unf)", "Chocolate dust", 26, 67.5),
    ("Defence potion(3)", "Ranarr potion (unf)", "White berries", 30, 75),
    ("Prayer potion(3)", "Ranarr potion (unf)", "Snape grass", 38, 87.5),
    ("Super attack(3)", "Irit potion (unf)", "Eye of newt", 45, 100),
    ("Superantipoison(3)", "Irit potion (unf)", "Unicorn horn dust", 48, 106.3),
    ("Super energy(3)", "Avantoe potion (unf)", "Mort myre fungus", 52, 117.5),
    ("Super strength(3)", "Kwuarm potion (unf)", "Limpwurt root", 55, 125),
    ("Super restore(3)", "Snapdragon potion (unf)", "Red spiders' eggs", 63, 142.5),
    ("Super defence(3)", "Cadantine potion (unf)", "White berries", 66, 150),
    ("Antifire potion(3)", "Lantadyme potion (unf)", "Dragon scale dust", 69, 157.5),
    ("Ranging potion(3)", "Dwarf weed potion (unf)", "Wine of zamorak", 72, 162.5),
    ("Magic potion(3)", "Lantadyme potion (unf)", "Potato cactus", 76, 172.5),
    ("Zamorak brew(3)", "Torstol potion (unf)", "Jangerberries", 78, 175),
    ("Saradomin brew(3)", "Toadflax potion (unf)", "Crushed nest", 81, 180),
]

GEMS = [("Sapphire", 20, 50), ("Emerald", 27, 67.5), ("Ruby", 34, 85), ("Diamond", 43, 107.5),
        ("Dragonstone", 55, 137.5), ("Onyx", 67, 167.5), ("Zenyte", 89, 200)]

# colour, body level, body xp
HIDES = [("Green", 63, 186), ("Blue", 71, 210), ("Red", 77, 234), ("Black", 84, 258)]

# element, level, xp
BATTLESTAVES = [("Water", 54, 100), ("Earth", 58, 112.5), ("Fire", 62, 125), ("Air", 66, 137.5)]

# logs, prefix, longbow level/xp, shortbow level/xp
BOWS = [("Oak logs", "Oak", 25, 25, 20, 16.5), ("Willow logs", "Willow", 40, 41.5, 35, 33.3),
        ("Maple logs", "Maple", 55, 58.3, 50, 50), ("Yew logs", "Yew", 70, 75, 65, 67.5),
        ("Magic logs", "Magic", 85, 91.5, 80, 83.3)]

# dart tip metal, level, xp per dart
DARTS = [("Mithril", 52, 7.5), ("Adamant", 67, 11.2), ("Rune", 81, 15),
         ("Amethyst", 90, 21), ("Dragon", 95, 25)]

# arrow metal, level, xp per arrow
ARROWS = [("Mithril", 45, 7.5), ("Adamant", 60, 10), ("Rune", 75, 12.5),
          ("Amethyst", 82, 13.5), ("Dragon", 90, 15)]

# bar, ore, coal at a normal furnace, level, xp, Blast Furnace bars per hour
BARS = [("Steel bar", "Iron ore", 2, 30, 17.5, 5000), ("Mithril bar", "Mithril ore", 4, 50, 30, 2800),
        ("Adamantite bar", "Adamantite ore", 6, 70, 37.5, 2200),
        ("Runite bar", "Runite ore", 8, 85, 50, 1200)]

# raw, cooked, level, xp
FISH = [("Raw lobster", "Lobster", 40, 120), ("Raw swordfish", "Swordfish", 45, 140),
        ("Raw monkfish", "Monkfish", 62, 150), ("Raw shark", "Shark", 80, 210),
        ("Raw sea turtle", "Sea turtle", 82, 211.3), ("Raw anglerfish", "Anglerfish", 84, 230),
        ("Raw dark crab", "Dark crab", 90, 215), ("Raw manta ray", "Manta ray", 91, 216.3)]

# logs, level, xp
FIRE = [("Willow logs", 30, 90), ("Maple logs", 45, 135), ("Yew logs", 60, 202.5),
        ("Magic logs", 75, 303.8), ("Redwood logs", 90, 350)]

# bones, xp at a gilded altar (350%)
BONES = [("Big bones", 52.5), ("Babydragon bones", 105), ("Wyrm bones", 175),
         ("Dragon bones", 252), ("Wyvern bones", 252), ("Drake bones", 280),
         ("Lava dragon bones", 297.5), ("Hydra bones", 385), ("Dagannoth bones", 437.5),
         ("Ourg bones", 490), ("Superior dragon bones", 525)]

# logs, plank, sawmill fee
PLANKS = [("Logs", "Plank", 100), ("Oak logs", "Oak plank", 250), ("Teak logs", "Teak plank", 500),
          ("Mahogany logs", "Mahogany plank", 1500)]

# plank, level, xp per plank, planks per hour
BUILD = [("Oak plank", 33, 60, 3500), ("Teak plank", 50, 90, 3500), ("Mahogany plank", 52, 140, 3000)]


def _processing():
    out = []
    for clean, grimy, unf, lvl, xp, unf_lvl in HERBS:
        out.append(R(f"Clean {grimy[6:]}", "Herblore", lvl, xp, [(grimy, 1)], [(clean, 1)], 4800,
                     note="Bank standing"))
        out.append(R(f"{unf}", "Herblore", unf_lvl, 0, [(clean, 1), ("Vial of water", 1)], [(unf, 1)], 2700,
                     note="No XP; a common money maker"))
    for pot, unf, sec, lvl, xp in POTIONS:
        out.append(R(pot, "Herblore", lvl, xp, [(unf, 1), (sec, 1)], [(pot, 1)], 2500))
    out.append(R("Super combat potion(4)", "Herblore", 90, 150,
                 [("Super attack(4)", 1), ("Super strength(4)", 1), ("Super defence(4)", 1), ("Torstol", 1)],
                 [("Super combat potion(4)", 1)], 1800))
    out.append(R("Extended antifire(4)", "Herblore", 84, 110,
                 [("Antifire potion(4)", 1), ("Lava scale shard", 4)], [("Extended antifire(4)", 1)], 2000))
    out.append(R("Stamina potion(4)", "Herblore", 77, 102,
                 [("Super energy(4)", 1), ("Amylase crystal", 4)], [("Stamina potion(4)", 1)], 2000))
    for gem, lvl, xp in GEMS:
        out.append(R(f"Cut {gem.lower()}", "Crafting", lvl, xp, [(f"Uncut {gem.lower()}", 1)], [(gem, 1)], 2700))
    for col, lvl, xp in HIDES:
        out.append(R(f"Tan {col.lower()} dragonhide", "Crafting", 1, 0,
                     [(f"{col} dragonhide", 1)], [(f"{col} dragon leather", 1)], 1500, coins=20,
                     note="Tanner fee 20 gp each"))
        out.append(R(f"{col} d'hide body", "Crafting", lvl, xp,
                     [(f"{col} dragon leather", 3)], [(f"{col} d'hide body", 1)], 1700,
                     note="Thread cost ignored"))
    for el, lvl, xp in BATTLESTAVES:
        out.append(R(f"{el} battlestaff", "Crafting", lvl, xp,
                     [("Battlestaff", 1), (f"{el} orb", 1)], [(f"{el} battlestaff", 1)], 2500))
    out.append(R("Unpowered orb", "Crafting", 46, 52.5, [("Molten glass", 1)], [("Unpowered orb", 1)], 1500))
    for logs, pre, llvl, lxp, slvl, sxp in BOWS:
        out.append(R(f"{pre} longbow (u)", "Fletching", llvl, lxp, [(logs, 1)], [(f"{pre} longbow (u)", 1)], 1700))
        out.append(R(f"{pre} shortbow (u)", "Fletching", slvl, sxp, [(logs, 1)], [(f"{pre} shortbow (u)", 1)], 1700))
        out.append(R(f"String {pre.lower()} longbow", "Fletching", llvl, lxp,
                     [(f"{pre} longbow (u)", 1), ("Bow string", 1)], [(f"{pre} longbow", 1)], 2400))
        out.append(R(f"String {pre.lower()} shortbow", "Fletching", slvl, sxp,
                     [(f"{pre} shortbow (u)", 1), ("Bow string", 1)], [(f"{pre} shortbow", 1)], 2400))
    for metal, lvl, xp in DARTS:
        out.append(R(f"{metal} darts (10)", "Fletching", lvl, xp * 10,
                     [(f"{metal} dart tip", 10), ("Feather", 10)], [(f"{metal} dart", 10)], 6000,
                     note="Per set of 10, very click intensive"))
    out.append(R("Headless arrows (15)", "Fletching", 1, 15, [("Arrow shaft", 15), ("Feather", 15)],
                 [("Headless arrow", 15)], 1000, note="Per set of 15"))
    for metal, lvl, xp in ARROWS:
        out.append(R(f"{metal} arrows (15)", "Fletching", lvl, xp * 15,
                     [(f"{metal} arrowtips", 15), ("Headless arrow", 15)], [(f"{metal} arrow", 15)], 1000,
                     note="Per set of 15"))
    for bar, ore, coal, lvl, xp, bf in BARS:
        out.append(R(f"{bar} (furnace)", "Smithing", lvl, xp, [(ore, 1), ("Coal", coal)], [(bar, 1)], 1300))
        out.append(R(f"{bar} (Blast Furnace)", "Smithing", lvl, xp, [(ore, 1), ("Coal", coal // 2)], [(bar, 1)], bf,
                     coins=72000 / bf, note="Coffer 72k gp per hour spread per bar"))
    out.append(R("Gold bar (Blast Furnace)", "Smithing", 40, 56.2, [("Gold ore", 1)], [("Gold bar", 1)], 5400,
                 coins=72000 / 5400, note="Goldsmith gauntlets, coffer spread per bar"))
    out.append(R("Steel cannonballs", "Smithing", 35, 25.6, [("Steel bar", 1)], [("Steel cannonball", 4)], 660,
                 note="Per bar, AFK"))
    out.append(R("Rune dart tips", "Smithing", 89, 75, [("Runite bar", 1)], [("Rune dart tip", 10)], 1200))
    out.append(R("Rune platebody", "Smithing", 99, 375, [("Runite bar", 5)], [("Rune platebody", 1)], 300))
    out.append(R("Adamant platebody", "Smithing", 88, 312.5, [("Adamantite bar", 5)], [("Adamant platebody", 1)], 300))
    for raw, cooked, lvl, xp in FISH:
        out.append(R(f"Cook {cooked.lower()}", "Cooking", lvl, xp, [(raw, 1)], [(cooked, 1)], 1300,
                     note="Burns not counted"))
    out.append(R("Cook karambwan", "Cooking", 30, 190, [("Raw karambwan", 1)], [("Cooked karambwan", 1)], 4000,
                 note="Tick cooking, burns not counted"))
    for logs, lvl, xp in FIRE:
        out.append(R(f"Burn {logs.lower()}", "Firemaking", lvl, xp, [(logs, 1)], [], 1300))
    for bones, xp in BONES:
        out.append(R(f"{bones} (gilded altar)", "Prayer", 1, xp, [(bones, 1)], [], 1300,
                     note="Own house altar with both burners lit"))
    for logs, plank, fee in PLANKS:
        out.append(R(f"Sawmill {plank.lower()}", "Construction", 1, 0, [(logs, 1)], [(plank, 1)], 2500,
                     coins=fee, note=f"Sawmill fee {fee} gp each"))
    for plank, lvl, xp, rate in BUILD:
        out.append(R(f"Build with {plank.lower()}s", "Construction", lvl, xp, [(plank, 1)], [], rate,
                     note="Per plank, butler costs not counted"))
    return out


# Item sets: set name and its parts. Combining or splitting is free at the GE clerk.
def _metal_sets():
    sets = []
    for metal, setname in (("Bronze", "Bronze set"), ("Iron", "Iron set"), ("Steel", "Steel set"),
                           ("Black", "Black set"), ("Mithril", "Mithril set"),
                           ("Adamant", "Adamant set"), ("Rune", "Rune armour set"),
                           ("Dragon", "Dragon armour set"), ("Gilded", "Gilded armour set"),
                           ("Saradomin", "Saradomin armour set"), ("Guthix", "Guthix armour set"),
                           ("Zamorak", "Zamorak armour set"), ("Armadyl", "Armadyl rune armour set"),
                           ("Bandos", "Bandos rune armour set"), ("Ancient", "Ancient rune armour set")):
        for leg, piece in (("lg", "platelegs"), ("sk", "plateskirt")):
            sets.append((f"{setname} ({leg})", [f"{metal} full helm", f"{metal} platebody",
                                                f"{metal} {piece}", f"{metal} kiteshield"]))
    for metal in ("Bronze", "Iron", "Steel", "Black", "Mithril", "Adamant", "Rune"):
        for kind, tag in (("trimmed", "t"), ("gold-trimmed", "g")):
            for leg, piece in (("lg", "platelegs"), ("sk", "plateskirt")):
                sets.append((f"{metal} {kind} set ({leg})",
                             [f"{metal} full helm ({tag})", f"{metal} platebody ({tag})",
                              f"{metal} {piece} ({tag})", f"{metal} kiteshield ({tag})"]))
    return sets


SETS = [
    ("Ahrim's armour set", ["Ahrim's hood", "Ahrim's robetop", "Ahrim's robeskirt", "Ahrim's staff"]),
    ("Dharok's armour set", ["Dharok's helm", "Dharok's platebody", "Dharok's platelegs", "Dharok's greataxe"]),
    ("Guthan's armour set", ["Guthan's helm", "Guthan's platebody", "Guthan's chainskirt", "Guthan's warspear"]),
    ("Karil's armour set", ["Karil's coif", "Karil's leathertop", "Karil's leatherskirt", "Karil's crossbow"]),
    ("Torag's armour set", ["Torag's helm", "Torag's platebody", "Torag's platelegs", "Torag's hammers"]),
    ("Verac's armour set", ["Verac's helm", "Verac's brassard", "Verac's plateskirt", "Verac's flail"]),
    ("Green dragonhide set", ["Green d'hide body", "Green d'hide chaps", "Green d'hide vambraces"]),
    ("Blue dragonhide set", ["Blue d'hide body", "Blue d'hide chaps", "Blue d'hide vambraces"]),
    ("Red dragonhide set", ["Red d'hide body", "Red d'hide chaps", "Red d'hide vambraces"]),
    ("Black dragonhide set", ["Black d'hide body", "Black d'hide chaps", "Black d'hide vambraces"]),
    ("Saradomin dragonhide set", ["Saradomin coif", "Saradomin d'hide body", "Saradomin chaps", "Saradomin bracers"]),
    ("Guthix dragonhide set", ["Guthix coif", "Guthix d'hide body", "Guthix chaps", "Guthix bracers"]),
    ("Zamorak dragonhide set", ["Zamorak coif", "Zamorak d'hide body", "Zamorak chaps", "Zamorak bracers"]),
    ("Armadyl dragonhide set", ["Armadyl coif", "Armadyl d'hide body", "Armadyl chaps", "Armadyl bracers"]),
    ("Bandos dragonhide set", ["Bandos coif", "Bandos d'hide body", "Bandos chaps", "Bandos bracers"]),
    ("Ancient dragonhide set", ["Ancient coif", "Ancient d'hide body", "Ancient chaps", "Ancient bracers"]),
    ("Mystic set (blue)", ["Mystic hat", "Mystic robe top", "Mystic robe bottom", "Mystic gloves", "Mystic boots"]),
    ("Mystic set (dark)", ["Mystic hat (dark)", "Mystic robe top (dark)", "Mystic robe bottom (dark)",
                           "Mystic gloves (dark)", "Mystic boots (dark)"]),
    ("Mystic set (light)", ["Mystic hat (light)", "Mystic robe top (light)", "Mystic robe bottom (light)",
                            "Mystic gloves (light)", "Mystic boots (light)"]),
    ("Mystic set (dusk)", ["Mystic hat (dusk)", "Mystic robe top (dusk)", "Mystic robe bottom (dusk)",
                           "Mystic gloves (dusk)", "Mystic boots (dusk)"]),
    ("Obsidian armour set", ["Obsidian helmet", "Obsidian platebody", "Obsidian platelegs"]),
    ("Justiciar armour set", ["Justiciar faceguard", "Justiciar chestguard", "Justiciar legguards"]),
    ("Inquisitor's armour set", ["Inquisitor's great helm", "Inquisitor's hauberk", "Inquisitor's plateskirt"]),
    ("Ancestral robes set", ["Ancestral hat", "Ancestral robe top", "Ancestral robe bottom"]),
    ("Torva armour set", ["Torva full helm", "Torva platebody", "Torva platelegs"]),
    ("Virtus armour set", ["Virtus mask", "Virtus robe top", "Virtus robe bottom"]),
    ("Masori armour set (f)", ["Masori mask (f)", "Masori body (f)", "Masori chaps (f)"]),
    ("Oathplate armour set", ["Oathplate helm", "Oathplate chest", "Oathplate legs"]),
    ("Dagon'hai robes set", ["Dagon'hai hat", "Dagon'hai robe top", "Dagon'hai robe bottom"]),
    ("Dwarf cannon set", ["Cannon base", "Cannon stand", "Cannon barrels", "Cannon furnace"]),
    ("Blood Moon armour set", ["Blood moon helm", "Blood moon chestplate", "Blood moon tassets", "Dual macuahuitl"]),
    ("Blue Moon armour set", ["Blue moon helm", "Blue moon chestplate", "Blue moon tassets", "Blue moon spear"]),
    ("Eclipse Moon armour set", ["Eclipse moon helm", "Eclipse moon chestplate", "Eclipse moon tassets",
                                 "Eclipse atlatl"]),
    ("Combat potion set", ["Attack potion(4)", "Strength potion(4)", "Defence potion(4)"]),
    ("Super potion set", ["Super attack(4)", "Super strength(4)", "Super defence(4)"]),
    ("Partyhat set", ["Red partyhat", "Yellow partyhat", "Blue partyhat", "Purple partyhat",
                      "Green partyhat", "White partyhat"]),
    ("Halloween mask set", ["Red halloween mask", "Green halloween mask", "Blue halloween mask"]),
    ("Holy book page set", ["Saradomin page 1", "Saradomin page 2", "Saradomin page 3", "Saradomin page 4"]),
    ("Book of Balance page set", ["Guthix page 1", "Guthix page 2", "Guthix page 3", "Guthix page 4"]),
    ("Unholy book page set", ["Zamorak page 1", "Zamorak page 2", "Zamorak page 3", "Zamorak page 4"]),
    ("Book of Law page set", ["Armadyl page 1", "Armadyl page 2", "Armadyl page 3", "Armadyl page 4"]),
    ("Book of War page set", ["Bandos page 1", "Bandos page 2", "Bandos page 3", "Bandos page 4"]),
    ("Book of Darkness page set", ["Ancient page 1", "Ancient page 2", "Ancient page 3", "Ancient page 4"]),
    ("Skeletal armour set", ["Skeletal helm", "Skeletal top", "Skeletal bottoms", "Skeletal boots", "Skeletal gloves"]),
    ("Spined armour set", ["Spined helm", "Spined body", "Spined chaps", "Spined boots", "Spined gloves"]),
    ("Rock-shell armour set", ["Rock-shell helm", "Rock-shell plate", "Rock-shell legs", "Rock-shell boots",
                               "Rock-shell gloves"]),
] + _metal_sets()

PROCESSING = _processing()


# Evaluation ------------------------------------------------------------------------

class Pricer:
    """Resolves item names to live prices. `patient` buys at the instant-sell price."""

    def __init__(self, mapping, latest, tax, stats=None, stale_s=20 * 60, now=None):
        self.by_name = {}
        for iid, m in mapping.items():
            self.by_name.setdefault(m["name"].lower(), iid)
        self.mapping, self.latest, self.tax = mapping, latest, tax
        self.stats = stats or {}
        self.stale_s = stale_s
        self.now = now or time.time()

    def iid(self, name):
        return self.by_name.get(name.lower())

    def _side(self, iid, key):
        lt = self.latest.get(iid) or {}
        ts = lt.get(key + "Time")
        return lt.get(key), (self.now - ts) if ts else None

    def buy(self, iid, patient):
        """(price, age in seconds) to buy one item."""
        return self._side(iid, "low" if patient else "high")

    def sell(self, iid, patient):
        """(net proceeds after tax, age). Patient sells list at the instant-buy price."""
        p, age = self._side(iid, "high" if patient else "low")
        return ((p - self.tax(p, iid)) if p else None), age

    def vol24(self, iid):
        return (self.stats.get(iid) or {}).get("vol24")


def evaluate(recipe, pricer, patient=True, per_hour=None):
    """Live cost, revenue and profit for one recipe, or None when an item is unknown.

    Each leg carries its price age and 24h volume. A recipe is flagged `stale` when any
    price is older than the stale limit and `thin` when any leg trades under 100 a day,
    since one old print on an illiquid item can fake a huge profit.
    """
    cost = float(recipe.get("coins") or 0)
    inputs, outputs, caps, ages, vols = [], [], [], [], []
    for name, qty in recipe["inputs"]:
        iid = pricer.iid(name)
        if iid is None:
            return None
        price, age = pricer.buy(iid, patient)
        if price is None:
            return None
        cost += price * qty
        limit = pricer.mapping[iid].get("limit")
        if limit:
            caps.append(limit / qty)
        ages.append(age)
        vols.append(pricer.vol24(iid))
        inputs.append({"id": iid, "name": pricer.mapping[iid]["name"], "qty": qty, "price": price,
                       "icon": pricer.mapping[iid].get("icon"), "age": int(age) if age is not None else None})
    revenue = 0.0
    for name, qty in recipe["outputs"]:
        iid = pricer.iid(name)
        if iid is None:
            return None
        net, age = pricer.sell(iid, patient)
        if net is None:
            return None
        revenue += net * qty
        ages.append(age)
        vols.append(pricer.vol24(iid))
        outputs.append({"id": iid, "name": pricer.mapping[iid]["name"], "qty": qty, "net": net,
                        "icon": pricer.mapping[iid].get("icon"), "age": int(age) if age is not None else None})
    profit = revenue - cost
    rate = per_hour if per_hour else recipe.get("per_hour") or 0
    # Buy limits cap how many actions you can supply from the GE in 4 hours.
    cap_hr = (min(caps) / 4.0) if caps else None
    eff_rate = min(rate, cap_hr) if cap_hr is not None and rate else rate
    xp = recipe.get("xp") or 0
    icon_item = (outputs or inputs)[0]
    known_vols = [v for v in vols if v is not None]
    min_vol = min(known_vols) if known_vols and len(known_vols) == len(vols) else None
    max_age = max((a for a in ages if a is not None), default=None)
    return {
        "name": recipe["name"], "skill": recipe.get("skill"), "level": recipe.get("level"),
        "group": recipe.get("group"), "note": recipe.get("note", ""), "xp": xp,
        "inputs": inputs, "outputs": outputs, "icon": icon_item.get("icon"), "id": icon_item["id"],
        "cost": round(cost, 1), "revenue": round(revenue, 1), "profit": round(profit, 1),
        "roi": (profit / cost) if cost else None,
        "gpXp": (profit / xp) if xp else None,
        "perHour": rate, "limitPerHour": cap_hr, "limited": bool(cap_hr is not None and rate and cap_hr < rate),
        "gpHr": profit * eff_rate if eff_rate else None,
        "xpHr": xp * eff_rate if eff_rate else None,
        "maxAge": int(max_age) if max_age is not None else None,
        "stale": bool(max_age is not None and max_age > pricer.stale_s),
        "minVol24": min_vol, "thin": bool(min_vol is not None and min_vol < 100),
        "custom": recipe.get("custom", False), "rid": recipe.get("rid"),
    }


def custom_recipes(db):
    out = []
    for r in db.q("SELECT * FROM recipes_custom ORDER BY rid"):
        try:
            inputs = [(a, float(b)) for a, b in json.loads(r["inputs"])]
            outputs = [(a, float(b)) for a, b in json.loads(r["outputs"])]
        except (ValueError, TypeError):
            continue
        rec = R(r["name"], r["skill"] or "Custom", r["level"] or 1, r["xp"] or 0, inputs, outputs,
                r["per_hour"] or 0, coins=r["coins"] or 0, group="Custom")
        rec["custom"] = True
        rec["rid"] = r["rid"]
        out.append(rec)
    return out


def money_making(pricer, db=None, patient=True, rates=None):
    rates = rates or {}
    recs = PROCESSING + (custom_recipes(db) if db is not None else [])
    out = []
    for rec in recs:
        ev = evaluate(rec, pricer, patient, rates.get(rec["name"]))
        if ev:
            out.append(ev)
    out.sort(key=lambda r: (r["gpHr"] is None, -(r["gpHr"] or 0)))
    return out


def set_arbitrage(pricer, stats=None, patient=True):
    """Both directions for every set: combine parts into a set, or split a set into parts."""
    stats = stats or {}
    out = []
    for set_name, parts in SETS:
        sid = pricer.iid(set_name)
        pids = [pricer.iid(p) for p in parts]
        if sid is None or any(p is None for p in pids):
            continue
        for direction in ("combine", "split"):
            if direction == "combine":
                rec = R(set_name, None, 0, 0, [(p, 1) for p in parts], [(set_name, 1)], 0)
            else:
                rec = R(set_name, None, 0, 0, [(set_name, 1)], [(p, 1) for p in parts], 0)
            ev = evaluate(rec, pricer, patient)
            if not ev:
                continue
            ids_in = [i["id"] for i in ev["inputs"]]
            ids_out = [o["id"] for o in ev["outputs"]]
            # Thinnest leg of the trade decides how fast it really fills.
            vol_in = min((stats.get(i, {}).get("vol24") or 0) for i in ids_in)
            vol_out = min((stats.get(i, {}).get("vol24") or 0) for i in ids_out)
            limit = min((pricer.mapping[i].get("limit") or 0) for i in ids_in) or None
            ev.update({"direction": direction, "setId": sid, "set": set_name,
                       "parts": len(parts), "limit": limit, "vol24In": vol_in, "vol24Out": vol_out,
                       "profitPerLimit": ev["profit"] * limit if limit else None,
                       "id": sid, "icon": pricer.mapping[sid].get("icon")})
            out.append(ev)
    out.sort(key=lambda r: -r["profit"])
    return out


def xp_methods(pricer, skill, level=None, db=None, patient=True):
    """Recipes that train a skill, cheapest GP per XP first (positive gpXp means profit)."""
    recs = [r for r in PROCESSING + (custom_recipes(db) if db is not None else [])
            if r.get("skill") == skill and (r.get("xp") or 0) > 0 and (level is None or r["level"] <= level)]
    out = []
    for rec in recs:
        ev = evaluate(rec, pricer, patient)
        if ev and ev["gpXp"] is not None:
            out.append(ev)
    out.sort(key=lambda r: -r["gpXp"])
    return out


def recipes_using(pricer, item_id, db=None, patient=True):
    """Recipes and sets that consume or produce an item (for the item panel)."""
    name = pricer.mapping.get(item_id, {}).get("name", "").lower()
    if not name:
        return []
    out = []
    for rec in PROCESSING + (custom_recipes(db) if db is not None else []):
        names = [n.lower() for n, _ in rec["inputs"] + rec["outputs"]]
        if name in names:
            ev = evaluate(rec, pricer, patient)
            if ev:
                ev["role"] = "input" if name in [n.lower() for n, _ in rec["inputs"]] else "output"
                out.append(ev)
    for set_name, parts in SETS:
        if name == set_name.lower() or name in [p.lower() for p in parts]:
            ev = evaluate(R(set_name, None, 0, 0, [(p, 1) for p in parts], [(set_name, 1)], 0), pricer, patient)
            if ev:
                ev["role"] = "set"
                ev["name"] = f"{set_name} (combine)"
                out.append(ev)
    return out
