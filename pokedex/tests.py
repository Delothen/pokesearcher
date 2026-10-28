from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings

from pokedex.models import Favourite


class PokedexTests(TestCase):
    def test_add_and_remove_favourite(self):
        client = Client()
        client.post("/favourites/add/",
                    {"type": "pokemon", "id": "25", "name": "pikachu", "sprite": "http://x/25.png"})
        saved = Favourite.objects.get()
        self.assertEqual(saved.name, "pikachu")
        self.assertEqual(saved.kind, "pokemon")
        client.post("/favourites/remove/", {"id": "25", "type": "pokemon"})
        self.assertEqual(Favourite.objects.count(), 0)

    def test_search_renders_pokemon(self):
        fake = {
            "id": 25, "name": "pikachu", "sprite": "http://x/25.png",
            "types": ["electric"],
            "stats": [{"name": "hp", "value": 35}],
            "moves": [{"name": "thunderbolt", "version_group": "scarlet-violet", "level": 1, "method": "level-up"}],
        }
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake), \
             mock.patch("pokedex.views.api.fetch_cards", return_value=[]):
            response = self.client.get("/search/?q=pikachu")
        self.assertContains(response, "Pikachu")

    def test_move_search(self):
        fake = {
            "id": 907, "name": "fickle-beam", "type": "dragon",
            "power": 80, "accuracy": 100, "pp": 5,
            "damage_class": "special", "effect": "30% chance to double power.",
            "games": [{"key": "scarlet-violet", "label": "Scarlet / Violet",
                       "type": "dragon", "power": 80, "accuracy": 100, "pp": 5}],
        }
        with mock.patch("pokedex.views.api.fetch_move", return_value=fake):
            response = self.client.get("/moves/?q=fickle-beam")
        self.assertContains(response, "Fickle-beam")
        self.assertContains(response, "Special")
        self.assertContains(response, "Scarlet / Violet")

    def test_fetch_move_handles_null_meta(self):
        from pokedex import api
        data = {
            "id": 907, "name": "fickle-beam", "meta": None,
            "type": {"name": "dragon"}, "damage_class": {"name": "special"},
            "power": 80, "accuracy": 100, "pp": 5, "effect_entries": [],
            "flavor_text_entries": [{
                "language": {"name": "en"}, "flavor_text": "A beam.",
                "version_group": {"name": "scarlet-violet"}}],
            "past_values": [],
        }
        resp = mock.Mock()
        resp.json.return_value = data
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            move = api.fetch_move("Fickle Beam")
        self.assertEqual(move["damage_class"], "special")
        self.assertEqual(move["games"][0]["key"], "scarlet-violet")

    def test_z_moves_are_only_in_the_games_that_have_z_moves(self):
        from pokedex import api
        flavour = [{"language": {"name": "en"}, "flavor_text": "A Z-Move.",
                    "version_group": {"name": vg}}
                   for vg in ("sun-moon", "ultra-sun-ultra-moon",
                              "lets-go-pikachu-lets-go-eevee", "sword-shield")]
        for slug in ("breakneck-blitz--physical", "catastropika"):
            data = {
                "id": 1, "name": slug, "meta": None,
                "type": {"name": "normal"}, "damage_class": {"name": "physical"},
                "power": 1, "accuracy": None, "pp": 1, "effect_entries": [],
                "flavor_text_entries": flavour, "past_values": [],
            }
            resp = mock.Mock()
            resp.json.return_value = data
            with mock.patch("pokedex.api.requests.get", return_value=resp):
                move = api.fetch_move(slug)
            keys = {g["key"] for g in move["games"]}
            self.assertEqual(keys, {"sun-moon", "ultra-sun-ultra-moon"})
        # a crystal added in Ultra Sun / Ultra Moon: only those games
        self.assertEqual(
            api.supplements.Z_MOVE_GAMES["light-that-burns-the-sky"],
            ("ultra-sun-ultra-moon",))
        self.assertIn("acid-downpour--special", api.supplements.Z_MOVE_GAMES)

    def test_favourites_page_renders_list(self):
        client = Client()
        client.post("/favourites/add/", {"type": "move", "id": "907", "name": "fickle-beam"})
        response = client.get("/favourites/")
        self.assertContains(response, "Fickle-beam")
        self.assertNotContains(response, "{% include")
        self.assertContains(response, "/moves/?q=fickle-beam")

    def test_compare_pokemon(self):
        pa = {"id": 1, "name": "bulbasaur", "sprite": "http://x/1.png", "types": ["grass"], "stats": [{"name": "hp", "value": 45}]}
        pb = {"id": 4, "name": "charmander", "sprite": "http://x/4.png", "types": ["fire"], "stats": [{"name": "hp", "value": 39}]}
        with mock.patch("pokedex.views.api.fetch_pokemon", side_effect=[pa, pb]):
            response = self.client.get("/compare/?mode=pokemon&a=bulbasaur&b=charmander")
        self.assertContains(response, "Bulbasaur")
        self.assertContains(response, "Charmander")

class GamesAndShinyTests(TestCase):
    @staticmethod
    def _raw(groups):
        shiny = {"front_default": "n.png", "front_shiny": "s.png"}
        return {
            "id": 25, "name": "pikachu",
            "sprites": {
                "front_default": "d.png", "front_shiny": "d_s.png",
                "versions": {
                    "generation-ii": {"gold": shiny, "silver": shiny},
                    "generation-ix": {"scarlet-violet": shiny},
                },
            },
            "types": [], "stats": [], "abilities": [],
            "moves": [{
                "move": {"name": "thunderbolt"},
                "version_group_details": [
                    {"version_group": {"name": g}, "level_learned_at": 1,
                     "move_learn_method": {"name": "level-up"}}
                    for g in groups
                ],
            }],
        }

    def _fetch(self, groups):
        from pokedex import api
        resp = mock.Mock()
        resp.json.return_value = self._raw(groups)
        with mock.patch("pokedex.api.requests.get", return_value=resp), \
             mock.patch("pokedex.api._fetch_ability", return_value=None):
            return api.fetch_pokemon("pikachu")

    def test_only_games_the_pokemon_is_in(self):
        p = self._fetch(["gold", "silver", "scarlet-violet"])
        self.assertEqual(
            [vg for vg, _ in p["games"]],
            ["scarlet-violet", "gold-silver"],
        )

    def test_shiny_sprites_and_gold_silver_key(self):
        p = self._fetch(["gold"])
        self.assertEqual(p["shiny_sprite"], "s.png")
        self.assertEqual(p["shiny_sprites_by_gen"]["gold-silver"], "s.png")
        self.assertEqual(p["sprites_by_gen"]["gold-silver"], "n.png")

    def test_favourite_slider_is_swapped_out_of_band(self):
        fake = {"id": 25, "name": "pikachu", "sprite": "d.png",
                "types": [], "stats": [], "moves": [],
                "games": [("scarlet-violet", "Scarlet / Violet")]}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake), \
             mock.patch("pokedex.views.api.fetch_cards", return_value=[]):
            response = self.client.get("/search/?q=pikachu", HTTP_HX_REQUEST="true")
        self.assertContains(response, 'id="fav-slot"')
        self.assertContains(response, 'id="shiny-toggle"')
        self.assertContains(response, 'class="fav-checkbox"', count=1)
        self.assertContains(response, "Scarlet / Violet")

    def test_spin_offs_and_champions_are_listed(self):
        from pokedex import api
        raw = self._raw(["colosseum", "xd", "x-y", "the-teal-mask"])
        raw["species"] = {"name": "pikachu"}

        def fake_get(url, **kwargs):
            resp = mock.Mock()
            if "pokemon-species" in url:
                resp.json.return_value = {"pokedex_numbers": [
                    {"entry_number": 1, "pokedex": {"name": "champions"}}]}
            else:
                resp.json.return_value = raw
            return resp

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
             mock.patch("pokedex.api._fetch_ability", return_value=None):
            p = api.fetch_pokemon("pikachu")
        self.assertEqual(
            [vg for vg, _ in p["games"]],
            ["champions", "scarlet-violet", "x-y", "xd", "colosseum"],
        )
        labels = dict(p["games"])
        self.assertEqual(labels["xd"], "XD: Gale of Darkness")

    def _fetch_champions(self, champions_sprite, existing_urls=()):
        from pokedex import api
        raw = self._raw(["scarlet-violet"])
        raw["id"] = 887
        raw["species"] = {"name": "hydrapple"}
        if champions_sprite is not None:
            raw["sprites"]["versions"]["generation-ix"]["champions"] = (
                champions_sprite
            )

        def fake_get(url, **kwargs):
            resp = mock.Mock()
            if "pokemon-species" in url:
                resp.json.return_value = {"pokedex_numbers": [
                    {"entry_number": 1, "pokedex": {"name": "champions"}}]}
            elif url.startswith("https://raw.githubusercontent.com"):
                resp.status_code = 200 if url in existing_urls else 404
            else:
                resp.json.return_value = raw
            return resp

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
             mock.patch("pokedex.api._fetch_ability", return_value=None):
            return api.fetch_pokemon("hydrapple")

    def test_champions_sprite_from_pokeapi_json(self):
        p = self._fetch_champions({"front_default": "champ.png"})
        self.assertEqual(p["sprites_by_gen"]["champions"], "champ.png")
        # no shiny for this game -> the page shows the "no shiny" text
        self.assertNotIn("champions", p["shiny_sprites_by_gen"])

    def test_champions_sprite_found_in_sprite_repo_when_json_omits_it(self):
        from pokedex import api
        base = api.CHAMPIONS_SPRITE_URL.format(887)
        p = self._fetch_champions(None, existing_urls={base})
        self.assertEqual(p["sprites_by_gen"]["champions"], base)
        self.assertNotIn("champions", p["shiny_sprites_by_gen"])

    def test_no_shiny_text_and_lightbox_toggle_are_rendered(self):
        fake = {"id": 25, "name": "pikachu", "sprite": "d.png",
                "types": [], "stats": [], "moves": [],
                "games": [("champions", "Pokémon Champions")]}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake), \
             mock.patch("pokedex.views.api.fetch_cards", return_value=[]):
            response = self.client.get("/search/?q=pikachu")
        self.assertContains(response, "No shiny sprite for this game")
        sprites = [{"game": "champions", "url": "c.png", "shiny": ""}]
        with mock.patch("pokedex.views.api.fetch_sprites", return_value=sprites):
            response = self.client.get("/sprites/?q=pikachu")
        self.assertContains(response, 'id="lightbox-shiny-toggle"')
        self.assertContains(response, "No shiny sprite for this game")


class PokemonListCacheTests(TestCase):
    def _api_response(self, count):
        results = [
            {"name": "mon%04d" % i,
             "url": "https://pokeapi.co/api/v2/pokemon/%d/" % i}
            for i in range(1, count + 1)
        ]
        # Mega / G-Max forms are searchable; other alternate forms are not
        results.append({"name": "pikachu-gmax",
                        "url": "https://pokeapi.co/api/v2/pokemon/10199/"})
        results.append({"name": "charizard-mega-x",
                        "url": "https://pokeapi.co/api/v2/pokemon/10034/"})
        results.append({"name": "pikachu-cosplay",
                        "url": "https://pokeapi.co/api/v2/pokemon/10080/"})
        resp = mock.Mock()
        resp.json.return_value = {"results": results}
        return resp

    def test_stale_1000_entry_cache_is_rebuilt(self):
        import json, os, tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "pokedex", "data"))
            path = os.path.join(tmp, "pokedex", "data", "pokemon_list.json")
            with open(path, "w") as f:
                json.dump([{"id": str(i), "name": "old%d" % i, "sprite": ""}
                           for i in range(1, 1001)], f)
            with override_settings(BASE_DIR=tmp), \
                 mock.patch("pokedex.views.requests.get",
                            return_value=self._api_response(1030)):
                data = self.client.get("/api/pokemon-list/").json()
        ids = {int(p["id"]) for p in data}
        self.assertEqual(len(data), 1032)
        self.assertIn(1025, ids)
        self.assertIn(1030, ids)
        self.assertIn(10199, ids)   # pikachu-gmax
        self.assertIn(10034, ids)   # charizard-mega-x
        self.assertNotIn(10080, ids)  # cosmetic forms stay out

    def test_stale_cache_used_when_api_is_down(self):
        import json, os, tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "pokedex", "data"))
            path = os.path.join(tmp, "pokedex", "data", "pokemon_list.json")
            with open(path, "w") as f:
                json.dump([{"id": "1", "name": "bulbasaur", "sprite": ""}], f)
            with override_settings(BASE_DIR=tmp), \
                 mock.patch("pokedex.views.requests.get",
                            side_effect=Exception("down")):
                data = self.client.get("/api/pokemon-list/").json()
        self.assertEqual(data[0]["name"], "bulbasaur")

    def test_cache_without_forms_is_rebuilt(self):
        """An old list with only base Pokémon is replaced by one with megas."""
        import json, os, tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "pokedex", "data"))
            path = os.path.join(tmp, "pokedex", "data", "pokemon_list.json")
            with open(path, "w") as f:
                json.dump([{"id": str(i), "name": "old%d" % i, "sprite": ""}
                           for i in range(1, 1100)], f)
            with override_settings(BASE_DIR=tmp), \
                 mock.patch("pokedex.views.requests.get",
                            return_value=self._api_response(1030)):
                names = [p["name"] for p in
                         self.client.get("/api/pokemon-list/").json()]
        self.assertIn("charizard-mega-x", names)
        self.assertIn("pikachu-gmax", names)


def _resp(payload=None, ok=True):
    r = mock.Mock()
    if ok:
        r.json.return_value = payload
    else:
        r.raise_for_status.side_effect = Exception("404")
    return r


def _poke(name, pid, sprite, shiny=None, species=None, extra_gen9=None):
    gen9 = {"scarlet-violet": {"front_default": sprite, "front_shiny": shiny}}
    gen9.update(extra_gen9 or {})
    return {
        "id": pid, "name": name, "species": {"name": species or name},
        "sprites": {"front_default": sprite, "front_shiny": shiny,
                    "versions": {"generation-ix": gen9}},
        "types": [], "stats": [], "abilities": [], "moves": [],
    }


class TcgCardTabTests(TestCase):
    def test_pokemon_page_has_cards_tab_loaded_lazily(self):
        fake = {"id": 25, "name": "pikachu", "species": "pikachu",
                "sprite": "d.png", "types": [], "stats": [], "moves": [],
                "games": [("scarlet-violet", "Scarlet / Violet")]}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake), \
             mock.patch("pokedex.views.api.fetch_cards") as cards:
            response = self.client.get("/search/?q=pikachu")
        cards.assert_not_called()
        self.assertContains(response, "TCG Cards")
        self.assertContains(response, 'hx-get="/cards/?q=pikachu&limit=12"')
        self.assertNotContains(response, 'class="tcg-card"')

    def test_cards_use_species_name_for_forms(self):
        fake = {"id": 778, "name": "mimikyu-disguised", "species": "mimikyu",
                "sprite": "d.png", "types": [], "stats": [], "moves": [],
                "games": []}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake):
            response = self.client.get("/search/?q=mimikyu-disguised")
        self.assertContains(response, 'hx-get="/cards/?q=mimikyu&limit=12"')

    def test_cards_partial_shows_photos_and_favourite_toggle(self):
        cards = [{"id": "base1-58", "name": "Pikachu", "set": "Base Set",
                  "rarity": "Common", "image": "https://a/58.png",
                  "image_alt": "https://a/58/high.png",
                  "fav_name": "Pikachu · Base Set",
                  "tcgplayer": 1.5, "cardmarket": None}]
        with mock.patch("pokedex.views.api.fetch_cards", return_value=cards):
            response = self.client.get("/cards/?q=pikachu")
        self.assertContains(response, 'src="https://a/58.png"')
        self.assertContains(response, 'data-alt="https://a/58/high.png"')
        self.assertContains(response, 'data-type="card"')
        self.assertContains(response, 'data-id="base1-58"')
        self.assertContains(response, "$ 1.5")

    def test_cards_partial_when_none(self):
        with mock.patch("pokedex.views.api.fetch_cards", return_value=[]), \
             mock.patch("pokedex.views.api.card_search_status",
                        return_value="no_matches"):
            response = self.client.get("/cards/?q=nothing")
        self.assertContains(response, "No trading cards with artwork")
        self.assertContains(response, "has no cards under that name")

    def test_cards_partial_says_when_tcgdex_is_unreachable(self):
        with mock.patch("pokedex.views.api.fetch_cards", return_value=[]), \
             mock.patch("pokedex.views.api.card_search_status",
                        return_value="unreachable"):
            response = self.client.get("/cards/?q=pikachu")
        self.assertContains(response, "could not be reached")

    def test_fetch_cards_only_returns_cards_with_images(self):
        from pokedex import api
        api._CARD_CACHE.clear()

        def fake_get(url, **kwargs):
            if url == api.TCGDEX_SEARCH:
                return _resp([
                    {"id": "a-1", "name": "Pikachu V", "image": "https://x/a1"},
                    {"id": "a-2", "name": "Pikachu"},   # no image in the list
                    {"id": "a-3", "name": "Pikachu", "image": "https://x/a3"},
                    {"id": "a-4", "name": "Pikachu", "image": "https://x/a4"},
                ])
            cid = url.rsplit("/", 1)[-1]
            detail = {"id": cid, "name": "Pikachu", "set": {"name": "S"},
                      "rarity": "Rare", "image": "https://x/" + cid.replace("-", ""),
                      "pricing": {}}
            if cid == "a-4":
                detail.pop("image")      # image missing on the full record
            return _resp(detail)

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get):
            cards = api.fetch_cards("pikachu")
        self.assertEqual([c["id"] for c in cards], ["a-3", "a-1"])  # exact name first
        for c in cards:
            self.assertTrue(c["image"].endswith(".png"))
            self.assertTrue(c["image_alt"].endswith("/high.png"))

    def test_card_can_be_favourited_and_listed(self):
        client = Client()
        client.post("/favourites/add/", {
            "type": "card", "id": "base1-58", "name": "Pikachu · Base Set",
            "sprite": "https://a/58.png"})
        self.assertEqual(Favourite.objects.get().kind, "card")
        page = client.get("/favourites/")
        self.assertContains(page, "TCG cards")
        self.assertContains(page, 'href="https://a/58.png"')
        self.assertContains(page, "fav-card-img")


