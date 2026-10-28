import json
import os
import time
from urllib.parse import urlparse

import requests

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from . import api, supplements
from .favourites import count_for, favourites_for, visitor_id
from .models import Favourite


def _build_groups(favs):
    groups = {}
    for f in favs:
        t = f.get("type", "pokemon")
        if t not in groups:
            groups[t] = []
        groups[t].append(f)
    type_order = [
        "pokemon", "move", "ability", "item", "sprite", "card", "compare"
    ]
    ordered = []
    for t in type_order:
        if t in groups:
            ordered.append((t, groups[t]))
    for t in groups:
        if t not in type_order:
            ordered.append((t, groups[t]))
    return ordered


# The cached Pokémon list is rebuilt if it is older than this, or if it has
# fewer entries than the National Dex had when this was written (a stale
# file once stopped at #1000, hiding every later Pokémon from the search).
POKEMON_CACHE_MAX_AGE = 7 * 24 * 3600
POKEMON_CACHE_MIN_ENTRIES = 1050
# Alternate forms that are searchable by name (id 10001+ in PokeAPI).
SEARCHABLE_FORM_MARKERS = (
    "-mega", "-gmax",
    # Tera forms: Terapagos (terastal / stellar) and Ogerpon's masks,
    # including its terastallized versions
    "-tera", "-stellar", "ogerpon-",
    # regional forms (Alolan Raichu, Galarian Meowth, Hisuian Zorua, Paldean
    # Wooper...)
    "-alola", "-galar", "-hisui", "-paldea",
    # gender forms (Indeedee, Basculegion, Meowstic, Oinkologne)
    "-female",
)
# Looks of those forms that are not worth a search result of their own
HIDDEN_FORM_MARKERS = ("-totem",)
# Bump this whenever the forms above change, so an old cached list (which
# would otherwise be reused for a week) is rebuilt.
POKEMON_LIST_VERSION = "forms-6"
# Other alternate forms that are searchable, by exact PokeAPI name.
SEARCHABLE_FORM_NAMES = ("necrozma-ultra", "floette-eternal")


def _fetch_pokemon_list():
    """Every base Pokémon plus Mega and Gigantamax forms, sorted by name."""
    r = requests.get(
        "https://pokeapi.co/api/v2/pokemon",
        # big enough for the whole dex plus alternate forms (ids 10001+)
        params={"limit": 3000},
        timeout=30,
    )
    r.raise_for_status()
    data = []
    for p in r.json().get("results", []):
        pid = p["url"].rstrip("/").split("/")[-1]
        if not pid.isdigit():
            continue
        # every alternate form is searchable now, except the looks that are
        # not worth a result of their own (caps, totems)
        if int(pid) >= 10000 and any(
                m in p["name"] for m in HIDDEN_FORM_MARKERS):
            continue
        data.append({
            "id": pid,
            "name": p["name"],
            "sprite": (
                "https://raw.githubusercontent.com/"
                "PokeAPI/sprites/master/sprites/pokemon/"
                + pid + ".png"
            ),
        })
    data.extend(_cosmetic_form_entries(data))
    data.sort(key=lambda x: x["name"])
    return data


def _cosmetic_form_entries(data):
    """Searchable entries for the looks that PokeAPI only has as a
    /pokemon-form/ (every Vivillon pattern, Unown letter, Furfrou trim,
    Alcremie flavour...), so each one can be found by name. They use their
    Pokémon's id and sprite; the page opens on that form."""
    try:
        r = requests.get(
            "https://pokeapi.co/api/v2/pokemon-form",
            params={"limit": 3000}, timeout=30)
        r.raise_for_status()
        forms = r.json().get("results", [])
    except Exception:
        return []
    have = {p["name"]: p for p in data}
    out = []
    for f in forms:
        name = f.get("name") or ""
        if (not name or name in have
                or any(m in name for m in HIDDEN_FORM_MARKERS)):
            continue
        # the Pokémon it is a look of: the longest known name it starts with
        parts = name.split("-")
        base = next((have["-".join(parts[:i])]
                     for i in range(len(parts) - 1, 0, -1)
                     if "-".join(parts[:i]) in have), None)
        if base is None:
            continue
        out.append({"id": base["id"], "name": name,
                    "sprite": base["sprite"]})
    return out


