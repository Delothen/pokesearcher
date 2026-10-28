"""Client for the two external APIs: PokeAPI and TCGdex."""
import math
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

from . import bulbapedia, supplements

POKEAPI_POKEMON = "https://pokeapi.co/api/v2/pokemon/{}"
POKEAPI_MOVE = "https://pokeapi.co/api/v2/move/{}"
POKEAPI_ABILITY = "https://pokeapi.co/api/v2/ability/{}"
POKEAPI_ITEM = "https://pokeapi.co/api/v2/item/{}"
POKEAPI_POKEDEX = "https://pokeapi.co/api/v2/pokedex/{}"
# Item sprites per game: the PokeAPI sprites repo keeps each generation's art
# in sprites/items/genN. Every game lists the folders to try in order (its own
# generation first, then the nearest ones), and the default sprite comes last.
ITEM_SPRITE_BASE = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/"
)
# Games whose item list is much smaller than their generation's.
ITEM_PARTIAL_GAMES = {
    "lets-go-pikachu-lets-go-eevee", "brilliant-diamond-shining-pearl",
    "legends-arceus", "legends-za", "champions",
}
# For each game, the folders to try in order (own generation first), then the
# default sprite. Folders only hold the items that have art for that
# generation, so a missing file just moves on to the next candidate.
ITEM_SPRITE_FOLDERS = {
    "red-blue": ["gen1"],
    "yellow": ["gen1"],
    "gold-silver": ["gen2"],
    "crystal": ["gen2"],
    "ruby-sapphire": ["gen3"],
    "emerald": ["gen3"],
    "firered-leafgreen": ["gen3"],
    "diamond-pearl": ["gen4"],
    "platinum": ["gen4"],
    "heartgold-soulsilver": ["gen4"],
    "black-white": ["gen5"],
    "black-2-white-2": ["gen5"],
    "x-y": ["gen6"],
    "omega-ruby-alpha-sapphire": ["gen6"],
    "sun-moon": ["gen7"],
    "ultra-sun-ultra-moon": ["gen7"],
    "lets-go-pikachu-lets-go-eevee": ["gen7"],
    "sword-shield": ["gen8"],
    "brilliant-diamond-shining-pearl": ["gen8", "gen4"],
    "legends-arceus": ["gen8"],
    "scarlet-violet": ["gen9", "gen8"],
    "legends-za": ["gen9", "gen8"],
    "champions": ["gen9", "gen8"],
}
SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/{}.png"
)
HOME_SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/home/{}.png"
)
OFFICIAL_ART_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/official-artwork/{}.png"
)
# Forms Pokémon HOME has no 3D render of (the ORAS-only Cosplay Pikachu): the
# lists show their official artwork rather than the 2D sprite.
_NO_HOME_FORMS = {
    "pikachu-" + _c for _c in (
        "cosplay", "rock-star", "belle", "pop-star", "phd", "libre")
}


# The Let's Go partner Pokémon look exactly like the normal ones, so their
# icons and list sprites are the normal Pokémon's.
_LOOKS_LIKE = {"pikachu-starter": 25, "eevee-starter": 133}


def list_sprite(name, pokemon_id, two_d=""):
    """(sprite, fallbacks) a Pokémon has in the suggestion and table lists:
    the 3D HOME render, then the official artwork, and only then its 2D
    sprite. The fallbacks are space-separated; the page moves to the next
    one when an image does not load."""
    pokemon_id = _LOOKS_LIKE.get(name, pokemon_id)
    art = OFFICIAL_ART_URL.format(pokemon_id)
    if name in _NO_HOME_FORMS:   # HOME has no render of these at all
        return art, two_d or ""
    return (HOME_SPRITE_URL.format(pokemon_id),
            " ".join(u for u in (art, two_d) if u))


POKEAPI_TYPE = "https://pokeapi.co/api/v2/type/{}"
POKEAPI_SPECIES = "https://pokeapi.co/api/v2/pokemon-species/{}"
POKEAPI_ENCOUNTERS = "https://pokeapi.co/api/v2/pokemon/{}/encounters"
TCGDEX_SEARCH = "https://api.tcgdex.net/v2/en/cards"
TCGDEX_CARD = "https://api.tcgdex.net/v2/en/cards/{}"
TIMEOUT = 20

# Data scraped into JSON files by `manage.py load_extras` for the games
# PokeAPI has not filled in (Scarlet / Violet and Legends Z-A have many
# Pokemon with no Pokedex entry or no move list). Merged over PokeAPI's own
# data, never replacing it.
_DATA_DIR = Path(__file__).resolve().parent / "data"
_EXTRAS_CACHE = {}


def _load_extra(filename):
    """Parsed data/<filename>, {} when it is missing or unreadable.
    Re-read when the file changes, so a fresh scrape shows up at once."""
    path = _DATA_DIR / filename
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    hit = _EXTRAS_CACHE.get(filename)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _EXTRAS_CACHE[filename] = (stamp, data)
    return data

# Games shown as buttons on the Pokemon pages, newest first. Includes the
# spin-offs PokeAPI tracks (Colosseum, XD) and Pokemon Champions.
VERSION_GROUPS = [
    # Newest to oldest by original release date.
    ("champions", "Pokémon Champions"),
    ("legends-za", "Legends Z-A"),
    ("scarlet-violet", "Scarlet / Violet"),
    ("legends-arceus", "Legends Arceus"),
    ("brilliant-diamond-shining-pearl", "BDSP"),
    ("sword-shield", "Sword / Shield"),
    ("lets-go-pikachu-lets-go-eevee", "Let's Go"),
    ("ultra-sun-ultra-moon", "Ultra Sun / Ultra Moon"),
    ("sun-moon", "Sun / Moon"),
    ("omega-ruby-alpha-sapphire", "ORAS"),
    ("x-y", "X / Y"),
    ("black-2-white-2", "BW2"),
    ("black-white", "Black / White"),
    ("heartgold-soulsilver", "HGSS"),
    ("platinum", "Platinum"),
    ("diamond-pearl", "Diamond / Pearl"),
    ("xd", "XD: Gale of Darkness"),
    ("emerald", "Emerald"),
    ("firered-leafgreen", "FireRed / LeafGreen"),
    ("colosseum", "Colosseum"),
    ("ruby-sapphire", "Ruby / Sapphire"),
    ("crystal", "Crystal"),
    ("gold-silver", "Gold / Silver"),
    ("yellow", "Yellow"),
    ("red-blue", "Red / Blue"),
]

# PokeAPI spellings of the Champions entry under versions.generation-ix.
CHAMPIONS_SPRITE_KEYS = ("champions", "pokemon-champions")
ZA_SPRITE_KEY = "legends-za"
# PokeAPI has no sprites filed under these games, which reused the art of
# the game on the right (Black 2/White 2 use the Black/White sprites; Sun/
# Moon's renders are the ones PokeAPI files under Ultra Sun/Ultra Moon).
# Used only when the game has no sprite of its own.
SHARED_SPRITES = {
    "black-2-white-2": "black-white",
    "sun-moon": "ultra-sun-ultra-moon",
}
_SPRITE_REPO = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/versions/generation-ix/champions/"
)
CHAMPIONS_SPRITE_URL = _SPRITE_REPO + "{}.png"
CHAMPIONS_SHINY_SPRITE_URL = _SPRITE_REPO + "shiny/{}.png"

GEN_KEYS = [
    "generation-i", "generation-ii", "generation-iii",
    "generation-iv", "generation-v", "generation-vi",
    "generation-vii", "generation-viii", "generation-ix",
]

GEN_SET_MAP = {
    "generation-ix": ["scarlet-violet"],
    "generation-viii": [
        "sword-shield", "legends-arceus",
        "brilliant-diamond-shining-pearl",
    ],
    "generation-vii": [
        "ultra-sun-ultra-moon", "sun-moon",
        "lets-go-pikachu-lets-go-eevee",
    ],
    "generation-vi": ["omega-ruby-alpha-sapphire", "x-y"],
    "generation-v": ["black-2-white-2", "black-white"],
    "generation-iv": [
        "diamond-pearl", "platinum", "heartgold-soulsilver",
    ],
    "generation-iii": [
        "emerald", "firered-leafgreen", "ruby-sapphire",
    ],
    "generation-ii": ["gold", "silver", "crystal"],
    "generation-i": ["red-blue", "yellow"],
}

# Games a move can appear in, newest first (used for the Moves page buttons).
MOVE_GAMES = [
    ("champions", "Pokémon Champions"),
    ("legends-za", "Legends Z-A"),
    ("scarlet-violet", "Scarlet / Violet"),
    ("legends-arceus", "Legends Arceus"),
    ("brilliant-diamond-shining-pearl", "BDSP"),
    ("sword-shield", "Sword / Shield"),
    ("lets-go-pikachu-lets-go-eevee", "Let's Go"),
    ("ultra-sun-ultra-moon", "Ultra Sun / Ultra Moon"),
    ("sun-moon", "Sun / Moon"),
    ("omega-ruby-alpha-sapphire", "ORAS"),
    ("x-y", "X / Y"),
    ("black-2-white-2", "BW2"),
    ("black-white", "Black / White"),
    ("heartgold-soulsilver", "HGSS"),
    ("platinum", "Platinum"),
    ("diamond-pearl", "Diamond / Pearl"),
    ("emerald", "Emerald"),
    ("firered-leafgreen", "FireRed / LeafGreen"),
    ("ruby-sapphire", "Ruby / Sapphire"),
    ("crystal", "Crystal"),
    ("gold-silver", "Gold / Silver"),
    ("yellow", "Yellow"),
    ("red-blue", "Red / Blue"),
]
_GAME_LABELS = dict(MOVE_GAMES)
# oldest -> newest, so past_values can be applied in release order
_GAME_ORDER = [k for k, _ in reversed(MOVE_GAMES)]

# PokeAPI spellings that differ from the keys used in this project.
_VG_ALIASES = {
    "brilliant-diamond-and-shining-pearl":
        "brilliant-diamond-shining-pearl",
    "the-isle-of-armor": "sword-shield",
    "the-crown-tundra": "sword-shield",
    "gold": "gold-silver",
    "silver": "gold-silver",
    # DLC / alternate PokeAPI names folded into the game they belong to
    "the-teal-mask": "scarlet-violet",
    "the-indigo-disk": "scarlet-violet",
    "mega-dimension": "legends-za",
    "pokemon-champions": "champions",
}


def _norm_vg(name):
    return _VG_ALIASES.get(name, name)


def _en_text(entries, field):
    for entry in entries or []:
        lang = (entry.get("language") or {}).get("name")
        if lang == "en" and entry.get(field):
            return entry[field]
    return ""


def _latest_sprite(sprites, field="front_default"):
    versions = sprites.get("versions", {})
    for gen_key in reversed(GEN_KEYS):
        gen = versions.get(gen_key, {})
        for set_key in GEN_SET_MAP.get(gen_key, []):
            entry = gen.get(set_key, {})
            if isinstance(entry, dict) and entry.get(field):
                return entry[field]
    return sprites.get(field)


def _sprites_by_gen(sprites, field="front_default", merge_aliases=False):
    """Sprite URL per game. field="front_shiny" gives the shiny sprites.

    merge_aliases folds Gold and Silver into the single "gold-silver" key
    that the game buttons use (the Gold sprite wins).
    """
    versions = sprites.get("versions", {})
    result = {}
    for gen_key in GEN_KEYS:
        gen = versions.get(gen_key, {})
        for set_key in GEN_SET_MAP.get(gen_key, []):
            entry = gen.get(set_key, {})
            if isinstance(entry, dict) and entry.get(field):
                key = _norm_vg(set_key) if merge_aliases else set_key
                result.setdefault(key, entry[field])
    # Pokemon Champions sits in generation-ix but isn't in GEN_SET_MAP
    # (that map also drives the move pages, which don't list Champions).
    gen9 = versions.get("generation-ix", {})
    for champ_key in CHAMPIONS_SPRITE_KEYS:
        entry = gen9.get(champ_key, {})
        if isinstance(entry, dict) and entry.get(field):
            result.setdefault("champions", entry[field])
    # Legends Z-A is in generation-ix too (PokeAPI serves .webp renders).
    # Only a Pokemon that really has a Z-A sprite gets the entry.
    za = gen9.get(ZA_SPRITE_KEY, {})
    if isinstance(za, dict) and za.get(field):
        result.setdefault("legends-za", za[field])
    if merge_aliases:
        for game, source in SHARED_SPRITES.items():
            if game not in result and source in result:
                result[game] = result[source]
    return result


def _url_exists(url):
    """True if the URL answers 200 (used to probe raw sprite files)."""
    try:
        r = requests.get(url, stream=True, timeout=5)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False


def _add_champions_fallback(pokemon_id, normal, shiny):
    """Fill in the Champions sprites when PokeAPI's JSON omits them.

    The files exist in the PokeAPI/sprites repo
    (versions/generation-ix/champions/<id>.png) even when the
    /pokemon/ JSON doesn't link them, so look for them directly.
    Only a file that really exists is added, so a Pokemon with no
    Champions shiny simply has no entry in `shiny`.
    """
    if not pokemon_id:
        return
    if "champions" not in normal:
        url = CHAMPIONS_SPRITE_URL.format(pokemon_id)
        if _url_exists(url):
            normal["champions"] = url
    if "champions" in normal and "champions" not in shiny:
        url = CHAMPIONS_SHINY_SPRITE_URL.format(pokemon_id)
        if _url_exists(url):
            shiny["champions"] = url


_ZA_SPRITE_REPO = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/versions/generation-ix/legends-za/"
)


def _add_za_fallback(pokemon_id, normal, shiny):
    """Fill in the Legends Z-A sprites when PokeAPI's JSON omits them.

    Newer forms (Mega Greninja...) have their Z-A render in the
    PokeAPI/sprites repo before the /pokemon/ JSON links it, so look for
    the file directly (.png or .webp). Only files that exist are added.
    """
    if not pokemon_id:
        return
    for table, folder in ((normal, ""), (shiny, "shiny/")):
        if "legends-za" in table:
            continue
        if folder and "legends-za" not in normal:
            continue
        for ext in ("png", "webp"):
            url = "{}{}{}.{}".format(_ZA_SPRITE_REPO, folder, pokemon_id, ext)
            if _url_exists(url):
                table["legends-za"] = url
                break


def _add_home_fallback(pokemon_id, normal, shiny, game="scarlet-violet"):
    """Give a game the Pokémon HOME render when PokeAPI has no sprite of the
    form for it (the Pikachu cap forms have none for Scarlet / Violet, though
    they are in it). Only a file that really exists is added."""
    if not pokemon_id or game in normal:
        return
    url = HOME_SPRITE_URL.format(pokemon_id)
    if _url_exists(url):
        normal[game] = url
        if game not in shiny:
            shiny_url = _OTHER_SPRITES.format("home", "shiny/", pokemon_id)
            if _url_exists(shiny_url):
                shiny[game] = shiny_url


def _ability_slug(name):
    """"Power Construct" / "power_construct" / "Poké Ball" ->
    "power-construct" / "poke-ball" (PokeAPI's name format)."""
    slug = unicodedata.normalize("NFKD", name or "")
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = slug.strip().lower().replace("\u2019", "").replace("'", "")
    return "-".join(slug.replace("_", " ").split())


_MON_TYPE_CACHE = {}   # Pokemon id -> [type 1, type 2?], filled once from /type/


def _mon_type_map():
    """{Pokemon id: [type 1, type 2]} for every Pokemon and form, built from
    the 18 /type/ records (each lists its Pokemon with a slot of 1 or 2)."""
    if _MON_TYPE_CACHE:
        return _MON_TYPE_CACHE
    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = list(pool.map(
            lambda t: (t, _get_json(POKEAPI_TYPE.format(t))),
            MAX_MOVE_BY_TYPE,
        ))
    if not all(isinstance(d, dict) for _, d in loaded):
        return {}   # try again next time rather than cache a partial map
    slots = {}
    for type_name, d in loaded:
        for entry in d.get("pokemon") or []:
            pid = _mon_id((entry.get("pokemon") or {}).get("url"))
            if pid:
                slots.setdefault(pid, []).append(
                    (entry.get("slot") or 1, type_name)
                )
    for pid, found in slots.items():
        _MON_TYPE_CACHE[pid] = [t for _, t in sorted(found)]
    return _MON_TYPE_CACHE


_SPECIES_DEX_CACHE = {}   # species name -> National Dex number


