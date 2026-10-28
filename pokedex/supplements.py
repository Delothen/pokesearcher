"""Hand-maintained patches for what PokeAPI does not have yet.

PokeAPI is slow to catch up with new games (Pokémon Legends: Z-A, Pokémon
Champions). Anything in here is merged over what PokeAPI returns, so adding
a line here is all it takes to fix a gap, and a line can be deleted once
PokeAPI has the data itself.

Sources: Bulbapedia, WikiDex, Pokémon Zone's Champions move list.
"""

# ---- Moves PokeAPI does not know at all ------------------------------------
# Nihil Light is the only new move of Legends: Z-A: Mega Zygarde's signature.
EXTRA_MOVES = {
    "nihil-light": {
        "id": None,
        "name": "nihil-light",
        "type": "dragon",
        "damage_class": "special",
        "power": 200,
        "accuracy": 100,
        "pp": 10,
        "effect": (
            "Inflicts damage and ignores the target's stat changes. It also "
            "hits Fairy-type Pokémon for neutral damage instead of being "
            "blocked. Mega Zygarde's signature move: a Zygarde (Complete "
            "Forme) that knows Core Enforcer turns it into Nihil Light when "
            "it Mega Evolves."
        ),
        "games": ["legends-za"],
        # Pokémon that learn it (PokeAPI /pokemon names)
        "learned_by": ["zygarde-mega"],
    },
}

# ---- Per-game stat fixes for moves PokeAPI does have -----------------------
# {move: {game key: {field: value}}}. Fields: power, accuracy, pp, type.
MOVE_GAME_OVERRIDES = {
    # Pokémon Champions rebalanced a number of moves.
    "make-it-rain": {"champions": {"accuracy": 95, "pp": 8}},
    "vise-grip": {"champions": {"accuracy": 95}},
    "syrup-bomb": {"champions": {"accuracy": 90}},
}

# ---- Learnset rows PokeAPI is missing --------------------------------------
# {pokemon: [{"name", "version_group", "level", "method"}]}
EXTRA_LEARNSETS = {
    "zygarde-mega": [
        {
            "name": "nihil-light",
            "version_group": "legends-za",
            "level": 0,
            "method": "mega evolution",
        },
    ],
}