class FormsAndZATests(TestCase):
    def _patch_api(self, table):
        def fake_get(url, **kwargs):
            for key, payload in table.items():
                if url.endswith(key):
                    return _resp(payload, ok=payload is not None)
            return _resp(None, ok=False)
        return mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
            mock.patch("pokedex.api._fetch_ability", return_value=None)

    def _mimikyu_table(self):
        return {
            "/pokemon/mimikyu-disguised": _poke("mimikyu-disguised", 778, "d.png", "ds.png", "mimikyu"),
            "/pokemon/mimikyu-busted": _poke("mimikyu-busted", 10143, "b.png", "bs.png", "mimikyu"),
            "/pokemon/mimikyu": None,   # PokeAPI has no plain /pokemon/mimikyu
            "/pokemon-species/mimikyu": {"varieties": [
                {"is_default": True, "pokemon": {"name": "mimikyu-disguised"}},
                {"is_default": False, "pokemon": {"name": "mimikyu-busted"}},
                {"is_default": False, "pokemon": {"name": "mimikyu-totem-disguised"}},
            ]},
        }

    def test_species_name_falls_back_to_default_form_and_lists_forms(self):
        from pokedex import api
        a, b = self._patch_api(self._mimikyu_table())
        with a, b:
            p = api.fetch_pokemon("mimikyu")
        self.assertEqual(p["name"], "mimikyu-disguised")
        self.assertEqual(p["species"], "mimikyu")
        self.assertEqual([f["label"] for f in p["forms"]], ["Busted", "Disguised"])
        self.assertEqual([f["current"] for f in p["forms"]], [False, True])
        self.assertEqual(p["forms"][0]["sprite"], "b.png")
        self.assertEqual(p["forms"][0]["shiny_sprites_by_gen"]["scarlet-violet"], "bs.png")

    def test_single_form_pokemon_has_no_toggle(self):
        from pokedex import api
        a, b = self._patch_api({
            "/pokemon/pikachu": _poke("pikachu", 25, "p.png"),
            "/pokemon-species/pikachu": {"varieties": [
                {"is_default": True, "pokemon": {"name": "pikachu"}},
                {"is_default": False, "pokemon": {"name": "pikachu-gmax"}},
                {"is_default": False, "pokemon": {"name": "pikachu-alola-cap"}},
            ]},
        })
        with a, b:
            p = api.fetch_pokemon("pikachu")
        self.assertEqual(p["forms"], [])

    def test_zygarde_default_form_is_titled_50_percent(self):
        from pokedex import api
        self.assertEqual(api._title_for("zygarde-50", "zygarde", "zygarde-50"), "Zygarde 50%")
        self.assertEqual(api._title_for("zygarde-10", "zygarde", "zygarde-50"), "Zygarde 10%")
        # other species keep the plain species name for their default form
        self.assertEqual(api._title_for("mimikyu-disguised", "mimikyu", "mimikyu-disguised"), "Mimikyu")

    def test_species_games_come_from_the_pokedexes_it_is_in(self):
        from pokedex import api
        species = {"pokedex_numbers": [
            {"pokedex": {"name": "national"}},
            {"pokedex": {"name": "paldea"}},
            {"pokedex": {"name": "lumiose"}},
        ]}
        games = api._species_games(species, 25)
        self.assertEqual(games, ["legends-za", "scarlet-violet",
                                 "brilliant-diamond-shining-pearl"])
        # newest first, so the first one is where a link should open
        self.assertNotIn("legends-arceus", games)
        # a species PokeAPI has no Pokedex list for has no game list
        self.assertEqual(api._species_games({}, 25), [])

    def test_minior_is_reduced_to_meteor_and_core(self):
        from pokedex import api
        names = ["minior-red-meteor", "minior-orange-meteor", "minior-red", "minior-blue"]
        table = {"/pokemon/" + n: _poke(n, 774, n + ".png", species="minior") for n in names}
        table["/pokemon-species/minior"] = {"varieties": [
            {"is_default": i == 0, "pokemon": {"name": n}} for i, n in enumerate(names)]}
        a, b = self._patch_api(table)
        with a, b:
            p = api.fetch_pokemon("minior-red-meteor")
        # one Meteor Form, then a Core Form per colour
        self.assertEqual([f["label"] for f in p["forms"]],
                         ["Blue Core", "Meteor", "Red Core"])

    def test_form_toggle_is_rendered(self):
        forms = [
            {"name": "a", "label": "Disguised", "current": True, "sprite": "d.png",
             "shiny_sprite": "", "sprites_by_gen": {}, "shiny_sprites_by_gen": {}},
            {"name": "b", "label": "Busted", "current": False, "sprite": "b.png",
             "shiny_sprite": "", "sprites_by_gen": {}, "shiny_sprites_by_gen": {}},
        ]
        fake = {"id": 778, "name": "mimikyu-disguised", "species": "mimikyu",
                "sprite": "d.png", "types": [], "stats": [], "moves": [],
                "games": [], "forms": forms}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=fake):
            response = self.client.get("/search/?q=mimikyu")
        self.assertContains(response, 'id="form-toggle"')
        self.assertContains(response, "Busted")
        self.assertContains(response, 'id="forms-data"')

    def test_legends_za_sprites_make_the_game_available(self):
        from pokedex import api
        za = {"legends-za": {"front_default": "https://x/za/25.webp",
                             "front_shiny": "https://x/za/shiny/25.webp"}}
        a, b = self._patch_api({
            "/pokemon/pikachu": _poke("pikachu", 25, "p.png", "ps.png", extra_gen9=za)})
        with a, b:
            p = api.fetch_pokemon("pikachu")
        self.assertEqual(p["sprites_by_gen"]["legends-za"], "https://x/za/25.webp")
        self.assertEqual(p["shiny_sprites_by_gen"]["legends-za"], "https://x/za/shiny/25.webp")
        self.assertIn("legends-za", [vg for vg, _ in p["games"]])

    def test_no_za_sprite_means_no_za_button(self):
        from pokedex import api
        za = {"legends-za": {"front_default": None, "front_shiny": None}}
        a, b = self._patch_api({
            "/pokemon/pikachu": _poke("pikachu", 25, "p.png", extra_gen9=za)})
        with a, b:
            p = api.fetch_pokemon("pikachu")
        self.assertNotIn("legends-za", [vg for vg, _ in p["games"]])

    def _greninja_table(self, extra=None):
        table = {
            "/pokemon/greninja": _poke("greninja", 658, "g.png", "gs.png"),
            "/pokemon/greninja-battle-bond": {
                "id": 10116, "name": "greninja-battle-bond",
                "species": {"name": "greninja"},
                "sprites": {"front_default": "bb.png", "front_shiny": "bbs.png"},
                "types": [], "stats": [], "abilities": [], "moves": [
                    {"move": {"name": "surf"}, "version_group_details": [
                        {"version_group": {"name": "ultra-sun-ultra-moon"},
                         "level_learned_at": 1,
                         "move_learn_method": {"name": "level-up"}}]}],
            },
            "/pokemon/greninja-ash": _poke("greninja-ash", 10117, "ash.png"),
            "/pokemon/greninja-mega": _poke("greninja-mega", 10300, "m.png"),
            "/pokemon-species/greninja": {"varieties": [
                {"is_default": True, "pokemon": {"name": "greninja"}},
                {"is_default": False, "pokemon": {"name": "greninja-battle-bond"}},
                {"is_default": False, "pokemon": {"name": "greninja-ash"}},
                {"is_default": False, "pokemon": {"name": "greninja-mega"}},
            ]},
        }
        table.update(extra or {})
        return table

    def test_ash_greninja_is_its_own_gen_7_form(self):
        from pokedex import api
        a, b = self._patch_api(self._greninja_table())
        with a, b:
            p = api.fetch_pokemon("greninja")
        labels = [f["label"] for f in p["forms"]]
        self.assertIn("Battle Bond", labels)
        self.assertIn("Ash", labels)
        ash = next(f for f in p["forms"] if f["label"] == "Ash")
        self.assertEqual(ash["display_name"], "Ash-Greninja")
        self.assertEqual(
            [vg for vg, _ in ash["games"]],
            ["ultra-sun-ultra-moon", "sun-moon"])

    def test_battle_bond_greninja_gets_a_scarlet_violet_button(self):
        from pokedex import api
        a, b = self._patch_api(self._greninja_table())
        with a, b:
            p = api.fetch_pokemon("greninja")
        bond = next(f for f in p["forms"] if f["label"] == "Battle Bond")
        self.assertIn("scarlet-violet", [vg for vg, _ in bond["games"]])
        self.assertIn("ultra-sun-ultra-moon", [vg for vg, _ in bond["games"]])

    def test_battle_bond_has_its_own_sprite(self):
        from pokedex import api
        a, b = self._patch_api(self._greninja_table())
        with a, b:
            p = api.fetch_pokemon("greninja")
        bond = next(f for f in p["forms"] if f["label"] == "Battle Bond")
        self.assertEqual(bond["sprite"], "bb.png")
        self.assertEqual(bond["shiny_sprite"], "bbs.png")

    def test_mega_greninja_gets_za_sprite_from_sprite_repo(self):
        from pokedex import api
        repo = "legends-za/10300"

        def fake_get(url, **kwargs):
            if "/pokemon/greninja-mega" in url:
                return _resp(_poke("greninja-mega", 10300, "m.png"))
            if url.endswith(repo + ".png") or url.endswith("shiny/10300.png"):
                r = mock.Mock(status_code=200)
                return r
            return _resp(None, ok=False)
        with mock.patch("pokedex.api.requests.get", side_effect=fake_get):
            entry = api._form_entry(
                "greninja-mega", "greninja",
                _poke("greninja-mega", 10300, "m.png"), "greninja")
        self.assertTrue(entry["sprites_by_gen"]["legends-za"].endswith("10300.png"))
        self.assertIn("legends-za", entry["shiny_sprites_by_gen"])
        self.assertIn("legends-za", [vg for vg, _ in entry["games"]])

    def test_normal_darkrai_gets_za_from_sprite_repo(self):
        from pokedex import api

        def fake_get(url, **kwargs):
            if url.endswith("legends-za/491.png"):
                return mock.Mock(status_code=200)
            if "/pokemon-species/darkrai" in url:
                return _resp({"varieties": []})
            if "/pokemon/darkrai" in url:
                return _resp(_poke("darkrai", 491, "d.png"))
            return _resp(None, ok=False)
        with mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
             mock.patch("pokedex.api._fetch_ability", return_value=None):
            p = api.fetch_pokemon("darkrai")
        self.assertIn("legends-za", [vg for vg, _ in p["games"]])
        self.assertTrue(p["sprites_by_gen"]["legends-za"].endswith("491.png"))

    def test_za_dex_membership_makes_the_game_available(self):
        from pokedex import api
        a, b = self._patch_api({
            "/pokemon/darkrai": _poke("darkrai", 491, "d.png"),
            "/pokemon-species/darkrai": {"varieties": [], "pokedex_numbers": [
                {"pokedex": {"name": "hyperspace"}}]},
        })
        with a, b:
            p = api.fetch_pokemon("darkrai")
        self.assertIn("legends-za", [vg for vg, _ in p["games"]])


class HoverColourTests(TestCase):
    def test_hover_colours_per_theme(self):
        import os
        from django.conf import settings
        path = os.path.join(str(settings.BASE_DIR), "pokedex", "static",
                            "pokedex", "style.css")
        css = open(path, encoding="utf-8").read()
        self.assertIn("--hover-bg: #dca93b;", css)   # Luxury Ball gold
        self.assertIn("--hover-bg: #ffffff;", css)   # Poké Ball white


class CardsPageTests(TestCase):
    def _card(self, cid, usd=None, eur=None):
        return {"id": cid, "name": "Pikachu", "set": {"name": "S"}, "rarity": "Rare",
                "image": "https://x/%s" % cid.replace("-", ""),
                "pricing": {"tcgplayer": {"marketPrice": usd} if usd is not None else {},
                            "cardmarket": {"avg": eur} if eur is not None else {}}}

    def _fetch(self, prices, **kw):
        from pokedex import api
        api._CARD_CACHE.clear()
        details = {cid: self._card(cid, *p) for cid, p in prices.items()}

        def fake_get(url, **kwargs):
            if url == api.TCGDEX_SEARCH:
                return _resp([{"id": cid, "name": "Pikachu", "image": "https://x/i"}
                              for cid in prices])
            return _resp(details[url.rsplit("/", 1)[-1]])

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get):
            return api.fetch_cards("pikachu", **kw)

    def test_sorted_most_expensive_first_with_unpriced_last(self):
        cards = self._fetch({"a-1": (2.0, None), "a-2": (None, None),
                             "a-3": (150.0, None), "a-4": (None, 40.0)})
        self.assertEqual([c["id"] for c in cards], ["a-3", "a-4", "a-1", "a-2"])

    def test_expensive_card_beyond_first_page_is_found_within_limit(self):
        prices = {"a-%d" % i: (float(i), None) for i in range(1, 8)}
        cards = self._fetch(prices, limit=7)
        self.assertEqual(cards[0]["id"], "a-7")

    def test_cards_page_renders_search_bar_and_nav_link(self):
        response = self.client.get("/cards/")
        self.assertContains(response, 'id="cards-search"')
        self.assertContains(response, 'href="/cards/"')

    def test_cards_page_search_lists_cards(self):
        cards = [{"id": "a-1", "name": "Pikachu", "set": "S", "rarity": None,
                  "image": "https://a/1.png", "image_alt": "https://a/1/high.png",
                  "fav_name": "Pikachu · S", "tcgplayer": 99.0, "cardmarket": None}]
        with mock.patch("pokedex.views.api.fetch_cards", return_value=cards) as fc:
            response = self.client.get("/cards/?q=pikachu")
        fc.assert_called_once_with("pikachu", limit=48)
        self.assertContains(response, "most expensive first")
        self.assertContains(response, "$ 99.0")

    def test_name_variants_are_tried(self):
        from pokedex import api
        calls = []

        def fake_get(url, params=None, **kwargs):
            calls.append(params["name"])
            return _resp([] if params["name"] == "mr-mime" else
                         [{"id": "a-1", "name": "Mr. Mime"}])

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get):
            q, results = api._search_cards("mr-mime")
        self.assertEqual(calls, ["mr-mime", "mr mime"])
        self.assertEqual(q, "mr mime")


class MissingGameSpriteTests(TestCase):
    def _fetch(self, versions):
        from pokedex import api
        raw = {"id": 25, "name": "pikachu", "types": [], "stats": [],
               "abilities": [], "moves": [],
               "sprites": {"front_default": "d.png", "versions": versions}}
        resp = mock.Mock()
        resp.json.return_value = raw
        with mock.patch("pokedex.api.requests.get", return_value=resp), \
             mock.patch("pokedex.api._fetch_ability", return_value=None):
            return api.fetch_pokemon("pikachu")

    def test_bw2_and_sun_moon_reuse_the_shared_sprites(self):
        p = self._fetch({
            "generation-v": {"black-white": {"front_default": "bw.png",
                                             "front_shiny": "bws.png"}},
            "generation-vii": {"ultra-sun-ultra-moon": {
                "front_default": "usum.png", "front_shiny": "usums.png"}},
        })
        self.assertEqual(p["sprites_by_gen"]["black-2-white-2"], "bw.png")
        self.assertEqual(p["shiny_sprites_by_gen"]["black-2-white-2"], "bws.png")
        self.assertEqual(p["sprites_by_gen"]["sun-moon"], "usum.png")

    def test_own_sprite_is_never_overridden(self):
        p = self._fetch({
            "generation-v": {"black-white": {"front_default": "bw.png"},
                             "black-2-white-2": {"front_default": "bw2.png"}},
        })
        self.assertEqual(p["sprites_by_gen"]["black-2-white-2"], "bw2.png")

    def test_game_without_any_sprite_has_no_entry(self):
        p = self._fetch({})
        self.assertNotIn("xd", p["sprites_by_gen"])
        self.assertNotIn("sun-moon", p["sprites_by_gen"])

    def test_pages_show_no_sprite_text_instead_of_a_wrong_sprite(self):
        import os
        from django.conf import settings
        base = os.path.join(str(settings.BASE_DIR), "pokedex", "templates", "pokedex")
        for name in ("pokemon_result.html", "compare_result.html"):
            html = open(os.path.join(base, name), encoding="utf-8").read()
            self.assertIn("No sprite for this game", html)