def _species_dex_map():
    """{species name: National Dex number}, e.g. {"zygarde": 718}.

    Read once from PokeAPI's species list (a species id is its Dex number).
    If PokeAPI cannot be reached, it is guessed from data/pokemon_list.json
    ("deoxys-normal" -> "deoxys") and not cached, so a later call retries.
    """
    if _SPECIES_DEX_CACHE:
        return _SPECIES_DEX_CACHE
    found = {}
    try:
        r = requests.get(
            POKEAPI_SPECIES.format(""),
            params={"limit": 3000}, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for sp in r.json().get("results") or []:
            sid = _mon_id(sp.get("url"))
            if sid and sp.get("name"):
                found[sp["name"]] = sid
    except Exception:
        found = {}
    if found:
        _SPECIES_DEX_CACHE.update(found)
        return _SPECIES_DEX_CACHE
    guess = {}
    try:
        with open(_DATA_DIR / "pokemon_list.json", encoding="utf-8") as f:
            listed = json.load(f)
    except (OSError, ValueError):
        listed = []
    for entry in listed if isinstance(listed, list) else []:
        try:
            pid = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid >= 10000:
            continue
        name = entry.get("name") or ""
        guess.setdefault(name, pid)
        if "-" in name:   # a default form: "deoxys-normal", "zygarde-50"
            guess.setdefault(name.rsplit("-", 1)[0], pid)
    return guess


def _dex_number(name, pid, species_dex):
    """National Dex number of a Pokemon / form: ids below 10000 already are
    the Dex number; a form (Mega, regional...) takes its species' number,
    found by dropping name parts until a species name matches."""
    if pid and pid < 10000:
        return pid
    parts = (name or "").split("-")
    while parts:
        hit = species_dex.get("-".join(parts))
        if hit:
            return hit
        parts.pop()
    return pid


def _add_types(mons):
    """Give each {id, ...} Pokemon dict a type1 and type2 ("" when none),
    plus its National Dex number as "dex" (a Mega has its own id but shares
    its base Pokemon's Dex number)."""
    types = _mon_type_map()
    species_dex = _species_dex_map()
    for m in mons:
        # Every Pokémon shows its 3D render in the lists (its 2D sprite is
        # the fallback when HOME has no render of that form).
        if m.get("id"):
            m["sprite"], m["sprite_alt"] = list_sprite(
                m.get("name"), m["id"], m.get("sprite") or "")
            m["sprite_3d"] = True
        t = types.get(m["id"], [])
        m["type1"] = t[0] if len(t) > 0 else ""
        m["type2"] = t[1] if len(t) > 1 else ""
        m["dex"] = _dex_number(m.get("name"), m["id"], species_dex)
    return mons


def _fetch_ability(name):
    try:
        r = requests.get(
            POKEAPI_ABILITY.format(quote(_ability_slug(name))),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    effect = ""
    for entry in data.get("effect_entries", []):
        if entry.get("language", {}).get("name") == "en":
            effect = entry.get("effect", "")
            break
    if not effect:
        # Newer abilities have no long effect text; use the in-game text.
        for entry in reversed(data.get("flavor_text_entries", [])):
            if entry.get("language", {}).get("name") == "en":
                effect = " ".join(
                    (entry.get("flavor_text") or "").split()
                )
                break
    generation = (data.get("generation") or {}).get("name") or ""
    mons = []
    for entry in data.get("pokemon") or []:
        mon = entry.get("pokemon") or {}
        url = (mon.get("url") or "").rstrip("/")
        mon_id = url.rsplit("/", 1)[-1]
        if not mon.get("name") or not mon_id.isdigit():
            continue
        mons.append({
            "id": int(mon_id),
            "name": mon["name"],
            "hidden": bool(entry.get("is_hidden")),
            "sprite": SPRITE_URL.format(mon_id),
        })
    _add_types(mons)
    mons.sort(key=lambda m: (m["dex"], m["id"], m["hidden"]))
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "effect": effect,
        "pokemon": mons,
        "generation": generation.replace("generation-", "").upper(),
        "pokemon_count": len(data.get("pokemon") or []) or None,
    }


# An item counts as a battle item when PokeAPI says it can be used in battle
# or does something while held. Tweak this tuple to widen or narrow the tab.
BATTLE_ITEM_ATTRIBUTES = (
    "usable-in-battle", "holdable-passive", "holdable-active",
)

# Mega Stones. PokeAPI only tags them "holdable" (not passive/active), so the
# attribute lookup misses every one of them. They are listed by name here and
# fetch_item() also recognises the "mega-stones" category.
MEGA_STONES = (
    "absolite", "absolite-z", "abomasite", "aerodactylite", "aggronite",
    "alakazite", "altarianite", "ampharosite", "audinite", "banettite",
    "barbaracite", "baxcalibrite", "beedrillite", "blastoisinite",
    "blazikenite", "cameruptite", "chandelurite", "charizardite-x",
    "charizardite-y", "chesnaughtite", "chimechite", "clefablite",
    "crabominite", "darkranite", "delphoxite", "diancite", "dragalgite",
    "dragoninite", "drampanite", "eelektrossite", "emboarite", "excadrite",
    "falinksite", "feraligite", "floettite", "froslassite", "galladite",
    "garchompite", "garchompite-z", "gardevoirite", "gengarite",
    "glalitite", "glimmoranite", "golisopite", "golurkite", "greninjite",
    "gyaradosite", "hawluchanite", "heatranite", "heracronite",
    "houndoominite", "kangaskhanite", "latiasite", "latiosite", "lopunnite",
    "lucarionite", "lucarionite-z", "magearnite", "malamarite", "manectite",
    "mawilite", "medichamite", "meganiumite", "meowsticite", "metagrossite",
    "mewtwonite-x", "mewtwonite-y", "pidgeotite", "pinsirite", "pyroarite",
    "raichunite-x", "raichunite-y", "sablenite", "salamencite",
    "sceptilite", "scizorite", "scolipite", "scovillainite", "scraftinite",
    "sharpedonite", "skarmorite", "slowbronite", "staraptite", "starminite",
    "steelixite", "swampertite", "tatsugirinite", "tyranitarite",
    "venusaurite", "victreebelite", "zeraorite", "zygardite",
)

# Newer battle items that PokeAPI has but has not tagged with any of the
# attributes above, so the attribute lookup misses them (Covert Cloak, Ability
# Shield...). Add a name here to make an item searchable in the Items tab.
BATTLE_ITEM_EXTRAS = (
    "ability-shield", "covert-cloak", "clear-amulet", "punching-glove",
    "loaded-dice", "booster-energy", "mirror-herb", "adrenaline-orb",
    "blunder-policy", "room-service", "throat-spray", "eject-pack",
    "utility-umbrella", "heavy-duty-boots", "protective-pads",
    "terrain-extender", "cornerstone-mask", "wellspring-mask",
    "hearthflame-mask", "fairy-feather",
    # Legends: Arceus items that transform a Pokémon (not true held items)
    "legend-plate", "griseous-core", "adamant-crystal", "lustrous-globe",
    # plain held items that boost Dialga's / Palkia's moves
    "adamant-orb", "lustrous-orb",
) + MEGA_STONES

# Z-Crystals (Sun / Moon, Ultra Sun / Ultra Moon). PokeAPI files each one as
# "<name>--held" (the in-battle item) and "<name>--bag" (the key-item copy),
# and only tags them "holdable", so they are listed here by their plain name.
# Ilima's Normalium Z has just the one entry.
Z_CRYSTALS = (
    "aloraichium-z", "buginium-z", "darkinium-z", "decidium-z",
    "dragonium-z", "eevium-z", "electrium-z", "fairium-z", "fightinium-z",
    "firium-z", "flyinium-z", "ghostium-z", "grassium-z", "groundium-z",
    "icium-z", "ilimas-normalium-z", "incinium-z", "kommonium-z",
    "lunalium-z", "lycanium-z", "marshadium-z", "mewnium-z", "mimikium-z",
    "normalium-z", "pikanium-z", "pikashunium-z", "poisonium-z",
    "primarium-z", "psychium-z", "rockium-z", "snorlium-z",
    "solganium-z", "steelium-z", "tapunium-z", "ultranecrozium-z",
    "waterium-z",
)
BATTLE_ITEM_EXTRAS = BATTLE_ITEM_EXTRAS + Z_CRYSTALS

# Items that make a Pokémon evolve (tagged "Evolution" in the Items tab). Every
# item an evolution line points at has to be in here, so the item above the
# arrow on the Details tab opens a real entry. PokeAPI's names, not the
# in-game ones ("up-grade" is the Upgrade).
EVOLUTION_ITEMS = (
    # evolution stones
    "fire-stone", "water-stone", "thunder-stone", "leaf-stone", "moon-stone",
    "sun-stone", "shiny-stone", "dusk-stone", "dawn-stone", "ice-stone",
    "oval-stone",
    # held while trading (or levelling up), or used on the Pokémon
    "kings-rock", "metal-coat", "dragon-scale", "up-grade", "protector",
    "electirizer", "magmarizer", "razor-claw", "razor-fang", "reaper-cloth",
    "deep-sea-tooth", "deep-sea-scale", "prism-scale", "dubious-disc",
    "sachet", "whipped-dream", "linking-cord",
    # newer games (Sword / Shield, Legends: Arceus, Scarlet / Violet)
    "sweet-apple", "tart-apple", "syrupy-apple", "cracked-pot",
    "chipped-pot", "galarica-cuff", "galarica-wreath", "black-augurite",
    "peat-block", "auspicious-armor", "malicious-armor",
    "scroll-of-darkness", "scroll-of-waters", "leaders-crest",
    "unremarkable-teacup", "masterpiece-teacup", "metal-alloy",
    # Milcery spins into Alcremie while holding one of these
    "strawberry-sweet", "love-sweet", "berry-sweet", "clover-sweet",
    "flower-sweet", "star-sweet", "ribbon-sweet",
)

# Items that change a Pokémon's form (tagged "Form changing"): Mega Stones,
# the Primal Reversion orbs, the Arceus plates, Silvally memories, Genesect
# drives, and the other items that swap a Pokémon's form or forme.
FORM_CHANGE_ITEMS = MEGA_STONES + (
    "red-orb", "blue-orb",
    # The Adamant / Lustrous Orbs only boost Dialga's / Palkia's moves; it is
    # the Adamant Crystal / Lustrous Globe that change their form.
    "griseous-orb",
    "griseous-core", "adamant-crystal", "lustrous-globe",
    "flame-plate", "splash-plate", "zap-plate", "meadow-plate",
    "icicle-plate", "fist-plate", "toxic-plate", "earth-plate", "sky-plate",
    "mind-plate", "insect-plate", "stone-plate", "spooky-plate",
    "draco-plate", "dread-plate", "iron-plate", "pixie-plate", "blank-plate",
    "legend-plate",
    "fighting-memory", "flying-memory", "poison-memory", "ground-memory",
    "rock-memory", "bug-memory", "ghost-memory", "steel-memory",
    "fire-memory", "water-memory", "grass-memory", "electric-memory",
    "psychic-memory", "ice-memory", "dragon-memory", "dark-memory",
    "fairy-memory",
    "burn-drive", "chill-drive", "douse-drive", "shock-drive",
    "rusted-sword", "rusted-shield",
    "wellspring-mask", "hearthflame-mask", "cornerstone-mask",
    "reveal-glass", "gracidea", "dna-splicers", "prison-bottle", "meteorite",
    "red-nectar", "yellow-nectar", "pink-nectar", "purple-nectar",
    "zygarde-cube", "rotom-catalog", "ultranecrozium-z",
    "n-solarizer", "n-lunarizer", "reins-of-unity",
)
_EVOLUTION_ITEM_SET = frozenset(EVOLUTION_ITEMS)
# Mega Stones only carry the Evolution tag (Mega Evolution), not this one
_FORM_CHANGE_ITEM_SET = frozenset(FORM_CHANGE_ITEMS) - frozenset(MEGA_STONES)
ITEM_TAG_LABELS = {"evolution": "Evolution", "form": "Form changing"}

# Both kinds are searchable in the Items tab, whatever PokeAPI tags them.
BATTLE_ITEM_EXTRAS = BATTLE_ITEM_EXTRAS + EVOLUTION_ITEMS + FORM_CHANGE_ITEMS


def item_tag_keys(name):
    """["evolution"], ["form"] or [] for an item's PokeAPI name."""
    plain = _plain_item_name(name)
    keys = []
    # Mega Stones count as evolution stones (Mega Evolution)
    if plain in _EVOLUTION_ITEM_SET or plain in MEGA_STONES:
        keys.append("evolution")
    if plain in _FORM_CHANGE_ITEM_SET and plain not in MEGA_STONES:
        keys.append("form")
    return keys


def _plain_item_name(name):
    """"firium-z--held" -> "firium-z" (the --bag copies are not shown)."""
    return (name or "").replace("--held", "")


# ---- which Pokémon an item acts on, game by game ----------------------------
# Form-changing and evolution items list the Pokémon they work on. Each
# Pokémon carries the games the item does its job for it in, so the item page
# can show "In <game>, this item changes the form of ..." for the game picked.
#
# A target is a PokeAPI Pokémon name, or (name, games) when the item only works
# on that Pokémon in some games. "games" of the spec is the default for every
# target; None means every game the item itself is in. Whatever the list says,
# a Pokémon is only listed for games it is actually in.
_G_ORB_DP = (
    "diamond-pearl", "platinum", "heartgold-soulsilver",
    "brilliant-diamond-shining-pearl",
)
_G_PLATES = (
    "diamond-pearl", "platinum", "heartgold-soulsilver", "black-white",
    "black-2-white-2", "x-y", "omega-ruby-alpha-sapphire", "sun-moon",
    "ultra-sun-ultra-moon", "champions",
)
_G_MEGA = (
    "x-y", "omega-ruby-alpha-sapphire", "sun-moon", "ultra-sun-ultra-moon",
    "lets-go-pikachu-lets-go-eevee", "legends-za", "champions",
)
_G_NEW_MEGA = ("legends-za", "champions")
_G_GEN8_PLUS = ("sword-shield", "legends-arceus", "scarlet-violet",
                "legends-za")


def _target_spec(kind, pokemon, games=None):
    return {"kind": kind, "pokemon": list(pokemon),
            "games": tuple(games) if games else None}


_ITEM_TARGETS = {
    # -- items that change a Pokémon's form --
    "red-orb": _target_spec("form", ["groudon"], (
        "omega-ruby-alpha-sapphire", "ultra-sun-ultra-moon", "champions")),
    "blue-orb": _target_spec("form", ["kyogre"], (
        "omega-ruby-alpha-sapphire", "ultra-sun-ultra-moon", "champions")),
    "griseous-orb": _target_spec("form", ["giratina"], (
        "platinum", "heartgold-soulsilver", "black-white", "black-2-white-2",
        "brilliant-diamond-shining-pearl", "champions")),
    "griseous-core": _target_spec("form", ["giratina"], (
        "legends-arceus", "scarlet-violet")),
    "adamant-crystal": _target_spec("form", ["dialga"], (
        "legends-arceus", "scarlet-violet")),
    "lustrous-globe": _target_spec("form", ["palkia"], (
        "legends-arceus", "scarlet-violet")),
    "legend-plate": _target_spec("form", ["arceus"], ("legends-arceus",)),
    "rusted-sword": _target_spec("form", ["zacian"], ("sword-shield", "champions")),
    "rusted-shield": _target_spec("form", ["zamazenta"], (
        "sword-shield", "champions")),
    "prison-bottle": _target_spec("form", ["hoopa"], (
        "omega-ruby-alpha-sapphire", "sun-moon", "ultra-sun-ultra-moon",
        "champions")),
    "gracidea": _target_spec("form", ["shaymin"], (
        "platinum", "heartgold-soulsilver", "black-white", "black-2-white-2",
        "x-y", "omega-ruby-alpha-sapphire", "sun-moon", "sword-shield",
        "legends-arceus", "champions")),
    "reveal-glass": _target_spec(
        "form", ["tornadus", "thundurus", "landorus", "enamorus"], (
            "black-white", "black-2-white-2", "omega-ruby-alpha-sapphire",
            "sun-moon", "ultra-sun-ultra-moon", "sword-shield",
            "legends-arceus", "scarlet-violet", "champions")),
    "dna-splicers": _target_spec("form", ["kyurem"], (
        "black-2-white-2", "ultra-sun-ultra-moon", "sword-shield",
        "champions")),
    "meteorite": _target_spec("form", ["deoxys"], (
        "firered-leafgreen", "omega-ruby-alpha-sapphire",
        "ultra-sun-ultra-moon", "champions")),
    "zygarde-cube": _target_spec("form", ["zygarde"], (
        "sun-moon", "ultra-sun-ultra-moon", "champions")),
    "rotom-catalog": _target_spec("form", ["rotom"], (
        "sun-moon", "ultra-sun-ultra-moon", "sword-shield",
        "scarlet-violet", "champions")),
    "ultranecrozium-z": _target_spec(
        "form", ["necrozma-dusk", "necrozma-dawn"], ("ultra-sun-ultra-moon",)),
    "wellspring-mask": _target_spec("form", ["ogerpon"], ("scarlet-violet",)),
    "hearthflame-mask": _target_spec("form", ["ogerpon"], ("scarlet-violet",)),
    "cornerstone-mask": _target_spec("form", ["ogerpon"], ("scarlet-violet",)),
    "red-nectar": _target_spec("form", ["oricorio"], (
        "sun-moon", "ultra-sun-ultra-moon")),
    "yellow-nectar": _target_spec("form", ["oricorio"], (
        "sun-moon", "ultra-sun-ultra-moon")),
    "pink-nectar": _target_spec("form", ["oricorio"], (
        "sun-moon", "ultra-sun-ultra-moon")),
    "purple-nectar": _target_spec("form", ["oricorio"], (
        "sun-moon", "ultra-sun-ultra-moon")),
    # -- items that make a Pokémon evolve (games = where the Pokémon is) --
    "fire-stone": _target_spec("evolution", [
        "vulpix", "growlithe", "eevee", "pansear", "capsakid"]),
    "water-stone": _target_spec("evolution", [
        "poliwhirl", "shellder", "staryu", "eevee", "lombre", "panpour"]),
    "thunder-stone": _target_spec("evolution", ["pikachu", "eevee", "eelektrik"]),
    "leaf-stone": _target_spec("evolution", [
        "gloom", "weepinbell", "exeggcute", "nuzleaf", "pansage",
        ("eevee", _G_GEN8_PLUS), "voltorb"]),
    "moon-stone": _target_spec("evolution", [
        "nidorina", "nidorino", "clefairy", "jigglypuff", "skitty", "munna"]),
    "sun-stone": _target_spec("evolution", [
        "gloom", "sunkern", "cottonee", "petilil", "helioptile"]),
    "shiny-stone": _target_spec("evolution", [
        "togetic", "roselia", "minccino", "floette"]),
    "dusk-stone": _target_spec("evolution", [
        "murkrow", "misdreavus", "lampent", "doublade"]),
    "dawn-stone": _target_spec("evolution", ["kirlia", "snorunt"]),
    "ice-stone": _target_spec("evolution", [
        "vulpix-alola", "sandshrew-alola", "darumaka-galar",
        ("eevee", _G_GEN8_PLUS)]),
    "oval-stone": _target_spec("evolution", ["happiny"]),
    "kings-rock": _target_spec("evolution", ["poliwhirl", "slowpoke"]),
    "metal-coat": _target_spec("evolution", ["onix", "scyther"]),
    "dragon-scale": _target_spec("evolution", ["seadra"]),
    "up-grade": _target_spec("evolution", ["porygon"]),
    "dubious-disc": _target_spec("evolution", ["porygon2"]),
    "protector": _target_spec("evolution", ["rhydon"]),
    "electirizer": _target_spec("evolution", ["electabuzz"]),
    "magmarizer": _target_spec("evolution", ["magmar"]),
    "razor-claw": _target_spec("evolution", ["sneasel", "sneasel-hisui"]),
    "razor-fang": _target_spec("evolution", ["gligar"]),
    "reaper-cloth": _target_spec("evolution", ["dusclops"]),
    "deep-sea-tooth": _target_spec("evolution", ["clamperl"]),
    "deep-sea-scale": _target_spec("evolution", ["clamperl"]),
    "prism-scale": _target_spec("evolution", ["feebas"]),
    "sachet": _target_spec("evolution", ["spritzee"]),
    "whipped-dream": _target_spec("evolution", ["swirlix"]),
    "linking-cord": _target_spec("evolution", [
        "kadabra", "machoke", "graveler", "haunter", "boldore", "gurdurr",
        "phantump", "pumpkaboo"], ("legends-arceus", "scarlet-violet")),
    "sweet-apple": _target_spec("evolution", ["applin"], (
        "sword-shield", "scarlet-violet")),
    "tart-apple": _target_spec("evolution", ["applin"], (
        "sword-shield", "scarlet-violet")),
    "syrupy-apple": _target_spec("evolution", ["applin"], ("scarlet-violet",)),
    "cracked-pot": _target_spec("evolution", ["sinistea"], (
        "sword-shield", "scarlet-violet")),
    "chipped-pot": _target_spec("evolution", ["sinistea"], (
        "sword-shield", "scarlet-violet")),
    "galarica-cuff": _target_spec("evolution", ["slowpoke-galar"], (
        "sword-shield",)),
    "galarica-wreath": _target_spec("evolution", ["slowpoke-galar"], (
        "sword-shield",)),
    "black-augurite": _target_spec("evolution", ["scyther"], (
        "legends-arceus", "scarlet-violet")),
    "peat-block": _target_spec("evolution", ["ursaring"], (
        "legends-arceus", "scarlet-violet")),
    "auspicious-armor": _target_spec("evolution", ["charcadet"], (
        "scarlet-violet",)),
    "malicious-armor": _target_spec("evolution", ["charcadet"], (
        "scarlet-violet",)),
    "scroll-of-darkness": _target_spec("evolution", ["kubfu"], ("sword-shield",)),
    "scroll-of-waters": _target_spec("evolution", ["kubfu"], ("sword-shield",)),
    "leaders-crest": _target_spec("evolution", ["bisharp"], ("scarlet-violet",)),
    "unremarkable-teacup": _target_spec("evolution", ["poltchageist"], (
        "scarlet-violet",)),
    "masterpiece-teacup": _target_spec("evolution", ["poltchageist"], (
        "scarlet-violet",)),
    "metal-alloy": _target_spec("evolution", ["duraludon"], ("scarlet-violet",)),
}
for _sweet in ("strawberry-sweet", "love-sweet", "berry-sweet",
               "clover-sweet", "flower-sweet", "star-sweet", "ribbon-sweet"):
    _ITEM_TARGETS[_sweet] = _target_spec("evolution", ["milcery"], (
        "sword-shield", "scarlet-violet"))

# Mega Stone -> the Pokémon that holds it, where the stone's name does not
# simply end in "ite" ("charizardite-x" and the like are handled below).
_MEGA_STONE_HOLDER = {
    "absolite": "absol", "abomasite": "abomasnow", "aerodactylite":
    "aerodactyl", "aggronite": "aggron", "alakazite": "alakazam",
    "altarianite": "altaria", "ampharosite": "ampharos",
    "audinite": "audino", "banettite": "banette",
    "barbaracite": "barbaracle", "beedrillite": "beedrill",
    "blastoisinite": "blastoise", "blazikenite": "blaziken",
    "cameruptite": "camerupt", "chandelurite": "chandelure",
    "chesnaughtite": "chesnaught", "chimechite": "chimecho",
    "clefablite": "clefable", "crabominite": "crabominable",
    "darkranite": "darkrai", "delphoxite": "delphox", "diancite": "diancie",
    "dragalgite": "dragalge", "dragoninite": "dragonite",
    "drampanite": "drampa", "eelektrossite": "eelektross",
    "emboarite": "emboar", "excadrite": "excadrill", "falinksite": "falinks",
    "feraligite": "feraligatr", "floettite": "floette",
    "froslassite": "froslass", "galladite": "gallade",
    "garchompite": "garchomp", "glalitite": "glalie",
    "glimmoranite": "glimmora", "golisopite": "golisopod",
    "golurkite": "golurk", "greninjite": "greninja",
    "gyaradosite": "gyarados", "hawluchanite": "hawlucha",
    "heatranite": "heatran", "heracronite": "heracross",
    "houndoominite": "houndoom", "kangaskhanite": "kangaskhan",
    "latiasite": "latias", "latiosite": "latios", "lopunnite": "lopunny",
    "lucarionite": "lucario", "magearnite": "magearna",
    "malamarite": "malamar", "manectite": "manectric", "mawilite": "mawile",
    "medichamite": "medicham", "meganiumite": "meganium",
    "meowsticite": "meowstic", "metagrossite": "metagross",
    "pidgeotite": "pidgeot", "pinsirite": "pinsir", "pyroarite": "pyroar",
    "sablenite": "sableye", "salamencite": "salamence",
    "sceptilite": "sceptile", "scizorite": "scizor",
    "scolipite": "scolipede", "scovillainite": "scovillain",
    "scraftinite": "scrafty", "sharpedonite": "sharpedo",
    "skarmorite": "skarmory", "slowbronite": "slowbro",
    "staraptite": "staraptor", "starminite": "starmie",
    "steelixite": "steelix", "swampertite": "swampert",
    "tatsugirinite": "tatsugiri", "tyranitarite": "tyranitar",
    "venusaurite": "venusaur", "victreebelite": "victreebel",
    "zeraorite": "zeraora", "zygardite": "zygarde-complete",
    "charizardite-x": "charizard", "charizardite-y": "charizard",
    "mewtwonite-x": "mewtwo", "mewtwonite-y": "mewtwo",
    "raichunite-x": "raichu", "raichunite-y": "raichu",
    "absolite-z": "absol", "garchompite-z": "garchomp",
    "lucarionite-z": "lucario", "baxcalibrite": "baxcalibur",
    "gardevoirite": "gardevoir", "gengarite": "gengar",
}
# The Mega Evolutions that only exist in Legends Z-A (and Champions)
_NEW_MEGA_STONES = {
    "clefablite", "victreebelite", "starminite", "dragoninite",
    "meganiumite", "feraligite", "skarmorite", "froslassite", "emboarite",
    "excadrite", "scolipite", "scraftinite", "eelektrossite", "chandelurite",
    "chesnaughtite", "delphoxite", "greninjite", "pyroarite", "floettite",
    "malamarite", "barbaracite", "dragalgite", "hawluchanite", "zygardite",
    "drampanite", "falinksite", "raichunite-x", "raichunite-y",
    "chimechite", "absolite-z", "staraptite", "garchompite-z",
    "lucarionite-z", "heatranite", "darkranite", "golurkite", "meowsticite",
    "crabominite", "golisopite", "magearnite", "scovillainite",
    "glimmoranite", "tatsugirinite", "baxcalibrite",
}
# Arceus' Plates (Judgment / Multitype): same Pokémon, one type per plate
for _plate in (
        "flame-plate", "splash-plate", "zap-plate", "meadow-plate",
        "icicle-plate", "fist-plate", "toxic-plate", "earth-plate",
        "sky-plate", "mind-plate", "insect-plate", "stone-plate",
        "spooky-plate", "draco-plate", "dread-plate", "iron-plate",
        "blank-plate"):
    _ITEM_TARGETS[_plate] = _target_spec("form", ["arceus"], _G_PLATES)
_ITEM_TARGETS["pixie-plate"] = _target_spec("form", ["arceus"], _G_PLATES[5:])
for _mem in ("fighting", "flying", "poison", "ground", "rock", "bug", "ghost",
             "steel", "fire", "water", "grass", "electric", "psychic", "ice",
             "dragon", "dark", "fairy"):
    _ITEM_TARGETS[_mem + "-memory"] = _target_spec("form", ["silvally"], (
        "sun-moon", "ultra-sun-ultra-moon"))
for _drive in ("burn-drive", "chill-drive", "douse-drive", "shock-drive"):
    _ITEM_TARGETS[_drive] = _target_spec("form", ["genesect"], (
        "black-2-white-2", "x-y", "omega-ruby-alpha-sapphire", "sun-moon",
        "ultra-sun-ultra-moon"))


# ---- Porto Marinada auction (Scarlet / Violet, The Indigo Disk) --------------
# The fourth auction venue only sells items made for one Pokémon, and only
# once that Pokémon is in the player's party or Boxes. Every one of them starts
# at a bid of 500, with a base price of 4,000 that sets the rival bidders'
# budgets (4,000 x each bidder's rate of 1 to 1.8). Source: Bulbapedia,
# "Porto Marinada Market".
AUCTION_START_BID = 500
AUCTION_NPC_BUDGETS = (4000, 7200)
AUCTION_ITEMS = {
    "soul-dew": "Latios or Latias", "meteorite": "Deoxys",
    "rotom-catalog": "Rotom",
    "adamant-orb": "Dialga", "adamant-crystal": "Dialga",
    "lustrous-orb": "Palkia", "lustrous-globe": "Palkia",
    "griseous-orb": "Giratina", "griseous-core": "Giratina",
    "gracidea": "Shaymin",
    "reveal-glass": "Tornadus, Thundurus, Landorus or Enamorus",
    "dna-splicers": "Kyurem", "prison-bottle": "Hoopa",
    "n-solarizer": "Necrozma", "n-lunarizer": "Necrozma",
    "rusted-sword": "Zacian", "rusted-shield": "Zamazenta",
    "scroll-of-darkness": "Kubfu", "scroll-of-waters": "Kubfu",
    "reins-of-unity": "Calyrex",
}
for _plate in (
        "flame-plate", "splash-plate", "zap-plate", "meadow-plate",
        "icicle-plate", "fist-plate", "toxic-plate", "earth-plate",
        "sky-plate", "mind-plate", "insect-plate", "stone-plate",
        "spooky-plate", "draco-plate", "dread-plate", "iron-plate",
        "pixie-plate"):
    AUCTION_ITEMS[_plate] = "Arceus"
_ITEM_TARGETS.setdefault("n-solarizer", _target_spec(
    "form", ["necrozma"], ("ultra-sun-ultra-moon",)))
_ITEM_TARGETS.setdefault("n-lunarizer", _target_spec(
    "form", ["necrozma"], ("ultra-sun-ultra-moon",)))
_ITEM_TARGETS.setdefault("reins-of-unity", _target_spec(
    "form", ["calyrex"], ("sword-shield",)))
# An auctioned item works in Scarlet / Violet too
for _name in AUCTION_ITEMS:
    _spec = _ITEM_TARGETS.get(_name)
    if _spec and _spec["games"] and "scarlet-violet" not in _spec["games"]:
        _spec["games"] = tuple(_spec["games"]) + ("scarlet-violet",)


def _auction_money(n):
    return "₽{:,}".format(n)


def _with_auction_info(where, name):
    """`where` (game -> price / location info, or None) with the Porto
    Marinada auction added to Scarlet / Violet for an auctioned item. The
    cached page data is copied, never changed."""
    needs = AUCTION_ITEMS.get(name)
    if not needs:
        return where
    where = dict(where or {})
    entry = dict(where.get("scarlet-violet") or {})
    entry.setdefault("cost", "")
    entry.setdefault("sell", "")
    entry.setdefault("bp", "")
    entry["finite"] = list(entry.get("finite") or [])
    entry["repeatable"] = list(entry.get("repeatable") or [])
    if not entry["cost"] or re.match(r"^n/a$", entry["cost"].strip(), re.I):
        entry["cost"] = "%s starting bid (auction)" % _auction_money(
            AUCTION_START_BID)
    line = (
        "Porto Marinada auction (The Indigo Disk): only up for sale once "
        "you have %s in your party or Boxes. Starting bid %s; the final "
        "price is whatever you outbid the rival bidders at, who drop out "
        "at budgets of %s to %s."
    ) % (needs, _auction_money(AUCTION_START_BID),
         _auction_money(AUCTION_NPC_BUDGETS[0]),
         _auction_money(AUCTION_NPC_BUDGETS[1]))
    # replace Bulbapedia's own one-line auction mention with this fuller one
    entry["finite"] = [l for l in entry["finite"] if "auction" not in l.lower()]
    entry["repeatable"] = [
        l for l in entry["repeatable"] if "auction" not in l.lower()]
    entry["finite"].append(line)
    where["scarlet-violet"] = entry
    return where


def _item_target_spec(name):
    """The kind ("form" / "evolution" / "mega"), Pokémon and games of an
    item's effect, or None when the item does not act on named Pokémon."""
    plain = _plain_item_name(name)
    spec = _ITEM_TARGETS.get(plain)
    if spec:
        return spec
    # All Mega Stones are species-specific
    holder = _MEGA_STONE_HOLDER.get(plain)
    if holder:
        return _target_spec("mega", [holder],
                     _G_NEW_MEGA if plain in _NEW_MEGA_STONES else _G_MEGA)
    return None


# Forms PokeAPI names "<species>-<word>" that read better another way.
_TARGET_LABELS = {
    "necrozma-dusk": "Dusk Mane Necrozma",
    "necrozma-dawn": "Dawn Wings Necrozma",
}
_TARGET_TITLES = {
    "form": "Pokémon whose form it changes",
    "evolution": "Pokémon it evolves",
    "mega": "Pokémon that Mega Evolve with it",
}
# Evolution items that work in Legends Z-A even though PokeAPI has no text
# for them there (they get a Z-A game button and their Pokémon are listed).
_ZA_EVOLUTION_ITEMS = frozenset({
    "fire-stone", "water-stone", "thunder-stone", "leaf-stone", "moon-stone",
    "sun-stone", "shiny-stone", "dusk-stone", "dawn-stone", "ice-stone",
    "oval-stone",
})
# Pokémon that are in a game without a number in any of its Pokédexes
# (Dialga, Palkia and Giratina are only in Scarlet / Violet's Indigo Disk as
# raid / Terarium legendaries), so the Pokédex lookup leaves the game out.
_TARGET_EXTRA_GAMES = {
    # every Pokémon an auction item is made for (they come in through HOME)
    _sp: {"scarlet-violet"} for _sp in (
        "dialga", "palkia", "giratina", "deoxys", "arceus", "shaymin",
        "tornadus", "thundurus", "landorus", "enamorus", "kyurem", "hoopa",
        "necrozma", "zacian", "zamazenta", "kubfu", "calyrex", "rotom")
}
_item_target_cache = {}


def _item_target_info(name, item_games=()):
    """What the item page's "Pokémon affected" table shows:

        {"kind": "form" | "evolution" | "mega",
         "pokemon": [names], "games": [every game it does it in],
         "mons": [{"id", "name", "sprite", "type1", "type2", "dex",
                   "games": [the games it does it for that Pokémon in]}]}

    Each Pokémon only lists the games the item works in for it AND that it is
    in, so picking a game on the item page shows exactly which Pokémon the
    item changes (or evolves) there. None for an item without targets."""
    plain = _plain_item_name(name)
    spec = _item_target_spec(plain)
    if not spec:
        return None
    key = (plain, tuple(sorted(item_games)))
    hit = _item_target_cache.get(key)
    if hit and time.time() - hit[0] < _SPECIES_CACHE_SECONDS:
        return hit[1]
    valid = {k for k, _ in MOVE_GAMES}
    item_set = set(item_games) & valid
    # Legends Z-A is a "partial" game (no PokeAPI text for most items), but
    # the Evolution Stones all work there, so count it for them.
    if spec["kind"] == "evolution" and plain in _ZA_EVOLUTION_ITEMS:
        item_set.add("legends-za")

    def load(entry):
        slug, only = entry if isinstance(entry, tuple) else (entry, None)
        wanted = set(only or spec["games"] or item_set or valid) & valid
        data = _get_pokemon_json(slug)
        if not data:
            return None
        species_name = (data.get("species") or {}).get("name") or slug
        species = _fetch_species_cached(species_name) or {}
        sid = species.get("id") or data.get("id")
        in_games = set(_species_games(species, sid)) if species else set()
        if in_games:
            in_games |= _TARGET_EXTRA_GAMES.get(species_name, set())
        games = (wanted & in_games) if in_games else wanted
        # a regional form (Alolan Vulpix...) only exists in its region's games
        region = slug.rsplit("-", 1)[-1]
        if slug != species_name and region in _REGION_GAMES:
            games &= _REGION_GAMES[region]
        if not games:
            return None
        pid = data.get("id")
        return {
            "id": pid, "name": data.get("name") or slug,
            # "Zygarde Complete", "Vulpix Alola"... for display
            "label": _TARGET_LABELS.get(slug) or _pretty_name(
                data.get("name") or slug, species_name),
            "sprite": SPRITE_URL.format(pid),
            "games": [g for g, _ in MOVE_GAMES if g in games],
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        mons = [m for m in pool.map(load, spec["pokemon"]) if m]
    if not mons:
        return None
    _add_types(mons)
    mons.sort(key=lambda m: (m["dex"], m["id"]))
    games = [g for g, _ in MOVE_GAMES
             if any(g in m["games"] for m in mons)]
    info = {"kind": spec["kind"], "title": _TARGET_TITLES[spec["kind"]],
            "pokemon": [m["name"] for m in mons],
            "games": games, "mons": mons}
    _item_target_cache[key] = (time.time(), info)
    return info



def _games_of_targets(item):
    """Game buttons of a Mega Stone / form-changing item: only the games the
    Pokémon it acts on does it in (Abomasite is not in Scarlet / Violet or
    Sword / Shield; Ultranecrozium Z is only in Ultra Sun / Ultra Moon). A
    Mega Stone also gets a button for every game its Mega is in, even where
    PokeAPI has no text for the stone. Other items are left alone."""
    info = item.get("target_info")
    if info and info.get("kind") == "evolution":
        # Z-A has no PokeAPI text for the stones: still give it a button
        if ("legends-za" in (info.get("games") or [])
                and not any(g["key"] == "legends-za"
                            for g in item.get("games") or [])):
            fallback = [item["sprite"]] if item.get("sprite") else []
            games = list(item.get("games") or [])
            games.append({"key": "legends-za", "label": "Legends Z-A",
                          "text": "", "sprites": fallback})
            order = {k: i for i, (k, _) in enumerate(MOVE_GAMES)}
            games.sort(key=lambda g: order.get(g["key"], len(order)))
            item["games"] = games
        return item
    if not info or info.get("kind") not in ("mega", "form"):
        return item
    allowed = set(info.get("games") or [])
    if not allowed:
        return item
    games = [g for g in item.get("games") or [] if g["key"] in allowed]
    if info["kind"] == "mega":
        have = {g["key"] for g in games}
        fallback = [item["sprite"]] if item.get("sprite") else []
        for key, label in MOVE_GAMES:
            if key in allowed and key not in have:
                games.append({"key": key, "label": label, "text": "",
                              "sprites": fallback})
        order = {k: i for i, (k, _) in enumerate(MOVE_GAMES)}
        games.sort(key=lambda g: order.get(g["key"], len(order)))
    item["games"] = games
    return item


def _z_crystal_games(name):
    """The games a Z-Crystal is in: Sun / Moon (unless it is one of the
    crystals added in Ultra Sun / Ultra Moon) and Ultra Sun / Ultra Moon."""
    games = ["ultra-sun-ultra-moon"]
    if name not in supplements.ULTRA_ONLY_Z_CRYSTALS:
        games.insert(0, "sun-moon")
    return games


def _z_moves_of(name):
    """{"from", "pokemon", "moves": [{"name", "label"}]} for a Z-Crystal,
    None for any other item."""
    info = supplements.Z_CRYSTAL_MOVES.get(name)
    if not info:
        return None
    return {
        "from": info["from"], "pokemon": info["pokemon"],
        "moves": [{"name": m, "label": label} for m, label in info["moves"]],
    }


def _z_crystal_effect(name):
    """What a Z-Crystal does, in words. PokeAPI only has the placeholder
    "XXX new effect for <name>--held" for them, so the effect is written from
    the Z-Move data. None for any other item."""
    info = supplements.Z_CRYSTAL_MOVES.get(name)
    if not info:
        return None
    moves = " / ".join(label for _, label in info["moves"])
    if info["from"].startswith("Any "):
        return (
            "A Z-Crystal. A Pokémon holding it can use a Z-Ring to turn any "
            "%s move it knows into the Z-Move %s, which can be used once per "
            "battle." % (info["from"][4:].replace("-type move", "-type"),
                         moves))
    return (
        "A Z-Crystal made for %s. Held with a Z-Ring, it turns %s into the "
        "exclusive Z-Move %s, which can be used once per battle."
        % (info["pokemon"], info["from"], moves))


def _is_placeholder_effect(text):
    """PokeAPI's "XXX new effect for ..." filler is not a real effect."""
    return (text or "").lstrip().upper().startswith("XXX")


def fetch_item(name):
    """A battle item (X Attack, Leftovers, Focus Sash...) as a dict, or
    None when PokeAPI does not know the name. Items that exist but are not
    battle items come back with "battle": False."""
    slug = _ability_slug(name)
    data = None
    # Z-Crystals only exist as "<name>--held" / "<name>--bag" on PokeAPI.
    for candidate in (slug, slug + "--held"):
        try:
            r = requests.get(
                POKEAPI_ITEM.format(quote(candidate)), timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            break
        except Exception:
            continue
    if data is None:
        return None
    effect = ""
    for entry in data.get("effect_entries", []):
        if entry.get("language", {}).get("name") == "en":
            effect = entry.get("effect", "")
            break

    # In-game description per game (newest game wins when PokeAPI repeats).
    texts = {}
    for f in data.get("flavor_text_entries") or []:
        if (f.get("language") or {}).get("name") != "en":
            continue
        vg = _norm_vg((f.get("version_group") or {}).get("name", ""))
        if vg in _GAME_LABELS:
            texts[vg] = " ".join((f.get("text") or "").split())
    # Newer items PokeAPI has no text for: use our own description.
    extra = supplements.EXTRA_ITEM_INFO.get(data.get("name")) or {}
    if extra:
        effect = effect or extra["effect"]
        for k in extra["games"]:
            if not texts.get(k):
                texts[k] = extra["effect"]
    is_mega_stone = (
        ((data.get("category") or {}).get("name") == "mega-stones")
        or data.get("name") in MEGA_STONES
    )
    plain_name = _plain_item_name(data.get("name"))
    if _is_placeholder_effect(effect):
        effect = ""
    for k in [k for k, t in texts.items() if _is_placeholder_effect(t)]:
        del texts[k]
    z_effect = _z_crystal_effect(plain_name)
    if z_effect:
        # PokeAPI has no real effect for the Z-Crystals
        effect = z_effect
        for k in _z_crystal_games(plain_name):
            texts.setdefault(k, z_effect)
    if not effect and not texts and is_mega_stone:
        # Newer stones (Legends: Z-A...) have no text on PokeAPI yet
        effect = ("One of a species' Mega Stones. Lets the matching "
                  "Pokémon Mega Evolve in battle when it holds this stone.")
    if not effect and texts:
        effect = next(
            (texts[k] for k, _ in MOVE_GAMES if texts.get(k)), ""
        )
    # Games with an in-game description, plus every main-series game in the
    # generations PokeAPI says the item exists in (its flavor text is often
    # missing for some games). The smaller games (Let's Go, BDSP, Legends
    # Arceus, Legends Z-A, Champions) have much smaller item sets than their
    # generation, so they only appear when PokeAPI has text for them.
    keys = set(texts)
    for gi in data.get("game_indices") or []:
        gen = (gi.get("generation") or {}).get("name", "")
        for vg in GEN_SET_MAP.get(gen, []):
            vg = _norm_vg(vg)
            if vg in _GAME_LABELS and vg not in ITEM_PARTIAL_GAMES:
                keys.add(vg)
    sprite = (data.get("sprites") or {}).get("default") or ""
    filename = sprite.rsplit("/", 1)[-1] if sprite else ""

    def game_sprites(key):
        """Sprite URLs to try for a game, best first, default last."""
        urls = []
        if filename:
            urls = [
                ITEM_SPRITE_BASE + folder + "/" + filename
                for folder in ITEM_SPRITE_FOLDERS.get(key, [])
            ]
        if sprite:
            urls.append(sprite)
        return urls

    if plain_name in Z_CRYSTALS:
        # Z-Crystals only exist in the Alola games (never Sword / Shield or
        # Scarlet / Violet), and the ones added in Ultra Sun / Ultra Moon are
        # not in Sun / Moon.
        keys = set(_z_crystal_games(plain_name))
    games = [
        {
            "key": k, "label": label, "text": texts.get(k, ""),
            "sprites": game_sprites(k),
        }
        for k, label in MOVE_GAMES if k in keys
    ]

    raw_attrs = [
        a["name"] for a in data.get("attributes", []) if a.get("name")
    ]
    category = ((data.get("category") or {}).get("name") or "")
    tag_keys = item_tag_keys(plain_name)
    holdable = any(a.startswith("holdable") for a in raw_attrs)
    return _games_of_targets({
        "id": data.get("id"),
        "name": plain_name,
        "english_name": _en_text(data.get("names"), "name"),
        # Evolution / Form changing badges shown next to the category
        "tags": [{"key": k, "label": ITEM_TAG_LABELS[k]} for k in tag_keys],
        "target_info": _item_target_info(
            plain_name, [g["key"] for g in games]),
        # the items in this tab share one tag; one that is not held (an
        # Evolution Stone, a key item) has just its Evolution / Form changing
        # badge
        "category": "held item" if (holdable or not tag_keys) else "",
        "cost": data.get("cost"),
        "fling_power": data.get("fling_power"),
        "sprite": sprite,
        "effect": effect,
        "attributes": [
            a.replace("-", " ") for a in raw_attrs
            if a not in BATTLE_ITEM_ATTRIBUTES
        ],
        # the Z-Moves a Z-Crystal creates (shown under the item, like the
        # Pokémon of a form-changing item)
        "z_moves": _z_moves_of(plain_name),
        "battle": (
            any(a in BATTLE_ITEM_ATTRIBUTES for a in raw_attrs)
            or data.get("name") in BATTLE_ITEM_EXTRAS
            or plain_name in BATTLE_ITEM_EXTRAS
            or is_mega_stone
        ),
        "games": games,
    })


# ---- item art of each game ---------------------------------------------------
# PokeAPI has one picture per item (and only documents that one set), so the
# art of each generation comes from Bulbapedia's bag sprites, which are named
# Bag_<Item>_<GAME>_Sprite.png (or Bag_<Item>_Sprite.png for the common one)
# and stored under a folder made from the file name's MD5. Not every item has
# art for every game, so each candidate is checked once and only the files
# that exist are offered; the default picture stays as the last resort.
BULBA_ARCHIVE = "https://archives.bulbagarden.net/media/upload/{}/{}/{}"
ITEM_SPRITE_TAGS = {
    "gold-silver": ["GS", "GSC"], "crystal": ["C", "GSC"],
    "ruby-sapphire": ["RS", "RSE"], "emerald": ["E", "RSE"],
    "firered-leafgreen": ["FRLG"],
    "diamond-pearl": ["DP", "DPPt"], "platinum": ["Pt", "DPPt"],
    "heartgold-soulsilver": ["HGSS"],
    "black-white": ["BW"], "black-2-white-2": ["B2W2", "BW"],
    "x-y": ["XY"], "omega-ruby-alpha-sapphire": ["ORAS", "XY"],
    "sun-moon": ["SM"], "ultra-sun-ultra-moon": ["USUM", "SM"],
    "lets-go-pikachu-lets-go-eevee": ["LGPE", ""],
    "sword-shield": ["SwSh", ""],
    "brilliant-diamond-shining-pearl": ["BDSP", ""],
    "legends-arceus": ["LA", ""],
    "scarlet-violet": ["SV", ""],
    "legends-za": ["ZA", "SV", ""],
}
_ITEM_SPRITE_CACHE = {}
_ITEM_SPRITE_LOCK = threading.Lock()
_item_sprite_backoff_until = 0.0


def _bag_sprite_url(english_name, tag):
    """The archive URL of an item's bag sprite for a game tag (\"SV\", or
    \"\" for the common one)."""
    base = "Bag_" + "_".join(english_name.split())
    filename = "%s_%s_Sprite.png" % (base, tag) if tag else base + "_Sprite.png"
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return BULBA_ARCHIVE.format(digest[0], digest[:2], quote(filename))


def _probe_url(url):
    """True / False when the URL does / does not exist, None when the check
    itself failed (no network)."""
    try:
        r = requests.get(url, stream=True, timeout=5)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return None


def _item_sprites_by_game(english_name, filename, games):
    """{game key: [URLs that exist, newest art first]} for an item, or None
    when the files could not be checked right now."""
    global _item_sprite_backoff_until
    wanted = {}
    for key in games:
        urls = [_bag_sprite_url(english_name, t)
                for t in ITEM_SPRITE_TAGS.get(key, [])]
        if filename:
            urls += [ITEM_SPRITE_BASE + folder + "/" + filename
                     for folder in ITEM_SPRITE_FOLDERS.get(key, [])]
        wanted[key] = urls
    unique = sorted({u for urls in wanted.values() for u in urls})
    with _ITEM_SPRITE_LOCK:
        if time.time() < _item_sprite_backoff_until:
            return None
        todo = [u for u in unique if u not in _ITEM_SPRITE_CACHE]
        if todo:
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(_probe_url, todo))
            if results and all(r is None for r in results):
                _item_sprite_backoff_until = time.time() + 60
                return None
            for url, ok in zip(todo, results):
                if ok is not None:        # a failed check is tried again
                    _ITEM_SPRITE_CACHE[url] = ok
    return {
        key: [u for u in urls if _ITEM_SPRITE_CACHE.get(u)]
        for key, urls in wanted.items()
    }


def add_item_sprites(item):
    """A copy of an item with each game's sprite list built from art that
    exists for that game (see above), the default picture last."""
    games = item.get("games") or []
    if not games or not item.get("english_name"):
        return item
    filename = (item.get("sprite") or "").rsplit("/", 1)[-1]
    found = _item_sprites_by_game(
        item["english_name"], filename, [g["key"] for g in games])
    if found is None:
        return item
    item = dict(item)
    out = []
    for g in games:
        urls = list(found.get(g["key"]) or [])
        if item.get("sprite"):
            urls.append(item["sprite"])
        out.append(dict(g, sprites=urls))
    item["games"] = out
    return item


# ---- where to find an item, and what it costs ------------------------------
# PokeAPI only has one shop price per item and no locations at all, so the
# Items tab reads both from the item's Bulbapedia page (its "Price" and
# "Acquisition" tables). Pages are cached on disk in item_pages/.cache, so
# each item is only downloaded once.
ITEM_PAGES_DIR = Path(__file__).resolve().parent.parent / "item_pages" / ".cache"
_ITEM_WHERE_CACHE = {}
_ITEM_WHERE_LOCK = threading.Lock()
_item_where_backoff_until = 0.0


# Battle Point prices live in the acquisition text ("Battle Frontier (48 BP)"),
# sometimes tagged with the games they apply to ("(E only)"). The tags are
# Bulbapedia's short game names.
_BP_AMOUNT = re.compile(r"(\d[\d,]*)\s*BP\b")
_ONLY_TAG = re.compile(r"\(([^()]*?)\s+only\)", re.I)
_TAG_GAMES = {
    "rby": ["red-blue", "yellow"], "rb": ["red-blue"], "y": ["yellow"],
    "gs": ["gold-silver"], "gsc": ["gold-silver", "crystal"],
    "c": ["crystal"],
    "rs": ["ruby-sapphire"], "r": ["ruby-sapphire"],
    "s": ["ruby-sapphire"], "e": ["emerald"],
    "frlg": ["firered-leafgreen"], "fr": ["firered-leafgreen"],
    "lg": ["firered-leafgreen"],
    "dp": ["diamond-pearl"], "d": ["diamond-pearl"],
    "p": ["diamond-pearl"], "dppt": ["diamond-pearl", "platinum"],
    "pt": ["platinum"], "hgss": ["heartgold-soulsilver"],
    "bw": ["black-white"], "b2w2": ["black-2-white-2"],
    "xy": ["x-y"], "oras": ["omega-ruby-alpha-sapphire"],
    "sm": ["sun-moon"], "usum": ["ultra-sun-ultra-moon"],
    "lgpe": ["lets-go-pikachu-lets-go-eevee"],
    "swsh": ["sword-shield"], "bdsp": ["brilliant-diamond-shining-pearl"],
    "pla": ["legends-arceus"], "la": ["legends-arceus"],
    "sv": ["scarlet-violet"], "za": ["legends-za"],
}


def _bp_prices(line, games):
    """[(game key, "48 BP")] for the Battle Point prices in one acquisition
    line. A line can hold several places, separated by commas outside the
    brackets ("Battle Tower (RS only), Battle Frontier (48 BP) (E only)"); a
    "(E only)" style tag limits the price in its own part to those games. A tag
    this cannot read gives no price at all rather than a wrong one."""
    out = []
    for part in re.split(r",\s*(?![^()]*\))", line):
        amounts = [m.group(1) for m in _BP_AMOUNT.finditer(part)]
        if not amounts:
            continue
        targets = list(games)
        tags = _ONLY_TAG.findall(part)
        if tags:
            only = set()
            for tag in tags:
                for name in re.split(r"[\s,/&]+|\band\b", tag):
                    only.update(_TAG_GAMES.get(name.strip().lower(), []))
            targets = [g for g in targets if g in only]
        out.extend((g, "%s BP" % a) for g in targets for a in amounts)
    return out


def _merge_item_page(page):
    """parse_item_page() output -> {game key: {"cost", "sell", "finite",
    "repeatable"}}. A game can have several rows (e.g. the Item Printer in
    its own row); their lists are joined without repeats."""
    where = {}

    def entry(game):
        return where.setdefault(game, {
            "cost": "", "sell": "", "bp": "", "finite": [], "repeatable": [],
        })

    for row in page.get("prices") or []:
        for game in row["games"]:
            e = entry(game)
            e["cost"] = e["cost"] or row["cost"]
            e["sell"] = e["sell"] or row["sell"]
    for row in page.get("acquisition") or []:
        for game in row["games"]:
            e = entry(game)
            for field in ("finite", "repeatable"):
                for line in row[field]:
                    if line not in e[field]:
                        e[field].append(line)
            # Battle Point price (shown as the cost when there is no ₽ price)
            for field in ("finite", "repeatable"):
                for line in row[field]:
                    for g, bp in _bp_prices(line, row["games"]):
                        have = where[g]["bp"].split(", ") if where[g]["bp"] else []
                        if bp not in have:
                            where[g]["bp"] = ", ".join(have + [bp])
    return where


def fetch_item_where(item):
    """{game key: {"cost", "sell", "finite", "repeatable"}} for an item
    from its Bulbapedia page, or None when the page could not be read."""
    global _item_where_backoff_until
    name = (item.get("english_name") or _pretty(item.get("name"))).strip()
    if not name:
        return None
    if name in _ITEM_WHERE_CACHE:
        return _ITEM_WHERE_CACHE[name]
    if time.time() < _item_where_backoff_until:
        return None
    try:
        with _ITEM_WHERE_LOCK:
            fetcher = bulbapedia.Fetcher(ITEM_PAGES_DIR, delay=0.5, timeout=8)
            page = bulbapedia.scrape_item(fetcher, name)
            if page is None:
                if fetcher.failed:           # network trouble: retry later
                    _item_where_backoff_until = time.time() + 60
                    return None
                where = {}                   # Bulbapedia has no such page
            else:
                where = _merge_item_page(page)
    except Exception as exc:
        # e.g. beautifulsoup4 not installed: show it in the server console
        logging.getLogger(__name__).warning(
            "Could not read item locations for %s from Bulbapedia: %s",
            name, exc,
        )
        _item_where_backoff_until = time.time() + 60
        return None
    _ITEM_WHERE_CACHE[name] = where
    return where


def add_item_locations(item):
    """A copy of a fetch_item() result with "where" added: the locations
    and prices per game. Games Bulbapedia lists the item in but PokeAPI
    missed get a button too. The item is returned as it was when nothing
    could be read."""
    where = fetch_item_where(item)
    # the auction (Scarlet / Violet) is added even when Bulbapedia could not
    # be read or has no locations for the item
    auction = _plain_item_name(item.get("name")) in AUCTION_ITEMS
    if auction:
        where = _with_auction_info(where, _plain_item_name(item.get("name")))
    if where is None:
        return item
    item = dict(item)
    item["where_loaded"] = True
    if not where:
        return item
    games = list(item.get("games") or [])
    have = {g["key"] for g in games}
    fallback = [item["sprite"]] if item.get("sprite") else []
    for key, label in MOVE_GAMES:
        if key in where and key not in have:
            games.append({"key": key, "label": label, "text": "",
                          "sprites": fallback})
    order = {k: i for i, (k, _) in enumerate(MOVE_GAMES)}
    games.sort(key=lambda g: order.get(g["key"], len(order)))
    item["games"] = games
    item["where"] = where
    return _games_of_targets(item)


def _get_json(url):
    """GET JSON with brief retries for transient API/network failures."""
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                timeout=TIMEOUT,
                headers={"User-Agent": "PokedexProject/1.0"},
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                return None
            time.sleep(0.25 * (attempt + 1))
    return None


def _get_pokemon_json(name):
    """The /pokemon/ record for a Pokemon name or a species name.

    "mimikyu", "aegislash", "minior"... are species names: PokeAPI only has a
    /pokemon/ entry per form (mimikyu-disguised), so those fall back to the
    species' default form. None when nothing matches.
    """
    raw_name = (name or "").strip().lower().replace(" ", "-")
    aliases = {
        "battle-bond-greninja": "greninja-battle-bond",
        "battlebond-greninja": "greninja-battle-bond",
        "ash-greninja": "greninja-ash",
        # the name shown on the form button: "Eternal Flower Floette"
        "eternal-flower-floette": "floette-eternal",
        "floette-eternal-flower": "floette-eternal",
        "eternal-floette": "floette-eternal",
    }
    raw_name = aliases.get(raw_name, raw_name)
    # "vivillon-polar" is a pattern of Vivillon, not a Pokémon of its own
    requested = raw_name
    for cosmetic in _COSMETIC_FORM_SPECIES:
        if raw_name.startswith(cosmetic + "-"):
            raw_name = cosmetic
    key = quote(raw_name)
    if not key:
        return None
    data = _get_json(POKEAPI_POKEMON.format(key))
    if isinstance(data, dict):
        # a look of the Pokémon (Polar Vivillon): the page opens on that form
        if requested != raw_name and requested in [
                (f or {}).get("name") for f in data.get("forms") or []]:
            return dict(data, name=requested)
        return data
    # "mega charizard x" -> PokeAPI's "charizard-mega-x"
    if raw_name.startswith("mega-") and len(raw_name) > 5:
        rest = raw_name[5:]
        if rest[-2:] in ("-x", "-y", "-z"):
            guess = rest[:-2] + "-mega" + rest[-2:]
        else:
            guess = rest + "-mega"
        data = _get_json(POKEAPI_POKEMON.format(quote(guess)))
        if isinstance(data, dict):
            return data
    sp = _get_json(POKEAPI_SPECIES.format(key))
    default = None
    if isinstance(sp, dict):
        default = next(
            (v for v in sp.get("varieties") or [] if v.get("is_default")),
            None,
        )
    default_name = ((default or {}).get("pokemon") or {}).get("name")
    if not default_name:
        # "unown-b": a look of a Pokémon, not a Pokémon of its own
        parts = raw_name.split("-")
        for i in range(len(parts) - 1, 0, -1):
            base = _get_json(POKEAPI_POKEMON.format(
                quote("-".join(parts[:i]))))
            if isinstance(base, dict):
                if raw_name in [(f or {}).get("name")
                                for f in base.get("forms") or []]:
                    return dict(base, name=raw_name)
                break
        return None
    data = _get_json(POKEAPI_POKEMON.format(quote(default_name)))
    return data if isinstance(data, dict) else None


def _fetch_species(species_name):
    """The PokeAPI species record ({} when unavailable)."""
    if not species_name:
        return {}
    data = _get_json(POKEAPI_SPECIES.format(quote(species_name)))
    return data if isinstance(data, dict) else {}


def _extra_games(species):
    """Games PokeAPI only records through the species' Pokedex list.

    Pokemon Champions has no per-Pokemon move data in PokeAPI yet, so
    membership is read from the Pokedexes the species appears in.
    """
    dexes = [
        (d.get("pokedex") or {}).get("name", "")
        for d in species.get("pokedex_numbers") or []
    ]
    games = set()
    if any("champions" in d for d in dexes):
        games.add("champions")
    # Z-A's Lumiose dex and the Mega Dimension (Hyperspace) dex
    if any("lumiose" in d or "hyperspace" in d for d in dexes):
        games.add("legends-za")
    # Pokédex entries read from Bulbapedia (data/extra_dex_entries.json) show
    # the Pokémon is in Z-A even where PokeAPI's Pokédex list does not
    if _extra_dex_entries((species or {}).get("name"), "legends-za"):
        games.add("legends-za")
    return games


# Cosmetic variants that are not battle forms.
# Megas, G-Max, Primal, Totem and Eternamax are kept.
# Ash-Greninja ("greninja-ash") is NOT skipped: PokeAPI keeps it separate
# from Battle Bond Greninja ("greninja-battle-bond"), and it is a real Gen 7
# battle form.
_SKIP_FORM_TOKENS = {
    "cap", "starter", "cosplay", "phd", "libre", "belle",
    "rock", "pop",
}
MAX_FORMS = 10
_MAX_FORMS_OF = {"pikachu": 20}     # Pikachu has 17 forms
# Button text / page title of forms whose PokeAPI name reads badly.
_FORM_LABEL_OVERRIDES = {
    "pikachu-starter": "Partner (Let's Go)",
    "pikachu-phd": "PhD",
    "qwilfish": "Johtoian",
    "qwilfish-hisui": "Hisuian",
}
_FORM_TITLE_OVERRIDES = {
    "pikachu-starter": "Partner Pikachu",
    "pikachu-phd": "PhD Pikachu",
    "qwilfish-hisui": "Hisuian Qwilfish",
}


# Short stat names shown in the Stats lists.
_STAT_SHORT = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "special-attack": "Sp.Atk", "special-defense": "Sp.Def",
    "speed": "Speed",
}


def _stat_label(name):
    return _STAT_SHORT.get(name) or (name or "").replace("-", " ").title()


def _form_label(variety, species_name):
    if variety in _FORM_LABEL_OVERRIDES:
        return _FORM_LABEL_OVERRIDES[variety]
    if variety == species_name:
        return "Normal"
    if variety.startswith(species_name + "-"):
        variety = variety[len(species_name) + 1:]
    label = variety.replace("-", " ").title()
    if label == "Gmax":
        label = "G-Max"
    return label

def _pretty_name(name, species_name):
    if name in _FORM_TITLE_OVERRIDES:
        return _FORM_TITLE_OVERRIDES[name]
    sp = (species_name or name).replace("-", " ").title()
    if not species_name or name == species_name:
        return sp
    if not name.startswith(species_name + "-"):
        return sp
    s = name[len(species_name) + 1:].replace("-", " ").title()
    if s.startswith("Mega X"):
        return "Mega " + sp + " X"
    if s.startswith("Mega Y"):
        return "Mega " + sp + " Y"
    if s == "Mega":
        return "Mega " + sp
    if s in ("Gmax", "G-Max"):
        return "Gigantamax " + sp
    if s.startswith("Gmax ") or s.startswith("G-Max "):
        return "Gigantamax " + sp
    if name == "greninja-ash":
        return "Ash-Greninja"
    if species_name == "zygarde":
        # zygarde-10 -> "Zygarde 10%", zygarde-complete -> "Zygarde Complete"
        s = " ".join(w + "%" if w in ("10", "50") else w for w in s.split())
    return sp + " " + s


def _title_for(name, species_name, default_name):
    """Heading for a form. The species' default form is just its species
    name, except Zygarde, whose default is shown as "Zygarde 50%" to go
    with "Zygarde 10%"."""
    if default_name and name == default_name and species_name != "zygarde":
        return _pretty_name(species_name, species_name)
    return _pretty_name(name, species_name)


def _default_variety(species):
    for v in (species or {}).get("varieties") or []:
        if v.get("is_default"):
            return (v.get("pokemon") or {}).get("name")
    return None

def _form_names(species_name, species):
    names = []
    # Pikachu's forms differ in stats, abilities and moves (Partner Pikachu,
    # Cosplay Pikachu), or are worth a button of their own (the eight caps),
    # so none of them is skipped.
    skip = set() if species_name == "pikachu" else _SKIP_FORM_TOKENS
    for v in species.get("varieties") or []:
        n = (v.get("pokemon") or {}).get("name")
        if n and not (set(n.split("-")) & skip):
            names.append(n)
    if species_name == "minior":
        # 14 variants: the Meteor Form looks the same in every colour, so
        # keep one, plus a Core Form for each of the seven colours.
        names = [
            n for n in names
            if n == "minior-red-meteor" or not n.endswith("-meteor")
        ]
    return names[:_MAX_FORMS_OF.get(species_name, MAX_FORMS)]


def _display_label(name, species_name):
    """Button text for a form: "Busted", "Meteor", "Blue Core"..."""
    label = _form_label(name, species_name)
    if species_name == "minior":
        label = "Meteor" if name.endswith("-meteor") else label + " Core"
    return label


# Games a battle form can exist in, whatever PokeAPI's move lists say. A form
# only gets buttons for the games in its set (Mega Evolution is not in
# Sword / Shield, Gigantamax is only in Sword / Shield...).
_FORM_GAME_LIMITS = {
    "mega": {
        "x-y", "omega-ruby-alpha-sapphire", "sun-moon",
        "ultra-sun-ultra-moon", "lets-go-pikachu-lets-go-eevee",
        "legends-za", "champions",
    },
    "primal": {"omega-ruby-alpha-sapphire"},
    "gmax": {"sword-shield"},
    "eternamax": {"sword-shield"},
}


# Mega Evolutions that are new in Legends Z-A (and its Mega Dimension DLC)
# only exist there (and in Champions where it has them), not in X / Y.
_NEW_MEGA_FORMS = {
    "clefable-mega", "victreebel-mega", "starmie-mega", "dragonite-mega",
    "meganium-mega", "feraligatr-mega", "skarmory-mega", "froslass-mega",
    "emboar-mega", "excadrill-mega", "scolipede-mega", "scrafty-mega",
    "eelektross-mega", "chandelure-mega", "chesnaught-mega",
    "delphox-mega", "greninja-mega", "pyroar-mega", "floette-mega",
    "malamar-mega", "barbaracle-mega", "dragalge-mega", "hawlucha-mega",
    "zygarde-mega", "drampa-mega", "falinks-mega",
    "raichu-mega-x", "raichu-mega-y", "chimecho-mega", "absol-mega-z",
    "staraptor-mega", "garchomp-mega-z", "lucario-mega-z", "heatran-mega",
    "darkrai-mega", "golurk-mega", "meowstic-mega", "crabominable-mega",
    "golisopod-mega", "magearna-mega", "scovillain-mega",
    "glimmora-mega", "tatsugiri-mega",
}
_NEW_MEGA_GAMES = {"legends-za", "champions"}
# Forms that exist in exactly these games.
_FORM_ONLY_GAMES = {
    "zygarde-mega": {"legends-za"},
    # Cosplay Pikachu is exclusive to Omega Ruby / Alpha Sapphire, and the
    # partner Pikachu to Let's Go.
    **{"pikachu-" + _c: {"omega-ruby-alpha-sapphire"} for _c in (
        "cosplay", "rock-star", "belle", "pop-star", "phd", "libre")},
    "pikachu-starter": {"lets-go-pikachu-lets-go-eevee"},
    # Battle Bond's transformation only exists in the Gen 7 games.
    "greninja-ash": {"sun-moon", "ultra-sun-ultra-moon"},
}
# Games PokeAPI's record for a form leaves out, added on top of whatever its
# moves and sprites say. Battle Bond Greninja is obtainable in Scarlet /
# Violet, but its PokeAPI record only has older games.
_FORM_EXTRA_GAMES = {
    "greninja-battle-bond": {"scarlet-violet"},
    "greninja-ash": {"sun-moon", "ultra-sun-ultra-moon"},
}


# Regional forms whose region's games include Champions but which are not
# themselves in it (Hisuian Qwilfish).
_NOT_IN_CHAMPIONS = {"qwilfish-hisui"}


def _limit_form_games(name, present):
    """`present` trimmed to the games this form really exists in."""
    present = set(present) | _FORM_EXTRA_GAMES.get(name, set())
    if name in _NOT_IN_CHAMPIONS:
        present.discard("champions")
    only = _FORM_ONLY_GAMES.get(name) or (
        _NEW_MEGA_GAMES if name in _NEW_MEGA_FORMS else None
    )
    if only:
        return (set(present) & only) or set(only)
    for token in (name or "").split("-"):
        limit = _FORM_GAME_LIMITS.get(token)
        if limit:
            limited = set(present) & limit
            if limited:
                return limited
    return present


def _both_genders(species):
    """False for a species that is genderless, male-only or female-only
    (Floette, Salazzle, Tauros...): it has nothing to toggle between, so it
    gets no Male / Female buttons. PokeAPI's gender_rate is the chance of
    female in eighths: -1 genderless, 0 male only, 8 female only. True when
    the species record is missing, so nothing is hidden by mistake."""
    rate = (species or {}).get("gender_rate")
    return rate is None or 0 < rate < 8


def _female_sprites(raw, both_genders=True):
    """Female sprites (for Pokemon with visible gender differences). Empty
    for a species that only has one gender."""
    if not both_genders:
        return {
            "female_sprite": "", "shiny_female_sprite": "",
            "female_sprites_by_gen": {}, "shiny_female_sprites_by_gen": {},
        }
    return {
        "female_sprite": _latest_sprite(raw, "front_female") or "",
        "shiny_female_sprite":
            _latest_sprite(raw, "front_shiny_female") or "",
        "female_sprites_by_gen": _sprites_by_gen(
            raw, "front_female", merge_aliases=True
        ),
        "shiny_female_sprites_by_gen": _sprites_by_gen(
            raw, "front_shiny_female", merge_aliases=True
        ),
    }


_OTHER_SPRITES = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/{}/{}{}.png"
)


