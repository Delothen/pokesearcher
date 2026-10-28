"""Tests for the scraped extras, evolution links and Z-A abilities.

These need no network and no database, so they also run with plain
`python -m unittest pokedex.test_extras`.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pokedex import api, bulbapedia

DEX_HTML = """
<h2><span class="mw-headline">Pokédex entries</span></h2>
<table><tr><th>Scarlet</th><td>A small mouse Pokémon that stores electricity in its cheeks.</td></tr>
<tr><th>Violet</th><td>A small mouse Pokémon that stores electricity in its cheeks.</td></tr>
<tr><th>Legends: Z-A</th><td>Lives in the streets of Lumiose City and sparks when startled.</td></tr>
<tr><th>Sword</th><td>This is a Galar entry that must not be picked up for Z-A.</td></tr></table>
<h2><span class="mw-headline">Game locations</span></h2>
<table><tr><th>Scarlet</th><td>Paldea, found in the wild near the starting town area</td></tr></table>
"""

LEARNSET_HTML = """
<h2><span class="mw-headline">Learnset</span></h2>
<h3><span class="mw-headline">Generation IX</span></h3>
<h4><span class="mw-headline">Pokémon Scarlet and Violet</span></h4>
<h5><span class="mw-headline">By leveling up</span></h5>
<table><tr><th>Level</th><th>Move</th><th>Type</th></tr>
<tr><td>1</td><td>Sv Only Move</td><td>Normal</td></tr></table>
<h4><span class="mw-headline">Pokémon Legends: Z-A</span></h4>
<h5><span class="mw-headline">By leveling up</span></h5>
<table><tr><th>Level</th><th>Move</th><th>Type</th><th>Power</th></tr>
<tr><td>1</td><td>Thunder Shock</td><td>Electric</td><td>40</td></tr>
<tr><td>12</td><td>Nuzzle</td><td>Electric</td><td>20</td></tr></table>
<h5><span class="mw-headline">By a prior Evolution</span></h5>
<table><tr><th>Move</th></tr><tr><td>Volt Tackle</td></tr></table>
<h5><span class="mw-headline">By TM</span></h5>
<table><tr><th>TM</th><th>Move</th></tr><tr><td>TM01</td><td>Will-O-Wisp</td></tr></table>
<h3><span class="mw-headline">Generation VIII</span></h3>
<h4><span class="mw-headline">Pokémon Sword and Shield</span></h4>
<h5><span class="mw-headline">By leveling up</span></h5>
<table><tr><th>Level</th><th>Move</th></tr><tr><td>1</td><td>Galar Only Move</td></tr></table>
"""

CHAIN = {"chain": {
    "species": {"name": "pichu", "url": ".../172/"},
    "evolves_to": [{
        "species": {"name": "pikachu", "url": ".../25/"},
        "evolution_details": [{"trigger": {"name": "level-up"},
                               "min_happiness": 220}],
        "evolves_to": [{
            "species": {"name": "raichu", "url": ".../26/"},
            "evolution_details": [{"trigger": {"name": "use-item"},
                                   "item": {"name": "thunder-stone"}}],
            "evolves_to": [],
        }],
    }],
}}


class ParserTests(unittest.TestCase):
    def test_dex_entries_for_za_only(self):
        rows = bulbapedia.parse_dex_entries(DEX_HTML, "legends-za")
        self.assertEqual(len(rows), 1)
        self.assertIn("Lumiose", rows[0]["text"])

    def test_dex_ignores_other_sections(self):
        rows = bulbapedia.parse_dex_entries(DEX_HTML, "scarlet-violet")
        self.assertFalse([r for r in rows if "Paldea" in r["text"]])

    def test_dex_entries_sv_deduplicated(self):
        rows = bulbapedia.parse_dex_entries(DEX_HTML, "scarlet-violet")
        self.assertEqual(len(rows), 1)       # same text under both versions

    def test_learnset_sections(self):
        rows = bulbapedia.parse_learnset(LEARNSET_HTML, "legends-za")
        self.assertIn(["thunder-shock", "legends-za", 1, "level-up"], rows)
        self.assertIn(["nuzzle", "legends-za", 12, "level-up"], rows)
        self.assertIn(["volt-tackle", "legends-za", 0, "level-up"], rows)
        self.assertIn(["will-o-wisp", "legends-za", 0, "machine"], rows)
        self.assertFalse([r for r in rows if r[0] == "sv-only-move"])
        self.assertFalse([r for r in rows if r[0] == "galar-only-move"])

    def test_learnset_scarlet_violet_only(self):
        rows = bulbapedia.parse_learnset(LEARNSET_HTML, "scarlet-violet")
        self.assertEqual(rows, [["sv-only-move", "scarlet-violet", 1,
                                 "level-up"]])


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(api, "_DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        api._EXTRAS_CACHE.clear()

    def _write(self, name, data):
        (Path(self.tmp.name) / name).write_text(json.dumps(data))

    def test_scraped_dex_entry_fills_gap(self):
        self._write("extra_dex_entries.json", {"legends-za": {"pikachu": [
            {"version": "Legends: Z-A", "text": "Scraped entry."}]}})
        species = {"name": "pikachu", "pokedex_numbers": [
            {"pokedex": {"name": "lumiose-city"}, "entry_number": 53}]}
        got = api._dex_by_game(species)["legends-za"]
        self.assertEqual(got["entries"][0]["text"], "Scraped entry.")
        self.assertEqual(got["dex"][0]["number"], 53)

    def test_pokeapi_entry_wins_over_scraped(self):
        self._write("extra_dex_entries.json", {"scarlet-violet": {"pikachu": [
            {"version": "Scarlet", "text": "Scraped."}]}})
        species = {"name": "pikachu", "flavor_text_entries": [{
            "language": {"name": "en"}, "version": {"name": "scarlet"},
            "flavor_text": "From PokeAPI."}]}
        entries = api._dex_by_game(species)["scarlet-violet"]["entries"]
        self.assertEqual([e["text"] for e in entries], ["From PokeAPI."])

    def test_scraped_moves_only_for_games_with_no_moves(self):
        self._write("extra_learnsets.json", {"pikachu": [
            ["nuzzle", "legends-za", 12, "level-up"],
            ["surf", "scarlet-violet", 0, "machine"]]})
        data = {"name": "pikachu", "moves": [{
            "move": {"name": "thunderbolt"},
            "version_group_details": [{
                "version_group": {"name": "scarlet-violet"},
                "level_learned_at": 0,
                "move_learn_method": {"name": "machine"}}]}]}
        species = {"varieties": [
            {"is_default": True, "pokemon": {"name": "pikachu"}}]}
        with mock.patch.object(api, "_get_json", return_value=None):
            rows = api._moves_for_record(data, species)
        pairs = {(m["name"], m["version_group"]) for m in rows}
        self.assertIn(("nuzzle", "legends-za"), pairs)          # gap filled
        self.assertNotIn(("surf", "scarlet-violet"), pairs)     # PokeAPI has SV


class LazyFillTests(unittest.TestCase):
    """The first visit to a Pokemon with gaps reads Bulbapedia once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (
            mock.patch.object(api, "_DATA_DIR", Path(self.tmp.name)),
            mock.patch.object(api, "_scrape_backoff_until", 0.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        api._EXTRAS_CACHE.clear()
        self.species = {
            "name": "pikachu",
            "names": [{"language": {"name": "en"}, "name": "Pikachu"}],
            "varieties": [{"is_default": True,
                           "pokemon": {"name": "pikachu"}}],
            "pokedex_numbers": [
                {"pokedex": {"name": "lumiose-city"}, "entry_number": 53}],
            "flavor_text_entries": [],
        }
        self.data = {"name": "pikachu", "moves": []}

    def _page(self, title):
        self.titles.append(title)
        return DEX_HTML + LEARNSET_HTML

    def test_fills_once_then_uses_saved_files(self):
        self.titles = []
        with mock.patch.object(bulbapedia.Fetcher, "page_html",
                               lambda f, t: self._page(t)):
            api._fill_gaps(self.species, self.data)
            api._fill_gaps(self.species, self.data)   # already checked
        self.assertEqual(self.titles, ["Pikachu (Pokémon)"])
        entries = api._dex_by_game(self.species)["legends-za"]["entries"]
        self.assertIn("Lumiose", entries[0]["text"])
        moves = api._moves_for_record(self.data, {
            "varieties": self.species["varieties"]})
        self.assertIn("nuzzle", {m["name"] for m in moves})

    def test_network_failure_is_not_remembered_as_done(self):
        def fail(fetcher, title):
            fetcher.failed = True
            return None
        with mock.patch.object(bulbapedia.Fetcher, "page_html", fail):
            api._fill_gaps(self.species, self.data)       # must not raise
        self.assertEqual(api._load_extra("extra_checked.json"), {})

    def test_off_switch(self):
        with mock.patch.object(api, "LAZY_SCRAPE", False), \
                mock.patch.object(bulbapedia.Fetcher, "page_html") as page:
            api._fill_gaps(self.species, self.data)
        page.assert_not_called()


class EvolutionAndAbilityTests(unittest.TestCase):
    def test_edges_have_links_and_generation(self):
        edges = api._evolution_edges(CHAIN, "pikachu")
        self.assertEqual([(e["src_slug"], e["dst_slug"]) for e in edges],
                         [("pichu", "pikachu"), ("pikachu", "raichu")])
        self.assertEqual(edges[1]["method"], "Use Thunder Stone")
        self.assertTrue(all(e["current"] for e in edges))
        self.assertEqual(edges[0]["gen"], 1)      # Pikachu is #25: Gen 1

    def test_no_evolution_gives_no_edges(self):
        self.assertEqual(api._evolution_edges({}, "tauros"), [])

    def test_za_has_no_abilities(self):
        source = {
            "species_name": "pikachu", "species_id": 25, "species": {"id": 25},
            "types": [{"type": {"name": "electric"}}],
            "abilities": [{"ability": {"name": "static"}, "is_hidden": False}],
            "chain": CHAIN, "encounters": [],
        }
        za = api.build_game_info(source, "legends-za")
        self.assertEqual(za["abilities"], [])
        self.assertTrue(za["no_abilities"])
        sv = api.build_game_info(source, "scarlet-violet")
        self.assertEqual([a["name"] for a in sv["abilities"]], ["static"])
        self.assertFalse(sv["no_abilities"])


if __name__ == "__main__":
    unittest.main()