class SpriteOrderTests(TestCase):
    def test_sprites_are_listed_newest_game_first(self):
        from pokedex import api
        entry = lambda n: {"front_default": n + ".png", "front_shiny": n + "s.png"}
        raw = {"id": 25, "name": "pikachu", "sprites": {"versions": {
            "generation-i": {"red-blue": entry("rb"), "yellow": entry("y")},
            "generation-ii": {"gold": entry("g"), "silver": entry("s"),
                              "crystal": entry("c")},
            "generation-iii": {"emerald": entry("e"), "ruby-sapphire": entry("rs")},
            "generation-v": {"black-white": entry("bw")},
            "generation-vii": {"ultra-sun-ultra-moon": entry("usum")},
            "generation-ix": {"scarlet-violet": entry("sv"),
                              "legends-za": entry("za"), "champions": entry("ch")},
        }}}
        resp = mock.Mock()
        resp.json.return_value = raw
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            games = [s["game"] for s in api.fetch_sprites("pikachu")]
        self.assertEqual(games, [
            "champions", "legends-za", "scarlet-violet", "ultra-sun-ultra-moon",
            "black-white", "emerald", "ruby-sapphire", "crystal", "gold",
            "silver", "yellow", "red-blue",
        ])


# ---------------------------------------------------------------------------
# Pokemon page tabs, Game Info, forms and the move page changes
# ---------------------------------------------------------------------------
def _page_fake(**extra):
    fake = {"id": 25, "name": "pikachu", "species": "pikachu",
            "sprite": "d.png", "types": ["electric"],
            "stats": [{"name": "hp", "value": 35}],
            "moves": [], "abilities": [],
            "games": [("scarlet-violet", "Scarlet / Violet")]}
    fake.update(extra)
    return fake


class PokemonTabTests(TestCase):
    def _page(self, fake=None):
        with mock.patch("pokedex.views.api.fetch_pokemon",
                        return_value=fake or _page_fake()):
            return self.client.get("/search/?q=pikachu").content.decode()

    def test_every_tab_has_its_panel(self):
        # showPokemonTab() looks each panel up by id; a missing one throws
        # and none of the tabs work
        html = self._page()
        for panel in ("tab-details", "tab-gameinfo", "tab-cards"):
            self.assertIn('id="%s"' % panel, html)
            self.assertEqual(html.count('id="%s"' % panel), 1)

    def test_tabs_are_details_then_game_info_then_cards(self):
        html = self._page()
        self.assertLess(html.index(">Details</button>"),
                        html.index(">Game Info</button>"))
        self.assertLess(html.index(">Game Info</button>"),
                        html.index(">TCG Cards</button>"))

    def test_details_panel_holds_stats_abilities_and_moves(self):
        html = self._page()
        start = html.index('id="tab-details"')
        end = html.index('id="tab-gameinfo"')
        panel = html[start:end]
        self.assertIn("<span>Hp</span><b>35</b>", panel)
        self.assertIn("abilities-section", panel)
        self.assertIn("moves-section", panel)

    def test_panels_are_balanced(self):
        # the Cards tab must sit inside the card, not after a stray </div>
        html = self._page()
        article = html[html.index("<article"):html.index("</article>")]
        self.assertIn('id="tab-cards"', article)
        self.assertEqual(article.count("<div"), article.count("</div>"))

    def test_game_info_is_loaded_for_the_selected_game_and_form(self):
        html = self._page()
        self.assertIn("/game-info/", html)
        self.assertIn("currentFormName", html)
        self.assertIn("refreshGameInfo()", html)

    def test_full_page_has_one_favourite_slider(self):
        with mock.patch("pokedex.views.api.fetch_pokemon",
                        return_value=_page_fake()):
            html = self.client.get("/search/?q=pikachu").content.decode()
        self.assertEqual(html.count('class="fav-checkbox"'), 1)


def _info_source(**over):
    """Hand-made PokeAPI data for a three-stage line (Charmander-ish)."""
    def sp(name, i):
        return {"name": name, "url": "https://pokeapi.co/api/v2/pokemon-species/%d/" % i}
    chain = {"chain": {
        "species": sp("charmander", 4), "evolution_details": [],
        "evolves_to": [{
            "species": sp("charmeleon", 5),
            "evolution_details": [{"trigger": {"name": "level-up"}, "min_level": 16}],
            "evolves_to": [{
                "species": sp("charizard", 6),
                "evolution_details": [{"trigger": {"name": "level-up"}, "min_level": 36}],
                "evolves_to": []}]}]}}
    species = {
        "id": 4, "capture_rate": 45, "base_happiness": 50, "hatch_counter": 20,
        "gender_rate": 1, "growth_rate": {"name": "medium-slow"},
        "egg_groups": [{"name": "monster"}, {"name": "ground"}],
        "pokedex_numbers": [
            {"entry_number": 4, "pokedex": {"name": "kanto"}},
            {"entry_number": 10, "pokedex": {"name": "original-johto"}}],
        "flavor_text_entries": [
            {"language": {"name": "en"}, "version": {"name": "red"},
             "flavor_text": "Obviously prefers\nhot places."},
            {"language": {"name": "en"}, "version": {"name": "blue"},
             "flavor_text": "Obviously prefers\fhot places."},
            {"language": {"name": "en"}, "version": {"name": "gold"},
             "flavor_text": "Gold text."}],
    }
    enc = lambda v, lo, hi, ch: {"version": {"name": v}, "max_chance": ch,
        "encounter_details": [{"method": {"name": "gift"}, "min_level": lo,
                               "max_level": hi, "chance": ch}]}
    src = {
        "name": "charmander", "species_name": "charmander", "species_id": 4,
        "species": species, "chain": chain,
        "types": [{"slot": 1, "type": {"name": "fire"}}],
        "past_types": [],
        "abilities": [
            {"ability": {"name": "blaze"}, "is_hidden": False},
            {"ability": {"name": "solar-power"}, "is_hidden": True}],
        "past_abilities": [],
        "encounters": [
            {"location_area": {"name": "pallet-town-area"},
             "version_details": [enc("red", 5, 5, 100), enc("blue", 5, 5, 100)]},
            {"location_area": {"name": "cerulean-city-area"},
             "version_details": [enc("red", 8, 8, 100)]}],
    }
    src.update(over)
    return src


class GameInfoDataTests(TestCase):
    def test_evolution_line_with_methods(self):
        from pokedex import api
        info = api.build_game_info(_info_source(), "red-blue")
        steps = [(e["src"], e["dst"], e["method"]) for e in info["evolution"]["edges"]]
        self.assertEqual(steps, [("Charmander", "Charmeleon", "Level 16"),
                                 ("Charmeleon", "Charizard", "Level 36")])
        self.assertTrue(all(e["available"] for e in info["evolution"]["edges"]))

    def test_evolution_methods_are_readable(self):
        from pokedex import api
        m = api._evolution_method
        self.assertEqual(m({"trigger": {"name": "use-item"},
                            "item": {"name": "fire-stone"}}), "Use Fire Stone")
        self.assertEqual(m({"trigger": {"name": "trade"},
                            "held_item": {"name": "metal-coat"}}),
                         "Trade holding Metal Coat")
        self.assertEqual(m({"trigger": {"name": "level-up"}, "min_happiness": 160,
                            "time_of_day": "day"}),
                         "Level up with high friendship during the day")

    def test_later_generation_evolutions_are_flagged_per_game(self):
        from pokedex import api
        src = _info_source()
        src["chain"]["chain"]["evolves_to"][0]["evolves_to"][0]["species"][
            "url"] = "https://pokeapi.co/api/v2/pokemon-species/700/"   # Gen 6
        gen3 = api.build_game_info(src, "emerald")
        gen6 = api.build_game_info(src, "x-y")
        self.assertEqual([e["available"] for e in gen3["evolution"]["edges"]],
                         [True, False])
        self.assertEqual([e["available"] for e in gen6["evolution"]["edges"]],
                         [True, True])

    def test_wild_locations_for_only_this_games_versions(self):
        from pokedex import api
        rb = api.build_game_info(_info_source(), "red-blue")
        rows = {r["location"]: r for r in rb["encounters"]["rows"]}
        self.assertEqual(sorted(rows), ["Cerulean City", "Pallet Town"])
        self.assertEqual(rows["Pallet Town"]["versions"], "")        # both
        self.assertEqual(rows["Cerulean City"]["versions"], "Red")   # red only
        self.assertEqual(rows["Pallet Town"]["levels"], "5")
        yellow = api.build_game_info(_info_source(), "yellow")
        self.assertEqual(yellow["encounters"]["rows"], [])

    def test_pokedex_entries_and_regional_numbers_match_the_game(self):
        from pokedex import api
        info = api.build_game_info(_info_source(), "red-blue")
        self.assertEqual([d["name"] for d in info["dex"]], ["Kanto"])
        # Red and Blue say the same thing once whitespace is cleaned up
        self.assertEqual([e["text"] for e in info["entries"]],
                         ["Obviously prefers hot places."])

    def test_abilities_depend_on_the_generation(self):
        from pokedex import api
        self.assertTrue(api.build_game_info(_info_source(), "red-blue")["no_abilities"])
        bw = api.build_game_info(_info_source(), "black-white")["abilities"]
        self.assertEqual([a["name"] for a in bw], ["blaze", "solar-power"])
        dp = api.build_game_info(_info_source(), "diamond-pearl")["abilities"]
        self.assertEqual([a["name"] for a in dp], ["blaze"])   # no hidden yet

    def test_types_before_a_type_change(self):
        from pokedex import api
        src = _info_source(
            types=[{"slot": 1, "type": {"name": "fairy"}}],
            past_types=[{"generation": {"name": "generation-v"},
                         "types": [{"slot": 1, "type": {"name": "normal"}}]}])
        self.assertEqual(api.build_game_info(src, "black-white")["types"], ["normal"])
        self.assertEqual(api.build_game_info(src, "x-y")["types"], ["fairy"])

    def test_gen_one_has_no_friendship_or_egg_facts(self):
        from pokedex import api
        labels = lambda g: [l for l, _ in api.build_game_info(_info_source(), g)["facts"]]
        self.assertNotIn("Base friendship", labels("red-blue"))
        self.assertIn("Base friendship", labels("gold-silver"))
        self.assertIn("Catch rate", labels("red-blue"))

    def test_source_is_fetched_once_and_cached(self):
        from pokedex import api
        table = {
            "/pokemon/charmander": {"id": 4, "name": "charmander",
                                    "species": {"name": "charmander"},
                                    "types": [], "abilities": []},
            "/pokemon-species/charmander": {
                "id": 4, "evolution_chain": {"url": "https://pokeapi.co/api/v2/evolution-chain/2/"}},
            "/evolution-chain/2/": _info_source()["chain"],
            "/pokemon/4/encounters": _info_source()["encounters"],
        }
        calls = []
        def fake_get(url, **kw):
            calls.append(url)
            for key, payload in table.items():
                if url.endswith(key):
                    return _resp(payload)
            return _resp(None, ok=False)
        api._GAME_INFO_CACHE.clear()
        with mock.patch("pokedex.api.requests.get", side_effect=fake_get):
            a = api.fetch_game_info("charmander", "red-blue")
            n = len(calls)
            b = api.fetch_game_info("Charmander", "x-y")
        self.assertEqual(len(calls), n)            # second game: no new requests
        self.assertEqual(a["label"], "Red / Blue")
        self.assertEqual(b["label"], "X / Y")
        api._GAME_INFO_CACHE.clear()

    def test_unknown_game_or_pokemon(self):
        from pokedex import api
        self.assertIsNone(api.fetch_game_info("charmander", "not-a-game"))
        api._GAME_INFO_CACHE.clear()
        with mock.patch("pokedex.api.requests.get", return_value=_resp(None, ok=False)):
            self.assertIsNone(api.fetch_game_info("nope", "red-blue"))


class GameInfoViewTests(TestCase):
    def test_renders_evolution_locations_and_facts(self):
        from pokedex import api
        info = api.build_game_info(_info_source(), "red-blue")
        with mock.patch("pokedex.views.api.fetch_game_info", return_value=info):
            html = self.client.get("/game-info/?q=charmander&game=red-blue").content.decode()
        # ordinary evolutions are on the Details tab, not repeated here
        self.assertNotIn("Charmander → Charmeleon", html)
        self.assertNotIn("Evolution methods", html)
        self.assertIn("Pallet Town", html)
        self.assertIn("Catch rate", html)
        self.assertIn("Kanto", html)
        self.assertIn("Obviously prefers hot places.", html)

    def test_special_evolution_methods_are_explained_here(self):
        from pokedex import api
        src = _info_source()
        step = src["chain"]["chain"]["evolves_to"][0]
        step["evolution_details"] = [
            {"trigger": {"name": "three-critical-hits"}}]
        info = api.build_game_info(src, "sword-shield")
        with mock.patch("pokedex.views.api.fetch_game_info", return_value=info):
            html = self.client.get("/game-info/?q=charmander&game=sword-shield").content.decode()
        self.assertIn("Evolution methods", html)
        self.assertIn("Charmander → Charmeleon", html)
        self.assertIn("Land three critical hits in one battle", html)
        self.assertNotIn("Charmeleon → Charizard", html)   # level 36: Details tab

    def test_game_without_location_data_says_so(self):
        from pokedex import api
        info = api.build_game_info(_info_source(), "scarlet-violet")
        with mock.patch("pokedex.views.api.fetch_game_info", return_value=info):
            html = self.client.get("/game-info/?q=charmander&game=scarlet-violet").content.decode()
        self.assertIn("Wild location data is not available", html)

    def test_not_found(self):
        with mock.patch("pokedex.views.api.fetch_game_info", return_value=None):
            html = self.client.get("/game-info/?q=zzz&game=red-blue").content.decode()
        self.assertIn("No game info found", html)

    def test_missing_params_do_not_crash(self):
        self.assertEqual(self.client.get("/game-info/").status_code, 200)


class ColourFormTests(TestCase):
    def _minior_table(self):
        names = ["minior-red-meteor", "minior-orange-meteor",
                 "minior-red", "minior-orange", "minior-blue"]
        table = {"/pokemon/" + n: _poke(n, 774, n + ".png", n + "-s.png", "minior")
                 for n in names}
        table["/pokemon/minior"] = None
        table["/pokemon-species/minior"] = {"varieties": [
            {"is_default": i == 0, "pokemon": {"name": n}} for i, n in enumerate(names)]}
        return table

    def _patched(self, table):
        def fake_get(url, **kw):
            for key, payload in table.items():
                if url.endswith(key):
                    return _resp(payload, ok=payload is not None)
            return _resp(None, ok=False)
        return mock.patch("pokedex.api.requests.get", side_effect=fake_get)

    def test_sprites_page_accepts_a_species_name_and_lists_colours(self):
        from pokedex import api
        with self._patched(self._minior_table()):
            sprites = api.fetch_sprites("minior")          # species name
            forms = api.fetch_sprite_forms("minior")
        self.assertTrue(sprites)
        self.assertEqual([f["label"] for f in forms],
                         ["Meteor", "Red Core", "Orange Core", "Blue Core"])
        blue = forms[-1]["sprites"][0]
        self.assertEqual(blue["url"], "minior-blue.png")

    def test_sprites_page_renders_form_buttons(self):
        forms = [
            {"name": "minior-red-meteor", "label": "Meteor", "current": True,
             "sprites": [{"game": "scarlet-violet", "url": "a.png", "shiny": ""}]},
            {"name": "minior-blue", "label": "Blue Core", "current": False,
             "sprites": [{"game": "scarlet-violet", "url": "b.png", "shiny": ""}]},
        ]
        with mock.patch("pokedex.views.api.fetch_sprites",
                        return_value=forms[0]["sprites"]), \
             mock.patch("pokedex.views.api.fetch_sprite_forms", return_value=forms):
            html = self.client.get("/sprites/?q=minior").content.decode()
        self.assertIn('id="form-toggle"', html)
        self.assertIn("Blue Core", html)
        self.assertIn("setSpriteForm(1)", html)
        self.assertIn('id="sprite-forms-data"', html)

    def test_sprites_page_without_forms_has_no_toggle(self):
        sprites = [{"game": "scarlet-violet", "url": "a.png", "shiny": ""}]
        with mock.patch("pokedex.views.api.fetch_sprites", return_value=sprites), \
             mock.patch("pokedex.views.api.fetch_sprite_forms", return_value=[]):
            html = self.client.get("/sprites/?q=pikachu").content.decode()
        self.assertNotIn('id="form-toggle"', html)

    def test_compare_page_has_form_buttons_per_side(self):
        form = lambda n, l, cur: {
            "name": n, "label": l, "current": cur, "sprite": n + ".png",
            "shiny_sprite": "", "sprites_by_gen": {}, "shiny_sprites_by_gen": {},
            "stats": [], "types": [], "games": []}
        a = _page_fake(name="minior-red-meteor", forms=[
            form("minior-red-meteor", "Meteor", True), form("minior-blue", "Blue Core", False)])
        b = _page_fake(name="pikachu", forms=[])
        with mock.patch("pokedex.views.api.fetch_pokemon", side_effect=[a, b]):
            html = self.client.get("/compare/?a=minior&b=pikachu").content.decode()
        self.assertIn('id="cmp-a-forms"', html)
        self.assertIn("setCmpForm('a', 1)", html)
        self.assertIn('id="cmp-a-forms-data"', html)
        self.assertNotIn('id="cmp-b-forms"', html)