# Every Pokémon in the suggestions shows the 3D (Pokémon HOME) render instead
# of the small 2D sprite, like Mega Zygarde. The 2D one stays as the fallback
# (sprite_alt) for a form HOME has no render of.
HOME_SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/home/{}.png"
)


def _with_3d_sprites(data):
    """The list with the 3D sprite on every Pokémon. Done when the list is
    sent, so an older cached list gets it too."""
    out = []
    for p in data:
        if p.get("id"):
            sprite, alt = api.list_sprite(p.get("name"), p["id"], p["sprite"])
            p = dict(p, sprite=sprite, sprite_alt=alt, sprite_3d=True)
        out.append(p)
    return out


def pokemon_list(request):
    cache_path = os.path.join(
        str(settings.BASE_DIR), "pokedex", "data",
        "pokemon_list.json",
    )
    version_path = cache_path + ".version"
    cached = None
    fresh = False
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            age = time.time() - os.path.getmtime(cache_path)
            try:
                with open(version_path, "r") as f:
                    right_version = f.read().strip() == POKEMON_LIST_VERSION
            except OSError:
                right_version = False
            has_forms = right_version and any(
                m in p["name"] for p in cached
                for m in SEARCHABLE_FORM_MARKERS
            )
            fresh = (
                len(cached) >= POKEMON_CACHE_MIN_ENTRIES
                and has_forms
                and age < POKEMON_CACHE_MAX_AGE
            )
        except Exception:
            cached = None
    if fresh:
        return JsonResponse(_with_3d_sprites(cached), safe=False)

    try:
        data = _fetch_pokemon_list()
    except Exception:
        # PokeAPI unreachable: an old list beats an empty one
        return JsonResponse(_with_3d_sprites(cached or []), safe=False)

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f)
    with open(version_path, "w") as f:
        f.write(POKEMON_LIST_VERSION)

    return JsonResponse(_with_3d_sprites(data), safe=False)


def move_list(request):
    cache_path = os.path.join(
        str(settings.BASE_DIR), "pokedex", "data",
        "move_list.json",
    )
    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            data = json.load(f)
        return JsonResponse(_with_extra_moves(data), safe=False)

    moves = []
    offset = 0
    try:
        while True:
            r = requests.get(
                "https://pokeapi.co/api/v2/move",
                params={"limit": 1000, "offset": offset},
                timeout=30,
            )
            r.raise_for_status()
            results = r.json()
            batch = results.get("results", [])
            for m in batch:
                moves.append(m["name"])
            if not batch:
                break
            offset += 1000
    except Exception:
        pass

    moves.sort()

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(moves, f)

    return JsonResponse(_with_extra_moves(moves), safe=False)


def _with_extra_moves(names):
    """Add the moves PokeAPI does not have yet (supplements.EXTRA_MOVES)."""
    return sorted(set(names) | set(supplements.EXTRA_MOVES))


def _cached_list(filename, build):
    """A list of names built by build() (which may raise), cached on disk and
    refreshed monthly. If PokeAPI is down an old list beats an empty one."""
    cache_path = os.path.join(
        str(settings.BASE_DIR), "pokedex", "data", filename,
    )
    cached = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            age = time.time() - os.path.getmtime(cache_path)
            if cached and age < 30 * 24 * 3600:
                return JsonResponse(cached, safe=False)
        except Exception:
            cached = None
    try:
        names = sorted(set(build()))
    except Exception:
        return JsonResponse(cached or [], safe=False)
    if not names:
        return JsonResponse(cached or [], safe=False)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(names, f)
    return JsonResponse(names, safe=False)


def _cached_name_list(request, filename, url):
    """Every name from a paged PokeAPI list endpoint."""
    def build():
        names = []
        offset = 0
        while True:
            r = requests.get(
                url, params={"limit": 500, "offset": offset}, timeout=30,
            )
            r.raise_for_status()
            batch = r.json().get("results", [])
            if not batch:
                break
            names.extend(a["name"] for a in batch)
            offset += 500
        return names
    return _cached_list(filename, build)


def ability_list(request):
    return _cached_name_list(
        request, "ability_list.json", "https://pokeapi.co/api/v2/ability"
    )