# ---- Item descriptions PokeAPI does not have --------------------------------
# Newer battle items often have no effect text or in-game description in
# PokeAPI. These are used when PokeAPI has none: "effect" is shown under
# Effect, and every game in "games" gets a button whose in-game description is
# the same text. Keys are PokeAPI item names (see api.BATTLE_ITEM_EXTRAS).
_SWSH_SV = ["scarlet-violet", "sword-shield"]
EXTRA_ITEM_INFO = {
    "ability-shield": {
        "effect": "Prevents the holder's Ability from being changed or "
                  "suppressed by other Pokémon's moves and Abilities.",
        "games": _SWSH_SV,
    },
    "covert-cloak": {
        "effect": "Protects the holder from the additional effects of "
                  "moves used by other Pokémon.",
        "games": ["scarlet-violet"],
    },
    "clear-amulet": {
        "effect": "Prevents other Pokémon's moves and Abilities from "
                  "lowering the holder's stats.",
        "games": ["scarlet-violet"],
    },
    "punching-glove": {
        "effect": "Boosts the power of the holder's punching moves by 10% "
                  "and stops them from making contact with the target.",
        "games": ["scarlet-violet"],
    },
    "loaded-dice": {
        "effect": "Moves that hit two to five times in a row will hit at "
                  "least four times when the holder uses them.",
        "games": ["scarlet-violet"],
    },
    "booster-energy": {
        "effect": "Activates the Abilities Protosynthesis and Quark Drive, "
                  "boosting the holder's highest stat. Single use.",
        "games": ["scarlet-violet"],
    },
    "mirror-herb": {
        "effect": "When an opposing Pokémon's stats are raised, the holder "
                  "copies those stat increases. Single use.",
        "games": ["scarlet-violet"],
    },
    "adrenaline-orb": {
        "effect": "Raises the holder's Speed when it is affected by "
                  "Intimidate. Single use.",
        "games": _SWSH_SV,
    },
    "blunder-policy": {
        "effect": "Sharply raises the holder's Speed if one of its moves "
                  "misses because of accuracy. Single use.",
        "games": _SWSH_SV,
    },
    "room-service": {
        "effect": "Lowers the holder's Speed if Trick Room is in effect. "
                  "Single use.",
        "games": _SWSH_SV,
    },
    "throat-spray": {
        "effect": "Raises the holder's Sp. Atk after it uses a "
                  "sound-based move. Single use.",
        "games": _SWSH_SV,
    },
    "eject-pack": {
        "effect": "If the holder's stats are lowered, it is switched out "
                  "for another Pokémon in the party. Single use.",
        "games": _SWSH_SV,
    },
    "utility-umbrella": {
        "effect": "Protects the holder from the effects of sunny and rainy "
                  "weather.",
        "games": _SWSH_SV,
    },
    "heavy-duty-boots": {
        "effect": "Protects the holder from the effects of entry hazards "
                  "when it switches in.",
        "games": _SWSH_SV,
    },
    "protective-pads": {
        "effect": "Protects the holder from effects caused by making "
                  "contact with the target.",
        "games": _SWSH_SV,
    },
    "terrain-extender": {
        "effect": "Extends the duration of a terrain the holder creates "
                  "from five turns to eight.",
        "games": _SWSH_SV,
    },
    "cornerstone-mask": {
        "effect": "A mask that changes Ogerpon's form and makes it Rock "
                  "type when it holds it. Boosts the power of its moves by "
                  "20%.",
        "games": ["scarlet-violet"],
    },
    "wellspring-mask": {
        "effect": "A mask that changes Ogerpon's form and makes it Water "
                  "type when it holds it. Boosts the power of its moves by "
                  "20%.",
        "games": ["scarlet-violet"],
    },
    "hearthflame-mask": {
        "effect": "A mask that changes Ogerpon's form and makes it Fire "
                  "type when it holds it. Boosts the power of its moves by "
                  "20%.",
        "games": ["scarlet-violet"],
    },
    "fairy-feather": {
        "effect": "Boosts the power of the holder's Fairy-type moves by "
                  "20%.",
        "games": ["scarlet-violet"],
    },
    # Pokémon Legends: Arceus items that change a Pokémon's form
    "legend-plate": {
        "effect": "A plate in Pokémon Legends: Arceus tied to Arceus's "
                  "form. It is not held in battle: it is used to change "
                  "Arceus.",
        "games": ["legends-arceus"],
    },
    "griseous-core": {
        "effect": "A core (Legends: Arceus, Scarlet / Violet) that changes Giratina "
                  "into its Origin Forme. It is not held in battle: it is "
                  "used to transform Giratina.",
        "games": ["legends-arceus", "scarlet-violet"],
    },
    "adamant-crystal": {
        "effect": "A crystal (Legends: Arceus, Scarlet / Violet) that changes Dialga "
                  "into its Origin Forme. It is not held in battle: it is "
                  "used to transform Dialga.",
        "games": ["legends-arceus", "scarlet-violet"],
    },
    "lustrous-globe": {
        "effect": "A globe (Legends: Arceus, Scarlet / Violet) that changes Palkia "
                  "into its Origin Forme. It is not held in battle: it is "
                  "used to transform Palkia.",
        "games": ["legends-arceus", "scarlet-violet"],
    },
}