def _flat_art_urls(pokemon_id, shiny=False):
    """Pokémon HOME and official-artwork images of a Pokemon or form, best
    first. PokeAPI often has these for forms that are too new to have a
    default sprite (Mega Zygarde)."""
    if not pokemon_id:
        return []
    folder = "shiny/" if shiny else ""
    return [
        _OTHER_SPRITES.format("home", folder, pokemon_id),
        _OTHER_SPRITES.format("official-artwork", folder, pokemon_id),
    ]


def _sprite_or_flat_art(raw, pokemon_id, field="front_default"):
    """The newest sprite of a record; when it has none, the first HOME /
    official-artwork image that exists (probed only in that case)."""
    sprite = _latest_sprite(raw, field)
    if sprite or not pokemon_id:
        return sprite or ""
    for url in _flat_art_urls(pokemon_id, shiny=field == "front_shiny"):
        if _url_exists(url):
            return url
    return ""


def _sprite_candidates(pokemon_id, front_default=None):
    """URLs to try, best first, for a Pokemon or form's icon in the evolution
    lines and form buttons: the 3D Pokémon HOME render (like the search
    suggestions), then its 2D default sprite, then (for forms that are new in
    Legends Z-A or Champions) their render in those games. The page moves to
    the next one when an image does not load."""
    if not pokemon_id:
        return []
    urls = []
    for url in (
        *_flat_art_urls(pokemon_id),     # HOME render, then official artwork
        front_default,
        SPRITE_URL.format(pokemon_id),
        "{}{}.png".format(_ZA_SPRITE_REPO, pokemon_id),
        "{}{}.webp".format(_ZA_SPRITE_REPO, pokemon_id),
        CHAMPIONS_SPRITE_URL.format(pokemon_id),
    ):
        if url and url not in urls:
            urls.append(url)
    return urls


def with_default_game(p, game=None):
    """Copy of a Pokemon record already set up for the game it opens on.

    The page script used to pick the newest game after the page had been
    drawn, so for a moment the oldest-looking state showed: the generic
    sprite, the abilities of a game that has none, every game's moves. This
    picks that game on the server instead, so the first paint is already
    right. `game` forces one (the game two compared Pokemon share);
    otherwise it is the newest game the Pokemon has a sprite in.
    """
    games = [g for g, _ in p.get("games") or []]
    sprites = p.get("sprites_by_gen") or {}
    shinies = p.get("shiny_sprites_by_gen") or {}
    if game not in games:
        game = next((g for g in games if g in sprites),
                    games[0] if games else "")
    out = dict(p)
    out["default_game"] = game
    if game and sprites.get(game):
        out["sprite"] = sprites[game]
    if game and shinies.get(game):
        out["shiny_sprite"] = shinies[game]
    # Legends Z-A has no abilities; Legends Arceus has none except for
    # Arceus, Cherrim and Regigigas (same rule as pksNoAbilities).
    out["start_no_abilities"] = bool(
        game == "legends-za"
        or (game == "legends-arceus"
            and not re.match(r"(arceus|cherrim|regigigas)(?![a-z])",
                             p.get("name") or "", re.I))
    )
    return out


def _form_entry(name, species_name, data, current, extra_games=(),
                default_name=None, both_genders=True):
    raw = data.get("sprites") or {}
    if name in _LOOKS_LIKE:
        # partner Pikachu / Eevee: the same icon as the normal one
        icons = _sprite_candidates(_LOOKS_LIKE[name])
    else:
        icons = _sprite_candidates(data.get("id"), raw.get("front_default"))
    label = _display_label(name, species_name)
    sprites_by_gen = _sprites_by_gen(raw, merge_aliases=True)
    shiny_sprites_by_gen = _sprites_by_gen(
        raw, "front_shiny", merge_aliases=True
    )
    stats = [
        {"name": s["stat"]["name"], "label": _stat_label(s["stat"]["name"]),
         "value": s["base_stat"]}
        for s in data.get("stats", [])
    ]
    types = _unique_types(data.get("types"))
    moves_vgs = set()
    for m in data.get("moves", []):
        for vgd in m.get("version_group_details", []):
            vg = _norm_vg(vgd["version_group"]["name"])
            if vg:
                moves_vgs.add(vg)
    # PokeAPI's JSON often omits Z-A sprites (Darkrai, Mega Greninja...).
    _add_za_fallback(data.get("id"), sprites_by_gen, shiny_sprites_by_gen)
    if _is_gmax(name):
        moves_vgs = {"sword-shield"}   # Gigantamax only exists there
    present = moves_vgs or set(sprites_by_gen)
    # A game with a sprite is always selectable, even without move data.
    present |= {"legends-za", "champions"} & set(sprites_by_gen)
    present |= set(extra_games)  # e.g. Z-A via the species' Pokedex list
    present = _limit_form_games(name, present)
    # in Scarlet / Violet but with no sprite of its own there: the HOME render
    if "scarlet-violet" in present:
        _add_home_fallback(
            data.get("id"), sprites_by_gen, shiny_sprites_by_gen)
    games = [(vg, lbl) for vg, lbl in VERSION_GROUPS if vg in present]
    return {
        **_female_sprites(raw, both_genders),
        # small sprite shown above the form's button
        "icon": icons[0] if icons else "",
        "icon_alt": " ".join(icons[1:]),
        "name": name,
        "label": label,
        "display_name": _title_for(name, species_name, default_name),
        "current": name == current,
        "sprite": _sprite_or_flat_art(raw, data.get("id")),
        "shiny_sprite": _sprite_or_flat_art(
            raw, data.get("id"), "front_shiny"),
        "sprites_by_gen": sprites_by_gen,
        "shiny_sprites_by_gen": shiny_sprites_by_gen,
        "stats": stats,
        "stat_total": sum(st["value"] or 0 for st in stats),
        "types": types,
        "types_history": _types_history(data),
        "stats_history": _stats_history(data),
        "games": games,
        "abilities": _abilities_for(data),
        "abilities_history": _abilities_history(data),
        **_form_facts(data),
    }


def _unique_types(slots):
    """Type names from PokeAPI's [{"slot", "type": {"name"}}] list, each one
    once and in slot order (a repeated type would double the damage the type
    chart shows)."""
    out = []
    for t in slots or []:
        name = (t.get("type") or {}).get("name")
        if name and name not in out:
            out.append(name)
    return out


# ---- type chart, abilities and facts ----------------------------------------
# Attacking type -> {defending type: multiplier} for every pairing that is
# not 1x (the Gen 6+ chart, with Fairy).
_SE, _NVE, _NO = 2, 0.5, 0
_CHART_RULES = {
    "normal": ({}, ["rock", "steel"], ["ghost"]),
    "fire": (["grass", "ice", "bug", "steel"],
             ["fire", "water", "rock", "dragon"], []),
    "water": (["fire", "ground", "rock"], ["water", "grass", "dragon"], []),
    "electric": (["water", "flying"], ["electric", "grass", "dragon"],
                 ["ground"]),
    "grass": (["water", "ground", "rock"],
              ["fire", "grass", "poison", "flying", "bug", "dragon",
               "steel"], []),
    "ice": (["grass", "ground", "flying", "dragon"],
            ["fire", "water", "ice", "steel"], []),
    "fighting": (["normal", "ice", "rock", "dark", "steel"],
                 ["poison", "flying", "psychic", "bug", "fairy"],
                 ["ghost"]),
    "poison": (["grass", "fairy"], ["poison", "ground", "rock", "ghost"],
               ["steel"]),
    "ground": (["fire", "electric", "poison", "rock", "steel"],
               ["grass", "bug"], ["flying"]),
    "flying": (["grass", "fighting", "bug"], ["electric", "rock", "steel"],
               []),
    "psychic": (["fighting", "poison"], ["psychic", "steel"], ["dark"]),
    "bug": (["grass", "psychic", "dark"],
            ["fire", "fighting", "poison", "flying", "ghost", "steel",
             "fairy"], []),
    "rock": (["fire", "ice", "flying", "bug"],
             ["fighting", "ground", "steel"], []),
    "ghost": (["psychic", "ghost"], ["dark"], ["normal"]),
    "dragon": (["dragon"], ["steel"], ["fairy"]),
    "dark": (["psychic", "ghost"], ["fighting", "dark", "fairy"], []),
    "steel": (["ice", "rock", "fairy"],
              ["fire", "water", "electric", "steel"], []),
    "fairy": (["fighting", "dragon", "dark"],
              ["fire", "poison", "steel"], []),
}
TYPE_CHART = {
    atk: {
        **{t: _SE for t in se},
        **{t: _NVE for t in nve},
        **{t: _NO for t in no},
    }
    for atk, (se, nve, no) in _CHART_RULES.items()
}

_ability_cache = {}


def _ability_with_effect(name):
    if name not in _ability_cache:
        detail = _fetch_ability(name)
        if detail is None:
            return ""      # try again next time rather than cache a failure
        _ability_cache[name] = detail["effect"]
    return _ability_cache[name]