class MovePageTests(TestCase):
    MOVE = {"id": 85, "name": "thunderbolt", "type": "electric", "power": 90,
            "accuracy": 100, "pp": 15, "damage_class": "special",
            "effect": "May paralyze.", "games": []}

    def test_no_category_pill_beside_the_type_and_title_is_move_type(self):
        with mock.patch("pokedex.views.api.fetch_move", return_value=self.MOVE):
            html = self.client.get("/moves/?q=thunderbolt").content.decode()
        self.assertIn("type-electric", html)
        self.assertNotIn('class="type-badge type-special"', html)
        self.assertNotIn("<span>Category</span>", html)
        self.assertIn("<span>Move type</span>", html)
        self.assertIn(">Special</b>", html)

    def test_compare_moves_has_no_pill_and_each_card_uses_its_own_class(self):
        other = dict(self.MOVE, id=94, name="psychic", type="psychic",
                     damage_class="physical")
        with mock.patch("pokedex.views.api.fetch_move", side_effect=[self.MOVE, other]):
            html = self.client.get("/compare/?mode=move&a=thunderbolt&b=psychic").content.decode()
        self.assertNotIn("type-special", html)
        self.assertNotIn("type-physical", html)
        self.assertNotIn("Category", html)
        self.assertEqual(html.count("Move type"), 2)
        self.assertLess(html.index("Special"), html.index("Physical"))

class FormGamesAndGenderTests(TestCase):
    def test_battle_forms_only_exist_in_their_games(self):
        from pokedex import api
        everything = {"x-y", "sword-shield", "scarlet-violet"}
        self.assertEqual(
            api._limit_form_games("charizard-mega-x", everything), {"x-y"}
        )
        self.assertEqual(
            api._limit_form_games("charizard-gmax", everything),
            {"sword-shield"},
        )
        # A normal form is left alone.
        self.assertEqual(
            api._limit_form_games("charizard", everything), everything
        )

    def test_female_sprites_are_read_per_game(self):
        from pokedex import api
        raw = {
            "front_female": "https://img/f.png",
            "versions": {"generation-iv": {"diamond-pearl": {
                "front_default": "https://img/m.png",
                "front_female": "https://img/dp-f.png",
            }}},
        }
        out = api._female_sprites(raw)
        self.assertEqual(out["female_sprite"], "https://img/f.png")
        self.assertEqual(
            out["female_sprites_by_gen"].get("diamond-pearl"),
            "https://img/dp-f.png",
        )
        self.assertEqual(out["shiny_female_sprites_by_gen"], {})


class MoveGamesAndTmTests(TestCase):
    def test_games_come_from_flavor_text_and_tm_records_with_tm_numbers(self):
        from pokedex import api
        move = {
            "id": 85, "name": "thunderbolt", "type": {"name": "electric"},
            "power": 90, "accuracy": 100, "pp": 15,
            "damage_class": {"name": "special"},
            "generation": {"name": "generation-i"}, "effect_entries": [],
            "flavor_text_entries": [{
                "language": {"name": "en"}, "flavor_text": "x",
                "version_group": {"name": "sword-shield"},
            }],
            "machines": [
                {"machine": {"url": "m1"},
                 "version_group": {"name": "sword-shield"}},
                {"machine": {"url": "m2"},
                 "version_group": {"name": "legends-za"}},
            ],
            "past_values": [],
        }
        items = {"m1": "tr10", "m2": "tm24"}

        def fake_get(url, **kwargs):
            resp = mock.Mock()
            resp.json.return_value = (
                {"item": {"name": items[url]}} if url in items else move
            )
            return resp

        # requests.get is patched for every module, so keep the Bulbapedia
        # TM lookup (and its disk cache) out of this test
        with mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
             mock.patch("pokedex.api._tm_rows", return_value=[]):
            games = api.fetch_move("thunderbolt")["games"]
        tms = {g["key"]: g["tm"] for g in games}
        self.assertEqual(tms["sword-shield"], "TR10")
        self.assertEqual(tms["legends-za"], "TM24")
        # No flavor text or TM record, but a Gen 1 move is in BDSP.
        self.assertIn("brilliant-diamond-shining-pearl", tms)
        self.assertEqual(tms["brilliant-diamond-shining-pearl"], "")

class SpritesMovesAbilitiesLayoutTests(TestCase):
    MOVE = {"id": 85, "name": "thunderbolt", "type": "electric", "power": 90,
            "accuracy": 100, "pp": 15, "damage_class": "special",
            "effect": "May paralyze.",
            "games": [{"key": "scarlet-violet", "label": "Scarlet / Violet",
                       "type": "electric", "power": 90, "accuracy": 100,
                       "pp": 15}]}

    def test_sprites_page_has_no_favourite_slider_outside_the_popup(self):
        sprites = [{"game": "scarlet-violet", "url": "a.png", "shiny": "b.png"}]
        with mock.patch("pokedex.views.api.fetch_sprites", return_value=sprites), \
             mock.patch("pokedex.views.api.fetch_sprite_forms", return_value=[]):
            html = self.client.get("/sprites/?q=abomasnow").content.decode()
        self.assertIn("sprites-top", html)
        self.assertNotIn('id="sprite-fav"', html)
        # the only favourite slider left is the one in the popup
        self.assertEqual(html.count('class="fav-checkbox"'), 1)
        self.assertIn('id="lightbox-fav-cb"', html)

    def test_sprites_forms_sit_left_of_the_grid_under_a_forms_title(self):
        sprites = [{"game": "scarlet-violet", "url": "a.png", "shiny": "b.png"}]
        forms = [{"label": "Disguised", "name": "mimikyu", "current": True,
                  "sprites": sprites},
                 {"label": "Busted", "name": "mimikyu-busted", "current": False,
                  "sprites": sprites}]
        with mock.patch("pokedex.views.api.fetch_sprites", return_value=sprites), \
             mock.patch("pokedex.views.api.fetch_sprite_forms", return_value=forms):
            html = self.client.get("/sprites/?q=mimikyu").content.decode()
        body = html[html.index("sprites-body"):]
        self.assertLess(body.index('id="form-toggle"'), body.index("sprite-grid"))
        self.assertLess(body.index(">Forms</span>"), body.index("form-btn"))

    def test_sprites_page_has_no_black_white_sprites_for_later_pokemon(self):
        from pokedex import api
        bw = {"front_default": "bw.png", "front_shiny": "bws.png"}
        versions = {"generation-v": {"black-white": bw},
                    "generation-vi": {"x-y": {"front_default": "xy.png"}}}
        new = {"id": 700, "name": "sylveon", "sprites": {"versions": versions},
               "species": {"url": "https://pokeapi.co/api/v2/pokemon-species/700/"}}
        old = {"id": 133, "name": "eevee", "sprites": {"versions": versions},
               "species": {"url": "https://pokeapi.co/api/v2/pokemon-species/133/"}}
        with mock.patch("pokedex.api._add_champions_fallback"), \
             mock.patch("pokedex.api._add_za_fallback"):
            new_games = [s["game"] for s in api._sprite_list(new)]
            old_games = [s["game"] for s in api._sprite_list(old)]
        self.assertNotIn("black-white", new_games)
        self.assertIn("x-y", new_games)
        self.assertIn("black-white", old_games)

    def test_only_mega_zygarde_uses_a_3d_sprite_in_lists(self):
        from pokedex import api, views
        self.assertEqual(views.SUGGEST_3D_NAMES, ("zygarde-mega",))
        with mock.patch("pokedex.api._mon_type_map", return_value={}), \
             mock.patch("pokedex.api._species_dex_map", return_value={}):
            mons = api._add_types([
                {"id": 718, "name": "zygarde-50", "sprite": "2d50.png"},
                {"id": 10181, "name": "zygarde-mega", "sprite": "2dm.png"},
            ])
        self.assertEqual(mons[0]["sprite"], "2d50.png")
        self.assertFalse(mons[0].get("sprite_3d"))
        self.assertTrue(mons[1]["sprite_3d"])
        self.assertIn("/home/", mons[1]["sprite"])

    def test_signature_z_move_lists_who_can_use_it(self):
        from pokedex import api
        ids = {"snorlax": 143}
        fake = lambda url: {"id": ids[url.rsplit("/", 1)[-1]]} \
            if url.rsplit("/", 1)[-1] in ids else None
        api._SPECIAL_LEARNER_CACHE.clear()
        with mock.patch("pokedex.api._get_json", side_effect=fake), \
             mock.patch("pokedex.api._mon_type_map", return_value={}), \
             mock.patch("pokedex.api._species_dex_map", return_value={}):
            out = api._special_move_learners("pulverizing-pancake", "normal")
        self.assertEqual([m["name"] for m in out], ["snorlax"])

    def test_max_move_learners_are_the_dynamax_capable_pokemon_of_that_type(self):
        from pokedex import api
        api._SPECIAL_LEARNER_CACHE.clear()
        moves = {"flamethrower": {"learned_by_pokemon": [
            {"name": "charizard", "url": "https://x/pokemon/6/"},
            {"name": "zacian", "url": "https://x/pokemon/888/"},
            {"name": "bulbasaur", "url": "https://x/pokemon/1/"}]}}
        get = lambda url: moves.get(url.rsplit("/", 1)[-1])
        with mock.patch("pokedex.api._move_type_map",
                        return_value={"flamethrower": "fire"}), \
             mock.patch("pokedex.api._load_extra", return_value={
                 "flamethrower": {"g": 1, "c": "special"}}), \
             mock.patch("pokedex.api._game_species",
                        return_value={"charizard", "zacian"}), \
             mock.patch("pokedex.api._species_dex_map", return_value={
                 "charizard": 6, "zacian": 888, "bulbasaur": 1}), \
             mock.patch("pokedex.api._get_json", side_effect=get), \
             mock.patch("pokedex.api._mon_type_map", return_value={}):
            out = api._special_move_learners("max-flare", "fire")
        self.assertEqual([m["name"] for m in out], ["charizard"])

    def test_eternamax_is_shown_as_a_form_of_the_evolution_line(self):
        from pokedex import api
        self.assertTrue(api._in_evolution_line("eternatus-eternamax"))
        self.assertEqual(
            api._transformation_name("eternatus-eternamax", "eternatus"),
            "Eternamax Eternatus")

    def test_move_favourite_slider_is_in_the_game_row(self):
        for url in ("/moves/?q=thunderbolt",):
            with mock.patch("pokedex.views.api.fetch_move", return_value=self.MOVE):
                html = self.client.get(url).content.decode()
            row = html[html.index("move-games-row"):html.index('class="pokemon-top"')]
            self.assertIn('id="move-fav"', row)
            self.assertIn("Scarlet / Violet", row)

    def test_moves_htmx_result_has_the_same_inline_slider(self):
        with mock.patch("pokedex.views.api.fetch_move", return_value=self.MOVE):
            html = self.client.get(
                "/moves/?q=thunderbolt", HTTP_HX_REQUEST="true"
            ).content.decode()
        self.assertIn('id="move-fav"', html)
        self.assertNotIn('hx-swap-oob', html)

    def test_ability_search_uses_the_full_list_endpoint(self):
        html = self.client.get("/abilities/").content.decode()
        self.assertIn("/api/ability-list/", html)
        self.assertNotIn("'wonder-skill'", html)  # the old hard-coded list

    def test_ability_list_endpoint_returns_every_ability(self):
        import tempfile
        from django.test import override_settings
        pages = [
            {"results": [{"name": "power-construct"}, {"name": "stench"}]},
            {"results": []},
        ]
        resp = [mock.Mock(**{"json.return_value": p}) for p in pages]
        with tempfile.TemporaryDirectory() as tmp, \
             override_settings(BASE_DIR=tmp), \
             mock.patch("pokedex.views.requests.get", side_effect=resp):
            data = self.client.get("/api/ability-list/").json()
        self.assertEqual(data, ["power-construct", "stench"])

    def test_ability_slug_accepts_spaces_and_apostrophes(self):
        from pokedex import api
        self.assertEqual(api._ability_slug("Power Construct"), "power-construct")
        self.assertEqual(api._ability_slug("Mind's Eye"), "minds-eye")