# ---- G-Max moves (Sword / Shield) --------------------------------------------
# PokeAPI has no G-Max moves, so the Moves page had nothing for them. Each is
# the signature move of one Gigantamax form and replaces any move of its type
# when that Pokémon Gigantamaxes. G-Max moves have no fixed power or accuracy
# (the power comes from the move they replace), so those show as "-".
# (move, display, type, [Gigantamax Pokémon], effect)
_GMAX = [
    ("g-max-wildfire", "Fire", ["charizard-gmax"],
     "Inflicts damage and, for four turns, damages every opposing Pokémon that is not Fire-type at the end of each turn."),
    ("g-max-vine-lash", "Grass", ["venusaur-gmax"],
     "Inflicts damage and, for four turns, damages every opposing Pokémon that is not Grass-type at the end of each turn."),
    ("g-max-cannonade", "Water", ["blastoise-gmax"],
     "Inflicts damage and, for four turns, damages every opposing Pokémon that is not Water-type at the end of each turn."),
    ("g-max-volcalith", "Rock", ["coalossal-gmax"],
     "Inflicts damage and, for four turns, damages every opposing Pokémon that is not Rock-type at the end of each turn."),
    ("g-max-steelsurge", "Steel", ["copperajah-gmax"],
     "Inflicts damage and scatters sharp steel spikes on the opposing side. Pokémon that switch in there are hurt, based on how effective Steel-type moves are against them."),
    ("g-max-befuddle", "Bug", ["butterfree-gmax"],
     "Inflicts damage and puts each opposing Pokémon to sleep, paralyzes it or poisons it (one of the three, chosen at random for each)."),
    ("g-max-volt-crash", "Electric", ["pikachu-gmax"],
     "Inflicts damage and paralyzes all opposing Pokémon."),
    ("g-max-gold-rush", "Normal", ["meowth-gmax"],
     "Inflicts damage, confuses all opposing Pokémon and scatters coins, earning extra money after the battle."),
    ("g-max-chi-strike", "Fighting", ["machamp-gmax"],
     "Inflicts damage and raises the critical-hit ratio of the user and its allies."),
    ("g-max-terror", "Ghost", ["gengar-gmax"],
     "Inflicts damage and keeps the target from switching out or fleeing."),
    ("g-max-resonance", "Ice", ["lapras-gmax"],
     "Inflicts damage and sets up Aurora Veil on the user's side for five turns."),
    ("g-max-cuddle", "Normal", ["eevee-gmax"],
     "Inflicts damage and makes all opposing Pokémon infatuated."),
    ("g-max-replenish", "Normal", ["snorlax-gmax"],
     "Inflicts damage and may restore Berries that the user and its allies have eaten."),
    ("g-max-malodor", "Poison", ["garbodor-gmax"],
     "Inflicts damage and poisons all opposing Pokémon."),
    ("g-max-meltdown", "Steel", ["melmetal-gmax"],
     "Inflicts damage and Torments all opposing Pokémon for three turns, so they cannot use the same move twice in a row."),
    ("g-max-drum-solo", "Grass", ["rillaboom-gmax"],
     "Inflicts damage. It ignores the target's Ability."),
    ("g-max-fireball", "Fire", ["cinderace-gmax"],
     "Inflicts damage. It ignores the target's Ability."),
    ("g-max-hydrosnipe", "Water", ["inteleon-gmax"],
     "Inflicts damage. It ignores the target's Ability."),
    ("g-max-wind-rage", "Flying", ["corviknight-gmax"],
     "Inflicts damage and blows away the opposing side's hazards, screens and terrain."),
    ("g-max-gravitas", "Psychic", ["orbeetle-gmax"],
     "Inflicts damage and intensifies gravity for five turns."),
    ("g-max-stonesurge", "Water", ["drednaw-gmax"],
     "Inflicts damage and scatters sharp stones on the opposing side, hurting Pokémon that switch in."),
    ("g-max-tartness", "Grass", ["flapple-gmax"],
     "Inflicts damage and lowers the evasiveness of all opposing Pokémon."),
    ("g-max-sweetness", "Grass", ["appletun-gmax"],
     "Inflicts damage and cures the status conditions of the user and its allies."),
    ("g-max-sandblast", "Ground", ["sandaconda-gmax"],
     "Inflicts damage and traps all opposing Pokémon in a sandstorm for four to five turns."),
    ("g-max-stun-shock", "Electric", ["toxtricity-amped-gmax", "toxtricity-low-key-gmax"],
     "Inflicts damage and either poisons or paralyzes all opposing Pokémon."),
    ("g-max-centiferno", "Fire", ["centiskorch-gmax"],
     "Inflicts damage and traps all opposing Pokémon in swirling fire for four to five turns."),
    ("g-max-smite", "Fairy", ["hatterene-gmax"],
     "Inflicts damage and confuses all opposing Pokémon."),
    ("g-max-snooze", "Dark", ["grimmsnarl-gmax"],
     "Inflicts damage and makes the target drowsy, so it falls asleep at the end of the next turn."),
    ("g-max-finale", "Fairy", ["alcremie-gmax"],
     "Inflicts damage and restores a sixth of the maximum HP of the user and its allies."),
    ("g-max-steelsurge", "Steel", ["copperajah-gmax"],
     "Inflicts damage and scatters sharp steel spikes on the opposing side. Pokémon that switch in there are hurt, based on how effective Steel-type moves are against them."),
    ("g-max-depletion", "Dragon", ["duraludon-gmax"],
     "Inflicts damage and reduces the PP of the target's last used move by two."),
    ("g-max-one-blow", "Dark", ["urshifu-single-strike-gmax"],
     "Inflicts damage and goes through protection such as Protect and Max Guard."),
    ("g-max-rapid-flow", "Water", ["urshifu-rapid-strike-gmax"],
     "Inflicts damage and goes through protection such as Protect and Max Guard."),
    ("g-max-foam-burst", "Water", ["kingler-gmax"],
     "Inflicts damage and harshly lowers the Speed of all opposing Pokémon."),
]
for _slug, _type, _mons, _effect in _GMAX:
    EXTRA_MOVES[_slug] = {
        "id": None, "name": _slug, "type": _type.lower(),
        "damage_class": "", "power": None, "accuracy": None, "pp": 5,
        "effect": _effect, "games": ["sword-shield"], "learned_by": _mons,
    }