def _abilities_for(data):
    """[{"name", "is_hidden", "effect"}] for one /pokemon/ record."""
    slots = [
        (a["ability"]["name"], a.get("is_hidden", False))
        for a in data.get("abilities", [])
    ]
    # Zygarde's Power Construct forms always have Power Construct, never
    # Aura Break.
    if (data.get("name") or "").endswith("-power-construct"):
        slots = [("power-construct", False)]
    return [
        {"name": n, "is_hidden": hidden, "effect": _ability_with_effect(n)}
        for n, hidden in slots
    ]


def _gen_of(generation):
    """1..9 for a PokeAPI generation reference ("generation-vi" -> 6)."""
    return _ROMAN_GEN.get((generation or {}).get("name"))


def _types_history(data):
    """What the Pokémon's types were in older games, from PokeAPI's
    past_types: [{"gen": last generation they applied in, "types": [...]}],
    oldest first. A game uses the first entry whose gen is not before its
    own; no such entry means the Pokémon's current types."""
    out = []
    for p in data.get("past_types") or []:
        gen = _gen_of(p.get("generation"))
        types = _unique_types(p.get("types"))
        if gen and types:
            out.append({"gen": gen, "types": types})
    out.sort(key=lambda e: e["gen"])
    return out


def _stats_history(data):
    """The base stats the Pokémon had in older games, from PokeAPI's
    past_stats: [{"gen": last generation they applied in, "stats": [...]}],
    oldest first, each one the complete stat list for games up to that gen.
    An entry may list only the stats that were different, so each one is
    laid over the newer stats, newest entry first."""
    past = data.get("past_stats") or []
    if not past:
        return []
    cur = {}
    order = []
    for s in data.get("stats") or []:
        name = (s.get("stat") or {}).get("name")
        if name:
            cur[name] = s.get("base_stat")
            order.append(name)
    entries = sorted(
        ((_gen_of(p.get("generation")), p.get("stats") or [])
         for p in past if _gen_of(p.get("generation"))),
        key=lambda e: e[0], reverse=True,
    )
    out = []
    for gen, changed in entries:      # newest first, each one undoes more
        for s in changed:
            name = (s.get("stat") or {}).get("name")
            if name and s.get("base_stat") is not None:
                if name not in cur:
                    order.append(name)
                cur[name] = s["base_stat"]
        out.append({
            "gen": gen,
            "stats": [
                {"name": n, "label": _stat_label(n), "value": cur[n]}
                for n in order
            ],
        })
    out.sort(key=lambda e: e["gen"])
    return out


def _abilities_history(data):
    """The same for abilities (PokeAPI's past_abilities, which only lists
    the slots that were different): [{"gen", "abilities": [...]}], oldest
    first, each one the complete ability list for games up to that gen."""
    past = data.get("past_abilities") or []
    if not past or (data.get("name") or "").endswith("-power-construct"):
        return []
    slots = {
        a.get("slot"): (a["ability"]["name"], a.get("is_hidden", False))
        for a in data.get("abilities", [])
    }
    entries = sorted(
        ((_gen_of(p.get("generation")), p.get("abilities") or [])
         for p in past if _gen_of(p.get("generation"))),
        key=lambda e: e[0], reverse=True,
    )
    out = []
    for gen, changed in entries:      # newest first, each one undoes more
        for a in changed:
            if a.get("ability"):
                slots[a.get("slot")] = (
                    a["ability"]["name"], a.get("is_hidden", False))
            else:
                slots.pop(a.get("slot"), None)     # no such slot back then
        out.append({
            "gen": gen,
            "abilities": [
                {"name": n, "is_hidden": hidden,
                 "effect": _ability_with_effect(n)}
                for _, (n, hidden) in sorted(
                    slots.items(), key=lambda kv: (kv[0] is None, kv[0]))
            ],
        })
    out.sort(key=lambda e: e["gen"])
    return out


_STAT_LABELS = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "special-attack": "Sp. Atk", "special-defense": "Sp. Def",
    "speed": "Speed",
}
_VERSION_LABELS = {
    "legends-za": "Legends Z-A", "legends-arceus": "Legends Arceus",
    "legends-z-a": "Legends Z-A", "mega-dimension": "Mega Dimension",
    "lets-go-pikachu": "Let's Go, Pikachu!",
    "lets-go-eevee": "Let's Go, Eevee!",
    "black-2": "Black 2", "white-2": "White 2",
    "brilliant-diamond": "Brilliant Diamond",
    "shining-pearl": "Shining Pearl",
    "x": "X", "y": "Y",
}


def _form_facts(data):
    """Facts that belong to one form: size, base experience, EV yield."""
    ev = [
        "%d %s" % (
            s["effort"],
            _STAT_LABELS.get(s["stat"]["name"], _pretty(s["stat"]["name"])),
        )
        for s in data.get("stats", []) if s.get("effort")
    ]
    height = data.get("height")
    weight = data.get("weight")
    return {
        "height": round(height / 10, 1) if height else None,
        "weight": round(weight / 10, 1) if weight else None,
        "base_exp": data.get("base_experience"),
        "ev_yield": ", ".join(ev) or "None",
    }


_GROWTH_NAMES = {
    "slow-then-very-fast": "Erratic",
    "fast-then-very-slow": "Fluctuating",
}
_EGG_GROUP_NAMES = {
    "ground": "Field", "humanshape": "Human-Like",
    "indeterminate": "Amorphous", "plant": "Grass",
    "no-eggs": "Undiscovered", "water1": "Water 1",
    "water2": "Water 2", "water3": "Water 3",
}


def _species_facts(species):
    """Facts shared by every form of a species."""
    genus = ""
    for g in species.get("genera") or []:
        if (g.get("language") or {}).get("name") == "en":
            genus = g.get("genus", "")
            break
    growth = (species.get("growth_rate") or {}).get("name", "")
    eggs = [
        _EGG_GROUP_NAMES.get(g["name"], _pretty(g["name"]))
        for g in species.get("egg_groups") or []
    ]
    return {
        "genus": genus,
        "catch_rate": species.get("capture_rate"),
        "friendship": species.get("base_happiness"),
        "growth_rate": _GROWTH_NAMES.get(growth, _pretty(growth)),
        "egg_groups": ", ".join(eggs),
        "gender": _gender_text(species.get("gender_rate")),
        "egg_cycles": species.get("hatch_counter"),
    }


def _version_in_game(game, version):
    """Does a PokeAPI version name belong to this game button? Newer games
    are matched loosely, since PokeAPI spells them in several ways
    (legends-za, legends-z-a, mega-dimension...)."""
    if version in GAME_VERSIONS.get(game, ()):
        return True
    compact = "".join(c for c in (version or "") if c.isalnum())
    if game == "legends-za":
        return (compact.startswith("legendsz")
                or compact in ("za", "megadimension"))
    if game == "champions":
        return compact in ("champions", "pokemonchampions")
    return _norm_vg(version) == game


# ---- filling the gaps on demand -------------------------------------------
# The first time a Scarlet / Violet or Legends Z-A Pokemon with missing
# Pokedex entries or moves is opened, its Bulbapedia page is read (through
# Bulbapedia's API) and the result saved in the two data/extra_*.json files,
# so every later visit is instant and offline. Set LAZY_SCRAPE = False to
# turn this off.
LAZY_SCRAPE = True
_SCRAPE_GAMES = ("legends-za", "scarlet-violet")
SCRAPE_RECHECK_SECONDS = 7 * 24 * 3600
SCRAPE_BACKOFF_SECONDS = 300
# Bump the number when the Bulbapedia parsers improve: Pokemon marked as
# checked by an older parser are then read again.
CHECKED_FILE = "extra_checked_v3.json"
_SCRAPE_LOCK = threading.Lock()
_scrape_backoff_until = 0.0


def _write_extra(filename, data):
    """Write a data/ JSON file atomically (a half-written file is never
    visible to another request)."""
    path = _DATA_DIR / filename
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
    _EXTRAS_CACHE.pop(filename, None)


def _in_game(species, game):
    return any(
        _dex_matches(game, (p.get("pokedex") or {}).get("name"))
        for p in species.get("pokedex_numbers") or []
    )


def _pokeapi_has_dex(species, game):
    return any(
        (e.get("language") or {}).get("name") == "en"
        and _version_in_game(game, (e.get("version") or {}).get("name"))
        for e in species.get("flavor_text_entries") or []
    )


def _pokeapi_has_moves(data, game):
    return any(m["version_group"] == game for m in _moves_from_data(data))


def _english_name(species, fallback):
    for n in species.get("names") or []:
        if (n.get("language") or {}).get("name") == "en" and n.get("name"):
            return n["name"]
    return (fallback or "").replace("-", " ").title()


def _fill_gaps(species, data):
    """Read missing Z-A / Scarlet-Violet Pokedex entries and moves of this
    Pokemon from Bulbapedia, once. Never raises: on any problem the page
    simply shows what PokeAPI has."""
    global _scrape_backoff_until
    name = (species or {}).get("name")
    if not (LAZY_SCRAPE and name and isinstance(data, dict)):
        return
    if time.time() < _scrape_backoff_until:
        return
    default_name = _default_variety(species) or name
    base_data = data
    if data.get("name") != default_name:
        base_data = _get_pokemon_json(default_name) or data
    needs = {
        g: (not _pokeapi_has_dex(species, g),
            not _pokeapi_has_moves(base_data, g))
        for g in _SCRAPE_GAMES if g == "legends-za" or _in_game(species, g)
    }
    needs = {g: n for g, n in needs.items() if any(n)}
    if not needs:
        return
    try:
        with _SCRAPE_LOCK:
            checked = _load_extra(CHECKED_FILE)
            if time.time() - checked.get(name, 0) < SCRAPE_RECHECK_SECONDS:
                return
            fetcher = bulbapedia.Fetcher(
                _DATA_DIR / ".bulbapedia_cache", delay=0.5, timeout=8
            )
            title = bulbapedia.pokemon_title(_english_name(species, name))
            default = _default_variety(species) or name
            dex_all = dict(_load_extra("extra_dex_entries.json"))
            moves_all = dict(_load_extra("extra_learnsets.json"))
            reached = False
            for game, (want_dex, want_moves) in needs.items():
                result = bulbapedia.scrape_page(
                    fetcher, title, game, dex=want_dex, moves=want_moves
                )
                if result is None:
                    if fetcher.failed:
                        _scrape_backoff_until = (
                            time.time() + SCRAPE_BACKOFF_SECONDS
                        )
                        return           # try again later, not marked done
                    continue             # no such page: marked done below
                reached = True
                if want_dex and result["dex"]:
                    dex_all.setdefault(game, {})[name] = result["dex"]
                if want_moves and result["moves"]:
                    moves_all[default] = [
                        r for r in moves_all.get(default, []) if r[1] != game
                    ] + result["moves"]
                # A page with a learnset per form (Qwilfish / Hisuian
                # Qwilfish): the regional form's moves are filed under it.
                for label, rows in (result.get("forms") or {}).items():
                    form_name = _form_for_label(species, label)
                    if want_moves and form_name and rows:
                        moves_all[form_name] = [
                            r for r in moves_all.get(form_name, [])
                            if r[1] != game
                        ] + rows
            _write_extra("extra_dex_entries.json", dex_all)
            _write_extra("extra_learnsets.json", moves_all)
            checked = dict(checked)
            checked[name] = time.time()
            _write_extra(CHECKED_FILE, checked)
    except Exception as exc:
        # e.g. beautifulsoup4 not installed: show it in the server console
        logging.getLogger(__name__).warning(
            "Could not fill in missing data for %s from Bulbapedia: %s",
            name, exc,
        )
        _scrape_backoff_until = time.time() + SCRAPE_BACKOFF_SECONDS


_REGION_ADJECTIVES = {
    "alolan": "alola", "galarian": "galar", "hisuian": "hisui",
    "paldean": "paldea",
}


def _form_for_label(species, label):
    """PokeAPI name of the regional form a Bulbapedia learnset heading
    ("Hisuian Qwilfish") is about, or None (the default form's own heading
    and anything that is not a regional form)."""
    region = _REGION_ADJECTIVES.get((label or "").split(" ")[0].lower())
    base = (species or {}).get("name") or ""
    if not region or not base:
        return None
    for v in (species or {}).get("varieties") or []:
        vname = (v.get("pokemon") or {}).get("name") or ""
        if vname.startswith(base + "-" + region):
            return vname
    return None


def _extra_dex_entries(species_name, game):
    """Scraped entries (data/extra_dex_entries.json) for one species and
    game, as [{"version", "text"}]. Shape of the file:
    {"legends-za": {"pikachu": [{"version": "Legends Z-A", "text": "..."}]}}
    """
    rows = (_load_extra("extra_dex_entries.json").get(game) or {}).get(
        species_name or "", []
    )
    return [
        {"version": r.get("version") or "", "text": r["text"]}
        for r in rows
        if isinstance(r, dict) and r.get("text")
    ]


def _dex_by_game(species):
    """{game: {"entries": [...], "dex": [...]}} for every game with a
    Pokedex entry or a regional number, for the Details tab."""
    result = {}
    species_name = (species or {}).get("name") or ""
    for game in GAME_VERSIONS:
        entries, seen = [], set()
        versions = GAME_VERSIONS[game]
        for e in species.get("flavor_text_entries") or []:
            if (e.get("language") or {}).get("name") != "en":
                continue
            v = (e.get("version") or {}).get("name")
            if not _version_in_game(game, v):
                continue
            text = " ".join(
                (e.get("flavor_text") or "").replace("\u00ad", "").split()
            )
            if text and (v, text) not in seen:
                seen.add((v, text))
                entries.append({
                    "version": _VERSION_LABELS.get(v, _pretty(v)),
                    "text": text,
                    "_order": versions.index(v) if v in versions else 0,
                })
        # one line per version, in the game's own version order
        entries.sort(key=lambda e: e["_order"])
        for e in entries:
            del e["_order"]
        if not entries:
            # PokeAPI has nothing for this game: use the scraped entries
            entries = _extra_dex_entries(species_name, game)
        dex = [
            {"name": _pretty(p["pokedex"]["name"]),
             "number": p.get("entry_number")}
            for p in species.get("pokedex_numbers") or []
            if _dex_matches(game, (p.get("pokedex") or {}).get("name"))
        ]
        if entries or dex:
            result[game] = {"entries": entries, "dex": dex}
    return result


# ---- moves of a form -------------------------------------------------------
# PokeAPI gives the Gigantamax records ("charizard-gmax") an empty move list:
# what they use in battle are Max Moves, one per move type, plus the
# Pokemon's own G-Max Move. Those are built here from the normal form's moves.
MAX_MOVE_BY_TYPE = {
    "normal": "max-strike", "fire": "max-flare", "water": "max-geyser",
    "electric": "max-lightning", "grass": "max-overgrowth",
    "ice": "max-hailstorm", "fighting": "max-knuckle", "poison": "max-ooze",
    "ground": "max-quake", "flying": "max-airstream",
    "psychic": "max-mindstorm", "bug": "max-flutterby", "rock": "max-rockfall",
    "ghost": "max-phantasm", "dragon": "max-wyrmwind", "dark": "max-darkness",
    "steel": "max-steelspike", "fairy": "max-starfall",
}
GMAX_MOVES = {
    "venusaur-gmax": "g-max-vine-lash", "charizard-gmax": "g-max-wildfire",
    "blastoise-gmax": "g-max-cannonade", "butterfree-gmax": "g-max-befuddle",
    "pikachu-gmax": "g-max-volt-crash", "meowth-gmax": "g-max-gold-rush",
    "machamp-gmax": "g-max-chi-strike", "gengar-gmax": "g-max-terror",
    "kingler-gmax": "g-max-foam-burst", "lapras-gmax": "g-max-resonance",
    "eevee-gmax": "g-max-cuddle", "snorlax-gmax": "g-max-replenish",
    "garbodor-gmax": "g-max-malodor", "melmetal-gmax": "g-max-meltdown",
    "rillaboom-gmax": "g-max-drum-solo", "cinderace-gmax": "g-max-fireball",
    "inteleon-gmax": "g-max-hydrosnipe", "corviknight-gmax": "g-max-wind-rage",
    "orbeetle-gmax": "g-max-gravitas", "drednaw-gmax": "g-max-stonesurge",
    "coalossal-gmax": "g-max-volcalith", "flapple-gmax": "g-max-tartness",
    "appletun-gmax": "g-max-sweetness", "sandaconda-gmax": "g-max-sandblast",
    "toxtricity-amped-gmax": "g-max-stun-shock",
    "toxtricity-low-key-gmax": "g-max-stun-shock",
    "centiskorch-gmax": "g-max-centiferno", "hatterene-gmax": "g-max-smite",
    "grimmsnarl-gmax": "g-max-snooze", "alcremie-gmax": "g-max-finale",
    "copperajah-gmax": "g-max-steelsurge", "duraludon-gmax": "g-max-depletion",
    "urshifu-single-strike-gmax": "g-max-one-blow",
    "urshifu-rapid-strike-gmax": "g-max-rapid-flow",
}
_MOVE_TYPE_CACHE = {}   # move name -> type name, filled once from /type/


def _is_gmax(name):
    return (name or "").endswith("-gmax")


def _moves_from_data(data):
    """The learnset rows of one /pokemon/ record, lowest level first."""
    moves = []
    for m in (data or {}).get("moves", []):
        move_name = m["move"]["name"]
        for vgd in m.get("version_group_details", []):
            moves.append({
                "name": move_name,
                "version_group": _norm_vg(vgd["version_group"]["name"]),
                "level": vgd.get("level_learned_at", 0),
                "method": vgd["move_learn_method"]["name"],
            })
    moves.sort(key=lambda m: (m["level"], m["name"]))
    return moves


def _move_type_map():
    """{move name: type name} for every move, from the 18 /type/ records."""
    if _MOVE_TYPE_CACHE:
        return _MOVE_TYPE_CACHE
    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = list(pool.map(
            lambda t: (t, _get_json(POKEAPI_TYPE.format(t))),
            MAX_MOVE_BY_TYPE,
        ))
    if not all(isinstance(d, dict) for _, d in loaded):
        return {}   # try again next time rather than cache a partial map
    for type_name, d in loaded:
        for m in d.get("moves") or []:
            _MOVE_TYPE_CACHE[m["name"]] = type_name
    return _MOVE_TYPE_CACHE


def _gmax_moves(name, own_types, base_moves):
    """Max Moves (and the G-Max Move) for a Gigantamax form.

    A Max Move takes the type of the move it replaces, so the list holds the
    Max Move of every type the normal form learns a Sword / Shield move of,
    plus Max Guard (which replaces status moves).
    """
    row = lambda move, how: {
        "name": move, "version_group": "sword-shield",
        "level": 0, "method": how,
    }
    learned = {
        m["name"] for m in base_moves if m["version_group"] == "sword-shield"
    }
    type_of = _move_type_map()
    types = {type_of[n] for n in learned if n in type_of}
    if not types:   # type data unavailable: fall back to its own type(s)
        types = set(own_types)
    rows = []
    if name in GMAX_MOVES:
        rows.append(row(GMAX_MOVES[name], "G-Max move"))
    for t, move in MAX_MOVE_BY_TYPE.items():
        if t in types:
            rows.append(row(move, "Max move"))
    rows.append(row("max-guard", "Max move"))
    return rows


# PokeAPI is slowest to fill in Legends Z-A (and Champions) per form, so a
# form can have moves for every older game and none for those. Where that
# happens it borrows the rows of its species' other forms.
_NEW_MOVE_GAMES = ("legends-za", "champions")


def _borrow_missing_games(own, name, species):
    have = {m["version_group"] for m in own}
    missing = [g for g in _NEW_MOVE_GAMES if g not in have]
    if not missing:
        return own
    varieties = [
        (v.get("pokemon") or {}).get("name")
        for v in (species or {}).get("varieties") or []
    ]
    # the default form first, then the rest
    for v in (species or {}).get("varieties") or []:
        if v.get("is_default"):
            default = (v.get("pokemon") or {}).get("name")
            varieties.sort(key=lambda n: 0 if n == default else 1)
            break
    rows = list(own)
    for cand in varieties:
        if not missing:
            break
        if not cand or cand == name or _is_gmax(cand):
            continue
        other = _get_json(POKEAPI_POKEMON.format(quote(cand)))
        if not isinstance(other, dict):
            continue
        other_rows = _moves_from_data(other)
        for game in list(missing):
            found = [m for m in other_rows if m["version_group"] == game]
            if found:
                rows.extend(found)
                missing.remove(game)
    rows.sort(key=lambda m: (m["level"], m["name"]))
    return rows


def _moves_for_record_raw(data, species):
    """The moves to show for a /pokemon/ record.

    Normally just its own learnset. A Gigantamax form gets Max / G-Max Moves,
    and a form with no learnset of its own shows its base form's moves.
    """
    name = data.get("name") or ""
    own = _moves_from_data(data)
    gmax = _is_gmax(name)
    if own and not gmax:
        return _borrow_missing_games(own, name, species)
    candidates = []
    if gmax:
        candidates.append(name[:-len("-gmax")])
    for v in (species or {}).get("varieties") or []:
        if v.get("is_default"):
            candidates.append((v.get("pokemon") or {}).get("name"))
    base = []
    for cand in candidates:
        if not cand or cand == name:
            continue
        base_data = _get_json(POKEAPI_POKEMON.format(quote(cand)))
        if isinstance(base_data, dict):
            base = _moves_from_data(base_data)
            if base:
                break
    if gmax:
        types = _unique_types(data.get("types"))
        return _gmax_moves(name, types, base)
    return _borrow_missing_games(base, name, species) if base else base


def _moves_for_record(data, species):
    """_moves_for_record_raw plus the rows PokeAPI is missing
    (supplements.EXTRA_LEARNSETS)."""
    rows = _moves_for_record_raw(data, species)
    have = {(m["name"], m["version_group"]) for m in rows}
    pname = data.get("name") or ""
    # Hand-written rows plus the scraped ones (data/extra_learnsets.json:
    # {"pikachu": [[move, game, level, method], ...]}). A scraped row is
    # only used for a game where PokeAPI has no moves for this Pokemon at
    # all, so it can never contradict PokeAPI's own data.
    have_games = {m["version_group"] for m in rows}
    scraped = []
    # Scraped rows are filed under the species' default form, so a form
    # with moves of its own in PokeAPI (Mega Rayquaza has its ORAS ones)
    # still needs the default form's rows for a game it has none in.
    scrape_names = [pname]
    default_name = _default_variety(species)
    if default_name and default_name != pname and not _is_gmax(pname):
        scrape_names.append(default_name)
    scraped_rows = []
    covered = set()          # games a more specific form already has rows for
    for n in scrape_names:
        mine = [
            r for r in _load_extra("extra_learnsets.json").get(n) or []
            if r[1] not in covered
        ]
        covered |= {r[1] for r in mine}
        scraped_rows.extend(mine)
    for r in scraped_rows:
        try:
            move, game, level, method = r
        except (TypeError, ValueError):
            continue
        if game in have_games:
            continue
        scraped.append({
            "name": move, "version_group": game,
            "level": level or 0, "method": method,
        })
    extra = [
        dict(r) for r in
        list(supplements.EXTRA_LEARNSETS.get(pname, [])) + scraped
        if (r["name"], r["version_group"]) not in have
    ]
    if extra:
        rows = sorted(rows + extra, key=lambda m: (m["level"], m["name"]))
    return _mega_zygarde_moves(pname, species, rows)


# Mega Zygarde keeps Zygarde's Legends Z-A moveset, but its signature move
# Core Enforcer becomes Nihil Light, learned at the very same level.
_MOVE_SWAPS = {"zygarde-mega": {"core-enforcer": "nihil-light"}}
# Whose Z-A learnset a swapped form copies when the species record gives
# no default variety (PokeAPI down, or a bare test record).
_SWAP_BASES = {"zygarde-mega": "zygarde-50"}


def _scraped_rows(pokemon, game):
    """data/extra_learnsets.json rows of one Pokemon for one game."""
    out = []
    for r in (_load_extra("extra_learnsets.json").get(pokemon) or []):
        try:
            move, g, level, method = r
        except (TypeError, ValueError):
            continue
        if g == game:
            out.append({
                "name": move, "version_group": g,
                "level": level or 0, "method": method,
            })
    return out


def _mega_zygarde_moves(pname, species, rows):
    swaps = _MOVE_SWAPS.get(pname)
    if not swaps:
        return rows
    game = "legends-za"
    base_name = _default_variety(species) or _SWAP_BASES.get(pname)
    base_rows = []
    if base_name and base_name != pname:
        base_data = _get_pokemon_json(base_name)
        if isinstance(base_data, dict):
            base_rows = [
                m for m in _moves_for_record(base_data, species)
                if m["version_group"] == game
            ]
    # The base form's rows may be missing, or lack the move being replaced,
    # so fall back to the scraped Z-A learnset for exactly those moves.
    scraped = _scraped_rows(_SWAP_BASES.get(pname) or base_name, game)
    have = {m["name"] for m in base_rows}
    if not base_rows:
        base_rows = scraped
    else:
        base_rows = base_rows + [
            r for r in scraped
            if r["name"] in swaps and r["name"] not in have
        ]
    if not any(m["name"] in swaps for m in base_rows):
        return rows
    placeholder = set(swaps.values())
    kept = [
        m for m in rows
        if m["version_group"] == game and m["name"] not in placeholder
    ]
    if not [m for m in base_rows if m["name"] not in swaps]:
        # Only the replaced move is known (the base form could not be
        # loaded): keep the Z-A rows we already have around it.
        base_rows = base_rows + kept
    rows = [m for m in rows if m["version_group"] != game]
    swapped, seen = [], set()
    for m in base_rows:
        row = dict(m, name=swaps.get(m["name"], m["name"]))
        key = (row["name"], row["level"], row["method"])
        if key not in seen:
            seen.add(key)
            swapped.append(row)
    return sorted(rows + swapped, key=lambda m: (m["level"], m["name"]))


# ---- Egg, Mirror Herb and Sketch moves --------------------------------------
# Level column labels for the moves that are not learned by level or machine.
_METHOD_LABELS = {
    "egg": "Egg", "light-ball-egg": "Egg",
    "mirror-herb": "Mirror Herb", "sketch": "Sketch",
}
_MIRROR_HERB_FILE = "extra_mirror_herb.json"   # {pokemon: [move, ...]}
_SKETCH_FILE = "sketch_moves.json"             # optional {game: [move, ...]}

# Moves Smeargle's Sketch cannot copy (besides Sketch, Chatter and Struggle):
# Z-Moves, Max Moves and the Colosseum / XD Shadow moves.
_NO_SKETCH = {
    "sketch", "chatter", "struggle",
    "catastropika", "10-000-000-volt-thunderbolt", "stoked-sparksurfer",
    "extreme-evoboost", "genesis-supernova", "guardian-of-alola",
    "light-that-burns-the-sky", "malicious-moonsault",
    "menacing-moonraze-maelstrom", "lets-snuggle-forever",
    "clangorous-soulblaze", "oceanic-operetta", "pulverizing-pancake",
    "searing-sunraze-smash", "soul-stealing-7-star-strike",
    "splintered-stormshards", "sinister-arrow-raid",
    "shadow-blitz", "shadow-bolt", "shadow-break", "shadow-chill",
    "shadow-down", "shadow-end", "shadow-fire", "shadow-half",
    "shadow-hold", "shadow-mist", "shadow-panic", "shadow-rave",
    "shadow-rush", "shadow-shed", "shadow-sky", "shadow-storm",
    "shadow-wave",
}


def _sketchable_moves(game):
    """The moves Sketch can copy in one game. data/sketch_moves.json (when
    present) lists them exactly; otherwise every move introduced by that
    game's generation, which can include a few the game removed."""
    exact = _load_extra(_SKETCH_FILE).get(game)
    if exact:
        return list(exact)
    gen = GAME_GENERATION.get(game, 9)
    if game == "brilliant-diamond-shining-pearl":
        gen = 4   # the remakes only have moves up to Generation IV
    out = []
    for name, meta in _load_extra("filters_move.json").items():
        if (name in _NO_SKETCH or "--" in name or name.startswith("max-")
                or name.startswith("g-max-")):
            continue
        if (meta.get("g") or 1) <= gen:
            out.append(name)
    return out


def _add_special_moves(rows, pname, species):
    """Rows PokeAPI does not have: the Scarlet / Violet moves that need a
    Mirror Herb (scraped, see _fill_mirror_herb) and everything Smeargle can
    Sketch."""
    rows = list(rows or [])
    default = _default_variety(species) or pname
    herb = set(_load_extra(_MIRROR_HERB_FILE).get(default) or [])
    if herb:
        rows = [
            m for m in rows
            if not (m["version_group"] == "scarlet-violet"
                    and m["name"] in herb and m["method"] == "egg")
        ]
        have = {(m["name"], m["version_group"]) for m in rows}
        rows += [
            {"name": mv, "version_group": "scarlet-violet", "level": 0,
             "method": "mirror-herb"}
            for mv in sorted(herb) if (mv, "scarlet-violet") not in have
        ]
    if pname == "smeargle":
        games = {m["version_group"] for m in rows if m["name"] == "sketch"}
        have = {(m["name"], m["version_group"]) for m in rows}
        for game in sorted(games):
            rows += [
                {"name": mv, "version_group": game, "level": 0,
                 "method": "sketch"}
                for mv in _sketchable_moves(game) if (mv, game) not in have
            ]
    return rows


def _moves_to_show(data, species):
    """The finished move list of a /pokemon/ record for the tables."""
    rows = _add_special_moves(
        _moves_for_record(data, species), data.get("name") or "", species)
    return _finish_moves(rows)


def _fill_mirror_herb(species, data):
    """Read which Scarlet / Violet moves of this Pokémon need a Mirror Herb
    from Bulbapedia, once (data/extra_mirror_herb.json). Never raises."""
    global _scrape_backoff_until
    name = (species or {}).get("name")
    if not (LAZY_SCRAPE and name and isinstance(data, dict)):
        return
    default = _default_variety(species) or name
    if default in _load_extra(_MIRROR_HERB_FILE):
        return
    if time.time() < _scrape_backoff_until or not _in_game(
            species, "scarlet-violet"):
        return
    try:
        with _SCRAPE_LOCK:
            known = dict(_load_extra(_MIRROR_HERB_FILE))
            if default in known:
                return
            fetcher = bulbapedia.Fetcher(
                _DATA_DIR / ".bulbapedia_cache", delay=0.5, timeout=8)
            title = bulbapedia.pokemon_title(_english_name(species, name))
            result = bulbapedia.scrape_page(
                fetcher, title, "scarlet-violet", dex=False, moves=True)
            if result is None:
                if fetcher.failed:
                    _scrape_backoff_until = time.time() + SCRAPE_BACKOFF_SECONDS
                return
            known[default] = sorted(
                r[0] for r in result["moves"] if r[3] == "mirror-herb")
            _write_extra(_MIRROR_HERB_FILE, known)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Could not read Mirror Herb moves for %s: %s", name, exc)
        _scrape_backoff_until = time.time() + SCRAPE_BACKOFF_SECONDS


# ---- TM / TR labels for the Level column of a Pokémon's moves --------------
# PokeAPI only says a move is learned by "machine". Sword / Shield is the one
# game with both TMs and TRs, so its TR moves are looked up once from PokeAPI
# (/item/tr00 ... /item/tr99 -> machine -> move) and kept in data/tr_moves.json.
_TR_FILE = Path(__file__).resolve().parent / "data" / "tr_moves.json"
_TR_MOVES = {}
_TR_LAST_TRY = [0.0]


def _fetch_one_tr(i):
    item = _get_json(POKEAPI_ITEM.format("tr%02d" % i))
    if not isinstance(item, dict):
        return None
    for m in item.get("machines") or []:
        if (m.get("version_group") or {}).get("name") != "sword-shield":
            continue
        rec = _get_json((m.get("machine") or {}).get("url") or "")
        move = ((rec or {}).get("move") or {}).get("name")
        if move:
            return move
    return None