class ItemsTabTests(TestCase):
    def setUp(self):
        # the page also looks the item up on Bulbapedia; keep tests offline
        patcher = mock.patch("pokedex.views.api.add_item_locations",
                             side_effect=lambda item: item)
        patcher.start()
        self.addCleanup(patcher.stop)

    ITEM = {"id": 4, "name": "poke-ball", "category": "standard balls",
            "cost": 200, "fling_power": None, "sprite": "https://x/pb.png",
            "effect": "Tries to catch a wild Pokémon.", "attributes": ["countable"],
            "battle": True,
            "games": [{"key": "scarlet-violet", "label": "Scarlet / Violet", "text": "A tool for catching wild Pokémon."},
                      {"key": "red-blue", "label": "Red / Blue", "text": "Old text."}]}

    def test_items_tab_is_in_the_nav(self):
        html = self.client.get("/compare/").content.decode()
        self.assertIn('href="/items/"', html)
        html = self.client.get("/items/").content.decode()
        self.assertIn("/api/item-list/", html)

    def test_item_page_matches_the_other_pages(self):
        with mock.patch("pokedex.views.api.fetch_item", return_value=self.ITEM):
            html = self.client.get("/items/?q=poke-ball").content.decode()
        self.assertIn('class="pokemon-card"', html)
        self.assertIn("Poke-ball", html)
        self.assertIn("₽200", html)
        self.assertIn("Tries to catch", html)
        self.assertIn('id="item-fav"', html)
        self.assertIn('data-type="item"', html)
        self.assertIn('data-sprite="https://x/pb.png"', html)
        # slider on the heading line, like Abilities
        head = html[html.index('class="page-head"'):html.index('class="search-wrapper"')]
        self.assertIn("Search an Item", head)
        self.assertIn('id="fav-slot"', head)

    def test_item_not_found_says_so(self):
        with mock.patch("pokedex.views.api.fetch_item", return_value=None):
            html = self.client.get("/items/?q=zzz", HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('No item found for "zzz"', html)

    def test_item_favourites_are_listed_with_a_link_back(self):
        client = Client()
        client.post("/favourites/add/", {"type": "item", "id": "poke-ball",
                                         "name": "poke-ball", "sprite": "https://x/pb.png"})
        html = client.get("/favourites/").content.decode()
        self.assertIn("Item", html)
        self.assertIn("/items/?q=poke-ball", html)
        self.assertIn('class="fav-item-img"', html)

    def test_item_slug_strips_accents(self):
        from pokedex import api
        self.assertEqual(api._ability_slug("Poké Ball"), "poke-ball")

    def test_ability_slider_shares_the_heading_line(self):
        html = self.client.get("/abilities/").content.decode()
        head = html[html.index('class="page-head"'):html.index('class="search-wrapper"')]
        self.assertIn("Search an Ability", head)
        self.assertIn('id="fav-slot"', head)


class CompareAbilitiesAndItemsTests(TestCase):
    AB = {"id": 150, "name": "power-construct", "effect": "Transforms.",
          "generation": "VII", "pokemon_count": 2}
    AB2 = {"id": 22, "name": "intimidate", "effect": "Lowers Attack.",
           "generation": "III", "pokemon_count": 120}
    IT = {"id": 4, "name": "poke-ball", "category": "standard balls",
          "cost": 200, "fling_power": None, "sprite": "https://x/pb.png",
          "effect": "Catches.", "attributes": ["countable"]}
    IT2 = dict(IT, id=1, name="master-ball", cost=0)

    def test_compare_has_ability_and_item_tabs_and_lists(self):
        html = self.client.get("/compare/").content.decode()
        for tab in ("tab-ability", "tab-item", "/api/ability-list/", "/api/item-list/"):
            self.assertIn(tab, html)

    def test_compare_two_abilities(self):
        with mock.patch("pokedex.views.api.fetch_ability", side_effect=[self.AB, self.AB2]):
            html = self.client.get("/compare/?mode=ability&a=power-construct&b=intimidate").content.decode()
        self.assertIn("Power-construct", html)
        self.assertIn("Lowers Attack.", html)
        self.assertIn('id="ability-fav-a"', html)
        self.assertIn('id="ability-fav-b"', html)
        self.assertIn('id="tab-ability"', html)

    def test_compare_two_items(self):
        with mock.patch("pokedex.views.api.fetch_item", side_effect=[self.IT, self.IT2]):
            html = self.client.get("/compare/?mode=item&a=poke-ball&b=master-ball").content.decode()
        self.assertIn("Poke-ball", html)
        self.assertIn("Master-ball", html)
        self.assertIn('id="item-fav-a"', html)
        self.assertIn('data-sprite="https://x/pb.png"', html)

    def test_compare_unknown_ability_names_what_is_missing(self):
        with mock.patch("pokedex.views.api.fetch_ability", side_effect=[self.AB, None]):
            html = self.client.get("/compare/?mode=ability&a=power-construct&b=zzz").content.decode()
        self.assertIn('"zzz"', html)
        self.assertIn("an ability", html)

    def test_compare_same_item_twice(self):
        html = self.client.get("/compare/?mode=item&a=poke-ball&b=Poke-Ball").content.decode()
        self.assertIn("Pick two different items", html)


class FavouriteLabelTests(TestCase):
    def test_pokemon_favourite_shows_clean_game_label_not_raw_flags(self):
        client = Client()
        client.post("/favourites/add/", {"type": "pokemon", "id": "hydrapple~champions|s",
                                         "name": "hydrapple", "sprite": "https://x/h.png"})
        html = client.get("/favourites/").content.decode()
        self.assertNotIn("champions|s", html)
        self.assertIn("Shiny", html)
        self.assertIn("game=champions", html)
        self.assertNotIn("game=champions%7Cs", html)


class BattleItemsTests(TestCase):
    ITEM = ItemsTabTests.ITEM

    def setUp(self):
        patcher = mock.patch("pokedex.views.api.add_item_locations",
                             side_effect=lambda item: item)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_item_page_has_game_buttons_newest_first(self):
        with mock.patch("pokedex.views.api.fetch_item", return_value=self.ITEM):
            html = self.client.get("/items/?q=poke-ball").content.decode()
        self.assertIn('id="game-buttons"', html)
        self.assertIn('data-text="A tool for catching wild Pokémon."', html)
        self.assertLess(html.index("Scarlet / Violet"), html.index("Red / Blue"))
        self.assertIn('id="item-game-text"', html)

    def test_non_battle_item_is_explained(self):
        town_map = dict(self.ITEM, name="town-map", battle=False)
        with mock.patch("pokedex.views.api.fetch_item", return_value=town_map):
            html = self.client.get("/items/?q=town-map").content.decode()
        self.assertIn("isn't a battle item", html)
        self.assertNotIn('id="item-fav"', html)

    def test_item_list_is_battle_items_only_and_alphabetical(self):
        import tempfile
        from django.test import override_settings
        pages = [
            {"items": [{"name": "x-attack"}, {"name": "potion"}]},
            {"items": [{"name": "leftovers"}, {"name": "x-attack"}]},
            {"items": [{"name": "focus-sash"}]},
        ]
        resp = [mock.Mock(**{"json.return_value": p}) for p in pages]
        with tempfile.TemporaryDirectory() as tmp, \
             override_settings(BASE_DIR=tmp), \
             mock.patch("pokedex.views.requests.get", side_effect=resp) as get:
            data = self.client.get("/api/item-list/").json()
        self.assertEqual(data, ["focus-sash", "leftovers", "potion", "x-attack"])
        asked = [c.args[0] for c in get.call_args_list]
        self.assertTrue(all("item-attribute" in u for u in asked))

    def test_fetch_item_flags_battle_items_and_builds_games(self):
        from pokedex import api
        data = {"id": 234, "name": "leftovers", "cost": 200, "fling_power": 10,
                "category": {"name": "held-items"},
                "attributes": [{"name": "holdable"}, {"name": "holdable-passive"}],
                "effect_entries": [{"language": {"name": "en"}, "effect": "Restores HP."}],
                "flavor_text_entries": [
                    {"language": {"name": "en"}, "text": "Heals a little\nHP.",
                     "version_group": {"name": "sword-shield"}},
                    {"language": {"name": "en"}, "text": "Old.",
                     "version_group": {"name": "gold-silver"}}],
                "sprites": {"default": "l.png"}}
        resp = mock.Mock()
        resp.json.return_value = data
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            item = api.fetch_item("Leftovers")
        self.assertTrue(item["battle"])
        self.assertEqual([g["key"] for g in item["games"]], ["sword-shield", "gold-silver"])
        self.assertEqual(item["games"][0]["text"], "Heals a little HP.")
        self.assertNotIn("holdable passive", item["attributes"])
        data["attributes"] = [{"name": "consumable"}]
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            self.assertFalse(api.fetch_item("town-map")["battle"])


class NavAndAbilityPokemonTests(TestCase):
    def test_current_tab_is_highlighted_and_unclickable(self):
        html = self.client.get("/moves/").content.decode()
        nav = html[html.index('id="main-nav"'):html.index("</nav>")]
        self.assertEqual(nav.count('aria-current="page"'), 1)
        moves_link = nav[nav.index('href="/moves/"'):]
        moves_link = moves_link[:moves_link.index(">")]
        self.assertIn('class="active"', moves_link)
        self.assertIn("onclick=\"return false;\"", moves_link)
        abilities_link = nav[nav.index('href="/abilities/"'):]
        self.assertNotIn("active", abilities_link[:abilities_link.index(">")])

    def test_pokemon_tab_is_active_on_home_and_search(self):
        for url in ("/", "/search/?q=pikachu"):
            with mock.patch("pokedex.views.api.fetch_pokemon", return_value=None):
                html = self.client.get(url).content.decode()
            nav = html[html.index('id="main-nav"'):html.index("</nav>")]
            link = nav[nav.index('href="/"'):]
            self.assertIn('aria-current="page"', link[:link.index(">")])

    def test_ability_page_lists_every_pokemon_with_it(self):
        ability = {"id": 150, "name": "power-construct", "effect": "Transforms.",
                   "generation": "VII", "pokemon_count": 2,
                   "pokemon": [
                       {"id": 718, "name": "zygarde", "hidden": False, "sprite": "https://x/718.png"},
                       {"id": 10118, "name": "zygarde-10", "hidden": True, "sprite": "https://x/10118.png"}]}
        with mock.patch("pokedex.views.api.fetch_ability", return_value=ability):
            html = self.client.get("/abilities/?q=power-construct").content.decode()
        self.assertIn("Pokémon with this ability (2)", html)
        self.assertIn("/search/?q=zygarde-10", html)
        self.assertIn('<span class="hidden-tag">Hidden</span>', html)
        self.assertEqual(html.count('class="hidden-tag"'), 1)
        self.assertIn('<th class="mon-col-hidden">Hidden ability</th>', html)
        self.assertIn('class="mon-table"', html)
        self.assertIn('class="moves-list"', html)
        self.assertEqual(html.count('class="mon-item"'), 2)
        self.assertIn('id="mon-search"', html)

    def test_fetch_ability_builds_the_pokemon_list(self):
        from pokedex import api
        data = {"id": 150, "name": "power-construct",
                "effect_entries": [{"language": {"name": "en"}, "effect": "x"}],
                "generation": {"name": "generation-vii"},
                "pokemon": [
                    {"is_hidden": True, "slot": 3,
                     "pokemon": {"name": "zygarde-10", "url": "https://pokeapi.co/api/v2/pokemon/10118/"}},
                    {"is_hidden": False, "slot": 1,
                     "pokemon": {"name": "zygarde", "url": "https://pokeapi.co/api/v2/pokemon/718/"}}]}
        resp = mock.Mock()
        resp.json.return_value = data
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            ab = api.fetch_ability("power construct")
        self.assertEqual([p["name"] for p in ab["pokemon"]], ["zygarde", "zygarde-10"])
        self.assertTrue(ab["pokemon"][1]["hidden"])
        self.assertTrue(ab["pokemon"][0]["sprite"].endswith("/718.png"))


class SupplementTests(TestCase):
    def test_nihil_light_exists_even_though_pokeapi_lacks_it(self):
        from pokedex import api
        mega = {"id": 10181, "name": "zygarde-mega"}
        with mock.patch("pokedex.api.requests.get", side_effect=Exception("404")), \
             mock.patch("pokedex.api._get_json", return_value=mega):
            move = api.fetch_move("Nihil Light")
        self.assertEqual(move["name"], "nihil-light")
        self.assertEqual((move["type"], move["power"], move["damage_class"]),
                         ("dragon", 200, "special"))
        self.assertEqual([g["key"] for g in move["games"]], ["legends-za"])
        self.assertEqual(move["learned_by"][0]["name"], "zygarde-mega")
        self.assertIsNone(api.fetch_move("not-a-real-move-xyz"))

    def test_nihil_light_page_renders_without_a_number(self):
        move = {"id": None, "name": "nihil-light", "type": "dragon", "power": 200,
                "accuracy": 100, "pp": 10, "damage_class": "special", "effect": "x",
                "tm_table": [], "learned_by": [{"id": 1, "name": "zygarde-mega", "sprite": "z.png"}],
                "games": [{"key": "legends-za", "label": "Legends Z-A", "type": "dragon",
                           "power": 200, "accuracy": 100, "pp": 10, "tm": "", "tm_cost": None, "tm_where": ""}]}
        with mock.patch("pokedex.views.api.fetch_move", return_value=move):
            html = self.client.get("/moves/?q=nihil-light").content.decode()
        self.assertNotIn("#None", html)
        self.assertIn("Pokémon that learn this move (1)", html)
        self.assertIn("/search/?q=zygarde-mega", html)
        self.assertIn('class="mon-table"', html)
        self.assertNotIn("Hidden ability", html)   # abilities only

    def test_move_list_includes_supplement_moves(self):
        import tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp, override_settings(BASE_DIR=tmp), \
             mock.patch("pokedex.views.requests.get", side_effect=Exception("down")):
            data = self.client.get("/api/move-list/").json()
        self.assertIn("nihil-light", data)

    def test_make_it_rain_is_95_percent_in_champions_only(self):
        from pokedex import api
        data = {"id": 791, "name": "make-it-rain", "accuracy": 100, "power": 120, "pp": 5,
                "type": {"name": "steel"}, "damage_class": {"name": "special"},
                "effect_entries": [{"language": {"name": "en"}, "effect": "x"}],
                "flavor_text_entries": [
                    {"language": {"name": "en"}, "flavor_text": "x",
                     "version_group": {"name": "scarlet-violet"}}],
                "learned_by_pokemon": [
                    {"name": "gholdengo", "url": "https://pokeapi.co/api/v2/pokemon/1000/"}],
                "generation": {"name": "generation-ix"}}
        resp = mock.Mock()
        resp.json.return_value = data
        with mock.patch("pokedex.api.requests.get", return_value=resp):
            move = api.fetch_move("make-it-rain")
        by_game = {g["key"]: g for g in move["games"]}
        self.assertEqual(by_game["champions"]["accuracy"], 95)
        self.assertEqual(by_game["champions"]["pp"], 8)
        self.assertEqual(by_game["scarlet-violet"]["accuracy"], 100)
        self.assertEqual(move["learned_by"][0]["name"], "gholdengo")

    def test_zygarde_mega_gets_nihil_light_in_its_learnset(self):
        from pokedex import api
        rows = api._moves_for_record({"name": "zygarde-mega", "moves": []}, {})
        self.assertIn(("nihil-light", "legends-za"),
                      {(m["name"], m["version_group"]) for m in rows})


class ItemWhereTests(TestCase):
    """Locations and prices on the Items tab, read from Bulbapedia's item
    pages (two saved copies live in item_pages/)."""
    PAGES = Path(__file__).resolve().parent.parent / "item_pages"

    def page(self, name):
        from pokedex import bulbapedia
        html = (self.PAGES / (name + ".html")).read_text(encoding="utf-8")
        return bulbapedia.parse_item_page(html)

    def test_prices_are_read_per_game_group(self):
        prices = self.page("Leftovers")["prices"]
        sv = next(p for p in prices if "scarlet-violet" in p["games"])
        self.assertEqual((sv["cost"], sv["sell"]), ("₽20,000", "₽5,000"))
        old = next(p for p in prices if "emerald" in p["games"])
        self.assertEqual(old["cost"], "N/A")        # never sold in a shop

    def test_acquisition_lists_one_time_and_repeatable_ways(self):
        rows = self.page("Leftovers")["acquisition"]
        gs = next(r for r in rows if "gold-silver" in r["games"])
        self.assertIn("Celadon City Restaurant", gs["finite"])
        self.assertTrue(any("Clefable" in x for x in gs["repeatable"]))
        # superscript game tags stay readable
        rse = next(r for r in rows if "emerald" in r["games"])
        self.assertTrue(any("(E only)" in x for x in rse["repeatable"]))

    def test_games_without_a_button_in_this_project_are_dropped(self):
        for row in self.page("Leftovers")["acquisition"]:
            self.assertTrue(row["games"])   # Colosseum / Stadium rows gone

    def test_rows_for_one_game_are_merged(self):
        from pokedex import api
        where = api._merge_item_page(self.page("Covert_Cloak"))
        sv = where["scarlet-violet"]
        self.assertEqual(sv["finite"], ["Montenevera"])
        self.assertEqual(sv["repeatable"],
                         ["Delibird Presents (Levincia Branch)", "Item Printer"])
        self.assertEqual(sv["cost"], "₽20,000")

    def test_add_item_locations_adds_games_and_keeps_order(self):
        from pokedex import api
        where = api._merge_item_page(self.page("Covert_Cloak"))
        item = {"name": "covert-cloak", "sprite": "c.png",
                "games": [{"key": "red-blue", "label": "Red / Blue",
                           "text": "", "sprites": []}]}
        with mock.patch("pokedex.api.fetch_item_where", return_value=where):
            out = api.add_item_locations(item)
        self.assertEqual([g["key"] for g in out["games"]],
                         ["scarlet-violet", "red-blue"])
        self.assertEqual(out["where"], where)
        self.assertNotIn("where", item)             # input not changed

    def test_add_item_locations_returns_item_when_page_unreadable(self):
        from pokedex import api
        item = {"name": "x", "games": []}
        with mock.patch("pokedex.api.fetch_item_where", return_value=None):
            self.assertIs(api.add_item_locations(item), item)

    def test_item_page_shows_where_and_price(self):
        from pokedex import api
        where = api._merge_item_page(self.page("Covert_Cloak"))
        item = dict(ItemsTabTests.ITEM, where=where, where_loaded=True)
        with mock.patch("pokedex.views.api.fetch_item", return_value=item), \
             mock.patch("pokedex.views.api.add_item_locations",
                        side_effect=lambda i: i):
            html = self.client.get("/items/?q=poke-ball").content.decode()
        self.assertIn("Where to find it", html)
        self.assertIn('id="item-where-data"', html)
        self.assertIn("Montenevera", html)
        self.assertIn('id="item-sell-row"', html)
        self.assertIn("renderItemWhere(btn.dataset.vg)", html)

    def test_item_page_says_so_when_locations_could_not_load(self):
        with mock.patch("pokedex.views.api.fetch_item",
                        return_value=ItemsTabTests.ITEM), \
             mock.patch("pokedex.views.api.add_item_locations",
                        side_effect=lambda i: i):
            html = self.client.get("/items/?q=poke-ball").content.decode()
        self.assertIn("Locations could not be loaded", html)


class TmLocationTests(TestCase):
    """Where a TM is found and what it costs, read from the machine's
    Bulbapedia page: one table per move, one row per group of games."""
    HTML = """
<h2><span class="mw-headline">In the core series games</span></h2>
<table><tr><td><b><a title="Thunderbolt (move)">Thunderbolt</a></b></td></tr></table>
<table>
<tr><th></th><th>Location</th><th>Purchase price</th><th>Sell price</th></tr>
<tr><td><b><a title="Pokémon Ruby and Sapphire Versions">RS</a><a title="Pokémon Emerald Version">E</a></b></td>
<td><a>Mauville City</a>, <a>Mauville Game Corner</a></td>
<td>4000<a title="Coin (Game Corner)">C</a></td><td><a title="Pokémon Dollar">$</a>1500</td></tr>
<tr><td><b><a title="Pokémon Colosseum">Colo</a></b></td><td>Mt. Battle</td>
<td>4000<a title="Poké Coupon">PC</a></td><td>N/A</td></tr>
</table>
<table><tr><td><b><a title="Fire Spin (move)">Fire Spin</a></b></td></tr></table>
<table>
<tr><th></th><th>Location</th><th>Purchase price</th><th>Sell price</th></tr>
<tr><td><a title="Pokémon Scarlet and Violet">SV</a></td>
<td><a>Asado Desert</a>, <a>TM Machine</a><hr/><b>Only in The Teal Mask:</b><br/><a>Paradise Barrens</a></td>
<td>800 <a title="League Point">LP</a><br/>Growlithe Fur ×3</td><td><a title="Pokémon Dollar">$</a>800</td></tr>
</table>
<h2>In the spin-off games</h2>
<table><tr><th></th><th>Location</th><th>Purchase price</th><th>Sell price</th></tr>
<tr><td><a title="Pokémon Super Mystery Dungeon">SMD</a></td><td>x</td><td>1</td><td>1</td></tr></table>
"""

    def rows(self):
        from pokedex import bulbapedia
        return bulbapedia.parse_tm_page(self.HTML)

    def test_page_titles_use_bulbapedias_digit_counts(self):
        from pokedex import bulbapedia
        self.assertEqual(bulbapedia.tm_title("TM24"), "TM024")
        self.assertEqual(bulbapedia.tm_title("tm024"), "TM024")
        self.assertEqual(bulbapedia.tm_title("HM1"), "HM01")
        self.assertEqual(bulbapedia.tm_title("TR00"), "TR00")
        self.assertIsNone(bulbapedia.tm_title("Leftovers"))

    def test_rows_hold_location_and_prices_per_game(self):
        rows = self.rows()
        tb = next(r for r in rows if "emerald" in r["games"])
        self.assertEqual(tb["games"], ["ruby-sapphire", "emerald"])
        self.assertEqual(tb["where"], ["Mauville City, Mauville Game Corner"])
        self.assertEqual(tb["cost"], "4,000 Coins")
        self.assertEqual(tb["sell"], "₽1,500")

    def test_games_without_a_button_and_spin_offs_are_dropped(self):
        games = [g for r in self.rows() for g in r["games"]]
        self.assertEqual(len(games), 3)             # no Colosseum / Mystery Dungeon

    def test_notes_split_into_lines_and_costs_keep_materials(self):
        sv = next(r for r in self.rows() if "scarlet-violet" in r["games"])
        self.assertEqual(sv["where"], ["Asado Desert, TM Machine",
                                       "Only in The Teal Mask:",
                                       "Paradise Barrens"])
        self.assertEqual(sv["cost"], "800 LP, Growlithe Fur ×3")

    def test_the_move_decides_which_table_a_game_uses(self):
        from pokedex import bulbapedia
        rows = self.rows() + [{"move": "Snore", "games": ["scarlet-violet"],
                               "where": ["Elsewhere"], "cost": "N/A", "sell": "N/A"}]
        got = bulbapedia.pick_tm_row(rows, "scarlet-violet", "fire-spin")
        self.assertEqual(got["where"][0], "Asado Desert, TM Machine")
        # two tables for the game and no matching move: do not guess
        self.assertIsNone(bulbapedia.pick_tm_row(rows, "scarlet-violet", "x"))

    def test_fetch_move_adds_location_and_price_to_each_game(self):
        from pokedex import api
        move = {
            "id": 85, "name": "thunderbolt", "type": {"name": "electric"},
            "power": 90, "accuracy": 100, "pp": 15,
            "damage_class": {"name": "special"},
            "generation": {"name": "generation-i"}, "effect_entries": [],
            "flavor_text_entries": [],
            "machines": [{"machine": {"url": "m1"},
                          "version_group": {"name": "emerald"}}],
            "past_values": [],
        }

        def fake_get(url, **kwargs):
            resp = mock.Mock()
            resp.json.return_value = (
                {"item": {"name": "tm24"}} if url == "m1" else move)
            return resp

        with mock.patch("pokedex.api.requests.get", side_effect=fake_get), \
             mock.patch("pokedex.api._tm_rows", return_value=self.rows()):
            got = api.fetch_move("thunderbolt")
        emerald = next(g for g in got["games"] if g["key"] == "emerald")
        self.assertEqual(emerald["tm"], "TM24")
        self.assertEqual(emerald["tm_where"], "Mauville City, Mauville Game Corner")
        self.assertEqual(emerald["tm_price"], "4,000 Coins")
        self.assertEqual(emerald["tm_sell"], "₽1,500")
        self.assertEqual(got["tm_table"][0]["price"], "4,000 Coins")

    def test_move_page_carries_location_and_price_for_the_game_button(self):
        move = dict(SpritesMovesAbilitiesLayoutTests.MOVE)
        move["games"] = [dict(move["games"][0], tm="TM024",
                              tm_where="Asado Desert\nTM Machine",
                              tm_price="800 LP", tm_sell="₽800")]
        move["tm_table"] = [{"game": "Scarlet / Violet", "tm": "TM024",
                             "cost": None, "price": "800 LP"}]
        with mock.patch("pokedex.views.api.fetch_move", return_value=move):
            html = self.client.get("/moves/?q=thunderbolt").content.decode()
        self.assertIn('data-tm-price="800 LP"', html)
        self.assertIn('data-tm-sell="₽800"', html)
        self.assertIn("Asado Desert", html)
        self.assertIn("Where to find it", html)
        self.assertIn("Purchase price: ", html)


class SearchFilterTests(TestCase):
    """The data behind the filter bars (Type, Generation, Gender, Form...)."""

    def setUp(self):
        from pokedex import api
        self.api = api
        api._GENERATION_CACHE.clear()
        self.addCleanup(api._GENERATION_CACHE.clear)

    @staticmethod
    def _species(*pairs):
        return [{"name": n, "url": "https://pokeapi.co/api/v2/%s/%d/" % (k, i)}
                for k, n, i in pairs]

    def _fake_json(self, url):
        sp = lambda *p: self._species(*[("pokemon-species",) + x for x in p])
        mv = lambda *n: [{"name": x, "url": ""} for x in n]
        table = {
            "generation?limit=100": {"results": [
                {"name": "generation-i", "url": ".../generation/1/"},
                {"name": "generation-ii", "url": ".../generation/2/"},
            ]},
            "generation/1": {
                "pokemon_species": sp(("bulbasaur", 1), ("magnemite", 81),
                                      ("tauros", 128), ("chansey", 113)),
                "moves": mv("pound", "thunderbolt"),
                "abilities": mv("stench"),
            },
            "generation/2": {
                "pokemon_species": sp(("togepi", 175)),
                "moves": mv("sunny-day"),
                "abilities": mv("pressure"),
            },
            "gender/female": {"pokemon_species_details": [
                {"rate": 1, "pokemon_species": s}
                for s in sp(("bulbasaur", 1), ("chansey", 113),
                            ("togepi", 175))]},
            "gender/male": {"pokemon_species_details": [
                {"rate": 7, "pokemon_species": s}
                for s in sp(("bulbasaur", 1), ("tauros", 128),
                            ("togepi", 175))]},
            "type?limit=100": {"results": [
                {"name": "electric"}, {"name": "psychic"},
                {"name": "normal"}, {"name": "unknown"},
            ]},
            "type/electric": {"moves": mv("thunderbolt")},
            "type/psychic": {"moves": mv("sunny-day")},
            "type/normal": {"moves": mv("pound")},
            "move-damage-class/physical": {"moves": mv("pound")},
            "move-damage-class/special": {"moves": mv("thunderbolt")},
            "move-damage-class/status": {"moves": mv("sunny-day")},
            "item-category?limit=200": {"results": [
                {"name": "choice"}, {"name": "z-crystals"}]},
            "item-category/choice": {"items": mv("choice-band")},
            "item-category/z-crystals": {"items": mv("firium-z--held")},
            "item-attribute/usable-in-battle": {"items": []},
            "item-attribute/holdable": {"items": mv("choice-band")},
            "item-attribute/holdable-passive": {"items": []},
            "item-attribute/holdable-active": {"items": mv("firium-z--held")},
        }
        return table.get(url.replace("https://pokeapi.co/api/v2/", ""))

    def test_pokemon_filters(self):
        mons = [
            {"id": "1", "name": "bulbasaur"},
            {"id": "81", "name": "magnemite"},
            {"id": "113", "name": "chansey"},
            {"id": "128", "name": "tauros"},
            {"id": "175", "name": "togepi"},
            {"id": "10033", "name": "bulbasaur-mega"},
        ]
        types = {1: ["grass", "poison"], 81: ["electric", "steel"],
                 113: ["normal"], 128: ["normal"], 175: ["fairy"],
                 10033: ["grass", "poison"]}
        dex = {"bulbasaur": 1, "magnemite": 81, "chansey": 113,
               "tauros": 128, "togepi": 175}
        with mock.patch.object(self.api, "_get_json", self._fake_json), \
             mock.patch.object(self.api, "_mon_type_map", return_value=types), \
             mock.patch.object(self.api, "_species_dex_map", return_value=dex):
            out = self.api.build_pokemon_filters(mons)
        self.assertEqual(out["bulbasaur"], {
            "t": ["grass", "poison"], "g": 1, "x": "both", "f": "base"})
        self.assertEqual(out["magnemite"]["x"], "none")      # genderless
        self.assertEqual(out["chansey"]["x"], "female")      # female only
        self.assertEqual(out["tauros"]["x"], "male")         # male only
        self.assertEqual(out["togepi"]["g"], 2)
        # a Mega takes its species' generation and gender, with its own form
        self.assertEqual(out["bulbasaur-mega"]["g"], 1)
        self.assertEqual(out["bulbasaur-mega"]["f"], "mega")

    def test_pokemon_filters_not_built_from_partial_data(self):
        def down(url):
            return None if "gender" in url else self._fake_json(url)
        with mock.patch.object(self.api, "_get_json", down), \
             mock.patch.object(self.api, "_mon_type_map",
                               return_value={1: ["grass"]}), \
             mock.patch.object(self.api, "_species_dex_map",
                               return_value={"bulbasaur": 1}):
            self.assertIsNone(
                self.api.build_pokemon_filters([{"id": "1", "name": "x"}]))

    def test_form_kinds(self):
        kind = self.api._form_kind
        self.assertEqual(kind("pikachu", 25), "base")
        self.assertEqual(kind("charizard-mega-x", 10034), "mega")
        self.assertEqual(kind("charizard-gmax", 10195), "gmax")
        self.assertEqual(kind("terapagos-terastal", 10290), "tera")
        self.assertEqual(kind("necrozma-ultra", 10157), "other")
        self.assertEqual(kind("terapagos", 1024), "base")

    def test_move_filters(self):
        with mock.patch.object(self.api, "_get_json", self._fake_json):
            out = self.api.build_move_filters()
        self.assertEqual(out["thunderbolt"],
                         {"t": "electric", "c": "special", "g": 1})
        self.assertEqual(out["sunny-day"],
                         {"t": "psychic", "c": "status", "g": 2})
        # moves PokeAPI lacks come from supplements.EXTRA_MOVES
        self.assertEqual(out["nihil-light"]["t"], "dragon")
        self.assertEqual(out["nihil-light"]["c"], "special")

    def test_ability_filters(self):
        with mock.patch.object(self.api, "_get_json", self._fake_json):
            out = self.api.build_ability_filters()
        self.assertEqual(out, {"stench": {"g": 1}, "pressure": {"g": 2}})

    def test_item_filters(self):
        with mock.patch.object(self.api, "_get_json", self._fake_json):
            out = self.api.build_item_filters()
        self.assertEqual(out["choice-band"], {"c": "choice", "u": ["held"]})
        # "firium-z--held" is shown as "firium-z", and always counts as held
        self.assertEqual(out["firium-z"], {"c": "z-crystals", "u": ["held"]})

    def test_filter_data_view_caches_and_rejects_unknown_kinds(self):
        import tempfile
        self.assertEqual(
            self.client.get("/api/filter-data/?kind=bogus").status_code, 400)
        with tempfile.TemporaryDirectory() as tmp, \
             override_settings(BASE_DIR=Path(tmp)):
            with mock.patch("pokedex.views.api.build_ability_filters",
                            return_value={"stench": {"g": 1}}) as build:
                first = self.client.get("/api/filter-data/?kind=ability")
                second = self.client.get("/api/filter-data/?kind=ability")
            self.assertEqual(first.json(), {"stench": {"g": 1}})
            self.assertEqual(second.json(), {"stench": {"g": 1}})
            self.assertEqual(build.call_count, 1)    # the second is cached
            # PokeAPI down and nothing cached: an empty answer, not an error
            with mock.patch("pokedex.views.api.build_move_filters",
                            return_value=None):
                down = self.client.get("/api/filter-data/?kind=move")
            self.assertEqual(down.status_code, 200)
            self.assertEqual(down.json(), {})

    @override_settings(STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    })
    def test_every_search_page_has_a_filter_bar(self):
        for url in ("/", "/moves/", "/abilities/", "/items/", "/sprites/",
                    "/cards/", "/compare/"):
            html = self.client.get(url).content.decode()
            self.assertIn("pksFilters.attach", html, url)
        self.assertContains(self.client.get("/"), "pokedex/filters.js")