# ---- Z-Moves each Z-Crystal creates ------------------------------------------
# {crystal: {"from": what it upgrades, "pokemon": who can use it,
#            "moves": [(PokeAPI move name, display name)]}}
# A type's crystal upgrades any move of that type; a signature crystal
# upgrades one move of one Pokémon.
def _type_z(from_type, link, name):
    return {"from": "Any " + from_type + "-type move", "pokemon": "Any Pokémon",
            "moves": [(link, name)]}


Z_CRYSTAL_MOVES = {
    "normalium-z": _type_z("Normal", "breakneck-blitz--physical", "Breakneck Blitz"),
    "ilimas-normalium-z": _type_z("Normal", "breakneck-blitz--physical", "Breakneck Blitz"),
    "fightinium-z": _type_z("Fighting", "all-out-pummeling--physical", "All-Out Pummeling"),
    "flyinium-z": _type_z("Flying", "supersonic-skystrike--physical", "Supersonic Skystrike"),
    "poisonium-z": _type_z("Poison", "acid-downpour--physical", "Acid Downpour"),
    "groundium-z": _type_z("Ground", "tectonic-rage--physical", "Tectonic Rage"),
    "rockium-z": _type_z("Rock", "continental-crush--physical", "Continental Crush"),
    "buginium-z": _type_z("Bug", "savage-spin-out--physical", "Savage Spin-Out"),
    "ghostium-z": _type_z("Ghost", "never-ending-nightmare--physical", "Never-Ending Nightmare"),
    "steelium-z": _type_z("Steel", "corkscrew-crash--physical", "Corkscrew Crash"),
    "firium-z": _type_z("Fire", "inferno-overdrive--physical", "Inferno Overdrive"),
    "waterium-z": _type_z("Water", "hydro-vortex--physical", "Hydro Vortex"),
    "grassium-z": _type_z("Grass", "bloom-doom--physical", "Bloom Doom"),
    "electrium-z": _type_z("Electric", "gigavolt-havoc--physical", "Gigavolt Havoc"),
    "psychium-z": _type_z("Psychic", "shattered-psyche--physical", "Shattered Psyche"),
    "icium-z": _type_z("Ice", "subzero-slammer--physical", "Subzero Slammer"),
    "dragonium-z": _type_z("Dragon", "devastating-drake--physical", "Devastating Drake"),
    "darkinium-z": _type_z("Dark", "black-hole-eclipse--physical", "Black Hole Eclipse"),
    "fairium-z": _type_z("Fairy", "twinkle-tackle--physical", "Twinkle Tackle"),
    "aloraichium-z": {"from": "Thunderbolt", "pokemon": "Alolan Raichu",
                      "moves": [("stoked-sparksurfer", "Stoked Sparksurfer")]},
    "decidium-z": {"from": "Spirit Shackle", "pokemon": "Decidueye",
                   "moves": [("sinister-arrow-raid", "Sinister Arrow Raid")]},
    "incinium-z": {"from": "Darkest Lariat", "pokemon": "Incineroar",
                   "moves": [("malicious-moonsault", "Malicious Moonsault")]},
    "primarium-z": {"from": "Sparkling Aria", "pokemon": "Primarina",
                    "moves": [("oceanic-operetta", "Oceanic Operetta")]},
    "eevium-z": {"from": "Last Resort", "pokemon": "Eevee",
                 "moves": [("extreme-evoboost", "Extreme Evoboost")]},
    "pikanium-z": {"from": "Volt Tackle", "pokemon": "Pikachu",
                   "moves": [("catastropika", "Catastropika")]},
    "pikashunium-z": {"from": "Thunderbolt", "pokemon": "Pikachu wearing a cap",
                      "moves": [("10-000-000-volt-thunderbolt", "10,000,000 Volt Thunderbolt")]},
    "snorlium-z": {"from": "Giga Impact", "pokemon": "Snorlax",
                   "moves": [("pulverizing-pancake", "Pulverizing Pancake")]},
    "mewnium-z": {"from": "Psychic", "pokemon": "Mew",
                  "moves": [("genesis-supernova", "Genesis Supernova")]},
    "tapunium-z": {"from": "Nature's Madness",
                   "pokemon": "Tapu Koko, Tapu Lele, Tapu Bulu, Tapu Fini",
                   "moves": [("guardian-of-alola", "Guardian of Alola")]},
    "marshadium-z": {"from": "Spectral Thief", "pokemon": "Marshadium",
                     "moves": [("soul-stealing-7-star-strike", "Soul-Stealing 7-Star Strike")]},
    "kommonium-z": {"from": "Clanging Scales", "pokemon": "Kommo-o",
                    "moves": [("clangorous-soulblaze", "Clangorous Soulblaze")]},
    "lycanium-z": {"from": "Stone Edge", "pokemon": "Lycanroc",
                   "moves": [("splintered-stormshards", "Splintered Stormshards")]},
    "mimikium-z": {"from": "Play Rough", "pokemon": "Mimikyu",
                   "moves": [("lets-snuggle-forever", "Let's Snuggle Forever")]},
    "solganium-z": {"from": "Sunsteel Strike",
                    "pokemon": "Solgaleo, Necrozma (Dusk Mane)",
                    "moves": [("searing-sunraze-smash", "Searing Sunraze Smash")]},
    "lunalium-z": {"from": "Moongeist Beam",
                   "pokemon": "Lunala, Necrozma (Dawn Wings)",
                   "moves": [("menacing-moonraze-maelstrom", "Menacing Moonraze Maelstrom")]},
    "ultranecrozium-z": {"from": "Photon Geyser",
                         "pokemon": "Necrozma (Dusk Mane / Dawn Wings)",
                         "moves": [("light-that-burns-the-sky", "Light That Burns the Sky")]},
}