def item_list(request):
    """Battle items only (see api.BATTLE_ITEM_ATTRIBUTES), A to Z."""
    def build():
        names = []
        for attr in api.BATTLE_ITEM_ATTRIBUTES:
            r = requests.get(
                "https://pokeapi.co/api/v2/item-attribute/" + attr,
                timeout=30,
            )
            r.raise_for_status()
            names.extend(i["name"] for i in r.json().get("items", []))
        return names
    resp = _cached_list("battle_item_list.json", build)
    # Newer items PokeAPI has not tagged as battle items are added here, on
    # every request, so an older cached list still gets them.
    try:
        names = json.loads(resp.content)
    except ValueError:
        names = []
    # Z-Crystals: show "firium-z", not PokeAPI's "firium-z--held"; the
    # "--bag" copies are left out.
    names = [api._plain_item_name(n) for n in names if "--bag" not in n]
    return JsonResponse(
        sorted(set(names) | set(api.BATTLE_ITEM_EXTRAS)), safe=False,
    )


FILTER_CACHE_MAX_AGE = 30 * 24 * 3600
FILTER_KINDS = ("pokemon", "move", "ability", "item")


def _only_listed_items(data):
    """Item filter data cut down to the items the Items search can actually
    find (item_list), so the filters never offer a category or use that none
    of the searchable items belong to. The Items search is battle items only,
    while PokeAPI's categories cover every item in the games."""
    try:
        listed = set(json.loads(item_list(None).content))
    except ValueError:
        return data
    if not listed:
        return data
    return {name: v for name, v in data.items() if name in listed}


def _tag_items(data):
    """Add "t": ["evolution"] / ["form"] to the items that are Evolution or
    Form changing items, for the Tag filter."""
    for name, entry in data.items():
        keys = api.item_tag_keys(name)
        if isinstance(entry, dict):
            if keys:
                entry["t"] = keys
            else:
                entry.pop("t", None)
    return data


def _trim(kind, data):
    # trimmed and tagged when served (not when cached), so an older cached
    # file is fixed too
    if kind == "item" and data:
        return _tag_items(_only_listed_items(data))
    return data


def filter_data(request):
    """What the filter bars next to the search boxes filter on (?kind=
    pokemon, move, ability or item): {name: {...}}, see api.build_*_filters.
    Cached on disk for a month; if PokeAPI is down an old copy beats none,
    and {} just means the page shows its search box without filters."""
    kind = request.GET.get("kind", "")
    if kind not in FILTER_KINDS:
        return JsonResponse({"error": "unknown kind"}, status=400)
    cache_path = os.path.join(
        str(settings.BASE_DIR), "pokedex", "data", "filters_%s.json" % kind,
    )
    cached = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            age = time.time() - os.path.getmtime(cache_path)
            right_version = True
            if kind == "pokemon":
                # the filters cover every name in the search list, so they
                # are rebuilt when the list's forms change
                try:
                    with open(cache_path + ".version", "r") as f:
                        right_version = (
                            f.read().strip() == POKEMON_LIST_VERSION)
                except OSError:
                    right_version = False
            if cached and age < FILTER_CACHE_MAX_AGE and right_version:
                return JsonResponse(_trim(kind, cached))
        except Exception:
            cached = None
    try:
        if kind == "pokemon":
            data = api.build_pokemon_filters(_fetch_pokemon_list())
        elif kind == "move":
            data = api.build_move_filters()
        elif kind == "ability":
            data = api.build_ability_filters()
        else:
            data = api.build_item_filters()
    except Exception:
        data = None
    if not data:
        return JsonResponse(_trim(kind, cached or {}))
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    if kind == "pokemon":
        with open(cache_path + ".version", "w") as f:
            f.write(POKEMON_LIST_VERSION)
    return JsonResponse(_trim(kind, data))


def move_meta(request):
    """Type, category, power and accuracy of up to 60 moves
    (?names=a,b,c), for the moves tables."""
    names = request.GET.get("names", "").split(",")[:60]
    return JsonResponse({"moves": api.fetch_move_meta(names)})


def form_moves(request):
    """Moves of one form as [[name, game, level or TM / TR], ...], for the
    form buttons on the Pokemon page and on the compare cards."""
    q = request.GET.get("q", "").strip()[:64]
    moves = api.fetch_form_moves(q) if q else None
    if moves is None:
        return JsonResponse({"moves": []}, status=404)
    return JsonResponse({"moves": [
        [m["name"], m["version_group"], m["level_label"]]
        for m in moves
    ]})


def _label(text):
    """\"ice-beam\" -> \"Ice Beam\" for the Last search link."""
    return " ".join(str(text).replace("-", " ").split()).title()