class SpecialEvolutionTests(TestCase):
    def test_only_requirements_that_need_a_sentence_are_special(self):
        from pokedex import api
        special = api._evolution_is_special
        self.assertFalse(special({"trigger": {"name": "level-up"}, "min_level": 16}))
        self.assertFalse(special({"trigger": {"name": "use-item"},
                                  "item": {"name": "fire-stone"}}))
        self.assertFalse(special({"trigger": {"name": "trade"}}))
        self.assertTrue(special({"trigger": {"name": "three-critical-hits"}}))
        self.assertTrue(special({"trigger": {"name": "take-damage"}}))
        self.assertTrue(special({"trigger": {"name": "other"}}))
        self.assertTrue(special({"trigger": {"name": "level-up"},
                                 "known_move": {"name": "rage-fist"},
                                 "min_move_count": 20}))

    def test_new_requirements_are_spelled_out(self):
        from pokedex import api
        text = api._evolution_method({
            "trigger": {"name": "level-up"},
            "known_move": {"name": "rage-fist"}, "min_move_count": 20})
        self.assertEqual(text, "Level up after using Rage Fist 20 times")

    def test_special_step_points_to_the_game_info_tab(self):
        from pokedex import api
        label = api._evolution_label({"trigger": {"name": "three-critical-hits"}})
        self.assertTrue(label["special"])
        self.assertEqual(label["sub"], "See Game Info tab")
        plain = api._evolution_label({"trigger": {"name": "level-up"}, "min_level": 16})
        self.assertFalse(plain.get("special"))

    def test_edges_are_flagged_and_listed(self):
        from pokedex import api
        src = _info_source()
        src["chain"]["chain"]["evolves_to"][0]["evolution_details"] = [
            {"trigger": {"name": "take-damage"}}]
        info = api.build_game_info(src, "sword-shield")
        self.assertEqual([e["src"] for e in info["evolution"]["special_edges"]],
                         ["Charmander"])
        plain = api.build_game_info(_info_source(), "sword-shield")
        self.assertEqual(plain["evolution"]["special_edges"], [])

    def test_leafeon_only_lists_the_way_it_evolves_in_that_game(self):
        from pokedex import api
        src = _info_source()
        sp = lambda n, i: {"name": n, "url": "https://pokeapi.co/api/v2/pokemon-species/%d/" % i}
        src["species_name"] = "eevee"
        src["chain"] = {"chain": {
            "species": sp("eevee", 133), "evolution_details": [],
            "evolves_to": [{"species": sp("leafeon", 470), "evolves_to": [],
                            "evolution_details": [
                {"trigger": {"name": "level-up"}, "location": {"name": "eterna-forest"}},
                {"trigger": {"name": "level-up"}, "location": {"name": "kalos-route-20"}},
                {"trigger": {"name": "use-item"}, "item": {"name": "leaf-stone"}}]}]}}
        def methods(game):
            info = api.build_game_info(src, game)
            return [e["method"] for e in info["evolution"]["edges"]]
        self.assertEqual(methods("scarlet-violet"), ["Use Leaf Stone"])
        self.assertEqual(methods("sword-shield"), ["Use Leaf Stone"])
        self.assertEqual(methods("platinum"),
                         ["Level up near the Moss Rock (Eterna Forest)"])
        self.assertEqual(methods("x-y"),
                         ["Level up near the Moss Rock (Kalos Route 20)"])


