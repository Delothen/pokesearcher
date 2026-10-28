"""OPTIONAL: the exact list of moves Sketch can copy in each game.

Without it, Smeargle's Sketch list is every move introduced by that game's
generation, which can include moves the game removed. This reads each move's
PokeAPI record and keeps, per game, the moves that have text for that game:

    python manage.py build_sketch_moves

Writes pokedex/data/sketch_moves.json ({game: [move, ...]}). A game PokeAPI
has no move text for at all (e.g. a brand new one) is left out and keeps the
generation-based list.
"""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from django.core.management.base import BaseCommand

from pokedex import api


class Command(BaseCommand):
    help = "Write data/sketch_moves.json (moves Sketch can copy per game)"

    def handle(self, *args, **options):
        names = [n for n in api._load_extra("filters_move.json")
                 if n not in api._NO_SKETCH and "--" not in n
                 and not n.startswith(("max-", "g-max-"))]

        def games_of(name):
            data = api._get_json(api.POKEAPI_MOVE.format(quote(name)))
            if not isinstance(data, dict):
                return name, None
            vgs = {api._norm_vg(e["version_group"]["name"])
                   for e in data.get("flavor_text_entries") or []
                   if (e.get("language") or {}).get("name") == "en"}
            return name, vgs

        per_game = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            for name, vgs in pool.map(games_of, names):
                for vg in vgs or ():
                    per_game.setdefault(vg, []).append(name)
        out = {g: sorted(m) for g, m in per_game.items()
               if g in api.GAME_GENERATION and len(m) > 50}
        api._write_extra(api._SKETCH_FILE, out)
        self.stdout.write(self.style.SUCCESS(
            "Wrote %d games to data/%s" % (len(out), api._SKETCH_FILE)))