def _sword_shield_trs():
    """{move name: \"TR10\"} for the Technical Records of Sword / Shield, or
    {} when they cannot be loaded (those moves then just say TM)."""
    if _TR_MOVES:
        return _TR_MOVES
    try:
        with open(_TR_FILE, encoding="utf-8") as f:
            _TR_MOVES.update(json.load(f))
    except (OSError, ValueError):
        pass
    if _TR_MOVES or time.time() - _TR_LAST_TRY[0] < 600:
        return _TR_MOVES
    _TR_LAST_TRY[0] = time.time()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            moves = list(pool.map(_fetch_one_tr, range(100)))
    except Exception:
        return _TR_MOVES
    found = {m: "TR%02d" % i for i, m in enumerate(moves) if m}
    if len(found) < 90:   # PokeAPI had a hiccup: do not keep half a list
        return _TR_MOVES
    _TR_MOVES.update(found)
    try:
        with open(_TR_FILE, "w", encoding="utf-8") as f:
            json.dump(_TR_MOVES, f, indent=0, sort_keys=True)
    except OSError:
        pass   # still kept in memory
    return _TR_MOVES


def _other_label(method):
    """Level column text for a move that is none of the usual kinds (Tutor,
    Form change, Max move, Mega evolution...), so it never just says 0."""
    method = str(method or "").strip()
    if not method:
        return "Other"
    if method != method.lower() or " " in method:
        return method   # already written for people, e.g. "G-Max move"
    return method.replace("-", " ").capitalize()


def _finish_moves(rows):
    """The moves of a Pokémon ready to show: each row gets `level_label`
    (the level it is learned at, or TM / TR / Egg / Sketch / Mirror Herb).

    Order: level-up moves (lowest level first), then TM, TR, Egg, Sketch and
    Mirror Herb moves, then anything else (Tutor, Max moves...) last.
    """
    trs = None
    levelled, other = [], []
    kinds = {k: [] for k in ("TM", "TR", "Egg", "Sketch", "Mirror Herb")}
    for m in rows or []:
        row = dict(m)
        method = row.get("method")
        if method == "machine":
            if trs is None:
                trs = (_sword_shield_trs()
                       if any(r.get("method") == "machine"
                              and r.get("version_group") == "sword-shield"
                              for r in rows) else {})
            is_tr = (row.get("version_group") == "sword-shield"
                     and row["name"] in trs)
            row["level_label"] = "TR" if is_tr else "TM"
            kinds[row["level_label"]].append(row)
        elif method in _METHOD_LABELS:
            row["level_label"] = _METHOD_LABELS[method]
            kinds[row["level_label"]].append(row)
        elif not method or method == "level-up":
            row["level_label"] = str(row.get("level", 0))
            levelled.append(row)
        else:
            row["level_label"] = _other_label(method)
            other.append(row)
    key = lambda m: (m["name"], m["version_group"])
    out = sorted(levelled, key=lambda m: (m.get("level", 0), m["name"]))
    for label in ("TM", "TR", "Egg", "Sketch", "Mirror Herb"):
        out += sorted(kinds[label], key=key)
    return out + other


def fetch_form_moves(name):
    """Moves of one form (None when the Pokemon is unknown)."""
    data = _get_pokemon_json(name)
    if not data:
        return None
    species = _fetch_species((data.get("species") or {}).get("name"))
    return _moves_to_show(data, species)


# Species whose forms are only a different look (Vivillon's 20 patterns,
# Furfrou's 10 trims). PokeAPI has no /pokemon/ record per form for them, just
# a /pokemon-form/ record with the sprites, so the form buttons are built from
# the Pokémon's own list of forms; stats and moves are the Pokémon's own.
_COSMETIC_FORM_SPECIES = {"vivillon", "furfrou"}
_COSMETIC_DEFAULT_LABEL = {"vivillon": "Meadow", "furfrou": "Natural"}
_MAX_COSMETIC_FORMS = 30   # Alcremie's 63 would be 63 requests per page
_COSMETIC_LABELS = {"poke-ball": "Poké Ball", "la-reine": "La Reine"}


_COSMETIC_RECORDS = {}     # /pokemon-form/ url -> its record (never changes)


def _cosmetic_record(url):
    rec = _COSMETIC_RECORDS.get(url)
    if rec is None:
        rec = _get_json(url)
        if isinstance(rec, dict):
            _COSMETIC_RECORDS[url] = rec
    return rec if isinstance(rec, dict) else None


def _cosmetic_forms(species_name, data):
    """[(form name, button label, its /pokemon-form/ record)] for a Pokémon
    whose forms are only a different look (Vivillon, Furfrou, Unown,
    Burmy...): the base form first, then the rest A-Z. [] for one with a
    single form."""
    refs = [f for f in data.get("forms") or []
            if isinstance(f, dict) and f.get("name") and f.get("url")]
    if len(refs) < 2 or len(refs) > _MAX_COSMETIC_FORMS:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda f: _cosmetic_record(f["url"]), refs))
    base, rest = None, []
    for i, (ref, rec) in enumerate(zip(refs, records)):
        if not rec or not rec.get("sprites"):
            continue
        name = ref["name"]
        if i == 0:      # PokeAPI lists the base form first
            label = _COSMETIC_DEFAULT_LABEL.get(species_name) or _pretty(
                rec.get("form_name") or "") or "Normal"
            base = (name, label, rec)
        else:
            key = name[len(species_name) + 1:]
            label = _COSMETIC_LABELS.get(key) or _form_label(
                name, species_name)
            rest.append((name, label, rec))
    rest.sort(key=lambda t: t[1].casefold())
    return ([base] if base else []) + rest


def _fetch_cosmetic_forms(species_name, species, data):
    """The Form buttons of a look-only Pokémon. Stats, types, abilities and
    games are the Pokémon's own, built once; each form only swaps in its own
    sprites (no extra requests per form: Unown has 28)."""
    forms = _cosmetic_forms(species_name, data)
    if len(forms) < 2:
        return []
    current = data.get("name")
    base_name = forms[0][0]
    both = _both_genders(species)
    template = _form_entry(
        base_name, species_name, data, current, _extra_games(species),
        _default_variety(species), both)
    pretty = _pretty_name(species_name, species_name)
    entries = []
    for name, label, rec in forms:
        raw = rec.get("sprites") or {}
        entry = dict(template)
        entry.update(
            name=name, label=label, current=name == current,
            display_name=pretty if name == base_name
            else "%s %s" % (pretty, label),
        )
        if name != base_name or not (
                template["sprite"] or template["sprites_by_gen"]):
            front = raw.get("front_default") or ""
            entry.update(
                icon=front, icon_alt="",
                sprite=_latest_sprite(raw) or front,
                shiny_sprite=_latest_sprite(raw, "front_shiny") or "",
                sprites_by_gen=_sprites_by_gen(raw, merge_aliases=True),
                shiny_sprites_by_gen=_sprites_by_gen(
                    raw, "front_shiny", merge_aliases=True),
                **_female_sprites(raw, both),
            )
        entries.append(entry)
    entries = [e for e in entries if e["sprite"] or e["sprites_by_gen"]]
    return entries if len(entries) >= 2 else []


def _cosmetic_sprite_list(rec):
    """_sprite_list for one look, without probing for extra sprite files."""
    raw = rec.get("sprites") or {}
    normal = _sprites_by_gen(raw)
    shiny = _sprites_by_gen(raw, "front_shiny")
    _drop_bw_sprites(rec, normal, shiny)
    return [
        {"game": g, "url": normal[g], "shiny": shiny.get(g, "")}
        for g in sorted(normal, key=_sprite_game_rank)
    ]


# Species whose only forms are Male / Female. The gender picks the form, so
# they get no Form buttons (on the Details or the Sprites tab).
_GENDER_FORM_SPECIES = {"indeedee", "basculegion"}


def _form_names_with_current(species_name, species, data,
                             hide_regional=False):
    current = data.get("name")
    if species_name in _GENDER_FORM_SPECIES:
        return current, [current] if current else []
    names = _form_names(species_name, species)
    if current and current not in names:
        names.insert(0, current)  # e.g. someone searched "charizard-mega-x"
    if hide_regional:
        # regional forms, Megas and Gigantamax are in the evolution line,
        # not in the Form buttons
        names = [n for n in names if not _in_evolution_line(n)]
    # forms are listed alphabetically by the label on their button
    names.sort(key=lambda n: _display_label(n, species_name).casefold())
    # the base form is the first button (Zygarde keeps its 10% / 50% order)
    base = _default_variety(species)
    if species_name != "zygarde" and base in names:
        names.remove(base)
        names.insert(0, base)
    return current, names


def _load_form_data(names, current, data):
    """{form name: its /pokemon/ record (None if it could not be loaded)}."""
    def load(n):
        if n == current:
            return n, data
        return n, _get_json(POKEAPI_POKEMON.format(quote(n)))

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(load, names))


def _fetch_forms(species_name, species, data):
    """Sprite data for each form of this Pokemon ([] when it has just one)."""
    if _in_evolution_line(data.get("name")):
        return []   # a regional / Mega / Gigantamax page has no Form buttons
    if species_name in _COSMETIC_FORM_SPECIES:
        return _fetch_cosmetic_forms(species_name, species, data)
    current, names = _form_names_with_current(
        species_name, species, data, hide_regional=True)
    if len(names) < 2:
        if species_name in _GENDER_FORM_SPECIES:
            return []
        return _fetch_cosmetic_forms(species_name, species, data)
    loaded = _load_form_data(names, current, data)
    extra = _extra_games(species)
    valid = [n for n in names if isinstance(loaded.get(n), dict)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(
            lambda n: _form_entry(
                n, species_name, loaded[n], current, extra,
                _default_variety(species), _both_genders(species),
            ),
            valid,
        ))
    forms = [e for e in entries if e["sprite"] or e["sprites_by_gen"]]
    return forms if len(forms) >= 2 else []


def fetch_pokemon(name):
    data = _get_pokemon_json(name)
    if not data:
        return None

    stats = [
        {"name": s["stat"]["name"], "label": _stat_label(s["stat"]["name"]),
         "value": s["base_stat"]}
        for s in data.get("stats", [])
    ]
    types = _unique_types(data.get("types"))

    abilities = _abilities_for(data)

    raw_sprites = data.get("sprites", {})
    sprites_by_gen = _sprites_by_gen(raw_sprites, merge_aliases=True)
    shiny_by_gen = _sprites_by_gen(
        raw_sprites, "front_shiny", merge_aliases=True
    )

    # Only offer the games this Pokemon is actually in. Move data is the
    # most reliable signal; fall back to sprites if it has none.
    species_name = (data.get("species") or {}).get("name")
    species = _fetch_species(species_name)
    _fill_gaps(species, data)
    _fill_mirror_herb(species, data)
    moves = _moves_to_show(data, species)
    present = {m["version_group"] for m in moves} or set(sprites_by_gen)
    present |= _extra_games(species)
    # PokeAPI's JSON often omits Z-A sprites, so look in the sprite repo.
    if "legends-za" not in sprites_by_gen:
        _add_za_fallback(data.get("id"), sprites_by_gen, shiny_by_gen)
    if "legends-za" in sprites_by_gen:
        present.add("legends-za")
    present = _limit_form_games(data.get("name"), present)
    games = [(vg, label) for vg, label in VERSION_GROUPS if vg in present]
    if "scarlet-violet" in present:
        _add_home_fallback(data.get("id"), sprites_by_gen, shiny_by_gen)

    if "champions" in present:
        _add_champions_fallback(
            data.get("id"), sprites_by_gen, shiny_by_gen
        )
    national_id = None
    for p in species.get("pokedex_numbers") or []:
        if (p.get("pokedex") or {}).get("name") == "national":
            national_id = p.get("entry_number")
            break
    chain = _fetch_chain(species)
    evolution = _evolution_edges(chain, species_name)
    try:
        evolution_tree = _evolution_tree(chain, species_name, data.get("name"))
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not build the evolution line for %s", species_name)
        evolution_tree = None
    return {
        **_female_sprites(raw_sprites, _both_genders(species)),
        "id": data.get("id"),
        "dex_number": national_id or data.get("id"),
        "display_name": _title_for(
            data.get("name"), species_name, _default_variety(species)
        ),
        "name": data.get("name"),
        "species": species_name or data.get("name"),
        "forms": _fetch_forms(species_name, species, data)
        if species_name else [],
        "sprite": _sprite_or_flat_art(raw_sprites, data.get("id")),
        "shiny_sprite": _sprite_or_flat_art(
            raw_sprites, data.get("id"), "front_shiny"),
        "sprites_by_gen": sprites_by_gen,
        "shiny_sprites_by_gen": shiny_by_gen,
        "games": games,
        "types": types,
        "stats": stats,
        "stat_total": sum(st["value"] or 0 for st in stats),
        "moves": moves,
        "abilities": abilities,
        "types_history": _types_history(data),
        "stats_history": _stats_history(data),
        "abilities_history": _abilities_history(data),
        "species_facts": _species_facts(species),
        "dex_by_game": _dex_by_game(species),
        "evolution": evolution,
        "evolution_family": _evolution_family(evolution, species_name),
        "evolution_tree": evolution_tree,
        **_form_facts(data),
    }


_BDSP_MOVE_GENS = {
    "generation-i", "generation-ii", "generation-iii", "generation-iv",
}
_machine_cache = {}


def _machine_info(url):
    """What one PokeAPI machine record tells us about a TM / HM / TR:
    {"label": "TM24", "cost": 3000} (label "" if it is not a machine)."""
    if url in _machine_cache:
        return _machine_cache[url]
    empty = {"label": "", "cost": None}
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        item = r.json().get("item") or {}
        name = (item.get("name") or "").lower()
    except Exception:
        return empty
    if not (name[:2] in ("tm", "hm", "tr") and name[2:].isdigit()):
        _machine_cache[url] = empty
        return empty
    info = {"label": name.upper(), "cost": None}
    # The item record holds the shop price.
    try:
        ir = requests.get(item.get("url"), timeout=TIMEOUT)
        ir.raise_for_status()
        cost = ir.json().get("cost")
        info["cost"] = cost if cost else None
    except Exception:
        pass
    _machine_cache[url] = info
    return info


# Where each TM is found, per game: data/tm_locations.json, shaped like
# {"games": {"scarlet-violet": {"TM001": "text"}}}. PokeAPI has the TM
# numbers and prices but not their locations, so this file is the source.
_TM_LOCATIONS_FILE = (
    Path(__file__).resolve().parent / "data" / "tm_locations.json"
)
_tm_locations = None


def _tm_location(game, label):
    global _tm_locations
    if _tm_locations is None:
        try:
            with open(_TM_LOCATIONS_FILE, encoding="utf-8") as f:
                _tm_locations = json.load(f).get("games") or {}
        except (OSError, ValueError):
            _tm_locations = {}
    return (_tm_locations.get(game) or {}).get(label, "")


# Where a machine is found and what it costs comes from its Bulbapedia page
# (TM024, HM01...), cached on disk like every other page Bulbapedia is asked
# for. data/tm_locations.json, when it has a note for a TM, wins over it.
_TM_ROWS_CACHE = {}
_tm_backoff_until = 0.0


def _tm_rows(label):
    """parse_tm_page() rows for a machine label, or None when the page
    could not be read right now."""
    global _tm_backoff_until
    title = bulbapedia.tm_title(label)
    if not title:
        return []
    if title in _TM_ROWS_CACHE:
        return _TM_ROWS_CACHE[title]
    if time.time() < _tm_backoff_until:
        return None
    try:
        fetcher = bulbapedia.Fetcher(
            _DATA_DIR / ".bulbapedia_cache", delay=0.2, timeout=8
        )
        rows = bulbapedia.scrape_tm(fetcher, label)
        if rows is None:
            if fetcher.failed:                # network trouble: retry later
                _tm_backoff_until = time.time() + 60
                return None
            rows = []                         # Bulbapedia has no such page
    except Exception as exc:
        # e.g. beautifulsoup4 not installed: show it in the server console
        logging.getLogger(__name__).warning(
            "Could not read %s from Bulbapedia: %s", label, exc
        )
        _tm_backoff_until = time.time() + 60
        return None
    _TM_ROWS_CACHE[title] = rows
    return rows


def _tm_where_info(rows, game, move_name):
    """{"lines": [...], "price": str, "sell": str} for one game from a
    machine's rows; empty values when Bulbapedia has nothing for it."""
    row = bulbapedia.pick_tm_row(rows or [], game, move_name)
    if not row:
        return {"lines": [], "price": "", "sell": ""}
    return {"lines": row["where"], "price": row["cost"], "sell": row["sell"]}


# ---- type / category / power / accuracy of many moves at once --------------
# Looked up one move at a time from PokeAPI, so the answers are kept in
# memory and in data/move_meta.json: each move is only ever fetched once.
_MOVE_META_FILE = (
    Path(__file__).resolve().parent / "data" / "move_meta.json"
)
_move_meta = None
_MOVE_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _load_move_meta():
    global _move_meta
    if _move_meta is None:
        try:
            with open(_MOVE_META_FILE, encoding="utf-8") as f:
                _move_meta = json.load(f)
        except (OSError, ValueError):
            _move_meta = {}
    return _move_meta


def _fetch_one_move_meta(name):
    data = _get_json(POKEAPI_MOVE.format(quote(name)))
    if not isinstance(data, dict):
        extra = supplements.EXTRA_MOVES.get(name)
        if not extra:
            return None
        return {
            "type": extra["type"], "category": extra["damage_class"],
            "power": extra["power"], "accuracy": extra["accuracy"],
            "priority": extra.get("priority", 0),
        }
    return {
        "type": (data.get("type") or {}).get("name", ""),
        "category": (data.get("damage_class") or {}).get("name", ""),
        "power": data.get("power"),
        "accuracy": data.get("accuracy"),
        "priority": data.get("priority", 0),
    }


def fetch_move_meta(names):
    """{move name: {"type", "category", "power", "accuracy", "priority"}}
    for the moves that exist (unknown names are left out)."""
    cache = _load_move_meta()
    wanted = []
    for n in names:
        n = (n or "").strip().lower()
        if n and set(n) <= _MOVE_NAME_OK and n not in wanted:
            wanted.append(n)
    # entries cached before priority was added are looked up again once
    missing = [
        n for n in wanted
        if n not in cache or "priority" not in cache[n]
    ]
    if missing:
        with ThreadPoolExecutor(max_workers=12) as pool:
            found = list(pool.map(_fetch_one_move_meta, missing))
        added = False
        for n, m in zip(missing, found):
            if m:
                cache[n] = m
                added = True
        if added:
            try:
                with open(_MOVE_META_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f)
            except OSError:
                pass     # still cached in memory
    return {n: cache[n] for n in wanted if n in cache}


def _mon_id(url):
    tail = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _move_learners(rows, move_name):
    """Pokémon that learn a move, as {id, name, sprite} sorted by Pokédex
    number: PokeAPI's list plus the ones only in supplements."""
    seen = {}
    for p in rows or []:
        pid = _mon_id(p.get("url"))
        if pid and p.get("name"):
            seen[p["name"]] = pid
    for mon, extra in supplements.EXTRA_LEARNSETS.items():
        if mon not in seen and any(r["name"] == move_name for r in extra):
            data = _get_json(POKEAPI_POKEMON.format(quote(mon)))
            if isinstance(data, dict) and data.get("id"):
                seen[mon] = data["id"]
    mons = _add_types([
        {"id": pid, "name": n, "sprite": SPRITE_URL.format(pid)}
        for n, pid in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))
    ])
    # Dex order, so a Mega sits right after its base Pokemon
    mons.sort(key=lambda m: (m["dex"], m["id"]))
    return mons


# ---- who can use a Z-Move / Max Move ----------------------------------------
# PokeAPI has no learner list for these. A signature Z-Move belongs to a few
# Pokémon (supplements.Z_MOVE_LEARNERS); a type's Z-Move or Max Move can be
# used by any Pokémon in the games that has a damaging move of that type; Max
# Guard by every Pokémon that can Dynamax.
_MAX_TYPE_BY_MOVE = {m: t for t, m in MAX_MOVE_BY_TYPE.items()}
_NO_DYNAMAX = {"eternatus", "zacian", "zamazenta"}   # cannot Dynamax
_SPECIAL_LEARNER_CACHE = {}
_Z_MOVE_GAME_KEYS = ("sun-moon", "ultra-sun-ultra-moon")


def _named_learners(names, move_name):
    """{id, name, sprite} for Pokémon given by PokeAPI name, Dex order."""
    def load(n):
        d = _get_json(POKEAPI_POKEMON.format(quote(n)))
        if isinstance(d, dict) and d.get("id"):
            return {"name": n, "url": POKEAPI_POKEMON.format(d["id"])}
        return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [r for r in pool.map(load, names) if r]
    return _move_learners(rows, move_name)


def _type_move_learner_rows(type_name, max_gen, games):
    """PokeAPI learner rows ({name, url}) of the Pokémon in `games` that
    learn at least one damaging move of that type (up to generation
    `max_gen`)."""
    type_of = _move_type_map()
    meta = _load_extra("filters_move.json")
    names = [
        n for n, t in type_of.items()
        if t == type_name and n in meta and "--" not in n
        and not n.startswith(("max-", "g-max-"))
        and (meta[n].get("g") or 99) <= max_gen
        and meta[n].get("c") != "status"
    ]
    species = set()
    for g in games:
        species |= _game_species(g)
    dex = _species_dex_map()

    def load(n):
        d = _get_json(POKEAPI_MOVE.format(quote(n)))
        return (d or {}).get("learned_by_pokemon") or []

    with ThreadPoolExecutor(max_workers=8) as pool:
        lists = list(pool.map(load, names))
    rows = {}
    for lst in lists:
        for p in lst:
            nm = p.get("name") or ""
            sp = _species_of(nm, dex)
            if (sp in species and sp not in _NO_DYNAMAX
                    and any(_form_in_game(nm, g) for g in games)):
                rows[nm] = p
    return list(rows.values())


def _special_move_learners(name, type_name):
    """Learners of a Z-Move / Max Move that PokeAPI lists none for ([] for
    any other move)."""
    hit = _SPECIAL_LEARNER_CACHE.get(name)
    if hit is not None:
        return hit
    out = []
    if name in supplements.Z_MOVE_LEARNERS:
        out = _named_learners(supplements.Z_MOVE_LEARNERS[name], name)
    elif name in supplements.Z_MOVE_GAMES:
        out = _move_learners(_type_move_learner_rows(
            type_name, 7, _Z_MOVE_GAME_KEYS), name)
    elif name in _MAX_TYPE_BY_MOVE:
        out = _move_learners(_type_move_learner_rows(
            _MAX_TYPE_BY_MOVE[name], 8, ("sword-shield",)), name)
    elif name == "max-guard":
        dex = _species_dex_map()
        rows = [
            {"name": s, "url": POKEAPI_POKEMON.format(dex[s])}
            for s in _game_species("sword-shield")
            if s in dex and s not in _NO_DYNAMAX
        ]
        out = _move_learners(rows, name)
    if out:
        _SPECIAL_LEARNER_CACHE[name] = out
    return out


def _supplement_move(name):
    """A move PokeAPI does not have, built from supplements.EXTRA_MOVES."""
    extra = supplements.EXTRA_MOVES.get(name)
    if not extra:
        return None
    labels = dict(MOVE_GAMES)
    games = [
        {
            "key": g, "label": labels.get(g, g), "type": extra["type"],
            "power": extra["power"], "accuracy": extra["accuracy"],
            "pp": extra["pp"], "tm": "", "tm_cost": None, "tm_where": "",
            "tm_price": "", "tm_sell": "",
        }
        for g in extra["games"]
    ]
    learners = []
    for mon in extra.get("learned_by", []):
        data = _get_json(POKEAPI_POKEMON.format(quote(mon)))
        if isinstance(data, dict) and data.get("id"):
            learners.append({
                "id": data["id"], "name": mon,
                "sprite": SPRITE_URL.format(data["id"]),
            })
    _add_types(learners)
    return {
        "id": extra["id"], "name": extra["name"],
        "learned_by": learners,
        "type": extra["type"], "power": extra["power"],
        "accuracy": extra["accuracy"], "pp": extra["pp"],
        "damage_class": extra["damage_class"], "effect": extra["effect"],
        "priority": extra.get("priority", 0),
        "games": games, "tm_table": [],
    }



def fetch_move(name):
    # "ice beam" / "Ice Beam" -> "ice-beam"
    clean = name.strip().lower().replace(" ", "-")
    try:
        r = requests.get(
            POKEAPI_MOVE.format(quote(clean)), timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        # a move PokeAPI does not have yet (Nihil Light...)
        return _supplement_move(clean)

    # Newer moves (Fickle Beam, Tera Starstorm...) have "meta": null and
    # sometimes no effect_entries, so every nested field is read defensively.
    chance = data.get("effect_chance")
    effect = _en_text(data.get("effect_entries"), "effect")
    if not effect:
        effect = _en_text(data.get("effect_entries"), "short_effect")
    if not effect:
        flavor = [
            f for f in data.get("flavor_text_entries") or []
            if (f.get("language") or {}).get("name") == "en"
        ]
        if flavor:
            effect = flavor[-1].get("flavor_text", "")
            effect = " ".join(effect.split())
    if effect and chance is not None:
        effect = effect.replace("$effect_chance", str(chance))

    base = {
        "type": (data.get("type") or {}).get("name", ""),
        "power": data.get("power"),
        "accuracy": data.get("accuracy"),
        "pp": data.get("pp"),
    }

    # Which games is this move in? PokeAPI's flavor text alone misses games
    # (Thunderbolt has none for BDSP or Z-A), so TM/HM records count too.
    keys = set()
    for f in data.get("flavor_text_entries") or []:
        vg = _norm_vg((f.get("version_group") or {}).get("name", ""))
        if vg in _GAME_LABELS:
            keys.add(vg)
    machine_urls = {}
    for m in data.get("machines") or []:
        vg = _norm_vg((m.get("version_group") or {}).get("name", ""))
        url = (m.get("machine") or {}).get("url")
        if vg in _GAME_LABELS and url:
            machine_urls.setdefault(vg, url)
    keys |= set(machine_urls)
    # Brilliant Diamond / Shining Pearl have every Gen 1-4 move.
    if (data.get("generation") or {}).get("name") in _BDSP_MOVE_GENS:
        keys.add("brilliant-diamond-shining-pearl")
    tm_by_game = {}
    tm_rows = {}
    if machine_urls:
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(_machine_info, machine_urls.values()))
        tm_by_game = {
            vg: info for vg, info in zip(machine_urls, infos)
            if info["label"]
        }
        # one Bulbapedia page per distinct machine (they are cached)
        labels = sorted({i["label"] for i in tm_by_game.values()})
        if labels:
            with ThreadPoolExecutor(max_workers=4) as pool:
                tm_rows = dict(zip(labels, pool.map(_tm_rows, labels)))
    # Pokemon Champions has no TMs and PokeAPI has no per-move data for it
    # yet, so a move counts as being in Champions when PokeAPI says so
    # (above) or when at least one Pokemon can learn it.
    if "champions" not in keys and data.get("learned_by_pokemon"):
        keys.add("champions")
    if not keys:
        gen = (data.get("generation") or {}).get("name", "")
        for vg in GEN_SET_MAP.get(gen, []):
            vg = _norm_vg(vg)
            if vg in _GAME_LABELS:
                keys.add(vg)
    # A Z-Move is only in the games that have Z-Moves (not Let's Go or
    # Sword / Shield, whatever PokeAPI's flavour text says).
    z_games = supplements.Z_MOVE_GAMES.get(data.get("name"))
    if z_games:
        keys = set(z_games)

    # past_values: stats the move had up to and including `version_group`.
    past = []
    for pv in data.get("past_values") or []:
        vg = _norm_vg((pv.get("version_group") or {}).get("name", ""))
        if vg not in _GAME_ORDER:
            continue
        past.append({
            "order": _GAME_ORDER.index(vg),
            "power": pv.get("power"),
            "accuracy": pv.get("accuracy"),
            "pp": pv.get("pp"),
            "type": (pv.get("type") or {}).get("name"),
        })
    past.sort(key=lambda p: p["order"])

    games = []
    for key, label in MOVE_GAMES:
        if key not in keys:
            continue
        stats = dict(base)
        seen = set()
        for pv in past:
            if pv["order"] < _GAME_ORDER.index(key):
                continue
            for field in ("power", "accuracy", "pp", "type"):
                if field not in seen and pv[field] is not None:
                    stats[field] = pv[field]
                    seen.add(field)
        tm = tm_by_game.get(key) or {}
        found = (
            _tm_where_info(tm_rows.get(tm["label"]), key, data.get("name"))
            if tm else {"lines": [], "price": "", "sell": ""}
        )
        note = _tm_location(key, tm["label"]) if tm else ""
        stats.update({
            "key": key, "label": label, "tm": tm.get("label", ""),
            "tm_cost": tm.get("cost"),
            # one location note per line; a hand-written note wins
            "tm_where": note or "\n".join(found["lines"]),
            "tm_price": found["price"],
            "tm_sell": found["sell"],
        })
        # Fixes for games PokeAPI has not updated (Champions balance...)
        stats.update(
            supplements.MOVE_GAME_OVERRIDES
            .get(data.get("name"), {}).get(key, {})
        )
        games.append(stats)

    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "learned_by": _move_learners(
            data.get("learned_by_pokemon"), data.get("name")
        ) or _special_move_learners(data.get("name"), base["type"]),
        "type": base["type"],
        "power": base["power"],
        "accuracy": base["accuracy"],
        "pp": base["pp"],
        # physical / special / status lives at the top level, not in "meta"
        "damage_class": (data.get("damage_class") or {}).get("name", ""),
        "priority": data.get("priority", 0),
        "effect": effect,
        "games": games,
        # every game that teaches this move by TM / HM / TR
        "tm_table": [
            {"game": g["label"], "tm": g["tm"], "cost": g["tm_cost"],
             "price": g.get("tm_price", "")}
            for g in games if g["tm"]
        ],
    }


def fetch_ability(name):
    return _fetch_ability(name)


# ---- which games an ability (and each Pokemon with it) is in ---------------
# Games with no abilities at all.
ABILITY_NO_GAMES = {
    "legends-za", "legends-arceus", "lets-go-pikachu-lets-go-eevee",
}
# Abilities that are still real abilities in Legends Z-A, and the species
# that have them there. These get a Legends Z-A tab on the Abilities page
# (with just those Pokemon); every other ability has no Z-A tab. The other
# Z-A passives (Sacred Sword, Volt Tackle) are not abilities and only show
# on the Pokemon details page.
ZA_ABILITY_SPECIES = {
    "stance-change": {"aegislash"},
    "disguise": {"mimikyu"},
    "hunger-switch": {"morpeko"},
    "commander": {"tatsugiri", "dondozo"},
    "power-construct": {"zygarde"},
}
_POKEDEX_NAMES = []        # every PokeAPI pokedex name, read once
_DEX_SPECIES = {}          # pokedex name -> {species names}
# Forms that only exist from this generation on.
_REGIONAL_FORM_MIN_GEN = {"alola": 7, "galar": 8, "hisui": 9, "paldea": 9}


def _all_pokedex_names():
    if _POKEDEX_NAMES:
        return _POKEDEX_NAMES
    data = _get_json(POKEAPI_POKEDEX.format("") + "?limit=200")
    if isinstance(data, dict):
        _POKEDEX_NAMES.extend(
            d["name"] for d in data.get("results") or [] if d.get("name")
        )
    return _POKEDEX_NAMES


def _dex_species(dex_name):
    """Species names listed in one pokedex (None if it cannot be loaded)."""
    if dex_name in _DEX_SPECIES:
        return _DEX_SPECIES[dex_name]
    data = _get_json(POKEAPI_POKEDEX.format(quote(dex_name)))
    if not isinstance(data, dict):
        return None
    found = {
        (e.get("pokemon_species") or {}).get("name")
        for e in data.get("pokemon_entries") or []
    }
    found.discard(None)
    _DEX_SPECIES[dex_name] = found
    return found