class PokemonHeaderLayoutTests(TestCase):
    def test_types_sit_under_the_sprite_and_evolution_beside_it(self):
        from pokedex import api
        mon = {
            "id": 25, "name": "pikachu", "species": "pikachu", "types": ["electric"],
            "sprite": "s.png", "games": [], "stats": [], "abilities": [],
            "evolution_tree": {"slug": "pikachu", "q": "pikachu", "form": "pikachu",
                               "name": "Pikachu", "sprite": "", "gen": 1,
                               "mega": False, "games": [], "current": True,
                               "branches": [{"labels": [{"text": "Special",
                                                         "sub": "See Game Info tab",
                                                         "special": True,
                                                         "title": "x"}],
                                             "node": {"slug": "raichu", "q": "raichu",
                                                      "form": "raichu", "name": "Raichu",
                                                      "sprite": "", "gen": 1, "mega": False,
                                                      "games": [], "branches": [],
                                                      "current": False}}]},
            "forms": [], "stat_total": 0,
        }
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=mon):
            html = self.client.get("/search/?q=pikachu", HTTP_HX_REQUEST="true").content.decode()
        col = html.index("pokemon-sprite-col")
        side = html.index("pokemon-side")
        self.assertLess(col, html.index('class="types"'))
        self.assertLess(html.index('class="types"'), side)     # types in the sprite column
        self.assertLess(side, html.index('id="evo-tree"'))     # evolution in the side column
        tab_details = html.index('id="tab-details"')
        self.assertLess(html.index('id="evo-tree"'), tab_details)   # not inside the Details tab
        self.assertIn("showPokemonTab('gameinfo')", html)


class FloetteEternalTests(TestCase):
    def test_form_button_name_finds_the_pokemon(self):
        from pokedex import api
        seen = []

        def fake_json(url, *a, **k):
            seen.append(url)
            return {"name": "floette-eternal", "id": 10061}

        with mock.patch("pokedex.api._get_json", side_effect=fake_json):
            for query in ("Eternal Flower Floette", "floette-eternal", "eternal floette"):
                seen.clear()
                self.assertEqual(api._get_pokemon_json(query)["name"], "floette-eternal")
                self.assertTrue(seen[0].endswith("/floette-eternal"))

    def test_it_is_in_the_search_list(self):
        from pokedex import views
        self.assertIn("floette-eternal", views.SEARCHABLE_FORM_NAMES)
        import json
        from pathlib import Path
        data = json.loads((Path(views.__file__).parent / "data" / "pokemon_list.json").read_text("utf-8"))
        self.assertIn("floette-eternal", [p["name"] for p in data])


class FlatArtFallbackTests(TestCase):
    def test_form_without_a_default_sprite_gets_home_or_artwork(self):
        from pokedex import api
        home = api._flat_art_urls(10301)[0]
        with mock.patch("pokedex.api._url_exists", side_effect=lambda u: u == api._flat_art_urls(10301)[1]):
            self.assertEqual(api._sprite_or_flat_art({}, 10301),
                             api._flat_art_urls(10301)[1])
        with mock.patch("pokedex.api._url_exists", return_value=False):
            self.assertEqual(api._sprite_or_flat_art({}, 10301), "")
        self.assertIn("/other/home/10301.png", home)

    def test_a_real_sprite_is_never_replaced_or_probed(self):
        from pokedex import api
        raw = {"front_default": "https://x/y.png"}
        with mock.patch("pokedex.api._url_exists") as probe:
            self.assertEqual(api._sprite_or_flat_art(raw, 25), "https://x/y.png")
        probe.assert_not_called()

    def test_icon_candidates_end_with_the_flat_art(self):
        from pokedex import api
        urls = api._sprite_candidates(10301)
        self.assertEqual(urls[-2:], api._flat_art_urls(10301))


class ItemGenerationSpriteTests(TestCase):
    def item(self):
        return {"name": "leftovers", "english_name": "Leftovers",
                "sprite": "https://x/items/leftovers.png",
                "games": [{"key": k, "label": k, "text": "", "sprites": []}
                          for k in ("gold-silver", "scarlet-violet", "red-blue")]}

    def test_bag_sprite_urls_follow_bulbapedias_file_paths(self):
        from pokedex import api
        self.assertEqual(
            api._bag_sprite_url("Leftovers", "SV"),
            "https://archives.bulbagarden.net/media/upload/7/7f/Bag_Leftovers_SV_Sprite.png")
        self.assertEqual(
            api._bag_sprite_url("Leftovers", ""),
            "https://archives.bulbagarden.net/media/upload/0/0f/Bag_Leftovers_Sprite.png")
        self.assertIn("Bag_Covert_Cloak_SV_Sprite.png",
                      api._bag_sprite_url("Covert Cloak", "SV"))

    def test_each_game_gets_only_art_that_exists_default_last(self):
        from pokedex import api
        api._ITEM_SPRITE_CACHE.clear()
        sv = api._bag_sprite_url("Leftovers", "SV")
        common = api._bag_sprite_url("Leftovers", "")
        exists = {sv, common}
        with mock.patch("pokedex.api._probe_url", side_effect=lambda u: u in exists):
            out = api.add_item_sprites(self.item())
        by = {g["key"]: g["sprites"] for g in out["games"]}
        self.assertEqual(by["scarlet-violet"], [sv, common, "https://x/items/leftovers.png"])
        self.assertEqual(by["gold-silver"], ["https://x/items/leftovers.png"])
        self.assertEqual(by["red-blue"], ["https://x/items/leftovers.png"])

    def test_nothing_changes_when_the_check_cannot_run(self):
        from pokedex import api
        api._ITEM_SPRITE_CACHE.clear()
        api._item_sprite_backoff_until = 0.0
        item = self.item()
        with mock.patch("pokedex.api._probe_url", return_value=None):
            self.assertIs(api.add_item_sprites(item), item)
        api._item_sprite_backoff_until = 0.0

class FavouriteModelTests(TestCase):
    ADD = {"type": "pokemon", "id": "25", "name": "pikachu"}

    def test_favourites_are_stored_in_the_database(self):
        client = Client()
        client.post("/favourites/add/", self.ADD)
        fav = Favourite.objects.get()
        self.assertEqual((fav.kind, fav.item_id, fav.name),
                         ("pokemon", "25", "pikachu"))
        self.assertEqual(fav.owner, client.session["visitor_id"])

    def test_saving_twice_keeps_one_row(self):
        client = Client()
        client.post("/favourites/add/", self.ADD)
        resp = client.post("/favourites/add/", self.ADD)
        self.assertEqual(Favourite.objects.count(), 1)
        self.assertEqual(resp["X-Fav-Count"], "1")

    def test_each_visitor_only_sees_their_own(self):
        a, b = Client(), Client()
        a.post("/favourites/add/", self.ADD)
        b.post("/favourites/add/", {"type": "move", "id": "tackle", "name": "tackle"})
        self.assertContains(a.get("/favourites/"), "Pikachu")
        self.assertNotContains(a.get("/favourites/"), "Tackle")
        # one visitor cannot remove another's favourite
        b.post("/favourites/remove/", {"id": "25", "type": "pokemon"})
        self.assertEqual(Favourite.objects.filter(kind="pokemon").count(), 1)

    def test_favourites_survive_a_new_session_cookie_for_the_same_visitor(self):
        client = Client()
        client.post("/favourites/add/", self.ADD)
        self.assertContains(client.get("/favourites/"), "Pikachu")
        self.assertEqual(client.get("/").context["favourites_count"], 1)

    def test_browsing_does_not_create_a_visitor(self):
        client = Client()
        client.get("/favourites/")
        self.assertNotIn("visitor_id", client.session)
        self.assertEqual(Favourite.objects.count(), 0)

    def test_old_session_favourites_are_moved_into_the_database(self):
        client = Client()
        session = client.session
        session["favourites"] = [
            {"type": "pokemon", "id": "25", "name": "pikachu", "sprite": "s.png"}]
        session.save()
        self.assertContains(client.get("/favourites/"), "Pikachu")
        fav = Favourite.objects.get()
        self.assertEqual((fav.item_id, fav.sprite), ("25", "s.png"))
        self.assertNotIn("favourites", client.session)

    def test_the_visitor_id_lasts_a_year(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 365)


class CompactEvolutionListTests(TestCase):
    """A line with many branches (Eevee) is listed by game, not drawn."""

    @staticmethod
    def _rec(**kw):
        base = {"trigger": {"name": "level-up"}}
        base.update(kw)
        return base

    def _games(self, text, methods):
        return next((m["games"] for m in methods if m["text"] == text), [])

    def test_leafeon_uses_the_rock_before_gen_8_and_the_stone_after(self):
        from pokedex import api
        every = [vg for vg, _ in api.VERSION_GROUPS]
        methods = api._evolution_step_methods([
            self._rec(location={"name": "eterna-forest"}),
            self._rec(location={"name": "kalos-route-20"}),
            self._rec(trigger={"name": "use-item"}, item={"name": "leaf-stone"}),
        ], every)
        self.assertIn("platinum", self._games(
            "Level up near the Moss Rock (Eterna Forest)", methods))
        self.assertNotIn("x-y", self._games(
            "Level up near the Moss Rock (Eterna Forest)", methods))
        self.assertIn("x-y", self._games(
            "Level up near the Moss Rock (Kalos Route 20)", methods))
        stone = self._games("Use Leaf Stone", methods)
        self.assertIn("scarlet-violet", stone)
        self.assertNotIn("platinum", stone)

    def test_sylveon_uses_affection_in_gen_6_and_7_then_friendship(self):
        from pokedex import api
        every = [vg for vg, _ in api.VERSION_GROUPS]
        methods = api._evolution_step_methods([
            self._rec(min_affection=2, known_move_type={"name": "fairy"}),
            self._rec(min_happiness=160, known_move_type={"name": "fairy"}),
        ], every)
        affection = [m for m in methods if "affection" in m["text"]][0]
        friendship = [m for m in methods if "friendship" in m["text"]][0]
        self.assertIn("x-y", affection["games"])
        self.assertNotIn("sword-shield", affection["games"])
        self.assertIn("sword-shield", friendship["games"])
        self.assertNotIn("x-y", friendship["games"])

    def test_an_ordinary_step_works_in_every_game_it_is_in(self):
        from pokedex import api
        games = ["scarlet-violet", "sword-shield"]
        methods = api._evolution_step_methods([
            self._rec(trigger={"name": "use-item"}, item={"name": "water-stone"}),
        ], games)
        self.assertEqual(methods, [{"text": "Use Water Stone", "games": games}])

    def test_only_lines_with_many_branches_are_compact(self):
        from pokedex import api
        self.assertEqual(api._COMPACT_BRANCHES, 3)

    def test_compact_line_has_no_tree_in_the_header(self):
        from django.template.loader import render_to_string
        row = {"name": "Vaporeon", "slug": "vaporeon", "q": "vaporeon",
               "form": "vaporeon", "sprite": "", "sprite_alt": "",
               "mega": False, "games": ["scarlet-violet"],
               "methods": [{"text": "Use Water Stone",
                            "games": ["scarlet-violet"]}]}
        root = dict(row, name="Eevee", slug="eevee", q="eevee", form="eevee")
        tree = {"slug": "eevee", "q": "eevee", "form": "eevee", "name": "Eevee",
                "sprite": "", "sprite_alt": "", "gen": 1, "mega": False,
                "games": [], "branches": [], "extra_lines": [],
                "compact": True,
                "methods": {"root": root, "rows": [row]}}
        pokemon = {"id": 133, "name": "eevee", "species": "eevee",
                   "sprite": "", "types": ["normal"], "stats": [],
                   "stat_total": 0, "abilities": [], "games": [],
                   "evolution_tree": tree}
        html = render_to_string("pokedex/pokemon_result.html",
                                {"pokemon": pokemon, "game_generation": {}})
        self.assertNotIn('id="evo-tree"', html)
        self.assertIn('id="evo-methods"', html)
        self.assertIn("Use Water Stone", html)


class RegionalEvolutionItemTests(TestCase):
    def test_alolan_ninetales_uses_an_ice_stone_not_the_fire_stone(self):
        from pokedex import api
        items = api._REGIONAL_EVO_ITEMS
        self.assertEqual(items[("vulpix-alola", "ninetales-alola")], "ice-stone")
        self.assertEqual(items[("sandshrew-alola", "sandslash-alola")], "ice-stone")
        # the regional step is looked up before the chain's own (Fire Stone) one
        self.assertNotIn(("vulpix", "ninetales"), items)


class CompareFavouriteSliderTests(TestCase):
    """One slider for the whole comparison (top right) plus one per card."""
    MV = {"id": 85, "name": "thunderbolt", "type": "electric", "power": 90,
          "accuracy": 100, "pp": 15, "damage_class": "special", "priority": 0,
          "effect": "May paralyze.", "games": []}
    MV2 = dict(MV, id=53, name="flamethrower", type="fire")
    AB = {"id": 150, "name": "power-construct", "effect": "Transforms.",
          "generation": "VII", "pokemon_count": 2}
    AB2 = {"id": 22, "name": "intimidate", "effect": "Lowers Attack.",
           "generation": "III", "pokemon_count": 120}
    IT = {"id": 4, "name": "poke-ball", "category": "standard balls",
          "cost": 200, "fling_power": None, "sprite": "https://x/pb.png",
          "effect": "Catches.", "attributes": ["countable"]}
    IT2 = dict(IT, id=1, name="master-ball", cost=0)

    def _top(self, html):
        return html[html.index('id="fav-slot"'):html.index('class="search-wrapper"')]

    def test_move_compare_has_one_compare_slider_and_one_per_move(self):
        with mock.patch("pokedex.views.api.fetch_move", side_effect=[self.MV, self.MV2]):
            html = self.client.get("/compare/?mode=move&a=thunderbolt&b=flamethrower").content.decode()
        top = self._top(html)
        self.assertIn('id="compare-fav"', top)
        self.assertIn('data-id="move:thunderbolt~flamethrower"', top)
        self.assertIn('data-name="thunderbolt vs flamethrower"', top)
        self.assertNotIn("move-fav-a", top)
        self.assertEqual(html.count('id="compare-fav"'), 1)
        self.assertIn('id="move-fav-a"', html)
        self.assertIn('id="move-fav-b"', html)
        self.assertIn('data-id="85"', html)
        self.assertIn('data-id="53"', html)

    def test_ability_compare_sliders(self):
        with mock.patch("pokedex.views.api.fetch_ability", side_effect=[self.AB, self.AB2]):
            html = self.client.get("/compare/?mode=ability&a=power-construct&b=intimidate").content.decode()
        top = self._top(html)
        self.assertIn('data-id="ability:power-construct~intimidate"', top)
        self.assertNotIn("ability-fav-a", top)
        self.assertIn('id="ability-fav-a"', html)
        self.assertIn('id="ability-fav-b"', html)

    def test_item_compare_sliders(self):
        with mock.patch("pokedex.views.api.fetch_item", side_effect=[self.IT, self.IT2]):
            html = self.client.get("/compare/?mode=item&a=poke-ball&b=master-ball").content.decode()
        top = self._top(html)
        self.assertIn('data-id="item:poke-ball~master-ball"', top)
        self.assertNotIn("item-fav-a", top)
        self.assertIn('id="item-fav-a"', html)
        self.assertIn('id="item-fav-b"', html)

    def test_pokemon_compare_has_a_slider_in_each_card(self):
        def mon(i, name):
            return {"id": i, "name": name, "sprite": "http://x/%d.png" % i,
                    "types": ["electric"], "stats": [], "moves": [],
                    "games": [("scarlet-violet", "Scarlet / Violet")]}
        with mock.patch("pokedex.views.api.fetch_pokemon",
                        side_effect=[mon(25, "pikachu"), mon(26, "raichu")]):
            html = self.client.get("/compare/?mode=pokemon&a=pikachu&b=raichu").content.decode()
        self.assertEqual(html.count('id="compare-fav"'), 1)
        self.assertIn('id="cmp-a-fav"', html)
        self.assertIn('id="cmp-b-fav"', html)
        # Shiny comes first (left), the Favourite slider after it (right)
        card = html[html.index('id="cmp-a-card"'):]
        self.assertLess(card.index('id="cmp-a-shiny"'), card.index('id="cmp-a-fav"'))

    def test_compare_favourites_link_back_to_their_mode(self):
        for fid, mode, a, b in (
            ("pikachu~raichu", "pokemon", "pikachu", "raichu"),
            ("move:thunderbolt~flamethrower", "move", "thunderbolt", "flamethrower"),
            ("ability:intimidate~levitate", "ability", "intimidate", "levitate"),
            ("item:poke-ball~master-ball", "item", "poke-ball", "master-ball"),
        ):
            Favourite.objects.all().delete()
            client = Client()
            client.post("/favourites/add/", {"type": "compare", "id": fid,
                                             "name": a + " vs " + b})
            html = client.get("/favourites/").content.decode()
            self.assertIn("/compare/?mode=%s&amp;a=%s&amp;b=%s" % (mode, a, b), html)