def _remember(request, tab, url_name, name, **params):
    """Keep the last successful search of one tab (Moves, Abilities, ...) in
    the session, so each tab can offer it again after a refresh or a return
    visit. Every tab has its own entry."""
    url = reverse("pokedex:" + url_name) + "?" + urlencode(params)
    store = request.session.get("last_searches", {})
    store[tab] = {"name": name, "url": url}
    request.session["last_searches"] = store
    # Also the very last search of the whole site, and so which tab it was
    # in: opening the site again goes back there (see home).
    request.session["last_page"] = url


def _last(request, tab):
    """The remembered last search of a tab, or None."""
    return request.session.get("last_searches", {}).get(tab)


def _arrived_from_this_site(request):
    """True when the visitor got here by clicking a link on this site."""
    ref = request.META.get("HTTP_REFERER", "")
    return bool(ref) and urlparse(ref).netloc == request.get_host()


def home(request):
    # Opening the site again (typed address, bookmark, another site) goes
    # back to the tab and search the visitor left off at, kept in the
    # session on the server. Clicking the Pokémon tab on the site itself
    # still shows the plain Pokémon search page.
    last_page = request.session.get("last_page")
    if (last_page and not request.GET and not request.htmx
            and not _arrived_from_this_site(request)):
        return redirect(last_page)
    # The last Pokémon searched for is kept in the session (server side), so
    # it can be offered again after a refresh, a lost connection or a return
    # visit.
    return render(request, "pokedex/home.html", {
        "last_search": request.session.get("last_search"),
    })


def search(request):
    q = request.GET.get("q", "").strip()[:64]
    game = request.GET.get("game", "").strip()[:64]
    context = {"q": q, "selected_game": game}
    if q:
        pokemon = api.fetch_pokemon(q)
        if pokemon:
            context["pokemon"] = pokemon
            request.session["last_search"] = {
                "q": q,
                "game": game,
                "name": pokemon.get("display_name") or pokemon.get("name") or q,
            }
            request.session["last_page"] = (
                reverse("pokedex:search")
                + "?" + urlencode({"q": q, "game": game} if game else {"q": q})
            )
            # TCG cards load on demand when their tab is opened (see
            # cards_page), which keeps the search itself quick.
            context["version_groups"] = api.VERSION_GROUPS
            context["type_chart"] = api.TYPE_CHART
        else:
            context["not_found"] = True
    if request.htmx:
        return render(
            request, "pokedex/pokemon_result.html", context
        )
    if not q:
        context["last_search"] = request.session.get("last_search")
    return render(request, "pokedex/home.html", context)


def game_info(request):
    """The Game Info tab of a Pokemon page: evolution, wild locations and
    other facts for one game. Loaded by htmx when the tab is opened or the
    game / form changes."""
    q = request.GET.get("q", "").strip()[:64]
    game = request.GET.get("game", "").strip()[:64]
    context = {"q": q}
    info = api.fetch_game_info(q, game) if q and game else None
    if info:
        # Same layout as the Pokédex data box on the Details tab: the
        # breeding facts get their own sub-heading.
        breeding = ("Egg groups", "Gender ratio", "Egg cycles")
        info = dict(info)
        facts = info.get("facts") or []
        info["facts"] = [f for f in facts if f[0] not in breeding]
        info["breeding"] = sorted(
            (f for f in facts if f[0] in breeding),
            key=lambda f: breeding.index(f[0]),
        )
        context["info"] = info
    else:
        context["not_found"] = True
    return render(request, "pokedex/game_info_result.html", context)


def cards_page(request):
    """Card search, most expensive first. Also feeds the TCG Cards tab of a
    Pokemon page (htmx, with ?limit=12)."""
    q = request.GET.get("q", "").strip()[:64]
    # No limit on the Cards page: every card is shown. Only the TCG Cards tab
    # of a Pokemon page asks for a few (?limit=12).
    try:
        limit = max(1, min(int(request.GET["limit"]), 60))
    except (KeyError, ValueError):
        limit = None
    context = {"q": q}
    if q:
        context["cards"] = api.fetch_cards(q, limit=limit)
        if not context["cards"]:
            # say why nothing came back (down vs. genuinely no cards)
            context["cards_reason"] = api.card_search_status(q)
        context["searched"] = True
        # (the TCG Cards tab of a Pokemon page calls this with ?limit=, and
        # must not replace what was last searched on the Cards tab)
        if context["cards"] and "limit" not in request.GET:
            _remember(request, "cards", "cards", _label(q), q=q)
    if request.htmx:
        return render(request, "pokedex/cards_result.html", context)
    if not q:
        context["last_search"] = _last(request, "cards")
    return render(request, "pokedex/cards.html", context)


