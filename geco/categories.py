"""Item categories for market indices, derived from item names.

The Wiki mapping has no category field, so these are name rules. They are meant to
group the big liquid markets (runes, logs, ores, herbs...) well enough to show whether
a price move is market wide or specific to one item; odd items can fall through.
"""
import re

RUNES = {"air", "water", "earth", "fire", "mind", "body", "cosmic", "chaos", "nature", "law",
         "death", "astral", "blood", "soul", "wrath", "mist", "dust", "mud", "smoke", "steam",
         "lava", "sunfire", "aether"}
GEMS = {"sapphire", "emerald", "ruby", "diamond", "dragonstone", "onyx", "zenyte", "opal", "jade",
        "red topaz"}
CLEAN_HERBS = {"guam leaf", "marrentill", "tarromin", "harralander", "ranarr weed", "toadflax",
               "irit leaf", "avantoe", "kwuarm", "huasca", "snapdragon", "cadantine", "lantadyme",
               "dwarf weed", "torstol"}
FOOD = {"shark", "manta ray", "anglerfish", "dark crab", "cooked karambwan", "monkfish", "lobster",
        "swordfish", "tuna", "sea turtle", "salmon", "trout", "bass", "pineapple pizza",
        "tuna potato", "curry", "cooked chompy"}
JEWELLERY_WORDS = ("necklace", "amulet", "ring", "bracelet", "glory", "burning", "teleport", "binding",
                   "pendant", "slayer", "chronicle", "games", "dueling")
POTION_RE = re.compile(r"\(\d\)$")

CATEGORIES = [
    ("runes", "Runes"),
    ("logs", "Logs and planks"),
    ("ores", "Ores and bars"),
    ("herbs", "Herbs"),
    ("potions", "Potions"),
    ("seeds", "Seeds"),
    ("bones", "Bones"),
    ("food", "Food and fish"),
    ("ammo", "Ammo"),
    ("gems", "Gems"),
    ("hides", "Hides and leather"),
]
LABELS = dict(CATEGORIES)
LABELS.update({"market": "Market (top 100 by gp traded)", "bigticket": "Big ticket (10m+)"})


def classify(name):
    n = name.lower()
    if n.endswith(" rune") and n[:-5] in RUNES:
        return "runes"
    if n in ("logs", "plank") or n.endswith(" logs") or n.endswith(" plank"):
        return "logs"
    if n == "coal" or n.endswith(" ore") or (n.endswith(" bar") and n != "chocolate bar"):
        return "ores"
    if n.startswith("grimy ") or n in CLEAN_HERBS or n.endswith(" potion (unf)"):
        return "herbs"
    if POTION_RE.search(n) and not any(w in n for w in JEWELLERY_WORDS):
        return "potions"
    if n.endswith(" seed"):
        return "seeds"
    if n.endswith("bones") and "bones to" not in n:
        return "bones"
    if n in FOOD or n.startswith("raw "):
        return "food"
    if (n.endswith(" arrow") or n.endswith(" dart") or n.endswith(" bolts") or n.endswith(" javelin")
            or n.endswith(" knife") or n.endswith(" cannonball") or "chinchompa" in n
            or n.endswith(" arrow(p++)") or n.endswith(" dart(p++)")):
        return "ammo"
    if n in GEMS or n.startswith("uncut "):
        return "gems"
    if n.endswith(" dragonhide") or n.endswith("dragon leather") or n in ("cowhide", "leather", "hard leather",
                                                                          "snakeskin", "snake hide"):
        return "hides"
    return None


def build(mapping):
    """{category key: [item ids]} for the static categories."""
    out = {k: [] for k, _ in CATEGORIES}
    for iid, m in mapping.items():
        c = classify(m["name"])
        if c:
            out[c].append(iid)
    return out