def _game_species(game):
    """Species that are in a game, from the pokedexes the game uses (empty
    when PokeAPI has no pokedex for it)."""
    names = [n for n in _all_pokedex_names() if _dex_matches(game, n)]
    species = set()
    for n in names:
        species |= _dex_species(n) or set()
    return species


def _species_of(name, species_dex):
    """Species name of a Pokemon form ("charizard-mega-x" -> "charizard")."""
    parts = (name or "").split("-")
    while parts:
        cand = "-".join(parts)
        if cand in species_dex:
            return cand
        parts.pop()
    return name


def _form_in_game(name, game):
    """False when this form cannot exist in the game (a Mega in Sword /
    Shield, an Alolan form in a Gen 3 game...)."""
    tokens = (name or "").split("-")
    for t in tokens:
        limit = _FORM_GAME_LIMITS.get(t)
        if limit is not None and game not in limit:
            return False
        min_gen = _REGIONAL_FORM_MIN_GEN.get(t)
        if min_gen and GAME_GENERATION.get(game, 9) < min_gen:
            return False
    if name in _NEW_MEGA_FORMS and game not in _NEW_MEGA_GAMES:
        return False
    only = _FORM_ONLY_GAMES.get(name)
    return game in only if only else True


def add_ability_games(ability):
    """Add the games tabs to a fetch_ability() result: ability["games"] (the
    games the ability is in, newest first) and, on every Pokemon in
    ability["pokemon"], "games": the games that Pokemon has it in. A game
    counts when the ability existed by then (and the game has abilities),
    the Pokemon is in that game's pokedex, the form can exist there, and a
    hidden ability is not counted before Gen 5. Changes ability in place."""
    mons = (ability or {}).get("pokemon") or []
    if not mons:
        return ability
    intro = _ROMAN_GEN.get(
        "generation-" + (ability.get("generation") or "").lower(), 3
    )
    za_species = ZA_ABILITY_SPECIES.get(
        (ability.get("name") or "").lower().replace(" ", "-")
    )
    candidates = [
        (k, label) for k, label in MOVE_GAMES
        if GAME_GENERATION.get(k, 9) >= max(intro, 3)
        and (k not in ABILITY_NO_GAMES
             or (k == "legends-za" and za_species))
    ]
    species_dex = _species_dex_map()
    sets = {}
    # Legends Z-A: the species are known, so no pokedex lookup is needed.
    lookup = [c for c in candidates if c[0] != "legends-za"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (k, _), found in zip(
            lookup, pool.map(lambda c: _game_species(c[0]), lookup)
        ):
            sets[k] = found
    if any(k == "legends-za" for k, _ in candidates):
        sets["legends-za"] = set(za_species)
    in_games = [(k, label) for k, label in candidates if sets[k]]
    if not in_games:
        return ability
    for m in mons:
        sp = _species_of(m.get("name"), species_dex)
        m["games"] = [
            k for k, _ in in_games
            if sp in sets[k] and _form_in_game(m.get("name"), k)
            # Mega Zygarde has no Power Construct (or any ability) in Z-A
            and not (k == "legends-za" and "-mega" in (m.get("name") or ""))
            and not (m.get("hidden") and GAME_GENERATION.get(k, 9) < 5)
        ]
    ability["games"] = [
        {"key": k, "label": label} for k, label in in_games
        if any(k in m["games"] for m in mons)
    ]
    return ability


def _sprite_game_rank(game):
    """Release order, newest first (Champions, Z-A, then MOVE_GAMES, whose
    Gold / Silver entry covers the separate gold and silver sprites)."""
    order = ["champions", "legends-za"]
    for key, _ in MOVE_GAMES:
        order.extend(["gold", "silver"] if key == "gold-silver" else [key])
    return order.index(game) if game in order else len(order)


_LAST_BW2_DEX = 649      # Victini..Genesect: the last Pokémon in Black 2 / White 2
_BW_SPRITE_GAMES = ("black-white", "black-2-white-2")


def _after_bw2(data):
    """True for a Pokémon (or form) that did not exist yet in Black 2 /
    White 2: a species past #649, or a Mega / regional / Gigantamax form."""
    species_url = (data.get("species") or {}).get("url")
    dex = _id_from_url(species_url) if species_url else None
    if dex is None:
        dex = data.get("id")
    if isinstance(dex, int) and dex > _LAST_BW2_DEX and dex < 10000:
        return True
    return _in_evolution_line(data.get("name") or "")


def _drop_bw_sprites(data, *by_game):
    """Take the Black / White and Black 2 / White 2 sprites out of the given
    {game: url} dicts when the Pokémon came after those games."""
    if not _after_bw2(data):
        return
    for sprites in by_game:
        for game in _BW_SPRITE_GAMES:
            sprites.pop(game, None)


def _sprite_list(data):
    """List of {"game", "url", "shiny"} dicts for one /pokemon/ record, one
    per game with a sprite, newest game first."""
    raw = data.get("sprites") or {}
    normal = _sprites_by_gen(raw)
    shiny = _sprites_by_gen(raw, "front_shiny")
    _drop_bw_sprites(data, normal, shiny)
    _add_champions_fallback(data.get("id"), normal, shiny)
    _add_za_fallback(data.get("id"), normal, shiny)
    games = sorted(normal, key=_sprite_game_rank)  # stable for unknowns
    return [
        {"game": game, "url": normal[game], "shiny": shiny.get(game, "")}
        for game in games
    ]


def fetch_sprites(name):
    """Sprites per game for a Pokemon (species names like "minior" work
    too), or None when it is not found."""
    data = _get_pokemon_json(name)
    if not data:
        return None
    return _sprite_list(data)


def fetch_sprite_forms(name):
    """One {"name", "label", "current", "sprites"} per form, where
    "sprites" is that form's own sprite list. [] when it has one form."""
    data = _get_pokemon_json(name)
    if not data:
        return []
    species_name = (data.get("species") or {}).get("name")
    if not species_name:
        return []
    species = _fetch_species(species_name)
    current, names = _form_names_with_current(species_name, species, data)
    # Basculin has three striped forms. PokeAPI uses "basculin" as the
    # default Red-Striped record, so explicitly request the three named forms
    # instead of accidentally replacing Red-Striped with White-Striped.
    if species_name == "basculin":
        names = [
            "basculin-red-striped",
            "basculin-blue-striped",
            "basculin-white-striped",
        ]
        current = data.get("name")
        if current == "basculin":
            current = "basculin-red-striped"
        if current not in names:
            current = "basculin-red-striped"
    if len(names) < 2:
        if species_name in _GENDER_FORM_SPECIES:
            return []
        cosmetic = _cosmetic_forms(species_name, data)
        current = data.get("name")
        forms = [
            {"name": n, "label": label, "current": n == current,
             "sprites": _cosmetic_sprite_list(rec)}
            for n, label, rec in cosmetic
        ]
        forms = [f for f in forms if f["sprites"]]
        return forms if len(forms) >= 2 else []
    loaded = _load_form_data(names, current, data)
    valid = [n for n in names if isinstance(loaded.get(n), dict)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda n: _sprite_list(loaded[n]), valid))
    forms = [
        {
            "name": n,
            "label": _display_label(n, species_name),
            "current": n == current,
            "sprites": sprites,
        }
        for n, sprites in zip(valid, lists)
        if sprites
    ]
    return forms if len(forms) >= 2 else []


def _tcgplayer_price(tcgp):
    if not isinstance(tcgp, dict):
        return None
    if tcgp.get("marketPrice") is not None:
        return tcgp["marketPrice"]
    normal = tcgp.get("normal")
    if isinstance(normal, dict) and normal.get("marketPrice") is not None:
        return normal["marketPrice"]
    return None


def _cardmarket_price(cm):
    if isinstance(cm, dict) and cm.get("avg") is not None:
        return cm["avg"]
    return None


_CARD_CACHE = {}          # card id -> (time fetched, detail)
CARD_CACHE_SECONDS = 3600


def _fetch_card_detail(card_id):
    """One card's full record (cached for an hour: a card search can mean
    dozens of lookups, and repeat searches should be quick)."""
    hit = _CARD_CACHE.get(card_id)
    if hit and time.time() - hit[0] < CARD_CACHE_SECONDS:
        return hit[1]
    data = _get_json(TCGDEX_CARD.format(card_id))
    if not isinstance(data, dict):
        return None
    base = data.get("image")
    if not base:
        return None  # no artwork: not worth showing
    pricing = data.get("pricing") or {}
    name = data.get("name") or ""
    set_name = (data.get("set") or {}).get("name")
    detail = {
        "id": data.get("id") or card_id,
        "name": name,
        "set": set_name,
        "rarity": data.get("rarity"),
        "image": base + ".png",
        # tried if the first URL fails to load
        "image_alt": base + "/high.png",
        "fav_name": (name + (" · " + set_name if set_name else ""))[:64],
        "tcgplayer": _tcgplayer_price(pricing.get("tcgplayer")),
        "cardmarket": _cardmarket_price(pricing.get("cardmarket")),
    }
    _CARD_CACHE[card_id] = (time.time(), detail)
    return detail


def card_sort_price(card):
    """Price used to rank a card: USD (TCGplayer), else EUR (Cardmarket).
    Cards with no price rank last."""
    for key in ("tcgplayer", "cardmarket"):
        if card.get(key) is not None:
            return card[key]
    return -1


# Hyphenated endings that are still the same Pokemon on a card ("Pikachu-EX").
# Any other hyphenated ending ("Porygon-Z") is a different Pokemon.
_CARD_NAME_SUFFIXES = {
    "ex", "gx", "v", "vmax", "vstar", "vunion", "break", "star", "prime",
    "lv", "x", "legend",
}


def _name_tokens(text):
    """Lower-case word tokens of a name, accents/punctuation removed, each
    paired with whether a hyphen joined it to the previous token."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"['\u2019.]", "", text)
    out = []
    for m in re.finditer(r"[a-z0-9]+", text):
        joined = m.start() > 0 and text[m.start() - 1] == "-"
        out.append((m.group(), joined))
    return out


def card_name_matches(card_name, query):
    """True when the card is for the Pokemon that was typed: the typed name
    appears in the card's name as whole words. 'Porygon' matches 'Porygon'
    and 'Dark Porygon' but not 'Porygon2', 'Porygon-Z'; 'Mew' matches
    'Mew ex' but not 'Mewtwo'."""
    want = [t for t, _ in _name_tokens(query)]
    have = _name_tokens(card_name)
    if not want:
        return False
    n = len(want)
    for i in range(len(have) - n + 1):
        if [t for t, _ in have[i:i + n]] != want:
            continue
        if have[i][1]:                       # "Ho-Oh" is not "Oh"
            continue
        nxt = have[i + n] if i + n < len(have) else None
        if nxt and nxt[1] and nxt[0] not in _CARD_NAME_SUFFIXES:
            continue                         # "Porygon-Z" is not "Porygon"
        return True
    return False


def _search_cards(name):
    """TCGdex search results for the name, trying friendlier spellings
    ('mr-mime' -> 'mr mime', 'mimikyu-busted' -> 'mimikyu'). TCGdex
    matches part of a name, so the results are cut down to cards that are
    really for the typed Pokemon (not Porygon2 / Porygon-Z for 'porygon',
    nor Mewtwo for 'mew')."""
    tries = []
    for q in (name, name.replace("-", " "), name.split("-")[0]):
        if q and q not in tries:
            tries.append(q)
    for q in tries:
        try:
            r = requests.get(
                TCGDEX_SEARCH, params={"name": q}, timeout=TIMEOUT
            )
            r.raise_for_status()
            results = r.json()
        except Exception:
            continue
        if isinstance(results, list):
            results = [
                c for c in results
                if isinstance(c, dict)
                and card_name_matches(c.get("name"), q)
            ]
        if results:
            return q, results
    return name, []


def fetch_cards(name, limit=None):
    """Trading cards for a Pokemon, most expensive first.

    Every card returned has an image. With no `limit` every matching card is
    returned; with one, only the first `limit` (exact name matches first) are
    priced and ranked.
    """
    name = (name or "").strip()
    if not name:
        return []
    used, results = _search_cards(name)
    wanted = used.lower()
    # Skip imageless cards before spending a request on them; exact name
    # matches first (the search also returns "Detective Pikachu" etc.).
    candidates = [
        c for c in results
        if isinstance(c, dict) and c.get("id") and c.get("image")
    ]
    candidates.sort(
        key=lambda c: 0 if (c.get("name") or "").lower() == wanted else 1
    )
    if limit:
        candidates = candidates[:limit + 4]  # a few spares for failed lookups
    with ThreadPoolExecutor(max_workers=24) as pool:
        details = list(
            pool.map(lambda c: _fetch_card_detail(c["id"]), candidates)
        )
    cards = [d for d in details if d]
    if limit:
        cards = cards[:limit]
    cards.sort(key=card_sort_price, reverse=True)  # stable for ties
    return cards


def card_search_status(name):
    """Why a card search came back empty: "unreachable" (TCGdex could not be
    reached), "no_artwork" (it lists cards but none could be loaded with an
    image) or "no_matches" (no card has that name)."""
    name = (name or "").strip()
    tries = []
    for q in (name, name.replace("-", " "), name.split("-")[0]):
        if q and q not in tries:
            tries.append(q)
    reached = False
    for q in tries:
        try:
            r = requests.get(TCGDEX_SEARCH, params={"name": q}, timeout=8)
            r.raise_for_status()
            results = r.json()
        except Exception:
            continue
        reached = True
        if isinstance(results, list) and any(
            isinstance(c, dict) and card_name_matches(c.get("name"), q)
            for c in results
        ):
            return "no_artwork"
    return "no_matches" if reached else "unreachable"

# ---------------------------------------------------------------------------
# Game info (the "Game Info" tab of a Pokemon page)
# ---------------------------------------------------------------------------

# PokeAPI "version" names that belong to each game button.
GAME_VERSIONS = {
    "champions": ["champions"],
    "legends-za": ["legends-za"],
    "scarlet-violet": ["scarlet", "violet"],
    "sword-shield": ["sword", "shield"],
    "brilliant-diamond-shining-pearl": ["brilliant-diamond", "shining-pearl"],
    "legends-arceus": ["legends-arceus"],
    "lets-go-pikachu-lets-go-eevee": ["lets-go-pikachu", "lets-go-eevee"],
    "ultra-sun-ultra-moon": ["ultra-sun", "ultra-moon"],
    "sun-moon": ["sun", "moon"],
    "omega-ruby-alpha-sapphire": ["omega-ruby", "alpha-sapphire"],
    "x-y": ["x", "y"],
    "black-2-white-2": ["black-2", "white-2"],
    "black-white": ["black", "white"],
    "heartgold-soulsilver": ["heartgold", "soulsilver"],
    "diamond-pearl": ["diamond", "pearl"],
    "platinum": ["platinum"],
    "firered-leafgreen": ["firered", "leafgreen"],
    "ruby-sapphire": ["ruby", "sapphire"],
    "emerald": ["emerald"],
    "xd": ["xd"],
    "colosseum": ["colosseum"],
    "gold-silver": ["gold", "silver"],
    "crystal": ["crystal"],
    "yellow": ["yellow"],
    "red-blue": ["red", "blue"],
}

# Generation each game belongs to.
GAME_GENERATION = {
    "red-blue": 1, "yellow": 1,
    "gold-silver": 2, "crystal": 2,
    "ruby-sapphire": 3, "emerald": 3, "firered-leafgreen": 3,
    "colosseum": 3, "xd": 3,
    "diamond-pearl": 4, "platinum": 4, "heartgold-soulsilver": 4,
    "black-white": 5, "black-2-white-2": 5,
    "x-y": 6, "omega-ruby-alpha-sapphire": 6,
    "sun-moon": 7, "ultra-sun-ultra-moon": 7,
    "lets-go-pikachu-lets-go-eevee": 7,
    "sword-shield": 8, "brilliant-diamond-shining-pearl": 8,
    "legends-arceus": 8,
    "scarlet-violet": 9, "legends-za": 9, "champions": 9,
}

# The regional Pokedexes (PokeAPI names) each game uses.
GAME_DEXES = {
    "red-blue": ("kanto",),
    "yellow": ("kanto",),
    "gold-silver": ("original-johto",),
    "crystal": ("original-johto",),
    "ruby-sapphire": ("hoenn",),
    "emerald": ("hoenn",),
    "firered-leafgreen": ("kanto",),
    "diamond-pearl": ("original-sinnoh",),
    "platinum": ("extended-sinnoh",),
    "heartgold-soulsilver": ("updated-johto",),
    "black-white": ("original-unova",),
    "black-2-white-2": ("updated-unova",),
    "x-y": ("kalos-central", "kalos-coastal", "kalos-mountain"),
    "omega-ruby-alpha-sapphire": ("updated-hoenn",),
    "sun-moon": (
        "original-alola", "original-melemele", "original-akala",
        "original-ulaula", "original-poni",
    ),
    "ultra-sun-ultra-moon": (
        "updated-alola", "updated-melemele", "updated-akala",
        "updated-ulaula", "updated-poni",
    ),
    "lets-go-pikachu-lets-go-eevee": ("letsgo-kanto",),
    "sword-shield": ("galar", "isle-of-armor", "crown-tundra"),
    "brilliant-diamond-shining-pearl": ("original-sinnoh",),
    "legends-arceus": ("hisui",),
    "scarlet-violet": ("paldea", "kitakami", "blueberry"),
}

# National Dex number of the last Pokemon of each generation.
_GEN_LAST_ID = ((151, 1), (251, 2), (386, 3), (493, 4),
                (649, 5), (721, 6), (809, 7), (905, 8))
_ROMAN_GEN = {
    "generation-i": 1, "generation-ii": 2, "generation-iii": 3,
    "generation-iv": 4, "generation-v": 5, "generation-vi": 6,
    "generation-vii": 7, "generation-viii": 8, "generation-ix": 9,
}
# Games where Pokemon have no abilities (the Abilities section is hidden and
# Game Info says so). Legends: Z-A is the one asked for; "legends-arceus"
# could be added here too, since it has no abilities either.
NO_ABILITY_GAMES = {"legends-za"}
MAX_ENCOUNTER_ROWS = 40
NO_ENCOUNTER_DATA = {
    "champions", "legends-za", "scarlet-violet", "sword-shield",
    "brilliant-diamond-shining-pearl", "legends-arceus",
    "lets-go-pikachu-lets-go-eevee", "xd", "colosseum",
}

# PokeAPI has no wild-location data for Scarlet/Violet, Legends Arceus, BDSP
# or Legends Z-A, so the Game Info tab falls back to written locations in
# data/location_notes.json (credited inside that file).
_LOCATION_NOTES_FILE = (
    Path(__file__).resolve().parent / "data" / "location_notes.json"
)
_location_notes = None


def _load_location_notes():
    global _location_notes
    if _location_notes is None:
        try:
            with open(_LOCATION_NOTES_FILE, encoding="utf-8") as f:
                _location_notes = json.load(f)
        except (OSError, ValueError):
            _location_notes = {}
    return _location_notes
_GAME_INFO_CACHE = {}       # Pokemon name -> (time fetched, source data)
GAME_INFO_CACHE_SECONDS = 3600


def _pretty(name):
    return (name or "").replace("-", " ").title()


def _id_from_url(url):
    try:
        return int((url or "").rstrip("/").split("/")[-1])
    except ValueError:
        return None


def _species_generation(species_id):
    """Generation a species first appeared in, from its National Dex number."""
    if not species_id:
        return 1
    for last, gen in _GEN_LAST_ID:
        if species_id <= last:
            return gen
    return 9


def _flatten_chain(node, out=None):
    """Every evolution step of a PokeAPI evolution chain, in order."""
    out = [] if out is None else out
    species = node.get("species") or {}
    for child in node.get("evolves_to") or []:
        child_species = child.get("species") or {}
        out.append({
            "from": species.get("name"),
            "to": child_species.get("name"),
            "to_id": _id_from_url(child_species.get("url")),
            "from_id": _id_from_url(species.get("url")),
            "details": child.get("evolution_details") or [],
        })
        _flatten_chain(child, out)
    return out


_TRIGGER_TEXT = {
    "trade": "Trade",
    "shed": "Level up Nincada with an empty party slot and a Poké Ball",
    "spin": "Spin around",
    "tower-of-two-fists": "Complete the Tower of Two Fists",
    "three-critical-hits": "Land three critical hits in one battle",
    "take-damage": "Take damage, then visit the stone arch",
    "agile-style-move": "Use an agile-style move enough times",
    "strong-style-move": "Use a strong-style move enough times",
    "recoil-damage": "Take recoil damage",
}


def _evolution_method(d):
    """One evolution_details record as a sentence ("Level 16",
    "Use Fire Stone", "Level up with high friendship during the day")."""
    def name(key):
        return ((d.get(key) or {}).get("name")) or ""

    trigger = name("trigger")
    if trigger == "use-item":
        base = "Use " + _pretty(name("item")) if name("item") else "Use an item"
    elif trigger == "level-up":
        base = "Level %s" % d["min_level"] if d.get("min_level") else "Level up"
    else:
        base = _TRIGGER_TEXT.get(trigger) or _pretty(trigger) or "Special"
        if d.get("min_level") and trigger not in ("shed",):
            base += " (level %s)" % d["min_level"]

    extra = []
    if d.get("min_happiness"):
        extra.append("with high friendship")
    if d.get("min_affection"):
        extra.append("with %s+ affection" % d["min_affection"])
    if d.get("min_beauty"):
        extra.append("with %s+ beauty" % d["min_beauty"])
    if name("held_item"):
        extra.append("holding " + _pretty(name("held_item")))
    if name("known_move") and not d.get("min_move_count"):
        extra.append("knowing " + _pretty(name("known_move")))
    if name("known_move_type"):
        extra.append("knowing a %s-type move" % _pretty(name("known_move_type")))
    if name("location"):
        extra.append("at " + _pretty(name("location")))
    if d.get("time_of_day"):
        extra.append({"day": "during the day", "night": "at night",
                      "dusk": "at dusk"}.get(d["time_of_day"], d["time_of_day"]))
    if d.get("gender") in (1, 2):
        extra.append("if female" if d["gender"] == 1 else "if male")
    stats = d.get("relative_physical_stats")
    if stats is not None:
        extra.append({1: "if Attack is higher than Defense",
                      -1: "if Defense is higher than Attack",
                      0: "if Attack equals Defense"}.get(stats, ""))
    if name("party_species"):
        extra.append("with %s in the party" % _pretty(name("party_species")))
    if name("party_type"):
        extra.append("with a %s-type in the party" % _pretty(name("party_type")))
    if name("trade_species"):
        extra.append("with " + _pretty(name("trade_species")))
    if d.get("needs_overworld_rain"):
        extra.append("while it is raining")
    if d.get("turn_upside_down"):
        extra.append("with the console held upside down")
    if d.get("min_move_count"):
        move = _pretty(name("known_move") or name("used_move"))
        extra.append("after using %s %s times" % (move or "the move",
                                                  d["min_move_count"]))
    if d.get("min_steps"):
        extra.append("after walking %s steps" % d["min_steps"])
    if d.get("min_damage_taken"):
        extra.append("after taking %s+ damage" % d["min_damage_taken"])
    return " ".join([base] + [e for e in extra if e])


# Evolution triggers the Evolution line on the Details tab can show in a few
# words (a level, an item, a trade...). Anything else, and any requirement
# that needs a sentence (use a move 20 times, take 49 damage, three critical
# hits...), is explained on the Game Info tab instead.
_PLAIN_TRIGGERS = {"level-up", "use-item", "trade", "shed", "spin"}


def _evolution_is_special(d):
    """True when one evolution_details record has a requirement too long
    for the Evolution line (most of the Generation 8+ special methods)."""
    trigger = ((d.get("trigger") or {}).get("name")) or ""
    if trigger not in _PLAIN_TRIGGERS:
        return True
    return any(d.get(k) for k in (
        "min_move_count", "min_steps", "min_damage_taken",
        "needs_multiplayer", "base_form_id", "region_id",
    ))


def _evolution_info(source, game_gen, game=None):
    """The evolution line, each step marked as available (or not) in a game
    of this generation. With `game` (a version group), a step that works
    differently from game to game (Leafeon / Glaceon: a Moss / Ice Rock, then
    a stone; Sylveon: affection, then friendship) only gives the way it works
    in that game."""
    chain = (source.get("chain") or {}).get("chain") or {}
    current = source.get("species_name")
    # a line drawn as a ring (Eevee) leaves the steps that need a place or
    # affection to this tab
    ring = len(chain.get("evolves_to") or []) > _COMPACT_BRANCHES
    edges = []
    for step in _flatten_chain(chain):
        methods = []
        if game and step["details"]:
            methods = [m["text"] for m in
                       _evolution_step_methods(step["details"], [game])]
        if not methods:
            for d in step["details"]:
                m = _evolution_method(d)
                if m and m not in methods:
                    methods.append(m)
        edges.append({
            "src": _pretty(step["from"]),
            "dst": _pretty(step["to"]),
            # PokeAPI species names, for the links to those Pokemon
            "src_slug": step["from"],
            "dst_slug": step["to"],
            "method": " or ".join(methods) or "Special",
            "available": _species_generation(step["to_id"]) <= game_gen,
            "current": current in (step["from"], step["to"]),
            # no record at all (PokeAPI has no method yet) counts as special
            "special": not step["details"] or any(
                _evolution_is_special(d) for d in step["details"]
            ) or (ring and step["from"] == (chain.get("species") or {}).get(
                "name") and _needs_game_info(step["details"])),
        })
    return {
        "root": _pretty((chain.get("species") or {}).get("name") or current),
        "edges": edges,
        # the steps the Evolution line cannot explain: the Game Info tab
        # only lists these (and nothing at all when there are none)
        "special_edges": ([] if game == "champions"
                          else [e for e in edges if e["special"]]),
    }


def _evolution_edges(chain, current):
    """The evolution line of a species for the Details tab, one dict per
    step: names, the slugs to link to, the method and the generation the
    evolved form first appeared in (so a game can grey out a step that
    does not exist in it). [] for a Pokemon that does not evolve."""
    root = (chain or {}).get("chain") or {}
    edges = []
    for step in _flatten_chain(root):
        methods = []
        for d in step["details"]:
            m = _evolution_method(d)
            if m and m not in methods:
                methods.append(m)
        edges.append({
            "src": _pretty(step["from"]),
            "dst": _pretty(step["to"]),
            "src_slug": step["from"],
            "dst_slug": step["to"],
            "method": " or ".join(methods) or "Special",
            "gen": _species_generation(step["to_id"]),
            "src_gen": _species_generation(step.get("from_id")),
            "current": current in (step["from"], step["to"]),
        })
    return edges


# PokeAPI lists every way a species can evolve, regional forms included, on
# the species' own step. Vulpix -> Ninetales therefore carries both the Fire
# Stone (Kantonian) and the Ice Stone (Alolan). The Alolan one is drawn in its
# own line (see _REGIONAL_EVO_ITEMS), so the normal line drops it.
# (from species, to species) -> items that belong to a regional form only.
_REGIONAL_ONLY_STONES = {
    ("vulpix", "ninetales"): {"ice-stone"},
}


def _clean_chain(chain):
    """The chain with the regional-only evolution items taken out of the
    normal line's steps (a step is left alone if that would empty it)."""
    root = (chain or {}).get("chain") if isinstance(chain, dict) else None
    if not root:
        return chain

    def walk(node):
        parent = (node.get("species") or {}).get("name")
        for child in node.get("evolves_to") or []:
            drop = _REGIONAL_ONLY_STONES.get(
                (parent, (child.get("species") or {}).get("name")))
            if drop:
                kept = [d for d in child.get("evolution_details") or []
                        if ((d.get("item") or {}).get("name")) not in drop]
                if kept:
                    child["evolution_details"] = kept
            walk(child)

    walk(root)
    return chain


def _fetch_chain(species):
    """The PokeAPI evolution chain record of a species ({} when unavailable)."""
    url = (species.get("evolution_chain") or {}).get("url")
    chain = _get_json(url) if url else None
    return _clean_chain(chain) if isinstance(chain, dict) else {}


def _fetch_evolution(species, species_name):
    return _evolution_edges(_fetch_chain(species), species_name)


def _evolution_family(edges, current):
    """Every Pokemon of the evolution line in order, for the Evolution
    buttons: [{"name", "slug", "gen", "current"}]. [] when it has no line."""
    order, found = [], {}
    for e in edges or []:
        for side, gen_key in (("src", "src_gen"), ("dst", "gen")):
            slug = e.get(side + "_slug")
            if slug and slug not in found:
                found[slug] = {
                    "name": e[side], "slug": slug,
                    "gen": e.get(gen_key) or 1, "current": slug == current,
                }
                order.append(slug)
    return [found[slug] for slug in order] if len(order) > 1 else []


# ---- the evolution line on the Details tab ---------------------------------
# A tree of Pokémon buttons joined by arrows. Above each arrow sits what makes
# the Pokémon evolve: the level, an item (a link to its Items entry), or a
# short word (Trade, Friendship...). Mega Evolutions and Primal Reversions
# hang off their Pokémon the same way, with the Mega Stone / Orb above the
# arrow.

# Mega Stone name, without the -x / -y / -z ending, for each Pokémon that has
# a Mega Evolution. Every stone here is in MEGA_STONES.
_MEGA_STONE_STEMS = {
    "venusaur": "venusaurite", "charizard": "charizardite",
    "blastoise": "blastoisinite", "beedrill": "beedrillite",
    "pidgeot": "pidgeotite", "alakazam": "alakazite",
    "slowbro": "slowbronite", "gengar": "gengarite",
    "kangaskhan": "kangaskhanite", "pinsir": "pinsirite",
    "gyarados": "gyaradosite", "aerodactyl": "aerodactylite",
    "mewtwo": "mewtwonite", "ampharos": "ampharosite",
    "steelix": "steelixite", "scizor": "scizorite",
    "heracross": "heracronite", "houndoom": "houndoominite",
    "tyranitar": "tyranitarite", "sceptile": "sceptilite",
    "blaziken": "blazikenite", "swampert": "swampertite",
    "gardevoir": "gardevoirite", "sableye": "sablenite",
    "mawile": "mawilite", "aggron": "aggronite", "medicham": "medichamite",
    "manectric": "manectite", "sharpedo": "sharpedonite",
    "camerupt": "cameruptite", "altaria": "altarianite",
    "banette": "banettite", "absol": "absolite", "glalie": "glalitite",
    "salamence": "salamencite", "metagross": "metagrossite",
    "latias": "latiasite", "latios": "latiosite", "lopunny": "lopunnite",
    "garchomp": "garchompite", "lucario": "lucarionite",
    "abomasnow": "abomasite", "gallade": "galladite", "audino": "audinite",
    "diancie": "diancite",
    # Legends: Z-A and its Mega Dimension DLC
    "clefable": "clefablite", "victreebel": "victreebelite",
    "starmie": "starminite", "dragonite": "dragoninite",
    "meganium": "meganiumite", "feraligatr": "feraligite",
    "skarmory": "skarmorite", "froslass": "froslassite",
    "heatran": "heatranite", "darkrai": "darkranite",
    "emboar": "emboarite", "excadrill": "excadrite",
    "scolipede": "scolipite", "scrafty": "scraftinite",
    "eelektross": "eelektrossite", "chandelure": "chandelurite",
    "chesnaught": "chesnaughtite", "delphox": "delphoxite",
    "greninja": "greninjite", "pyroar": "pyroarite", "floette": "floettite",
    "malamar": "malamarite", "barbaracle": "barbaracite",
    "dragalge": "dragalgite", "hawlucha": "hawluchanite",
    "zygarde": "zygardite", "drampa": "drampanite", "zeraora": "zeraorite",
    "falinks": "falinksite", "raichu": "raichunite",
    "chimecho": "chimechite", "staraptor": "staraptite",
    "golurk": "golurkite", "meowstic": "meowsticite",
    "crabominable": "crabominite", "golisopod": "golisopite",
    "magearna": "magearnite", "scovillain": "scovillainite",
    "glimmora": "glimmoranite", "tatsugiri": "tatsugirinite",
    "baxcalibur": "baxcalibrite",
}
_PRIMAL_ORBS = {"groudon": "red-orb", "kyogre": "blue-orb"}