def moves_page(request):
    q = request.GET.get("q", "").strip()[:64]
    context = {"q": q}
    if q:
        move = api.fetch_move(q)
        if move:
            context["move"] = move
            _remember(request, "moves", "moves",
                      _label(move.get("name") or q), q=q)
        else:
            context["not_found"] = True
    if request.htmx:
        return render(
            request, "pokedex/moves_result.html", context
        )
    if not q:
        context["last_search"] = _last(request, "moves")
    return render(request, "pokedex/moves.html", context)


def abilities_page(request):
    q = request.GET.get("q", "").strip()[:64]
    context = {"q": q}
    if q:
        ability = api.fetch_ability(q)
        if ability:
            try:
                api.add_ability_games(ability)
            except Exception:
                pass   # no games tabs; the full Pokémon list still shows
            context["ability"] = ability
            _remember(request, "abilities", "abilities",
                      _label(ability.get("name") or q), q=q)
        else:
            context["not_found"] = True
    if request.htmx:
        return render(
            request, "pokedex/abilities_result.html", context
        )
    if not q:
        context["last_search"] = _last(request, "abilities")
    return render(request, "pokedex/abilities.html", context)


def items_page(request):
    q = request.GET.get("q", "").strip()[:64]
    context = {"q": q}
    if q:
        item = api.fetch_item(q)
        if item and item.get("battle", True):
            # where it is found and what it costs, per game
            item = api.add_item_locations(item)
            # the art of each game (Gen 3 bag icons, Gen 5...) where it exists
            item = api.add_item_sprites(item)
            context["item"] = item
            _remember(request, "items", "items",
                      _label(item.get("name") or q), q=q)
        elif item:
            context["not_battle"] = True
        else:
            context["not_found"] = True
    if request.htmx:
        return render(request, "pokedex/items_result.html", context)
    if not q:
        context["last_search"] = _last(request, "items")
    return render(request, "pokedex/items.html", context)


def sprites_page(request):
    q = request.GET.get("q", "").strip()[:64]
    context = {"q": q}
    if q:
        sprites = api.fetch_sprites(q)
        if sprites:
            context["sprites"] = sprites
            context["pokemon_name"] = q
            context["pokemon_id"] = q
            # Minior's colours, Mimikyu's Busted form... each with its own
            # sprites ([] for a Pokemon with a single form).
            context["forms"] = api.fetch_sprite_forms(q)
            _remember(request, "sprites", "sprites", _label(q), q=q)
        else:
            context["not_found"] = True
    if request.htmx:
        return render(
            request, "pokedex/sprites_result.html", context
        )
    if not q:
        context["last_search"] = _last(request, "sprites")
    return render(request, "pokedex/sprites.html", context)


def _remember_compare(request, mode, a, b):
    # One entry per sub-tab (Pokémon, moves, abilities, items), so a
    # comparison in one does not overwrite the last search of another.
    _remember(request, "compare_" + mode, "compare",
              "%s vs %s" % (_label(a), _label(b)),
              mode=mode, a=a, b=b)
    # The sub-tab of the last comparison: Compare opens on it next time.
    request.session["compare_mode"] = mode


