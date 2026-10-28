"""OPTIONAL bulk version of what the site already does by itself.

The site fills these gaps on demand (see api._fill_gaps): the first time a
Pokémon with missing data is opened, it is read from Bulbapedia and saved.
This command just does it for a whole game in one go.

Fill the gaps PokeAPI has for Scarlet / Violet and Legends: Z-A, using
Bulbapedia's MediaWiki API (one page per Pokémon).

    python manage.py load_extras --game legends-za
    python manage.py load_extras --game scarlet-violet --what dex
    python manage.py load_extras --game legends-za --names pikachu raichu

Finds the Pokémon in that game's Pokédex whose Pokédex entry or move list
PokeAPI lacks, scrapes just those, and merges the result into
pokedex/data/extra_dex_entries.json and extra_learnsets.json. Safe to stop
and re-run: pages are cached and finished Pokémon are skipped.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from pokedex import api, bulbapedia

DATA = Path(api.__file__).resolve().parent / "data"
DEX_FILE = DATA / "extra_dex_entries.json"
MOVES_FILE = DATA / "extra_learnsets.json"
POKEAPI = "https://pokeapi.co/api/v2/"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def roster(game):
    """Species names in the game's Pokédex(es), from PokeAPI."""
    listing = api._get_json(POKEAPI + "pokedex?limit=100") or {}
    dexes = [
        d["name"] for d in listing.get("results", [])
        if api._dex_matches(game, d["name"])
    ]
    names = set()
    for dex in dexes:
        data = api._get_json(POKEAPI + "pokedex/" + dex) or {}
        for e in data.get("pokemon_entries", []):
            names.add(e["pokemon_species"]["name"])
    return sorted(names)


class Command(BaseCommand):
    help = "Scrape missing Pokédex entries and move pools into data/*.json"

    def add_arguments(self, parser):
        parser.add_argument("--game", required=True,
                            choices=sorted(bulbapedia.GAME_LABEL))
        parser.add_argument("--what", nargs="+", default=["dex", "moves"],
                            choices=["dex", "moves"])
        parser.add_argument("--names", nargs="+",
                            help="only these species (PokeAPI names)")
        parser.add_argument("--limit", type=int, default=0,
                            help="stop after this many Pokémon (0 = all)")
        parser.add_argument("--delay", type=float, default=1.5,
                            help="seconds between requests to a site")
        parser.add_argument("--refresh", action="store_true",
                            help="also redo Pokémon already scraped")

    def handle(self, *args, game, what, names, limit, delay, refresh,
               **options):
        if bulbapedia.BeautifulSoup is None:
            raise CommandError(
                "BeautifulSoup is not installed. Run: pip install beautifulsoup4"
            )
        fetcher = bulbapedia.Fetcher(DATA / ".bulbapedia_cache", delay=delay)
        species_list = names or roster(game)
        if not species_list:
            raise CommandError(
                "PokeAPI lists no Pokédex for %s yet; pass --names." % game
            )
        dex_data, move_data = _read(DEX_FILE), _read(MOVES_FILE)
        done = 0
        for i, sp_name in enumerate(species_list, 1):
            species = api._fetch_species(sp_name)
            default = api._default_variety(species) or sp_name
            todo = []
            if "dex" in what and (refresh or not self._has_dex(
                    species, game, dex_data)):
                todo.append("dex")
            if "moves" in what and (refresh or not self._has_moves(
                    default, game, move_data)):
                todo.append("moves")
            if not todo:
                continue
            if limit and done >= limit:
                break
            done += 1
            title = bulbapedia.pokemon_title(self._english_name(species, sp_name))
            result = bulbapedia.scrape_page(
                fetcher, title, game,
                dex="dex" in todo, moves="moves" in todo,
            )
            if result is None:
                self.stdout.write("[%d/%d] %s: no Bulbapedia page (%s)" % (
                    i, len(species_list), sp_name, title))
                continue
            got = []
            if "dex" in todo:
                if result["dex"]:
                    dex_data.setdefault(game, {})[sp_name] = result["dex"]
                got.append("dex %d" % len(result["dex"]))
            if "moves" in todo:
                if result["moves"]:
                    kept = [r for r in move_data.get(default, [])
                            if r[1] != game]
                    move_data[default] = kept + result["moves"]
                got.append("moves %d" % len(result["moves"]))
            self.stdout.write("[%d/%d] %s: %s" % (
                i, len(species_list), sp_name, ", ".join(got)))
            if done % 10 == 0:
                _write(DEX_FILE, dex_data)
                _write(MOVES_FILE, move_data)
        _write(DEX_FILE, dex_data)
        _write(MOVES_FILE, move_data)
        self.stdout.write(self.style.SUCCESS("Done: %d Pokémon." % done))

    @staticmethod
    def _english_name(species, fallback):
        for n in species.get("names") or []:
            if (n.get("language") or {}).get("name") == "en" and n.get("name"):
                return n["name"]
        return fallback.replace("-", " ").title()

    @staticmethod
    def _has_dex(species, game, scraped):
        if (scraped.get(game) or {}).get(species.get("name") or ""):
            return True
        # PokeAPI's own entries (extras are merged, so look at the raw list)
        for e in species.get("flavor_text_entries") or []:
            v = (e.get("version") or {}).get("name")
            if (e.get("language") or {}).get("name") == "en" and \
                    api._version_in_game(game, v):
                return True
        return False

    @staticmethod
    def _has_moves(default, game, scraped):
        if any(r[1] == game for r in scraped.get(default, [])):
            return True
        data = api._get_pokemon_json(default) or {}
        return any(m["version_group"] == game
                   for m in api._moves_from_data(data))