# Item names PokeAPI spells without punctuation.
_ITEM_DISPLAY_NAMES = {
    "kings-rock": "King's Rock", "leaders-crest": "Leader's Crest",
    "up-grade": "Upgrade", "dubious-disc": "Dubious Disc",
}


def _item_display(slug):
    return _ITEM_DISPLAY_NAMES.get(slug) or _pretty(slug)


def _mega_stone_for(form_name, species_name):
    """The Mega Stone that turns a Pokémon into this Mega form ("charizard-
    mega-x" -> "charizardite-x"), or None (Mega Rayquaza has none)."""
    base, sep, rest = (form_name or "").partition("-mega")
    if not sep:
        return None
    variant = rest.lstrip("-")
    stem = _MEGA_STONE_STEMS.get(species_name or base)
    if not stem:
        return None
    for candidate in ((stem + "-" + variant) if variant else None, stem):
        if candidate and candidate in MEGA_STONES:
            return candidate
    return None


_SPECIES_CACHE = {}               # species name -> (time fetched, record)
_SPECIES_CACHE_SECONDS = 3600


def _fetch_species_cached(species_name):
    """_fetch_species, remembered for an hour: one evolution line means a
    species lookup for every Pokémon in it."""
    hit = _SPECIES_CACHE.get(species_name)
    if hit and time.time() - hit[0] < _SPECIES_CACHE_SECONDS:
        return hit[1]
    data = _fetch_species(species_name)
    if data:
        _SPECIES_CACHE[species_name] = (time.time(), data)
    return data


_SHORT_TRIGGER = {
    "trade": "Trade", "shed": "Shed", "spin": "Spin",
    "tower-of-two-fists": "Tower", "three-critical-hits": "3 crits",
    "take-damage": "Damage", "agile-style-move": "Agile move",
    "strong-style-move": "Strong move", "recoil-damage": "Recoil",
    "use-move": "Move", "other": "Special",
}


def _evolution_label(d):
    """What goes above the arrow for one evolution_details record:
    {"text", "sub", "item", "item_name", "item_sprite", "title"}.
    text is the big part ("16", "Trade", "Friendship"), sub the small
    condition under it ("night", "♀"), item an Items-tab link."""
    def name(key):
        return ((d.get(key) or {}).get("name")) or ""

    trigger = name("trigger")
    if _evolution_is_special(d):
        return {
            "text": "Special", "sub": "See Game Info tab", "special": True,
            "item": "", "item_name": "", "item_sprite": "",
            "title": _evolution_method(d),
        }
    item = name("item") if trigger == "use-item" else ""
    item = item or name("held_item")

    text = ""
    used = set()          # conditions already said by the big text
    if trigger == "use-item":
        text = "" if item else "Item"
    elif trigger == "level-up":
        if d.get("min_level"):
            text = str(d["min_level"])
        elif d.get("min_happiness"):
            text = "Friendship"
            used.add("happiness")
        elif d.get("min_affection"):
            text = "Affection"
            used.add("affection")
        elif d.get("min_beauty"):
            text = "Beauty"
            used.add("beauty")
        elif name("known_move"):
            text = "Move"
        elif name("location"):
            text = "Place"
        else:
            text = "" if item else "Level up"
    else:
        text = _SHORT_TRIGGER.get(trigger) or _pretty(trigger) or "Special"

    sub = []
    if d.get("min_happiness") and "happiness" not in used:
        sub.append("friendship")
    if d.get("min_affection") and "affection" not in used:
        sub.append("affection")
    if d.get("min_beauty") and "beauty" not in used:
        sub.append("beauty")
    if d.get("time_of_day"):
        sub.append(d["time_of_day"])
    if d.get("gender") in (1, 2):
        sub.append("♀" if d["gender"] == 1 else "♂")
    if name("known_move"):
        sub.append(_pretty(name("known_move")))
    if name("known_move_type"):
        sub.append("%s move" % _pretty(name("known_move_type")))
    if name("location"):
        sub.append(_pretty(name("location")))
    stats = d.get("relative_physical_stats")
    if stats is not None:
        sub.append({1: "Atk > Def", -1: "Def > Atk", 0: "Atk = Def"}
                   .get(stats, ""))
    if name("party_species"):
        sub.append("with " + _pretty(name("party_species")))
    if name("party_type"):
        sub.append("with %s-type" % _pretty(name("party_type")))
    if name("trade_species"):
        sub.append("for " + _pretty(name("trade_species")))
    if d.get("needs_overworld_rain"):
        sub.append("rain")
    if d.get("turn_upside_down"):
        sub.append("upside down")

    return {
        "text": text,
        "sub": ", ".join(s for s in sub if s),
        "item": item,
        "item_name": _item_display(item) if item else "",
        "item_sprite": (ITEM_SPRITE_BASE + item + ".png") if item else "",
        "title": _evolution_method(d),
    }


def _evolution_labels(details):
    """The labels above one arrow, one per different way to evolve."""
    labels, seen = [], set()
    for d in details or []:
        lab = _evolution_label(d)
        key = (lab["text"], lab["sub"], lab["item"])
        if key not in seen:
            seen.add(key)
            labels.append(lab)
    return labels or [{
        "text": "Special", "sub": "See Game Info tab", "special": True,
        "item": "", "item_name": "", "item_sprite": "", "title": "Special",
    }]


# ---- regional forms in the evolution line ----------------------------------
# Pokémon with a regional form get it drawn as its own sprite and its own
# line (Alolan Vulpix -> Alolan Ninetales) instead of reusing the normal
# sprites. Each entry is (region, (species, regional variety or None,
# [evolutions...])). A line that starts with a regional variety is a line of
# its own, below the normal one. A line that starts with None is a regional
# evolution of a normal Pokémon (Quilava -> Hisuian Typhlosion), so it hangs
# off that Pokémon as another branch.
_REGION_ADJ = {
    "alola": "Alolan", "galar": "Galarian", "hisui": "Hisuian",
    "paldea": "Paldean",
}
# The games each region's forms exist in (edit here if one is missing).
_REGION_GAMES = {
    "alola": {"sun-moon", "ultra-sun-ultra-moon",
              "lets-go-pikachu-lets-go-eevee", "scarlet-violet",
              "champions"},
    "galar": {"sword-shield", "scarlet-violet", "champions"},
    "hisui": {"legends-arceus", "scarlet-violet", "champions"},
    "paldea": {"scarlet-violet", "champions"},
}
# Games a regional form (or what only it evolves into) is in on top of its
# region's games (Hisuian Qwilfish and Overqwil are in Legends Z-A).
_REGIONAL_EXTRA_GAMES = {
    "qwilfish-hisui": {"legends-za"},
    "overqwil": {"legends-za"},
}
_REGIONAL_LINES = [
    ("alola", ("rattata", "rattata-alola", [("raticate", "raticate-alola", [])])),
    ("alola", ("sandshrew", "sandshrew-alola", [("sandslash", "sandslash-alola", [])])),
    ("alola", ("vulpix", "vulpix-alola", [("ninetales", "ninetales-alola", [])])),
    ("alola", ("diglett", "diglett-alola", [("dugtrio", "dugtrio-alola", [])])),
    ("alola", ("meowth", "meowth-alola", [("persian", "persian-alola", [])])),
    ("alola", ("geodude", "geodude-alola", [("graveler", "graveler-alola", [
        ("golem", "golem-alola", [])])])),
    ("alola", ("grimer", "grimer-alola", [("muk", "muk-alola", [])])),
    ("alola", ("pikachu", None, [("raichu", "raichu-alola", [])])),
    ("alola", ("exeggcute", None, [("exeggutor", "exeggutor-alola", [])])),
    ("alola", ("cubone", None, [("marowak", "marowak-alola", [])])),
    ("galar", ("meowth", "meowth-galar", [("perrserker", None, [])])),
    ("galar", ("ponyta", "ponyta-galar", [("rapidash", "rapidash-galar", [])])),
    ("galar", ("slowpoke", "slowpoke-galar", [
        ("slowbro", "slowbro-galar", []),
        ("slowking", "slowking-galar", [])])),
    ("galar", ("farfetchd", "farfetchd-galar", [("sirfetchd", None, [])])),
    ("galar", ("mime-jr", None, [("mr-mime", "mr-mime-galar", [
        ("mr-rime", None, [])])])),
    ("galar", ("corsola", "corsola-galar", [("cursola", None, [])])),
    ("galar", ("zigzagoon", "zigzagoon-galar", [("linoone", "linoone-galar", [
        ("obstagoon", None, [])])])),
    ("galar", ("yamask", "yamask-galar", [("runerigus", None, [])])),
    ("galar", ("darumaka", "darumaka-galar", [
        ("darmanitan", "darmanitan-galar-standard", [])])),
    ("galar", ("koffing", None, [("weezing", "weezing-galar", [])])),
    ("hisui", ("growlithe", "growlithe-hisui", [("arcanine", "arcanine-hisui", [])])),
    ("hisui", ("voltorb", "voltorb-hisui", [("electrode", "electrode-hisui", [])])),
    ("hisui", ("qwilfish", "qwilfish-hisui", [("overqwil", None, [])])),
    ("hisui", ("sneasel", "sneasel-hisui", [("sneasler", None, [])])),
    ("hisui", ("zorua", "zorua-hisui", [("zoroark", "zoroark-hisui", [])])),
    ("hisui", ("basculin", "basculin-white-striped", [("basculegion", None, [])])),
    ("hisui", ("quilava", None, [("typhlosion", "typhlosion-hisui", [])])),
    ("hisui", ("dewott", None, [("samurott", "samurott-hisui", [])])),
    ("hisui", ("dartrix", None, [("decidueye", "decidueye-hisui", [])])),
    ("hisui", ("petilil", None, [("lilligant", "lilligant-hisui", [])])),
    ("hisui", ("rufflet", None, [("braviary", "braviary-hisui", [])])),
    ("hisui", ("bergmite", None, [("avalugg", "avalugg-hisui", [])])),
    ("hisui", ("goomy", None, [("sliggoo", "sliggoo-hisui", [
        ("goodra", "goodra-hisui", [])])])),
    ("paldea", ("wooper", "wooper-paldea", [("clodsire", None, [])])),
]
# What a regional step needs instead of what PokeAPI's chain says (it only
# knows the species): (from, to) -> item.
_REGIONAL_EVO_ITEMS = {
    ("slowpoke-galar", "slowbro-galar"): "galarica-cuff",
    ("slowpoke-galar", "slowking-galar"): "galarica-wreath",
    ("darumaka-galar", "darmanitan-galar-standard"): "ice-stone",
    # Kantonian Vulpix needs a Fire Stone (the chain's own step); the Alolan
    # one needs an Ice Stone
    ("vulpix-alola", "ninetales-alola"): "ice-stone",
    ("sandshrew-alola", "sandslash-alola"): "ice-stone",
    ("voltorb-hisui", "electrode-hisui"): "leaf-stone",
}


def _regional_walk(node):
    yield node
    for kid in node[2]:
        for n in _regional_walk(kid):
            yield n


# Every regional form that has a place in an evolution line. The Details tab
# leaves them out of its Form buttons, since the line shows them.
REGIONAL_VARIETIES = {
    n[1] for _, spec in _REGIONAL_LINES for n in _regional_walk(spec) if n[1]
} | {"darmanitan-galar-zen"}
# Mega Evolutions, Primal Reversions and Gigantamax forms are in the line too
_TRANSFORM_TOKENS = {"mega", "gmax", "primal", "eternamax"}


# Shown in the evolution line AND as a Form button (Basculin's three striped
# forms are all buttons, on every Basculin page; Qwilfish gets Johtoian /
# Hisuian buttons)
_FORM_BUTTON_TOO = {"basculin-white-striped", "qwilfish-hisui"}


def _in_evolution_line(name):
    """True for a form the evolution line shows (so no Form button)."""
    if name in _FORM_BUTTON_TOO:
        return False
    return name in REGIONAL_VARIETIES or bool(
        set((name or "").split("-")) & _TRANSFORM_TOKENS)


# Pokémon that only evolve from a regional form (Perrserker, Sirfetchd...):
# the normal line leaves them out.
_REGIONAL_ONLY = set()
for _region, _spec in _REGIONAL_LINES:
    for _kid in _spec[2]:
        for _n in _regional_walk(_kid):
            if _n[1] is None:
                _REGIONAL_ONLY.add(_n[0])


def _transformation_name(variety, species_slug):
    """"charizard-mega-x" -> "Mega Charizard X", "kyogre-primal" -> "Primal
    Kyogre"."""
    sp = _pretty(species_slug)
    rest = variety[len(species_slug) + 1:] if variety.startswith(
        species_slug + "-") else ""
    parts = rest.split("-") if rest else []
    if parts and parts[0] == "mega":
        return "Mega " + sp + (" " + parts[1].upper() if len(parts) > 1 else "")
    if parts and parts[0] == "primal":
        return "Primal " + sp
    if "eternamax" in parts:
        return "Eternamax " + sp
    if "gmax" in parts:
        # "toxtricity-amped-gmax" -> "Gigantamax Toxtricity (Amped)"
        extra = " ".join(p for p in parts if p != "gmax").title()
        return "Gigantamax " + sp + (" (%s)" % extra if extra else "")
    return _pretty(variety)


# A Mega that only the Pokémon's other form can reach: Mega Floette needs the
# Eternal Flower Floette, not the Floette of the normal line. It is drawn as its
# own line below the normal one: base form -> (variety name, label).
_MEGA_BASE_FORMS = {
    "floette-mega": ("floette-eternal", "Eternal Flower"),
}


def _transformation_branches(slug, species, species_gen):
    """Mega Evolution / Primal Reversion branches of one Pokémon: the forms
    PokeAPI lists as varieties of its species, each reached with a Mega Stone
    or Orb (Mega Rayquaza with the move Dragon Ascent).

    Returns (branches, extra_lines): a Mega that needs another form of the
    Pokémon (_MEGA_BASE_FORMS) is not a branch of it, but sits under a node
    for that form, which is returned as a line of its own."""
    out = []
    extras = {}              # base form's variety name -> its node
    varieties = {
        (v.get("pokemon") or {}).get("name"): v.get("pokemon") or {}
        for v in species.get("varieties") or []
    }
    for v in species.get("varieties") or []:
        vp = v.get("pokemon") or {}
        vname = vp.get("name") or ""
        tokens = vname.split("-")
        if "mega" in tokens:
            item = _mega_stone_for(vname, slug)
            title = "Hold %s to Mega Evolve" % (
                _item_display(item) if item else "its Mega Stone")
        elif "primal" in tokens:
            item = _PRIMAL_ORBS.get(slug)
            title = "Hold the %s for Primal Reversion" % (
                _item_display(item) if item else "Orb")
        elif "gmax" in tokens:
            item = None
            title = "Dynamax into its Gigantamax form"
        elif "eternamax" in tokens:
            item = None
            title = "Eternamax: its Dynamax form"
        else:
            continue
        vid = _id_from_url(vp.get("url"))
        icons = _sprite_candidates(vid)
        label = {
            "text": "", "sub": "", "item": item or "",
            "item_name": _item_display(item) if item else "",
            "item_sprite": (ITEM_SPRITE_BASE + item + ".png") if item else "",
            "title": title, "move": "",
        }
        if "gmax" in tokens or "eternamax" in tokens:
            # no item: the Dynamax logo goes above the arrow
            label.update(dynamax=True)
        elif not item:
            if vname == "rayquaza-mega":
                label.update(text="Dragon Ascent", move="dragon-ascent",
                             title="Know Dragon Ascent to Mega Evolve")
            else:
                label.update(text="Mega", title="Mega Evolution")
        branch = {
            "labels": [label],
            "node": {
                "slug": slug, "q": vname, "form": vname,
                "name": _transformation_name(vname, slug),
                "sprite": icons[0] if icons else "",
                "sprite_alt": " ".join(icons[1:]),
                "gen": species_gen, "mega": True,
                # the games this form exists in (a Mega is not in Sword /
                # Shield...), so the page can grey it out
                "games": [vg for vg, _ in VERSION_GROUPS
                          if _form_in_game(vname, vg)],
                "gated": True,
                "branches": [], "current": False,
            },
        }
        base = _MEGA_BASE_FORMS.get(vname)
        if not base:
            out.append(branch)
            continue
        base_name, base_label = base
        node = extras.get(base_name)
        if node is None:
            bp = varieties.get(base_name) or {}
            bid = _id_from_url(bp.get("url"))
            base_icons = _sprite_candidates(bid)
            node = extras[base_name] = {
                "slug": slug, "q": base_name, "form": base_name,
                "name": "%s %s" % (base_label, _pretty(slug)),
                "sprite": base_icons[0] if base_icons else "",
                "sprite_alt": " ".join(base_icons[1:]),
                "gen": species_gen, "mega": False, "games": [],
                "branches": [], "current": False,
            }
        node["branches"].append(branch)
    return out, list(extras.values())


def _species_games(species, species_id):
    """Games (newest first) a species is in, read from the Pokedexes it is
    listed in. [] when PokeAPI has no Pokedex list for it."""
    dexes = [(d.get("pokedex") or {}).get("name", "")
             for d in species.get("pokedex_numbers") or []]
    if not dexes:
        return []
    games = {vg for vg, _ in VERSION_GROUPS
             if any(_dex_matches(vg, d) for d in dexes)}
    # BDSP's own dex is the Sinnoh one, but the games hold every Pokemon
    # up to Gen 4
    if _species_generation(species_id) <= 4:
        games.add("brilliant-diamond-shining-pearl")
    return [vg for vg, _ in VERSION_GROUPS if vg in games]


# ---- the compact evolution list (lines with many branches) -------------------
# A line that splits into more than this many branches (Eevee: eight
# evolutions and Gigantamax) is not drawn as a tree, which would be far wider
# and taller than the rest of the page. The Details tab lists the methods
# instead, only the ones that work in the game selected on the page.
_COMPACT_BRANCHES = 3

# Some steps work differently from game to game. When a step lists both ways,
# each way is tied to the games it works in:
#  - Leafeon / Glaceon: level up near a Moss Rock / Ice Rock in the games up to
#    Generation 7 (and BDSP), a Leaf Stone / Ice Stone in the newer ones
#  - Sylveon: affection in Generations 6 and 7, friendship from Generation 8
_ROCK_ITEMS = {"leaf-stone": "Moss Rock", "ice-stone": "Ice Rock"}
# where each of those rocks is, by generation of the game ({} = any)
_ROCK_PLACES = {
    "eterna-forest": {4, "bdsp"}, "route-217": {4, "bdsp"},
    "pinwheel-forest": {5}, "twist-mountain": {5},
    "kalos-route-20": {6}, "frost-cavern": {6},
    "lush-jungle": {7}, "mount-lanakila": {7},
}


def _rock_game(vg, places):
    """True when game `vg` is one of the games `places` (generation numbers,
    or "bdsp") stands for."""
    if vg == "brilliant-diamond-shining-pearl":
        return "bdsp" in places
    return GAME_GENERATION.get(vg, 9) in places


# Evolutions that only work in Legends Arceus (use a strong / agile style move)
_STYLE_TRIGGERS = {"strong-style-move", "agile-style-move"}


def _ok_games(d, details, games):
    """The games (from `games`) in which the way to evolve `d` works, when a
    step lists several ways: a rock or a stone, affection or friendship, a
    style move in Legends Arceus or something else in the other games."""
    def name(x, key):
        return ((x.get(key) or {}).get("name")) or ""

    has_place = any(name(x, "location") for x in details)
    has_item = any(name(x, "trigger") == "use-item" for x in details)
    has_aff = any(x.get("min_affection") for x in details)
    has_hap = any(x.get("min_happiness") for x in details)
    has_style = any(name(x, "trigger") in _STYLE_TRIGGERS for x in details)
    old_rock = {vg for vg in games
                if GAME_GENERATION.get(vg, 9) <= 7
                or vg == "brilliant-diamond-shining-pearl"}
    place = name(d, "location")
    if name(d, "trigger") in _STYLE_TRIGGERS:
        return [vg for vg in games if vg == "legends-arceus"]
    if place and has_item:
        where = _ROCK_PLACES.get(place)
        return [vg for vg in games if vg in old_rock
                and (where is None or _rock_game(vg, where))]
    if name(d, "trigger") == "use-item" and has_place:
        return [vg for vg in games if vg not in old_rock]
    if d.get("min_affection") and has_hap:
        return [vg for vg in games if GAME_GENERATION.get(vg, 9) in (6, 7)]
    if d.get("min_happiness") and has_aff:
        return [vg for vg in games
                if GAME_GENERATION.get(vg, 9) not in (6, 7)]
    if has_style:
        # the other ways are for the games after Legends Arceus
        return [vg for vg in games if vg != "legends-arceus"]
    return list(games)


def _label_groups(details, games, ring=False):
    """The labels of one arrow for each way the step works in the different
    games: [{"games": [...], "labels": [...]}], the group of the newest game
    first. Games that share the same ways share a group. The page shows the
    group of the selected game only."""
    details = [d for d in details or [] if isinstance(d, dict)]
    games = list(games or [])
    if not details or not games:
        return []
    ok = [_ok_games(d, details, games) for d in details]
    by_set = {}
    for g in games:
        idx = tuple(i for i, o in enumerate(ok) if g in o)
        if not idx:
            idx = tuple(range(len(details)))     # no way on record: all
        by_set.setdefault(idx, []).append(g)
    groups = []
    for idx, gs in by_set.items():
        subset = [details[i] for i in idx]
        labels = _ring_labels(subset) if ring else _evolution_labels(subset)
        groups.append({"games": gs, "labels": labels})
    return groups


def _evolution_step_methods(details, games):
    """The ways one evolution step works, each with the games it works in:
    [{"text", "games"}], the games taken from `games` (the games the
    evolved Pokémon is in). Ways that read the same are joined."""
    def name(d, key):
        return ((d.get(key) or {}).get("name")) or ""

    details = details or []
    has_place = any(name(d, "location") for d in details)
    has_item = any(name(d, "trigger") == "use-item" for d in details)
    old_rock = {vg for vg in games
                if GAME_GENERATION.get(vg, 9) <= 7
                or vg == "brilliant-diamond-shining-pearl"}
    out = []
    for d in details:
        place = name(d, "location")
        text = _evolution_method(d)
        if place and has_item:
            stone = next((name(o, "item") for o in details
                          if name(o, "trigger") == "use-item"), "")
            rock = _ROCK_ITEMS.get(stone)
            if rock:
                text = "Level up near the %s (%s)" % (rock, _pretty(place))
        ok = _ok_games(d, details, games)
        if not ok:
            continue
        for row in out:
            if row["text"] == text:
                row["games"] = [g for g in games
                                 if g in set(row["games"]) | set(ok)]
                break
        else:
            out.append({"text": text, "games": ok})
    # a game none of the records above covers (no rock place on record for it)
    # still gets a way to evolve
    covered = {g for row in out for g in row["games"]}
    missing = [g for g in games if g not in covered]
    if missing and details:
        stone = next((name(o, "item") for o in details
                      if name(o, "trigger") == "use-item"), "")
        rock = _ROCK_ITEMS.get(stone) if has_place else None
        near = [g for g in missing if g in old_rock] if rock else []
        if near:
            out.append({"text": "Level up near the %s" % rock, "games": near})
        rest = [g for g in missing if g not in near]
        if rest:
            joined = " or ".join(dict.fromkeys(
                _evolution_method(d) for d in details)) or "Special"
            out.append({"text": joined, "games": rest})
    return out


def _evolution_methods(tree, edge_details):
    """The compact evolution list of a line with many branches:

        {"root": {...the first Pokémon...},
         "rows": [{"name", "slug", "q", "form", "sprite", "sprite_alt",
                  "mega", "current", "games", "methods": [{"text",
                  "games"}]}]}

    One row per Pokémon (or Mega / Gigantamax form) the line leads to from its
    first Pokémon. The page shows the rows and methods of the selected game."""
    def games_of(node):
        # no Pokédex list for it: every game from its generation on
        return node.get("games") or [
            vg for vg, _ in VERSION_GROUPS
            if GAME_GENERATION.get(vg, 9) >= (node.get("gen") or 1)]

    rows = []
    for b in tree["branches"]:
        node = b["node"]
        games = games_of(node)
        if node.get("mega"):
            methods = [{"text": l["title"], "games": games}
                       for l in b["labels"] if l.get("title")]
        else:
            details = edge_details.get((tree["slug"], node["slug"]))
            methods = _evolution_step_methods(details, games)
            if not methods:
                methods = [{"text": l["title"] or "Special", "games": games}
                           for l in b["labels"]]
        rows.append({
            "name": node["name"], "slug": node["slug"], "q": node["q"],
            "form": node["form"], "sprite": node["sprite"],
            "sprite_alt": node["sprite_alt"], "mega": bool(node.get("mega")),
            "current": False, "games": games, "methods": methods,
        })
    root = {k: tree[k] for k in ("name", "slug", "q", "form", "sprite",
                                "sprite_alt")}
    root["games"] = games_of(tree)
    return {"root": root, "rows": rows}


# ---- the Eevee ring (lines with many branches) -------------------------------
# A line with this many branches is drawn as a ring on the Details tab: the
# first Pokémon in the middle, what it evolves into around it, and its
# Gigantamax / Mega forms to the right of it (as in the normal evolution line,
# greyed out in the games they are not in). Evolutions that need a place or affection
# (Leafeon, Glaceon, Sylveon) only say "See Game Info tab" there.
def _needs_game_info(details):
    """True when a step has a place or an affection / move-type requirement
    too long for the ring (the Game Info tab explains it)."""
    def name(d, key):
        return ((d.get(key) or {}).get("name")) or ""
    return any(
        name(d, "location") or d.get("min_affection")
        or name(d, "known_move_type")
        for d in details or [])


def _ring_labels(details):
    if _needs_game_info(details):
        methods = []
        for d in details or []:
            m = _evolution_method(d)
            if m and m not in methods:
                methods.append(m)
        return [{
            "text": "Special", "sub": "See Game Info tab", "special": True,
            "item": "", "item_name": "", "item_sprite": "",
            "title": " or ".join(methods) or "Special",
        }]
    return _evolution_labels(details)


def _evolution_ring(tree, edge_details):
    """Where everything goes in the ring (percentages of a box 100 wide and
    `height` tall) and the arrows between them (in the box's own units)."""
    evo = [b for b in tree["branches"] if not b["node"].get("mega")]
    extra = [b for b in tree["branches"] if b["node"].get("mega")]
    cx, cy, radius = 50.0, 50.0, 38.0
    r_node, r_root = 10.0, 11.0
    # where on its arrow the label sits, from the evolved Pokémon's end (so
    # labels of neighbours near the middle Pokémon do not touch)
    label_at = 0.36
    height = 100.0

    def f(v):
        return "%.2f" % v

    def games_of(node):
        return node.get("games") or [
            vg for vg, _ in VERSION_GROUPS
            if GAME_GENERATION.get(vg, 9) >= (node.get("gen") or 1)]

    def entry(node, px, py, labels, extra=False, groups=None):
        # the arrow runs from the middle Pokémon out to this one, so its head
        # (marker-end) always points away from the middle
        dx, dy = px - cx, py - cy
        dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        x1, y1 = cx + ux * r_root, cy + uy * r_root
        x2, y2 = px - ux * r_node, py - uy * r_node
        return {
            "name": node["name"], "slug": node["slug"], "q": node["q"],
            "form": node["form"], "sprite": node["sprite"],
            "sprite_alt": node["sprite_alt"], "gen": node.get("gen") or 1,
            "games": games_of(node), "mega": bool(node.get("mega")),
            "current": bool(node.get("current")), "extra": extra,
            "left": f(px), "top": f(py / height * 100),
            "x1": f(x1), "y1": f(y1), "x2": f(x2), "y2": f(y2),
            "lx": f(x2 + (x1 - x2) * label_at),
            "ly": f((y2 + (y1 - y2) * label_at) / height * 100),
            "labels": labels,
            # the labels of each game, when they differ between games
            "label_groups": groups or [],
        }

    def place(angle_deg):
        a = math.radians(angle_deg)                     # clockwise from top
        return cx + radius * math.sin(a), cy - radius * math.cos(a)

    # Gigantamax / Mega forms take the slots centred on the right-hand side
    # (90 degrees), the evolutions the rest, so the ring has the same layout
    # in every game (a form that is not in the game is greyed out, not
    # removed)
    slots = len(evo) + len(extra)
    step = 360.0 / max(slots, 1)
    extras = []
    for j, b in enumerate(extra):
        px, py = place(90.0 + (j - (len(extra) - 1) / 2.0) * step)
        extras.append(entry(b["node"], px, py, b["labels"], extra=True))
    # the slots left over, numbered clockwise from the top
    start = (90.0 + (len(extra) - 1) / 2.0 * step + step) if extra else step / 2
    free = sorted((start + k * step) % 360 for k in range(len(evo)))
    nodes = []
    for i, b in enumerate(evo):
        px, py = place(free[i])
        details = edge_details.get((tree["slug"], b["node"]["slug"]))
        groups = _label_groups(details, games_of(b["node"]), ring=True)
        nodes.append(entry(b["node"], px, py, _ring_labels(details),
                           groups=groups if len(groups) > 1 else None))
    root = {k: tree[k] for k in ("name", "slug", "q", "form", "sprite",
                                "sprite_alt", "current")}
    root["games"] = games_of(tree)
    root["gen"] = tree.get("gen") or 1
    root["left"] = "50.00"
    root["top"] = f(cy / height * 100)
    return {"height": f(height), "root": root, "nodes": nodes,
            "extras": extras}


# A Pokémon whose forms each have a line of their own: Kubfu evolves into
# Single Strike Urshifu with the Scroll of Darkness and into Rapid Strike
# Urshifu with the Scroll of Waters, and each of them has its own Gigantamax
# form. species -> [(variety, the item that leads to it)]
_FORM_EVOLUTIONS = {
    "urshifu": [
        ("urshifu-single-strike", "scroll-of-darkness"),
        ("urshifu-rapid-strike", "scroll-of-waters"),
    ],
}


def _split_form_evolutions(tree, species_of):
    """Replace the single node of a species in _FORM_EVOLUTIONS by one node
    per form, each with the Gigantamax form that belongs to it."""
    def walk(node):
        branches = []
        for b in node["branches"]:
            child = b["node"]
            forms = _FORM_EVOLUTIONS.get(child["slug"])
            if forms and not child["mega"]:
                branches.extend(_form_branches(b, child, forms, species_of))
            else:
                walk(child)
                branches.append(b)
        node["branches"] = branches

    walk(tree)


def _form_branches(branch, child, forms, species_of):
    species = species_of.get(child["slug"]) or {}
    varieties = {
        (v.get("pokemon") or {}).get("name"): v.get("pokemon") or {}
        for v in species.get("varieties") or []
    }
    out = []
    for variety, item in forms:
        vp = varieties.get(variety)
        if not vp:
            return [branch]     # PokeAPI does not list it: keep the one node
        icons = _sprite_candidates(_id_from_url(vp.get("url")))
        node = dict(child)
        node.update(
            q=variety, form=variety, name=_pretty(variety),
            sprite=icons[0] if icons else child["sprite"],
            sprite_alt=" ".join(icons[1:]),
            # that form's own Gigantamax form, not the other one's
            branches=[
                g for g in child["branches"]
                if (g["node"]["form"] or "").startswith(variety + "-")
            ],
        )
        labels = [lab for lab in branch["labels"] if lab.get("item") == item]
        out.append({"labels": labels or branch["labels"], "node": node})
    return out