def compare_page(request):
    mode = request.GET.get("mode")
    if mode is None:
        # No sub-tab asked for (the Compare tab itself): the one the last
        # comparison was made in.
        mode = request.session.get("compare_mode", "pokemon")
    mode = mode[:16]
    if mode not in ("pokemon", "move", "ability", "item"):
        mode = "pokemon"
    a = request.GET.get("a", "").strip()[:64]
    b = request.GET.get("b", "").strip()[:64]
    context = {"mode": mode, "a": a, "b": b, "type_chart": api.TYPE_CHART}

    def _key(text):
        return " ".join(text.lower().replace("-", " ").split())

    if a and b and _key(a) == _key(b):
        # Same name twice ("Pikachu" / "pikachu"): nothing to compare.
        context["same"] = True
    elif a and b:
        if mode in ("ability", "item"):
            fetch = api.fetch_ability if mode == "ability" else api.fetch_item
            ra, rb = fetch(a), fetch(b)
            if mode == "item":
                ra = ra if ra and ra.get("battle", True) else None
                rb = rb if rb and rb.get("battle", True) else None
            key = "abil" if mode == "ability" else "item"
            if mode == "ability":
                # Game buttons, same as the Abilities page.
                for r in (ra, rb):
                    if not r:
                        continue
                    try:
                        api.add_ability_games(r)
                    except Exception:
                        pass   # no game buttons; the card still shows
            if ra and rb and ra.get("name") == rb.get("name"):
                context["same"] = True
            elif ra and rb:
                if mode == "item":
                    # Game buttons, same as the Items page: prices per
                    # game and each game's art.
                    try:
                        ra = api.add_item_sprites(api.add_item_locations(ra))
                        rb = api.add_item_sprites(api.add_item_locations(rb))
                    except Exception:
                        pass   # no game buttons; the cards still show
                context[key + "_a"] = ra
                context[key + "_b"] = rb
                _remember_compare(request, mode, a, b)
            else:
                context["not_found"] = True
                context["missing"] = [
                    n for n, found in ((a, ra), (b, rb)) if not found
                ]
        elif mode == "move":
            ma = api.fetch_move(a)
            mb = api.fetch_move(b)
            if ma and mb and ma.get("name") == mb.get("name"):
                context["same"] = True
            elif ma and mb:
                context["ma"] = ma
                context["mb"] = mb
                _remember_compare(request, mode, a, b)
            else:
                context["not_found"] = True
                context["missing"] = [
                    n for n, found in ((a, ma), (b, mb)) if not found
                ]
        else:
            pa = api.fetch_pokemon(a)
            pb = api.fetch_pokemon(b)
            # A Pokémon and its Mega form have different ids, so they
            # still compare; "25" vs "pikachu" is the same id and does not.
            if pa and pb and pa.get("id") == pb.get("id"):
                context["same"] = True
            elif pa and pb:
                # Start on the right game: the newest one both have (what the
                # page script used to switch to after the first paint), else
                # each one's own newest.
                in_b = {g for g, _ in pb.get("games") or []}
                shared = next(
                    (g for g, _ in api.VERSION_GROUPS
                     if g not in ("xd", "colosseum") and g in in_b
                     and g in {x for x, _ in pa.get("games") or []}),
                    None,
                )
                pa = api.with_default_game(pa, shared)
                pb = api.with_default_game(pb, shared)
                context["pa"] = pa
                context["pb"] = pb
                context["version_groups"] = api.VERSION_GROUPS
                _remember_compare(request, mode, a, b)
            else:
                context["not_found"] = True
                context["missing"] = [
                    n for n, found in ((a, pa), (b, pb)) if not found
                ]
    # Server-side last search of every sub-tab (Pokémon, moves, abilities,
    # items); the page shows the one for the tab currently selected.
    context["last_searches"] = {
        m: _last(request, "compare_" + m)
        for m in ("pokemon", "move", "ability", "item")
    }
    if request.htmx:
        return render(
            request, "pokedex/compare_result.html", context
        )
    return render(request, "pokedex/compare.html", context)


def favourites(request):
    context = {"groups": _build_groups(favourites_for(request))}
    if request.htmx:
        return render(
            request, "pokedex/favourites_list.html", context
        )
    return render(
        request, "pokedex/favourites.html", context
    )


@require_POST
def add_favourite(request):
    fav_type = request.POST.get("type", "pokemon")[:32]
    fid = request.POST.get("id", "")[:128]
    name = request.POST.get("name", "")[:64]
    sprite = request.POST.get("sprite", "")[:512]
    if fid and name:
        Favourite.objects.get_or_create(
            owner=visitor_id(request, create=True),
            kind=fav_type, item_id=fid,
            defaults={"name": name, "sprite": sprite},
        )
    resp = HttpResponse("")
    resp["X-Fav-Count"] = count_for(request)
    return resp


@require_POST
def remove_favourite(request):
    fid = request.POST.get("id", "")[:128]
    fav_type = request.POST.get("type", "pokemon")[:32]
    vid = visitor_id(request)
    if vid:
        Favourite.objects.filter(
            owner=vid, kind=fav_type, item_id=fid).delete()
    favs = favourites_for(request)
    resp = render(
        request, "pokedex/favourites_list.html",
        {"groups": _build_groups(favs)},
    )
    resp["X-Fav-Count"] = len(favs)
    return resp