class CompareLastSearchPerTabTests(TestCase):
    """The Compare page keeps one last search per sub-tab, on the server."""

    def _mon(self, i, name):
        return {"id": i, "name": name, "sprite": "http://x/%d.png" % i,
                "types": ["normal"], "stats": [{"name": "hp", "value": 50}]}

    def _move(self, name):
        return {"name": name, "type": "normal", "power": 40, "accuracy": 100,
                "pp": 35, "damage_class": "physical", "effect": "x"}

    def test_each_sub_tab_remembers_its_own_last_search(self):
        pa, pb = self._mon(1, "bulbasaur"), self._mon(4, "charmander")
        with mock.patch("pokedex.views.api.fetch_pokemon", side_effect=[pa, pb]):
            self.client.get("/compare/?mode=pokemon&a=bulbasaur&b=charmander")
        with mock.patch("pokedex.views.api.fetch_move",
                        side_effect=[self._move("tackle"), self._move("growl")]):
            self.client.get("/compare/?mode=move&a=tackle&b=growl")

        # Back on the Pokémon sub-tab: its last search is still there.
        html = self.client.get("/compare/").content.decode()
        self.assertIn("Bulbasaur vs Charmander", html)
        self.assertIn("mode=pokemon", html)
        # And the moves one is offered as well (shown when that tab is picked).
        self.assertIn("Tackle vs Growl", html)
        self.assertIn("mode=move", html)

    def test_only_selected_tabs_last_search_is_visible(self):
        with mock.patch("pokedex.views.api.fetch_move",
                        side_effect=[self._move("tackle"), self._move("growl")]):
            self.client.get("/compare/?mode=move&a=tackle&b=growl")
        html = self.client.get("/compare/?mode=pokemon").content.decode()
        self.assertRegex(html, r'id="last-move"\s*hidden')
        html = self.client.get("/compare/?mode=move").content.decode()
        self.assertNotRegex(html, r'id="last-move"\s*hidden')


class CompareMoveFilterButtonTests(TestCase):
    def test_pokemon_results_attach_move_filters_to_each_card(self):
        pa = {"id": 1, "name": "bulbasaur", "sprite": "http://x/1.png", "types": ["grass"], "stats": [{"name": "hp", "value": 45}]}
        pb = {"id": 4, "name": "charmander", "sprite": "http://x/4.png", "types": ["fire"], "stats": [{"name": "hp", "value": 39}]}
        with mock.patch("pokedex.views.api.fetch_pokemon", side_effect=[pa, pb]):
            html = self.client.get(
                "/compare/?mode=pokemon&a=bulbasaur&b=charmander",
                HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('id="cmp-a-move-search-wrapper"', html)
        self.assertIn('id="cmp-b-move-search-wrapper"', html)
        self.assertIn("pksFilters.attach", html)
        self.assertIn("kind: 'move'", html)


class ResumeWhereYouLeftOffTests(TestCase):
    MOVE = {"id": 1, "name": "tackle", "type": "normal", "power": 40,
            "accuracy": 100, "pp": 35, "damage_class": "physical",
            "effect": "x", "games": []}

    def _search_move(self):
        with mock.patch("pokedex.views.api.fetch_move", return_value=self.MOVE):
            self.client.get("/moves/?q=tackle")

    def test_fresh_visit_goes_back_to_last_tab_and_search(self):
        self._search_move()
        response = self.client.get("/")
        self.assertRedirects(response, "/moves/?q=tackle",
                             fetch_redirect_response=False)

    def test_clicking_pokemon_tab_on_the_site_does_not_redirect(self):
        self._search_move()
        response = self.client.get("/", HTTP_REFERER="http://testserver/moves/")
        self.assertEqual(response.status_code, 200)

    def test_nothing_remembered_means_no_redirect(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_pokemon_search_is_remembered_as_the_last_page(self):
        pk = {"id": 25, "name": "pikachu", "sprite": "http://x/25.png",
              "types": ["electric"], "stats": [{"name": "hp", "value": 35}]}
        with mock.patch("pokedex.views.api.fetch_pokemon", return_value=pk), \
             mock.patch("pokedex.views.api.fetch_cards", return_value=[]):
            self.client.get("/search/?q=pikachu&game=x-y")
        response = self.client.get("/")
        self.assertRedirects(response, "/search/?q=pikachu&game=x-y",
                             fetch_redirect_response=False)

    def test_compare_opens_on_the_tab_of_the_last_comparison(self):
        with mock.patch("pokedex.views.api.fetch_move",
                        side_effect=[self.MOVE, dict(self.MOVE, name="growl")]):
            self.client.get("/compare/?mode=move&a=tackle&b=growl")
        html = self.client.get("/compare/").content.decode()
        self.assertRegex(html, r'class="sub-tab active"\s+id="tab-move"')
        self.assertNotRegex(html, r'class="sub-tab active"\s+id="tab-pokemon"')
        # An explicit sub-tab in the address still wins.
        html = self.client.get("/compare/?mode=item").content.decode()
        self.assertRegex(html, r'class="sub-tab active"\s+id="tab-item"')


class MoveLevelColumnTests(TestCase):
    ROWS = [
        {"name": "surf", "version_group": "sword-shield", "level": 0, "method": "machine"},
        {"name": "thunderbolt", "version_group": "sword-shield", "level": 0, "method": "machine"},
        {"name": "tackle", "version_group": "sword-shield", "level": 1, "method": "level-up"},
        {"name": "growl", "version_group": "sword-shield", "level": 5, "method": "level-up"},
        {"name": "surf", "version_group": "scarlet-violet", "level": 0, "method": "machine"},
        {"name": "agility", "version_group": "scarlet-violet", "level": 0, "method": "egg"},
    ]

    def test_machine_moves_follow_level_up_and_say_tm_or_tr(self):
        from pokedex import api
        with mock.patch("pokedex.api._sword_shield_trs",
                        return_value={"surf": "TR04"}):
            out = api._finish_moves(self.ROWS)
        # level-up first, then TM, TR, Egg
        self.assertEqual([m["level_label"] for m in out],
                         ["1", "5", "TM", "TM", "TR", "Egg"])
        label = {(m["name"], m["version_group"]): m["level_label"] for m in out}
        self.assertEqual(label[("surf", "sword-shield")], "TR")
        self.assertEqual(label[("thunderbolt", "sword-shield")], "TM")
        self.assertEqual(label[("surf", "scarlet-violet")], "TM")
        self.assertEqual(label[("growl", "sword-shield")], "5")

    def test_without_tr_data_machine_moves_say_tm(self):
        from pokedex import api
        with mock.patch("pokedex.api._sword_shield_trs", return_value={}):
            out = api._finish_moves(self.ROWS)
        self.assertEqual({m["level_label"] for m in out if m["method"] == "machine"}, {"TM"})

    def test_form_moves_have_no_method_column(self):
        from pokedex import api
        with mock.patch("pokedex.api._sword_shield_trs", return_value={}), \
             mock.patch("pokedex.views.api.fetch_form_moves",
                        return_value=api._finish_moves(self.ROWS)):
            data = self.client.get("/api/form-moves/?q=pikachu").json()
        self.assertTrue(all(len(r) == 3 for r in data["moves"]))
        self.assertEqual([r[2] for r in data["moves"]],
                         ["1", "5", "TM", "TM", "TM", "Egg"])

    def test_templates_have_no_method_header(self):
        import os
        base = os.path.join(os.path.dirname(__file__), "templates", "pokedex")
        for name in ("pokemon_result.html", "_compare_pokemon_card.html"):
            html = open(os.path.join(base, name), encoding="utf-8").read()
            self.assertNotIn("<th>Method</th>", html)


class EggMirrorHerbSketchTests(TestCase):
    def test_labels_and_order(self):
        from pokedex import api
        rows = [
            {"name": "surf", "version_group": "scarlet-violet", "level": 0, "method": "machine"},
            {"name": "splash", "version_group": "scarlet-violet", "level": 0, "method": "sketch"},
            {"name": "water-spout", "version_group": "scarlet-violet", "level": 0, "method": "mirror-herb"},
            {"name": "aqua-jet", "version_group": "scarlet-violet", "level": 0, "method": "egg"},
            {"name": "tackle", "version_group": "scarlet-violet", "level": 1, "method": "level-up"},
        ]
        with mock.patch("pokedex.api._sword_shield_trs", return_value={}):
            out = api._finish_moves(rows)
        self.assertEqual([m["level_label"] for m in out],
                         ["1", "TM", "Egg", "Sketch", "Mirror Herb"])

    def test_every_kind_in_the_requested_order(self):
        from pokedex import api
        mk = lambda name, level, method, vg="sword-shield": {
            "name": name, "version_group": vg, "level": level,
            "method": method}
        rows = [
            mk("herb-move", 0, "mirror-herb"), mk("sketched", 0, "sketch"),
            mk("egg-move", 0, "egg"), mk("tr-move", 0, "machine"),
            mk("tm-move", 0, "machine"), mk("late", 40, "level-up"),
            mk("early", 3, "level-up"), mk("taught", 0, "tutor"),
            mk("max-guard", 0, "Max move"),
        ]
        with mock.patch("pokedex.api._sword_shield_trs",
                        return_value={"tr-move": "TR01"}):
            out = api._finish_moves(rows)
        self.assertEqual(
            [(m["name"], m["level_label"]) for m in out],
            [("early", "3"), ("late", "40"), ("tm-move", "TM"),
             ("tr-move", "TR"), ("egg-move", "Egg"), ("sketched", "Sketch"),
             ("herb-move", "Mirror Herb"), ("taught", "Tutor"),
             ("max-guard", "Max move")])
        # nothing but a real level-up move ever says a number
        self.assertEqual([m["level_label"] for m in out
                          if m["level_label"].isdigit()], ["3", "40"])

    def test_mirror_herb_move_replaces_its_egg_row(self):
        from pokedex import api
        rows = [{"name": "water-spout", "version_group": "scarlet-violet", "level": 0, "method": "egg"},
                {"name": "aqua-jet", "version_group": "scarlet-violet", "level": 0, "method": "egg"}]
        fake = lambda name: {"azumarill": ["water-spout", "belly-drum"]} \
            if name == api._MIRROR_HERB_FILE else {}
        with mock.patch("pokedex.api._load_extra", side_effect=fake):
            out = api._add_special_moves(rows, "azumarill", {})
        methods = {m["name"]: m["method"] for m in out}
        self.assertEqual(methods["water-spout"], "mirror-herb")
        self.assertEqual(methods["belly-drum"], "mirror-herb")
        self.assertEqual(methods["aqua-jet"], "egg")
        self.assertEqual(len(out), 3)

    def test_smeargle_gets_a_sketch_row_for_every_sketchable_move(self):
        from pokedex import api
        rows = [{"name": "sketch", "version_group": "scarlet-violet", "level": 1, "method": "level-up"},
                {"name": "tackle", "version_group": "scarlet-violet", "level": 0, "method": "machine"}]
        with mock.patch("pokedex.api._load_extra", return_value={}), \
             mock.patch("pokedex.api._sketchable_moves",
                        return_value=["tackle", "splash", "ember"]):
            out = api._add_special_moves(rows, "smeargle", {})
        sketch = sorted(m["name"] for m in out if m["method"] == "sketch")
        self.assertEqual(sketch, ["ember", "splash"])   # tackle is already learned
        with mock.patch("pokedex.api._load_extra", return_value={}):
            other = api._add_special_moves(rows, "pikachu", {})
        self.assertFalse([m for m in other if m["method"] == "sketch"])

    def test_default_sketch_list_skips_unsketchable_moves(self):
        from pokedex import api
        meta = {"tackle": {"g": 1}, "sketch": {"g": 2}, "chatter": {"g": 4},
                "max-guard": {"g": 8}, "acid-downpour--physical": {"g": 7},
                "shadow-rush": {"g": 3}, "ember": {"g": 1},
                "dazzling-gleam": {"g": 6}}
        fake = lambda name: meta if name == "filters_move.json" else {}
        with mock.patch("pokedex.api._load_extra", side_effect=fake):
            moves = api._sketchable_moves("emerald")
        self.assertEqual(sorted(moves), ["ember", "tackle"])

    def test_scraper_flags_mirror_herb_rows(self):
        from pokedex import bulbapedia
        html = """
        <h3>Scarlet and Violet</h3><h4>By breeding</h4>
        <table><tr><th>Parent</th><th>Move</th><th>PP</th></tr>
        <tr><td>Golduck</td><td>Aqua Jet</td><td>20</td></tr>
        <tr><td><img alt="Mirror Herb" src="x.png">*</td><td>Water Spout</td><td>5</td></tr>
        </table>"""
        rows = bulbapedia.parse_learnset(html, "scarlet-violet")
        self.assertIn(["aqua-jet", "scarlet-violet", 0, "egg"], rows)
        self.assertIn(["water-spout", "scarlet-violet", 0, "mirror-herb"], rows)

class MoveLevelColumnPagesTests(TestCase):
    """The Pokémon results page and the compare card show TM / TR / Egg /
    Sketch / Mirror Herb in the Level column, and the results page has no
    Method column."""
    LABELS = ["12", "TM", "TR", "Egg", "Sketch", "Mirror Herb"]

    def _moves(self):
        names = ["level-move", "tm-move", "tr-move", "egg-move",
                 "sketch-move", "herb-move"]
        return [
            {"name": n, "version_group": "scarlet-violet",
             "level": 12 if lab == "12" else 0, "method": "x",
             "level_label": lab}
            for n, lab in zip(names, self.LABELS)
        ]

    def _mon(self, name, pid):
        return {
            "id": pid, "name": name, "species": name, "display_name": name,
            "sprite": "http://x/%d.png" % pid, "types": ["water"],
            "stats": [{"name": "hp", "label": "HP", "value": 35}],
            "moves": self._moves(), "abilities": [],
            "games": [("scarlet-violet", "Scarlet / Violet")],
            "sprites_by_gen": {}, "shiny_sprites_by_gen": {},
        }

    def _cells(self, html):
        import re
        return re.findall(r'<td>([^<]*)</td>\s*<td class="mm-type">', html)

    def test_results_page_shows_labels_and_has_no_method_column(self):
        with mock.patch("pokedex.views.api.fetch_pokemon",
                        return_value=self._mon("azumarill", 184)):
            html = self.client.get("/search/?q=azumarill",
                                   HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn("<th>Method</th>", html)
        self.assertEqual(self._cells(html), self.LABELS)

    def test_compare_card_shows_labels(self):
        with mock.patch("pokedex.views.api.fetch_pokemon",
                        side_effect=[self._mon("azumarill", 184),
                                     self._mon("marill", 183)]):
            html = self.client.get(
                "/compare/?mode=pokemon&a=azumarill&b=marill",
                HTTP_HX_REQUEST="true").content.decode()
        self.assertEqual(self._cells(html), self.LABELS * 2)


class CompareDefaultGameTests(TestCase):
    """Compare starts both sides on the newest game they share; with none in
    common each side starts on the newest game it is in."""

    def _mon(self, name, pid, games):
        return {
            "id": pid, "name": name, "species": name, "display_name": name,
            "sprite": "http://x/%d.png" % pid, "types": ["water"],
            "stats": [{"name": "hp", "label": "HP", "value": 35}],
            "moves": [], "abilities": [],
            "games": [(g, g) for g in games],
            "sprites_by_gen": {g: "http://x/%s.png" % g for g in games},
            "shiny_sprites_by_gen": {},
        }

    def _active(self, a, b):
        import re
        with mock.patch("pokedex.views.api.fetch_pokemon", side_effect=[a, b]):
            html = self.client.get(
                "/compare/?mode=pokemon&a=%s&b=%s" % (a["name"], b["name"]),
                HTTP_HX_REQUEST="true").content.decode()
        return re.findall(r'game-btn active" data-vg="([^"]+)"', html)

    def test_pokemon_start_on_newest_shared_game(self):
        a = self._mon("azumarill", 184, ["scarlet-violet", "sword-shield", "x-y"])
        b = self._mon("marill", 183, ["sword-shield", "x-y", "platinum"])
        self.assertEqual(self._active(a, b), ["sword-shield", "sword-shield"])

    def test_pokemon_with_no_shared_game_start_on_their_own_newest(self):
        a = self._mon("azumarill", 184, ["scarlet-violet", "sword-shield"])
        b = self._mon("marill", 183, ["platinum", "diamond-pearl"])
        self.assertEqual(self._active(a, b), ["scarlet-violet", "platinum"])

    def test_move_ability_item_compare_use_the_shared_game_picker(self):
        import os
        base = os.path.join(os.path.dirname(__file__), "templates", "pokedex")
        html = open(os.path.join(base, "compare_result.html"),
                    encoding="utf-8").read()
        for fn in ("setCmpMoveGame", "setCmpAbilityGame", "setCmpItemGame"):
            self.assertIn("pksPickGames", html)
            self.assertIn("window.%s('a', picks.a)" % fn, html)
            self.assertIn("window.%s('b', picks.b)" % fn, html)
        self.assertEqual(html.count("pksPageMemory.pick("), 0)