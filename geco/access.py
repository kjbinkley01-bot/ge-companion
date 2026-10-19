"""What it takes to reach bosses and raids: quests, levels and other unlocks.

Boss pages on the Wiki do not carry structured requirements, so this list is curated from
them. Each entry links its Wiki page; check it before a trip, since Jagex changes access now
and then. Recommended stats are shown separately as advice, not requirements.
"""

# name: {"group", "quests": [...], "skills": {skill: level}, "other": [text], "advice": text}
BOSSES = {
    "Vorkath": {"group": "Bosses", "quests": ["Dragon Slayer II"], "skills": {}, "other": [],
                "advice": "Strong ranged gear and antifire; 80+ Ranged recommended."},
    "Zulrah": {"group": "Bosses", "quests": ["Regicide"], "skills": {}, "other": [],
               "advice": "Mage and range switches; 80+ Magic and Ranged recommended."},
    "The Gauntlet": {"group": "Bosses", "quests": ["Song of the Elves"], "skills": {}, "other": [],
                     "advice": "No gear allowed in; 70+ combat stats recommended."},
    "Corrupted Gauntlet": {"group": "Bosses", "quests": ["Song of the Elves"], "skills": {},
                           "other": ["Finish the normal Gauntlet once"], "advice": "80+ combat stats recommended."},
    "Zalcano": {"group": "Bosses", "quests": ["Song of the Elves"], "skills": {"Mining": 70, "Smithing": 70}, "other": [],
                "advice": "Skilling boss; no combat gear needed."},
    "Phantom Muspah": {"group": "Bosses", "quests": ["Secrets of the North"], "skills": {}, "other": [],
                       "advice": "Ranged and magic switches."},
    "Duke Sucellus": {"group": "Desert Treasure II bosses", "quests": ["Desert Treasure II - The Fallen Empire"],
                      "skills": {}, "other": [], "advice": "Melee."},
    "The Leviathan": {"group": "Desert Treasure II bosses", "quests": ["Desert Treasure II - The Fallen Empire"],
                      "skills": {}, "other": [], "advice": "Ranged."},
    "The Whisperer": {"group": "Desert Treasure II bosses", "quests": ["Desert Treasure II - The Fallen Empire"],
                      "skills": {}, "other": [], "advice": "Magic."},
    "Vardorvis": {"group": "Desert Treasure II bosses", "quests": ["Desert Treasure II - The Fallen Empire"],
                  "skills": {}, "other": [], "advice": "Melee."},
    "Dagannoth Kings": {"group": "Bosses", "quests": ["The Fremennik Trials"], "skills": {}, "other": [],
                        "advice": "A mix of combat styles; usually done as a team or with a cannon."},
    "Barrows": {"group": "Bosses", "quests": ["Priest in Peril"], "skills": {}, "other": [],
                "advice": "Magic for most brothers; 70+ combat stats recommended."},
    "Grotesque Guardians": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 75},
                            "other": ["On a gargoyle task, with a brittle key"], "advice": "Granite hammer helps."},
    "Abyssal Sire": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 85},
                     "other": ["On an abyssal demon task"], "advice": ""},
    "Kraken": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 87},
               "other": ["On a cave kraken task"], "advice": "Magic."},
    "Cerberus": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 91},
                 "other": ["On a hellhound task"], "advice": ""},
    "Thermonuclear smoke devil": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 93},
                                  "other": ["On a smoke devil task"], "advice": ""},
    "Alchemical Hydra": {"group": "Slayer bosses", "quests": [], "skills": {"Slayer": 95},
                         "other": ["On a hydra task"], "advice": "Ranged; 85+ Ranged recommended."},
    "Hespori": {"group": "Skilling bosses", "quests": [], "skills": {"Farming": 65}, "other": [], "advice": ""},
    "Tempoross": {"group": "Skilling bosses", "quests": [], "skills": {"Fishing": 35}, "other": [], "advice": ""},
    "Wintertodt": {"group": "Skilling bosses", "quests": [], "skills": {"Firemaking": 50}, "other": [], "advice": ""},
    "Chambers of Xeric": {"group": "Raids", "quests": [], "skills": {}, "other": [],
                          "advice": "No requirements to enter; 80+ combat stats and a team recommended."},
    "Theatre of Blood": {"group": "Raids", "quests": ["Priest in Peril"], "skills": {}, "other": [],
                         "advice": "Entry mode is a good start; 90+ combat stats recommended for normal mode."},
    "Tombs of Amascut": {"group": "Raids", "quests": [], "skills": {}, "other": [],
                         "advice": "No requirements to enter; raid level scales difficulty."},
}

WIKI = "https://oldschool.runescape.wiki/w/"


def wiki_url(name):
    return WIKI + name.replace(" ", "_")


def listing():
    return [dict(v, name=k, wiki=wiki_url(k)) for k, v in BOSSES.items()]