# Z-Crystals that were added in Ultra Sun / Ultra Moon (not in Sun / Moon).
ULTRA_ONLY_Z_CRYSTALS = {
    "solganium-z", "lunalium-z", "marshadium-z", "pikashunium-z",
    "ultranecrozium-z", "kommonium-z", "mimikium-z",
}

# The Z-Moves themselves only exist in Sun / Moon and Ultra Sun / Ultra Moon
# (PokeAPI still has flavour text for them in later games, which is not
# the same as the move being in that game). {move: (games it is in)}
Z_MOVE_GAMES = {}
for _crystal, _info in Z_CRYSTAL_MOVES.items():
    _games = (("ultra-sun-ultra-moon",) if _crystal in ULTRA_ONLY_Z_CRYSTALS
              else ("sun-moon", "ultra-sun-ultra-moon"))
    for _move, _label in _info["moves"]:
        Z_MOVE_GAMES[_move] = _games
        if _move.endswith("--physical"):   # PokeAPI also has a --special twin
            Z_MOVE_GAMES[_move[:-len("--physical")] + "--special"] = _games


# Who can use each signature Z-Move (PokeAPI slugs of the Pokémon / forms).
Z_MOVE_LEARNERS = {
    "stoked-sparksurfer": ["raichu-alola"],
    "sinister-arrow-raid": ["decidueye"],
    "malicious-moonsault": ["incineroar"],
    "oceanic-operetta": ["primarina"],
    "extreme-evoboost": ["eevee"],
    "catastropika": ["pikachu"],
    "10-000-000-volt-thunderbolt": [
        "pikachu-original-cap", "pikachu-hoenn-cap", "pikachu-sinnoh-cap",
        "pikachu-unova-cap", "pikachu-kalos-cap", "pikachu-alola-cap",
        "pikachu-partner-cap"],
    "pulverizing-pancake": ["snorlax"],
    "genesis-supernova": ["mew"],
    "guardian-of-alola": ["tapu-koko", "tapu-lele", "tapu-bulu", "tapu-fini"],
    "soul-stealing-7-star-strike": ["marshadow"],
    "clangorous-soulblaze": ["kommo-o"],
    "splintered-stormshards": [
        "lycanroc-midday", "lycanroc-midnight", "lycanroc-dusk"],
    "lets-snuggle-forever": ["mimikyu-disguised", "mimikyu-busted"],
    "searing-sunraze-smash": ["solgaleo", "necrozma-dusk"],
    "menacing-moonraze-maelstrom": ["lunala", "necrozma-dawn"],
    "light-that-burns-the-sky": ["necrozma-dusk", "necrozma-dawn"],
}