# Rockruff evolves into one of three Lycanroc forms. A normal Rockruff turns
# into Midday Lycanroc (in the day) or Midnight Lycanroc (at night); Dusk
# Lycanroc only comes from an Own Tempo Rockruff (at dusk), so that is a line
# of its own, drawn below the other.
_LYCANROC_NORMAL = (
    ("lycanroc-midday", "Lycanroc (Midday)", "day",
     "Level %s during the day"),
    ("lycanroc-midnight", "Lycanroc (Midnight)", "night",
     "Level %s at night"),
)
_LYCANROC_DUSK = (
    "lycanroc-dusk", "Lycanroc (Dusk)", "dusk",
    "Level %s at dusk. Only a Rockruff with the Own Tempo ability evolves "
    "into Dusk Lycanroc",
)


def _variety_of(species, name):
    """The {"name", "url"} record of one variety of a species, or None."""
    for v in (species or {}).get("varieties") or []:
        vp = v.get("pokemon") or {}
        if vp.get("name") == name:
            return vp
    return None


def _find_line_node(node, slug):
    """The node of a species in a line (not its Mega forms), or None."""
    if node["slug"] == slug and not node.get("mega"):
        return node
    for b in node["branches"]:
        hit = _find_line_node(b["node"], slug)
        if hit:
            return hit
    return None


def _split_lycanroc(tree, species_of, edge_details, extra_lines):
    """Draw every Lycanroc form in Rockruff's line: Midday and Midnight
    from Rockruff, Dusk from Own Tempo Rockruff in a line of its own (added
    to `extra_lines`). Leaves the line alone when PokeAPI does not list a
    form."""
    rock = _find_line_node(tree, "rockruff")
    lyc = species_of.get("lycanroc") or {}
    rock_species = species_of.get("rockruff") or {}
    if rock is None or not lyc:
        return
    branch = next((b for b in rock["branches"]
                   if b["node"]["slug"] == "lycanroc"), None)
    if branch is None:
        return
    child = branch["node"]
    details = edge_details.get(("rockruff", "lycanroc")) or []
    level = next((d["min_level"] for d in details if d.get("min_level")), 25)

    def label(when, title):
        return {"text": str(level), "sub": when, "item": "", "move": "",
                "item_name": "", "item_sprite": "", "title": title % level}

    def form_node(spec):
        variety, name, when, title = spec
        vp = _variety_of(lyc, variety)
        if not vp:
            return None, None
        icons = _sprite_candidates(_id_from_url(vp.get("url")))
        node = dict(child)
        node.update(
            q=variety, form=variety, name=name, current=False,
            sprite=icons[0] if icons else child["sprite"],
            sprite_alt=" ".join(icons[1:]) if icons else child["sprite_alt"],
            branches=[],
        )
        return node, {"labels": [label(when, title)], "node": node}

    normal = [form_node(spec)[1] for spec in _LYCANROC_NORMAL]
    if not all(normal):
        return
    at = rock["branches"].index(branch)
    rock["branches"][at:at + 1] = normal
    dusk_branch = form_node(_LYCANROC_DUSK)[1]
    own = _variety_of(rock_species, "rockruff-own-tempo")
    if dusk_branch and own:
        icons = _sprite_candidates(_id_from_url(own.get("url")))
        root = dict(rock)
        root.update(
            q="rockruff-own-tempo", form="rockruff-own-tempo",
            name="Own Tempo Rockruff", current=False,
            sprite=icons[0] if icons else rock["sprite"],
            sprite_alt=" ".join(icons[1:]) if icons else rock["sprite_alt"],
            branches=[dusk_branch],
        )
        extra_lines.append(root)


# A Pokémon that evolves into a different form depending on its gender:
# species -> (requirement, [(variety, gender symbol, gender word)]). The
# requirement is the big text above both arrows, the symbol what tells them
# apart.
_GENDER_EVOLUTIONS = {
    "basculegion": (
        "294 recoil",
        "Level up after losing at least 294 HP to recoil damage without "
        "fainting (Legends: Arceus: no level up needed)",
        [("basculegion-male", "\u2642", "Male"),
         ("basculegion-female", "\u2640", "Female")],
    ),
}


def _gender_branches(child, spec, species):
    """Two branches for one evolution that depends on gender: the male form
    and the female form, each labelled with the requirement and which
    gender evolves into it. Both link to the Pokémon's own page, which has
    no Form buttons."""
    text, how, forms = spec
    varieties = {
        (v.get("pokemon") or {}).get("name"): v.get("pokemon") or {}
        for v in (species or {}).get("varieties") or []
    }
    out = []
    for variety, symbol, word in forms:
        vp = varieties.get(variety)
        icons = _sprite_candidates(_id_from_url((vp or {}).get("url")))
        node = dict(child)
        node.update(
            name="%s (%s)" % (child["name"], word),
            q=variety, form=variety,
            gender_split=True,
            sprite=icons[0] if icons else child["sprite"],
            sprite_alt=" ".join(icons[1:]) if icons else child["sprite_alt"],
        )
        out.append({
            "labels": [{
                "text": text,
                "sub": "%s %s only" % (symbol, word),
                "item": "", "move": "", "item_name": "", "item_sprite": "",
                "title": "%s. Only a %s White-Striped Basculin evolves into "
                         "%s." % (how, word.lower(), node["name"]),
            }],
            "node": node,
        })
    return out


def _evolution_tree(chain, current_species, current_form):
    """The whole evolution line as a tree for the Details tab, or None for a
    Pokémon that neither evolves nor has a Mega Evolution:

        {"slug", "q", "form", "name", "sprite", "gen", "mega", "games",
         "current", "branches": [{"labels": [...], "node": {...}}]}

    Branches are the Pokémon it evolves into, then its Mega / Primal forms.
    The node for `current_form` (the form on screen) is marked current; when
    that form has no node, the Pokémon's own node is."""
    root = (chain or {}).get("chain") or {}
    if not (root.get("species") or {}).get("name"):
        return None

    slugs = []

    def collect(n):
        slug = (n.get("species") or {}).get("name")
        if slug and slug not in slugs:
            slugs.append(slug)
        for child in n.get("evolves_to") or []:
            collect(child)

    collect(root)
    with ThreadPoolExecutor(max_workers=6) as pool:
        species_of = dict(zip(slugs, pool.map(_fetch_species_cached, slugs)))

    # the evolution_details of each step of the chain, for the labels above
    # the arrows of the regional lines
    edge_details = {}

    def collect_edges(n):
        parent = (n.get("species") or {}).get("name")
        for child in n.get("evolves_to") or []:
            edge_details[(parent, (child.get("species") or {}).get("name"))] \
                = child.get("evolution_details")
            collect_edges(child)

    collect_edges(root)
    by_slug = {}

    def build(n):
        sp = n.get("species") or {}
        slug = sp.get("name")
        species = species_of.get(slug) or {}
        sid = species.get("id") or _id_from_url(sp.get("url"))
        default = {}
        for v in species.get("varieties") or []:
            if v.get("is_default"):
                default = v.get("pokemon") or {}
                break
        pid = _id_from_url(default.get("url")) or sid
        gen = _species_generation(sid) if sid else 1
        icons = _sprite_candidates(pid)
        node = {
            "slug": slug, "q": slug, "form": default.get("name") or slug,
            "name": _pretty(slug),
            "sprite": icons[0] if icons else "",
            "sprite_alt": " ".join(icons[1:]),
            "gen": gen, "mega": False, "current": False,
            "games": _species_games(species, sid),
            "branches": [],
        }
        by_slug[slug] = node
        for child in n.get("evolves_to") or []:
            if (child.get("species") or {}).get("name") in _REGIONAL_ONLY:
                continue    # only reached from a regional form: its own line
            node["branches"].append({
                "labels": _evolution_labels(child.get("evolution_details")),
                "node": build(child),
            })
        branches, extras = _transformation_branches(slug, species, gen)
        node["branches"].extend(branches)
        extra_lines.extend(extras)
        return node

    def regional_node(slug, variety, region):
        """A node of a regional line: the regional form (its own sprite and
        id) or, for variety None, the Pokémon's normal form."""
        species = species_of.get(slug) or {}
        sid = species.get("id")
        pokemon = {}
        for v in species.get("varieties") or []:
            vp = v.get("pokemon") or {}
            if (variety and vp.get("name") == variety) or (
                    not variety and v.get("is_default")):
                pokemon = vp
                break
        if not pokemon:
            return None
        pid = _id_from_url(pokemon.get("url")) or sid
        icons = _sprite_candidates(pid)
        gated = bool(variety) or slug in _REGIONAL_ONLY
        if variety == "basculin-white-striped":
            name = "White-Striped Basculin"
        elif variety:
            name = "%s %s" % (_REGION_ADJ[region], _pretty(slug))
        else:
            name = _pretty(slug)
        return {
            "slug": slug, "q": pokemon.get("name") or slug,
            "form": pokemon.get("name") or slug, "name": name,
            "sprite": icons[0] if icons else "",
            "sprite_alt": " ".join(icons[1:]),
            "gen": _species_generation(sid) if sid else 1,
            "mega": False, "regional": gated, "gated": gated,
            "games": [vg for vg, _ in VERSION_GROUPS
                      if (vg in _REGION_GAMES[region]
                          or vg in _REGIONAL_EXTRA_GAMES.get(
                              variety or slug, ()))
                      and not (vg == "champions"
                               and variety in _NOT_IN_CHAMPIONS)] if gated
                     else _species_games(species, sid),
            "branches": [], "current": False,
        }

    def regional_line(spec, region):
        slug, variety, kids = spec
        node = regional_node(slug, variety, region)
        if node is None:
            return None
        for kid in kids:
            child = regional_line(kid, region)
            if child is None:
                continue
            if kid[0] in _GENDER_EVOLUTIONS:
                node["branches"].extend(_gender_branches(
                    child, _GENDER_EVOLUTIONS[kid[0]],
                    species_of.get(kid[0])))
                continue
            item = _REGIONAL_EVO_ITEMS.get(
                (variety or slug, kid[1] or kid[0]))
            labels = [{
                "text": "", "sub": "", "item": item, "move": "",
                "item_name": _item_display(item),
                "item_sprite": ITEM_SPRITE_BASE + item + ".png",
                "title": _item_display(item),
            }] if item else _evolution_labels(
                edge_details.get((slug, kid[0])))
            node["branches"].append({"labels": labels, "node": child})
        return node

    extra_lines = []
    tree = build(root)
    _split_form_evolutions(tree, species_of)
    if "rockruff" in species_of and "lycanroc" in species_of:
        _split_lycanroc(tree, species_of, edge_details, extra_lines)
    for region, spec in _REGIONAL_LINES:
        if not all(n[0] in species_of for n in _regional_walk(spec)):
            continue            # that line is not part of this chain
        if spec[1]:
            line = regional_line(spec, region)      # a line of its own
            if line is not None:
                extra_lines.append(line)
            continue
        host = by_slug.get(spec[0])                 # hangs off a normal one
        if host is None:
            continue
        for kid in spec[2]:
            child = regional_line(kid, region)
            if child is None:
                continue
            labels = _evolution_labels(edge_details.get((spec[0], kid[0])))
            # after the normal evolutions, before the Mega / Gigantamax ones
            at = sum(1 for b in host["branches"] if not b["node"]["mega"])
            host["branches"].insert(at, {"labels": labels, "node": child})
    # The normal (Unovian Red / Blue-Striped) Basculin is not drawn: the line
    # is just White-Striped -> Basculegion, on every Basculin page. All three
    # striped forms are the Form buttons instead.
    basculin_species = current_species == "basculin"
    if extra_lines and basculin_species:
        tree = extra_lines.pop(0)
    elif not tree["branches"] and extra_lines:
        # nothing evolves the normal way (Qwilfish): the regional line is
        # the line
        tree = extra_lines.pop(0)
    if not tree["branches"] and not extra_lines:
        return None
    # lines for forms that have a Mega of their own (Eternal Flower Floette),
    # drawn below the normal line
    tree["extra_lines"] = extra_lines

    nodes = []

    def flatten(node):
        nodes.append(node)
        for b in node["branches"]:
            flatten(b["node"])

    flatten(tree)
    for line in extra_lines:
        flatten(line)

    # Pokémon cannot evolve in Pokémon Champions: what a line evolves into is
    # not in that game (Mega forms are, so they keep it), so the page shows no
    # way to evolve there.
    def no_champions(node):
        for b in node["branches"]:
            child = b["node"]
            if not child.get("mega"):
                had = child.get("games") or []
                kept = [g for g in had if g != "champions"]
                # an empty list would mean "every game from its generation"
                child["games"] = kept or (["none"] if had else [])
            no_champions(child)

    no_champions(tree)
    for line in extra_lines:
        no_champions(line)

    # A step that works differently from game to game (a stone in one, a
    # place in another, a style move in Legends Arceus) shows only the way
    # of the selected game: the labels of each game's way are drawn and the
    # page keeps the one that applies.
    def per_game_labels(node):
        for b in node["branches"]:
            child = b["node"]
            games = child.get("games") or []
            details = edge_details.get((node["slug"], child["slug"]))
            if (details and games and not child.get("mega")
                    and (node.get("q"), child.get("q"))
                    not in _REGIONAL_EVO_ITEMS):
                groups = _label_groups(details, games)
                if len(groups) > 1:
                    b["label_groups"] = groups
            per_game_labels(child)

    per_game_labels(tree)
    for line in extra_lines:
        per_game_labels(line)
    if current_species == "zygarde":
        # Mega Zygarde comes from Zygarde Complete, so on every Zygarde page
        # the Zygarde in the line is drawn (and linked) as that form
        complete = _get_pokemon_json("zygarde-complete")
        icons = _sprite_candidates((complete or {}).get("id"))
        if icons:
            for n in nodes:
                if n["slug"] == "zygarde" and not n["mega"]:
                    n["form"] = n["q"] = "zygarde-complete"
                    n["name"] = "Zygarde Complete"
                    n["sprite"] = icons[0]
                    n["sprite_alt"] = " ".join(icons[1:])
    # on the Red / Blue-Striped pages no node of the line is the page viewed
    basculin_page = (basculin_species
                     and current_form != "basculin-white-striped")
    target = None
    if not basculin_page:
        target = next((n for n in nodes if n["form"] == current_form), None)
        if target is None:
            target = next((n for n in nodes
                           if n["slug"] == current_species and not n["mega"]
                           and not n.get("regional")), None)
        if target is None:
            target = next((n for n in nodes
                           if n["slug"] == current_species
                           and not n["mega"]), None)
    if target is not None:
        target["current"] = True
        # both gender forms are this one page
        for n in nodes:
            if n.get("gender_split") and n["slug"] == target["slug"]:
                n["current"] = True
    # a line with this many branches is listed (by game) rather than drawn
    tree["compact"] = len(tree["branches"]) > _COMPACT_BRANCHES
    if tree["compact"]:
        tree["methods"] = _evolution_methods(tree, edge_details)
        tree["ring"] = _evolution_ring(tree, edge_details)
    return tree


def _encounter_info(source, game):
    """Where the Pokemon is found in the wild in this game."""
    versions = GAME_VERSIONS.get(game, [])
    wanted = set(versions)
    rows = {}
    chance = {}
    for area in source.get("encounters") or []:
        loc = (area.get("location_area") or {}).get("name") or ""
        if loc.endswith("-area"):
            loc = loc[:-5]
        loc = _pretty(loc)
        for vd in area.get("version_details") or []:
            v = (vd.get("version") or {}).get("name")
            if v not in wanted:
                continue
            for ed in vd.get("encounter_details") or []:
                method = (ed.get("method") or {}).get("name") or "unknown"
                row = rows.setdefault((loc, method), {
                    "location": loc, "method": _pretty(method),
                    "min": None, "max": None, "chance": 0,
                    "versions": set(),
                })
                lo, hi = ed.get("min_level"), ed.get("max_level")
                if lo is not None:
                    row["min"] = lo if row["min"] is None else min(row["min"], lo)
                if hi is not None:
                    row["max"] = hi if row["max"] is None else max(row["max"], hi)
                row["versions"].add(v)
                key = (loc, method, v)
                chance[key] = chance.get(key, 0) + (ed.get("chance") or 0)
    out = []
    for (loc, method), row in sorted(rows.items()):
        best = max(chance[(loc, method, v)] for v in row["versions"])
        if row["min"] is None:
            levels = ""
        elif row["min"] == row["max"]:
            levels = str(row["min"])
        else:
            levels = "%s–%s" % (row["min"], row["max"])
        out.append({
            "location": loc,
            "method": row["method"],
            "levels": levels,
            "chance": min(best, 100),
            # blank when it is in every version of the game
            "versions": "" if row["versions"] == wanted else ", ".join(
                _pretty(v) for v in versions if v in row["versions"]
            ),
        })
    more = max(0, len(out) - MAX_ENCOUNTER_ROWS)

    # No rows from PokeAPI: use the written locations for this game.
    blocks = _load_location_notes().get(game) or []
    notes = []
    if not out:
        pid = str(source.get("species_id") or "")
        for block in blocks:
            hit = (block.get("pokemon") or {}).get(pid)
            if hit:
                notes.append({
                    "region": block.get("region", ""),
                    "text": hit.get("t", ""),
                    "version": hit.get("v", ""),
                })
    return {
        "rows": out[:MAX_ENCOUNTER_ROWS],
        "more": more,
        "notes": notes,
        # PokeAPI has no wild-location data for these games, but the
        # written locations cover them
        "supported": game not in NO_ENCOUNTER_DATA or bool(blocks),
        "from_notes": bool(blocks),
    }


def _dex_matches(game, dex_name):
    d = dex_name or ""
    if game == "legends-za":
        return "lumiose" in d or "hyperspace" in d
    if game == "champions":
        return "champions" in d
    return d in GAME_DEXES.get(game, ())


def _pick_past(records, key, game_gen):
    """past_types / past_abilities hold what a Pokemon had up to and including
    the generation named. The earliest such record at or after this game's
    generation is what applied in the game; None if the current data does."""
    best = None
    for rec in records or []:
        gen = _ROMAN_GEN.get((rec.get("generation") or {}).get("name"))
        if gen and gen >= game_gen and (best is None or gen < best[0]):
            best = (gen, rec.get(key) or [])
    return best[1] if best else None


def _gender_text(rate):
    if rate is None:
        return ""
    if rate < 0:
        return "Genderless"
    female = rate * 12.5
    return "%g%% male / %g%% female" % (100 - female, female)


def build_game_info(source, game):
    """Everything the Game Info tab shows for one game, from the data
    fetch_game_info_source collected. Pure, so it is cheap to call again
    each time another game button is picked."""
    gen = GAME_GENERATION.get(game, 9)
    versions = GAME_VERSIONS.get(game, [])
    species = source.get("species") or {}

    # types in that game
    types = _unique_types(source.get("types"))
    past = _pick_past(source.get("past_types"), "types", gen)
    if past:
        types = _unique_types(past)

    # abilities in that game (none before Gen 3, no hidden ones before Gen 5,
    # and none in Legends Arceus / Legends Z-A, which dropped abilities)
    abilities = []
    if gen >= 3 and game not in NO_ABILITY_GAMES:
        slots = _pick_past(source.get("past_abilities"), "abilities", gen)
        if slots is None:
            slots = source.get("abilities") or []
        for a in slots:
            ab = (a.get("ability") or {}).get("name")
            if not ab or (a.get("is_hidden") and gen < 5):
                continue
            abilities.append({"name": ab, "hidden": bool(a.get("is_hidden"))})

    # Pokedex entries and regional numbers
    entries = []
    seen = set()
    for e in species.get("flavor_text_entries") or []:
        if (e.get("language") or {}).get("name") != "en":
            continue
        v = (e.get("version") or {}).get("name")
        if not _version_in_game(game, v):
            continue
        text = " ".join((e.get("flavor_text") or "").split())
        if text and text not in seen:
            seen.add(text)
            entries.append({"version": _pretty(v), "text": text})
    if not entries:
        entries = _extra_dex_entries(species.get("name"), game)
    dex = [
        {"name": _pretty(p["pokedex"]["name"]), "number": p.get("entry_number")}
        for p in species.get("pokedex_numbers") or []
        if _dex_matches(game, (p.get("pokedex") or {}).get("name"))
    ]

    # facts
    facts = []
    # the Pokédex data that used to sit on the Details tab: species, size,
    # EV yield and base experience of the form on screen
    ff = _form_facts({
        "height": source.get("height"), "weight": source.get("weight"),
        "base_experience": source.get("base_experience"),
        "stats": source.get("stats") or [],
    })
    facts.append(("Species", _species_facts(species)["genus"] or "—"))
    facts.append(("Height", "%s m" % ff["height"] if ff["height"] else "—"))
    facts.append(("Weight", "%s kg" % ff["weight"] if ff["weight"] else "—"))
    facts.append(("EV yield", ff["ev_yield"]))
    facts.append(("Base exp", ff["base_exp"]
                  if ff["base_exp"] is not None else "—"))
    intro = _species_generation(species.get("id") or source.get("species_id"))
    facts.append(("Introduced in", "Generation %d" % intro))
    for flag, label in (("is_baby", "Baby Pokémon"),
                        ("is_legendary", "Legendary"),
                        ("is_mythical", "Mythical")):
        if species.get(flag):
            facts.append(("Category", label))
    if species.get("capture_rate") is not None:
        facts.append(("Catch rate", species["capture_rate"]))
    if species.get("growth_rate"):
        facts.append(("Growth rate", _pretty(species["growth_rate"]["name"])))
    if gen >= 2:
        if species.get("base_happiness") is not None:
            facts.append(("Base friendship", species["base_happiness"]))
        eggs = [_pretty(g["name"]) for g in species.get("egg_groups") or []]
        if eggs:
            facts.append(("Egg groups", ", ".join(eggs)))
        if species.get("hatch_counter") is not None:
            facts.append(("Egg cycles", species["hatch_counter"]))
        gender = _gender_text(species.get("gender_rate"))
        if gender:
            facts.append(("Gender ratio", gender))

    encounters = _encounter_info(source, game)
    return {
        "game": game,
        "label": dict(VERSION_GROUPS).get(game, _pretty(game)),
        "generation": gen,
        "name": _pretty(source.get("species_name") or source.get("name")),
        "species": source.get("species_name") or source.get("name"),
        "evolution": _evolution_info(source, gen, game),
        "encounters": encounters,
        "evolves_from": _pretty(
            (species.get("evolves_from_species") or {}).get("name")
        ),
        "types": types,
        "abilities": abilities,
        "no_abilities": gen < 3 or game in NO_ABILITY_GAMES,
        "dex": dex,
        "entries": entries,
        "facts": facts,
        "not_yet": intro > gen,
    }


def fetch_game_info_source(name):
    """Everything the Game Info tab needs about a Pokemon, game-independent:
    its record, species, evolution chain and wild encounters. Cached for an
    hour so flicking between games does not re-download it. None when the
    Pokemon is not found."""
    key = (name or "").strip().lower()
    if not key:
        return None
    hit = _GAME_INFO_CACHE.get(key)
    if hit and time.time() - hit[0] < GAME_INFO_CACHE_SECONDS:
        return hit[1]
    data = _get_pokemon_json(key)
    if not data:
        return None
    species_name = (data.get("species") or {}).get("name")
    species = _fetch_species(species_name)
    species_id = species.get("id") or data.get("id")
    chain_url = (species.get("evolution_chain") or {}).get("url")
    with ThreadPoolExecutor(max_workers=2) as pool:
        chain_job = pool.submit(_get_json, chain_url) if chain_url else None
        enc_job = (
            pool.submit(_get_json, POKEAPI_ENCOUNTERS.format(species_id))
            if species_id else None
        )
        chain = chain_job.result() if chain_job else None
        chain = _clean_chain(chain) if isinstance(chain, dict) else chain
        encounters = enc_job.result() if enc_job else None
    source = {
        "name": data.get("name"),
        "species_name": species_name or data.get("name"),
        "species_id": species_id,
        "species": species,
        "types": data.get("types") or [],
        "past_types": data.get("past_types") or [],
        "abilities": data.get("abilities") or [],
        "past_abilities": data.get("past_abilities") or [],
        # size, base experience and EV yield of this form (Pokédex data box)
        "height": data.get("height"),
        "weight": data.get("weight"),
        "base_experience": data.get("base_experience"),
        "stats": data.get("stats") or [],
        "chain": chain if isinstance(chain, dict) else {},
        "encounters": encounters if isinstance(encounters, list) else [],
    }
    if len(_GAME_INFO_CACHE) > 200:
        _GAME_INFO_CACHE.clear()
    _GAME_INFO_CACHE[key] = (time.time(), source)
    return source


def fetch_game_info(name, game):
    """Game Info for one Pokemon in one game (None if either is unknown)."""
    if game not in GAME_VERSIONS:
        return None
    source = fetch_game_info_source(name)
    if not source:
        return None
    return build_game_info(source, game)


# ---- search filters --------------------------------------------------------
# The filter bars next to every search box (Type, Generation, Gender, Form...)
# narrow the suggestion lists. Each build_*_filters() returns a small dict
# keyed by name, which views.filter_data() caches on disk; the browser does
# the actual filtering (static/pokedex/filters.js), so nothing here runs per
# keystroke. A builder returns None when PokeAPI could not be reached, so a
# partial answer is never cached.
POKEAPI_ROOT = "https://pokeapi.co/api/v2/"
_GENERATION_CACHE = []


def _names_at(data, key):
    """[name, ...] of the {name, url} entries under data[key]."""
    return [e["name"] for e in (data.get(key) or []) if e.get("name")]


def _generation_data():
    """[{n, species (ids), moves, abilities}] for every generation PokeAPI
    lists (one request each); [] when it cannot be reached."""
    if _GENERATION_CACHE:
        return _GENERATION_CACHE
    index = _get_json(POKEAPI_ROOT + "generation?limit=100")
    ids = [
        _mon_id(g.get("url"))
        for g in ((index or {}).get("results") or [])
    ]
    ids = [i for i in ids if i]
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = list(pool.map(
            lambda i: _get_json(POKEAPI_ROOT + "generation/%d" % i), ids,
        ))
    if not all(isinstance(d, dict) for d in loaded):
        return []   # retry next time rather than cache a partial list
    for n, d in zip(ids, loaded):
        _GENERATION_CACHE.append({
            "n": n,
            "species": {
                _mon_id(e.get("url")) for e in d.get("pokemon_species") or []
            },
            "moves": set(_names_at(d, "moves")),
            "abilities": set(_names_at(d, "abilities")),
        })
    return _GENERATION_CACHE


def _form_kind(name, pid):
    """base / mega / gmax / tera / other, for the Form filter."""
    name = name or ""
    if not pid or int(pid) < 10000:
        return "base"
    if "-mega" in name:
        return "mega"
    if "-gmax" in name or "-eternamax" in name:
        return "gmax"
    if "-tera" in name or "-stellar" in name:
        return "tera"
    if any(r in name for r in ("-alola", "-galar", "-hisui", "-paldea")):
        return "regional"
    if name.endswith("-female"):
        return "gender"
    return "other"


def _gender_sets():
    """(species ids that can be female, species ids that can be male), or
    None. A species in neither set is genderless."""
    sets = []
    for g in ("female", "male"):
        d = _get_json(POKEAPI_ROOT + "gender/" + g)
        if not isinstance(d, dict):
            return None
        sets.append({
            _mon_id((e.get("pokemon_species") or {}).get("url"))
            for e in d.get("pokemon_species_details") or []
        })
    return sets[0], sets[1]


def build_pokemon_filters(mons):
    """{pokemon name: {"t": [types], "g": generation, "x": gender, "f": form}}
    for the [{id, name}, ...] of the Pokemon search list. Gender is "both",
    "female" (only), "male" (only) or "none" (genderless)."""
    types = _mon_type_map()
    species_dex = _species_dex_map()
    gens = _generation_data()
    genders = _gender_sets()
    if not (types and gens and genders and species_dex):
        return None
    gen_of = {}
    for g in gens:
        for sid in g["species"]:
            gen_of[sid] = g["n"]
    female, male = genders
    out = {}
    for m in mons:
        try:
            pid = int(m["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = m.get("name") or ""
        sid = _dex_number(name, pid, species_dex)
        can_f, can_m = sid in female, sid in male
        out[name] = {
            "t": types.get(pid, []),
            "g": gen_of.get(sid),
            "x": ("both" if can_f and can_m else "female" if can_f
                  else "male" if can_m else "none"),
            "f": _form_kind(name, pid),
        }
    return out


def build_move_filters():
    """{move name: {"t": type, "c": physical / special / status,
    "g": generation}}."""
    index = _get_json(POKEAPI_ROOT + "type?limit=100")
    type_names = [
        t["name"] for t in (index or {}).get("results") or []
        if t.get("name") and t["name"] not in ("unknown", "shadow")
    ]
    classes = ("physical", "special", "status")
    gens = _generation_data()
    if not (type_names and gens):
        return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        by_type = list(pool.map(
            lambda t: _get_json(POKEAPI_TYPE.format(t)), type_names,
        ))
        by_class = list(pool.map(
            lambda c: _get_json(POKEAPI_ROOT + "move-damage-class/" + c),
            classes,
        ))
    if not all(isinstance(d, dict) for d in by_type + by_class):
        return None
    out = {}
    for t, d in zip(type_names, by_type):
        for move in _names_at(d, "moves"):
            out.setdefault(move, {})["t"] = t
    for c, d in zip(classes, by_class):
        for move in _names_at(d, "moves"):
            out.setdefault(move, {})["c"] = c
    for g in gens:
        for move in g["moves"]:
            if move in out:
                out[move]["g"] = g["n"]
    # moves PokeAPI does not have yet (supplements.EXTRA_MOVES)
    newest = max(g["n"] for g in gens)
    for name, extra in supplements.EXTRA_MOVES.items():
        out[name] = {
            "t": extra.get("type"), "c": extra.get("damage_class"),
            "g": newest,
        }
    return out


def build_ability_filters():
    """{ability name: {"g": generation}}."""
    gens = _generation_data()
    if not gens:
        return None
    out = {}
    for g in gens:
        for ability in g["abilities"]:
            out[ability] = {"g": g["n"]}
    return out


def build_item_filters():
    """{battle item name: {"c": category, "u": ["battle", "held"]}}. "battle"
    items can be used from the bag in a fight, "held" ones do something
    while a Pokemon holds them."""
    index = _get_json(POKEAPI_ROOT + "item-category?limit=200")
    cats = [
        c["name"] for c in (index or {}).get("results") or [] if c.get("name")
    ]
    attrs = ("usable-in-battle", "holdable", "holdable-passive",
             "holdable-active")
    if not cats:
        return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        by_cat = list(pool.map(
            lambda c: _get_json(POKEAPI_ROOT + "item-category/" + c), cats,
        ))
        by_attr = list(pool.map(
            lambda a: _get_json(POKEAPI_ROOT + "item-attribute/" + a), attrs,
        ))
    if not all(isinstance(d, dict) for d in by_cat + by_attr):
        return None
    out = {}
    for c, d in zip(cats, by_cat):
        for item in _names_at(d, "items"):
            out[_plain_item_name(item)] = {"c": c, "u": []}
    for a, d in zip(attrs, by_attr):
        use = "battle" if a == "usable-in-battle" else "held"
        for item in _names_at(d, "items"):
            entry = out.get(_plain_item_name(item))
            if entry is not None and use not in entry["u"]:
                entry["u"].append(use)
    # Mega Stones and Z-Crystals are held, whatever PokeAPI tags them
    for item in MEGA_STONES + Z_CRYSTALS:
        entry = out.get(item)
        if entry is not None and "held" not in entry["u"]:
            entry["u"].append("held")
    return out