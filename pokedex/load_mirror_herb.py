"""OPTIONAL bulk version of what the site does by itself.

The first time a Scarlet / Violet Pokémon is opened, the site reads from
Bulbapedia which of its moves need a Mirror Herb (the "By breeding" rows
marked with the herb) and saves them in data/extra_mirror_herb.json. This
command does every Pokémon in one go:

    python manage.py load_mirror_herb
    python manage.py load_mirror_herb --names smeargle azumarill

Pages are cached and finished Pokémon are skipped, so it is safe to stop and
re-run.
"""
from django.core.management.base import BaseCommand, CommandError

from pokedex import api, bulbapedia

from .load_extras import roster


class Command(BaseCommand):
    help = "Save which Scarlet / Violet moves need a Mirror Herb"

    def add_arguments(self, parser):
        parser.add_argument("--names", nargs="+",
                            help="only these species (PokeAPI names)")
        parser.add_argument("--delay", type=float, default=1.5)
        parser.add_argument("--refresh", action="store_true",
                            help="also redo Pokémon already saved")

    def handle(self, *args, names, delay, refresh, **options):
        if bulbapedia.BeautifulSoup is None:
            raise CommandError(
                "BeautifulSoup is not installed. Run: pip install beautifulsoup4")
        fetcher = bulbapedia.Fetcher(api._DATA_DIR / ".bulbapedia_cache",
                                     delay=delay)
        species_list = names or roster("scarlet-violet")
        known = dict(api._load_extra(api._MIRROR_HERB_FILE))
        done = 0
        for i, sp_name in enumerate(species_list, 1):
            species = api._fetch_species(sp_name)
            if not species:
                continue
            default = api._default_variety(species) or sp_name
            if default in known and not refresh:
                continue
            title = bulbapedia.pokemon_title(
                api._english_name(species, sp_name))
            result = bulbapedia.scrape_page(
                fetcher, title, "scarlet-violet", dex=False, moves=True)
            if result is None:
                self.stdout.write("[%d/%d] %s: no Bulbapedia page" % (
                    i, len(species_list), sp_name))
                continue
            known[default] = sorted(
                r[0] for r in result["moves"] if r[3] == "mirror-herb")
            done += 1
            self.stdout.write("[%d/%d] %s: %d Mirror Herb moves" % (
                i, len(species_list), sp_name, len(known[default])))
            if done % 10 == 0:
                api._write_extra(api._MIRROR_HERB_FILE, known)
        api._write_extra(api._MIRROR_HERB_FILE, known)
        self.stdout.write(self.style.SUCCESS("Done: %d Pokémon." % done))