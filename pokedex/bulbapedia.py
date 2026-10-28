"""Bulbapedia reader behind `manage.py load_extras`.

PokeAPI is missing Pokédex entries and move lists for many Pokémon in
Scarlet / Violet and Legends: Z-A. These helpers read them from Bulbapedia
through its MediaWiki API (action=parse returns a page's rendered HTML).
Nothing here runs while the site is being used: the command writes
data/extra_dex_entries.json and data/extra_learnsets.json, and api.py merges
those over PokeAPI's data.

Being polite: one request at a time with a delay, a User-Agent that says what
this is, and every page cached on disk so a re-run does not download it
again. Bulbapedia's text is CC BY-NC-SA, so credit it if you show the data.
"""
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:           # only the scraper needs it
    BeautifulSoup = None

USER_AGENT = "PokedexProject/1.0 (student project; polite MediaWiki API client)"
BULBA_API = "https://bulbapedia.bulbagarden.net/w/api.php"

# How each game is named in Bulbapedia's section headings and row labels.
GAME_LABEL = {
    "legends-za": re.compile(r"legends\W*z\W*a", re.I),
    "scarlet-violet": re.compile(r"scarlet|violet", re.I),
}


def page_url(title):
    """The API URL that returns the rendered HTML of a Bulbapedia page."""
    return BULBA_API + "?" + urlencode({
        "action": "parse", "page": title, "prop": "text",
        "format": "json", "formatversion": "2", "redirects": "1",
    })


def pokemon_title(english_name):
    """"Pikachu" -> "Pikachu (Pokémon)" (Bulbapedia's page title)."""
    return "%s (Pokémon)" % english_name


class Fetcher:
    """GET with a disk cache and a delay between real requests."""

    def __init__(self, cache_dir, delay=1.5, timeout=30):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        self.failed = False     # True when the last request itself failed

    def page_html(self, title):
        """Rendered HTML of a Bulbapedia page, or None when the page does
        not exist or the request failed."""
        self.failed = False
        url = page_url(title)
        path = self.cache / (hashlib.sha1(url.encode()).hexdigest() + ".html")
        if path.exists():
            return path.read_text(encoding="utf-8") or None
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            r = requests.get(
                url, timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            self.failed = True
            return None                  # not cached: worth another try
        if "error" in data:              # e.g. missingtitle
            path.write_text("", encoding="utf-8")
            return None
        html = (data.get("parse") or {}).get("text") or ""
        path.write_text(html, encoding="utf-8")
        return html or None


def _soup(html):
    if BeautifulSoup is None:
        raise RuntimeError(
            "The scraper needs BeautifulSoup: pip install beautifulsoup4"
        )
    return BeautifulSoup(html, "html.parser")


def _clean(text):
    return " ".join((text or "").replace("\u00ad", "").split())


def _heading_text(el):
    # MediaWiki headings contain "[edit]" links; the title is in the
    # .mw-headline span when there is one.
    span = el.find(class_="mw-headline")
    return _clean((span or el).get_text(" "))


def _own_cells(tr):
    """The th/td cells of this row only (not those of tables nested in it)."""
    return [c for c in tr.find_all(["th", "td"]) if c.find_parent("tr") is tr]


def parse_dex_entries(html, game):
    """[{"version", "text"}] for one game from a Pokémon's page.

    Reads the table rows under the "Pokédex entries" heading: a header cell
    naming the game ("Scarlet", "Legends: Z-A"...) and a cell with the entry
    text. Bulbapedia puts that heading at h3 level (under "Game data"), so
    h2 and h3 headings both open/close the section. Each version is kept
    once: later rows with the same label belong to alternate forms (e.g.
    Partner Pikachu), not to the Pokémon itself. If the page has no such
    heading the whole page is searched instead.
    """
    soup = _soup(html)
    pattern = GAME_LABEL[game]

    def is_dex_heading(h):
        t = _heading_text(h).lower()
        return "pokédex entries" in t or "pokedex entries" in t

    has_section = any(is_dex_heading(h) for h in soup.find_all(["h2", "h3"]))
    in_section = not has_section
    entries, seen_text, seen_label = [], set(), set()
    for el in soup.find_all(["h2", "h3", "tr"]):
        if el.name in ("h2", "h3"):
            if has_section:
                in_section = is_dex_heading(el)
            continue
        if not in_section:
            continue
        if el.find("tr"):               # wrapper row holding a nested table
            continue
        th, td = el.find("th"), el.find("td")
        if not th or not td:
            continue
        label = _clean(th.get_text(" "))
        text = _clean(td.get_text(" "))
        if not pattern.search(label) or len(text) < 20:
            continue
        if label in seen_label or text in seen_text:
            continue
        seen_label.add(label)
        seen_text.add(text)
        entries.append({"version": label, "text": text})
    return entries


_METHODS = (
    # heading text -> (move method, level is meaningful)
    (re.compile(r"evolution", re.I), ("level-up", False)),
    (re.compile(r"level", re.I), ("level-up", True)),
    (re.compile(r"\btm\b|\btr\b|technical|machine", re.I), ("machine", False)),
    (re.compile(r"breed|egg", re.I), ("egg", False)),
    (re.compile(r"tutor|remind", re.I), ("tutor", False)),
)


def _move_slug(name):
    slug = re.sub(r"[’']", "", name.strip().lower())
    return "-".join(re.sub(r"[^a-z0-9]+", " ", slug).split())


def _table_game(table, header):
    """Which game a learnset table belongs to when no heading says so.

    Bulbapedia's current layout labels learnset tables only "Generation IX";
    the game shows in the columns. Legends: Z-A has no PP / Accuracy but a
    "Learn / Plus" pair (levels) or a "CD" (cooldown) column; Scarlet /
    Violet keep the classic PP column.
    """
    outer = table.find_parent("table")
    label = ""
    while outer is not None:
        th = outer.find("th")
        label = _clean(th.get_text(" ")) if th else ""
        if label.lower().startswith("generation"):
            break
        outer = outer.find_parent("table")
    if not re.match(r"generation\s+ix\b", label, re.I):
        return None
    if "plus" in header or "cd" in header:
        return "legends-za"
    if "pp" in header:
        return "scarlet-violet"
    return None


def parse_learnset(html, game):
    """[[move, game, level, method], ...] for one game from a Pokémon's
    learnset section (or its "Generation IX learnset" page). When the page
    has a learnset per form (Qwilfish / Hisuian Qwilfish), these are the
    rows of the first (default) form; parse_learnset_forms has them all.
    """
    forms = parse_learnset_forms(html, game)
    if not forms:
        return []
    return next(iter(forms.values()))


def parse_learnset_forms(html, game):
    """{form heading or None: [[move, game, level, method], ...]} for one
    game, in page order. A page with a learnset per form puts each form's
    tables under its own heading ("Qwilfish", "Hisuian Qwilfish") below the
    "By leveling up" / "By TM" heading; those rows are filed per heading.

    Walks the headings: one naming the game switches to it, and one such as
    "By leveling up" / "By TM" / "By breeding" sets the method. Rows are read
    from the tables in between, using the header row that says "Move" (and
    "Level" / "Learn") to find the columns. When no heading names the game,
    each Generation IX table is assigned to a game from its columns.
    """
    pattern = GAME_LABEL[game]
    by_form, seen = {}, set()
    heading_game, method = None, None
    method_level, form = 0, None
    for el in _soup(html).find_all(["h2", "h3", "h4", "h5", "table"]):
        if el.name != "table":
            title = _heading_text(el)
            level = int(el.name[1])
            if (method and level > method_level
                    and not re.match(r"generation\b|pok[eé]mon\b", title, re.I)
                    and not title.lower().startswith("by")
                    and not pattern.search(title)):
                # a form's heading below "By leveling up" and the like
                form = title
                continue
            form = None
            if re.match(r"generation\b", title, re.I):
                heading_game, method = None, None
            elif pattern.search(title):
                heading_game, method = game, None
            elif re.match(r"pok[eé]mon\b", title, re.I):
                heading_game, method = None, None   # a different game
            elif title.lower().startswith("by"):
                method = None
                method_level = level
                for pat, found in _METHODS:
                    if pat.search(title):
                        method = found
                        break
            else:
                method = None       # e.g. "TCG-only moves", "Side game data"
            continue
        if not method:
            continue
        kind, has_level = method
        # only this table's own rows: wrapper tables contain the real ones
        trs = [tr for tr in el.find_all("tr") if tr.find_parent("table") is el]
        head_i = col_name = col_level = None
        header = []
        for i, tr in enumerate(trs):
            cells = [_clean(c.get_text(" ")).lower() for c in _own_cells(tr)]
            if "move" in cells or "name" in cells:
                head_i, header = i, cells
                col_name = cells.index("move" if "move" in cells else "name")
                col_level = next(
                    (j for j, h in enumerate(cells)
                     if h in ("level", "lv", "lv.", "learn")), None)
                break
        if head_i is None:
            continue
        table_game = heading_game or _table_game(el, header)
        if table_game != game:
            continue
        for tr in trs[head_i + 1:]:
            raw = _own_cells(tr)
            cells = [_clean(c.get_text(" ")) for c in raw]
            if len(cells) <= col_name or not cells[col_name]:
                continue
            move = _move_slug(cells[col_name])
            row_kind = kind
            # In Scarlet / Violet an egg-move row marked with the Mirror Herb
            # icon can only be copied from another Pokémon holding that herb.
            if kind == "egg" and any(
                    "mirror herb" in str(c).lower() for c in raw[:col_name]):
                row_kind = "mirror-herb"
            level = 0
            if has_level and col_level is not None and col_level < len(cells):
                digits = re.search(r"\d+", cells[col_level])
                level = int(digits.group()) if digits else 0
            key = (form, move, row_kind, level)
            if move and key not in seen:
                seen.add(key)
                by_form.setdefault(form, []).append(
                    [move, game, level, row_kind])
    return by_form


def scrape_page(fetcher, title, game, dex=True, moves=True):
    """{"dex": [...], "moves": [...]} for one game from a Pokémon's
    Bulbapedia page, or None when the page could not be loaded. A learnset
    missing from the main page is looked for on its "Generation IX
    learnset" subpage."""
    html = fetcher.page_html(title)
    if not html:
        return None
    out = {"dex": [], "moves": [], "forms": {}}
    if dex:
        out["dex"] = parse_dex_entries(html, game)
    if moves:
        forms = parse_learnset_forms(html, game)
        if not forms:
            sub = fetcher.page_html(title + "/Generation IX learnset")
            if sub:
                forms = parse_learnset_forms(sub, game)
        out["moves"] = next(iter(forms.values())) if forms else []
        # a page with a learnset per form: every form's rows by heading
        out["forms"] = {k: v for k, v in forms.items() if k}
    return out

# ---- item pages: price and where to find an item ----------------------------
# Bulbapedia's item pages have a "Price" table (cost / sell price per group
# of games) and an "Acquisition" table (finite and repeatable ways to get it
# per group of games), both under "In the core series games". The games of a
# row are only shown as coloured logos, so they are read from the link titles.
ITEM_GAME_PATTERNS = (
    (re.compile(r"red and (green|blue)|red, green", re.I), "red-blue"),
    (re.compile(r"yellow", re.I), "yellow"),
    (re.compile(r"gold and silver", re.I), "gold-silver"),
    (re.compile(r"crystal", re.I), "crystal"),
    (re.compile(r"ruby and sapphire", re.I), "ruby-sapphire"),
    (re.compile(r"emerald", re.I), "emerald"),
    (re.compile(r"firered", re.I), "firered-leafgreen"),
    (re.compile(r"diamond and pearl", re.I), "diamond-pearl"),
    (re.compile(r"platinum", re.I), "platinum"),
    (re.compile(r"heartgold", re.I), "heartgold-soulsilver"),
    (re.compile(r"black and white versions 2", re.I), "black-2-white-2"),
    (re.compile(r"black and white", re.I), "black-white"),
    (re.compile(r"omega ruby", re.I), "omega-ruby-alpha-sapphire"),
    (re.compile(r"\bx and y\b", re.I), "x-y"),
    (re.compile(r"ultra sun", re.I), "ultra-sun-ultra-moon"),
    (re.compile(r"sun and moon", re.I), "sun-moon"),
    (re.compile(r"let.s go", re.I), "lets-go-pikachu-lets-go-eevee"),
    (re.compile(r"sword and shield|isle of armor|crown tundra", re.I),
     "sword-shield"),
    (re.compile(r"brilliant diamond", re.I),
     "brilliant-diamond-shining-pearl"),
    (re.compile(r"legends\W+arceus", re.I), "legends-arceus"),
    (re.compile(r"scarlet and violet|teal mask|indigo disk", re.I),
     "scarlet-violet"),
    (re.compile(r"legends\W+z\W*a|mega dimension", re.I), "legends-za"),
    (re.compile(r"champions", re.I), "champions"),
)


def item_title(english_name):
    """\"Leftovers\" -> \"Leftovers\" (items share their name's page title;
    redirects are followed by the API)."""
    return english_name.strip()


def _title_game(title):
    for pattern, key in ITEM_GAME_PATTERNS:
        if pattern.search(title or ""):
            return key
    return None


def _row_games(td):
    """Game keys named by the logo links in a \"Games\" cell, newest first
    order not guaranteed; duplicates removed."""
    keys = []
    for a in td.find_all("a"):
        key = _title_game(a.get("title", ""))
        if key and key not in keys:
            keys.append(key)
    return keys


def _cell_lines(td):
    """The text of a cell, one entry per line (<br>), with the small game
    tags Bulbapedia puts in superscript shown as \"(RS only)\"."""
    td = BeautifulSoup(str(td), "html.parser")
    for sup in td.find_all("sup"):
        tag = _clean(sup.get_text(""))
        sup.replace_with(" (%s only)" % tag if tag else "")
    for br in td.find_all(["br", "hr"]):
        br.replace_with("\n")
    lines = []
    for raw in td.get_text().split("\n"):
        line = _clean(raw)
        line = re.sub(r"\s+([,.;:)])", r"\1", line)
        line = re.sub(r"\(\s+", "(", line)
        if line:
            lines.append(line)
    return lines


def _money(td):
    text = " ".join(_cell_lines(td))
    text = re.sub(r"^\$\s*", "₽", text)
    return text


def _item_tables(soup, section):
    """The data tables under the h3 called `section`, inside the \"In the
    core series games\" h2 only. Stops at the h4 \"Distribution\" (events)."""
    in_core = False
    current = None
    for el in soup.find_all(["h2", "h3", "h4", "table"]):
        if el.name == "h2":
            in_core = _heading_text(el).lower().startswith(
                "in the core series")
            current = None
            continue
        if el.name == "h3":
            current = _heading_text(el).lower()
            continue
        if el.name == "h4":
            if _heading_text(el).lower().startswith("distribution"):
                current = None
            continue
        if in_core and current and current.startswith(section.lower()):
            trs = [tr for tr in el.find_all("tr")
                   if tr.find_parent("table") is el]
            if trs and _clean(trs[0].get_text(" ")).lower().startswith(
                    "games"):
                yield [(_own_cells(tr)) for tr in trs]


def parse_item_page(html):
    """{\"prices\": [{\"games\", \"cost\", \"sell\"}], \"acquisition\":
    [{\"games\", \"finite\", \"repeatable\"}]} from an item's Bulbapedia page.
    \"games\" are this project's game keys; rows for games the project does
    not have (Colosseum, Stadium...) are dropped."""
    soup = _soup(html)
    prices, acquisition = [], []
    for rows in _item_tables(soup, "Price"):
        for cells in rows[1:]:
            if len(cells) < 2:
                continue
            games = _row_games(cells[0])
            if not games:
                continue
            prices.append({
                "games": games,
                "cost": _money(cells[1]),
                "sell": _money(cells[2]) if len(cells) > 2 else "",
            })
    for rows in _item_tables(soup, "Acquisition"):
        for cells in rows[1:]:
            if len(cells) < 2:
                continue
            games = _row_games(cells[0])
            if not games:
                continue
            acquisition.append({
                "games": games,
                "finite": _cell_lines(cells[1]),
                "repeatable": _cell_lines(cells[2]) if len(cells) > 2 else [],
            })
    return {"prices": prices, "acquisition": acquisition}


def scrape_item(fetcher, english_name):
    """parse_item_page() for an item, or None when the page could not be
    loaded (check fetcher.failed to tell a network error from no page)."""
    html = fetcher.page_html(item_title(english_name))
    if not html:
        return None
    return parse_item_page(html)


# ---- TM / HM / TR pages: where each machine is found and what it costs -----
# A machine's page (TM024, HM01, TR00...) has, per generation, one table per
# move the machine teaches: a small heading table naming the move, then a
# table of Location / Purchase price / Sell price rows, one per group of
# games. The move matters because a TM number teaches different moves in
# different games (TM024 is Thunderbolt in most, Fire Spin in Scarlet / Violet).
_UNIT_NAMES = {"C": "Coins", "PC": "Poké Coupons", "LP": "LP", "BP": "BP"}
_MOVE_LINK = re.compile(r"\(move\)\s*$", re.I)


def tm_title(label):
    """\"TM24\" -> \"TM024\", \"HM1\" -> \"HM01\" (Bulbapedia's page
    names: three digits for TMs since Generation IX, two for HMs / TRs).
    None when the label is not a machine."""
    m = re.match(r"(TM|HM|TR)\s*0*(\d+)$", (label or "").strip(), re.I)
    if not m:
        return None
    kind, number = m.group(1).upper(), int(m.group(2))
    return "%s%0*d" % (kind, 3 if kind == "TM" else 2, number)


def _price_lines(td):
    """A price cell as readable lines: \"$3,000\" -> \"₽3,000\", \"4000C\"
    -> \"4,000 Coins\"."""
    td = BeautifulSoup(str(td), "html.parser")
    for a in td.find_all("a"):
        unit = _UNIT_NAMES.get(_clean(a.get_text("")))
        if unit:
            a.replace_with(" " + unit)
    lines = []
    for line in _cell_lines(td):
        line = re.sub(r"^\$\s*", "₽", line)
        line = re.sub(
            r"(?<![\d,.])(\d{4,})(?![\d,])",
            lambda m: "{:,}".format(int(m.group(1))), line)
        lines.append(line)
    return lines


def _slug_alnum(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def parse_tm_page(html):
    """[{\"move\", \"games\", \"where\", \"cost\", \"sell\"}] from a machine's
    page, core series games only. \"games\" are this project's game keys,
    \"where\" is one line per location note, \"cost\" / \"sell\" are text
    (\"N/A\" when it cannot be bought / sold)."""
    soup = _soup(html)
    rows_out = []
    in_core = False
    move = ""
    for el in soup.find_all(["h2", "table"]):
        if el.name == "h2":
            in_core = _heading_text(el).lower().startswith(
                "in the core series")
            move = ""
            continue
        if not in_core:
            continue
        trs = [tr for tr in el.find_all("tr") if tr.find_parent("table") is el]
        header = None
        for i, tr in enumerate(trs):
            cells = [_clean(c.get_text(" ")).lower() for c in _own_cells(tr)]
            if "location" in cells and "purchase price" in cells:
                header = i
                break
        if header is None:
            # a heading table naming the move this machine teaches
            for a in el.find_all("a"):
                if _MOVE_LINK.search(a.get("title", "")):
                    move = _clean(a.get_text(" "))
                    break
            continue
        for tr in trs[header + 1:]:
            cells = _own_cells(tr)
            if len(cells) < 3:
                continue
            games = _row_games(cells[0])
            if not games:
                continue
            cost = ", ".join(_price_lines(cells[2]))
            rows_out.append({
                "move": move,
                "games": games,
                "where": _cell_lines(cells[1]),
                "cost": cost,
                "sell": ", ".join(_price_lines(cells[3]))
                if len(cells) > 3 else "",
            })
    return rows_out


def pick_tm_row(rows, game, move_name):
    """The row of parse_tm_page() output for one game and move: the table
    whose heading names the move, else the game's only row."""
    mine = [r for r in rows if game in r["games"]]
    want = _slug_alnum(move_name)
    for r in mine:
        if want and _slug_alnum(r["move"]) == want:
            return r
    return mine[0] if len(mine) == 1 else None


def scrape_tm(fetcher, label):
    """parse_tm_page() for a machine label, or None when the page could not
    be loaded (check fetcher.failed to tell a network error from no page)."""
    title = tm_title(label)
    if not title:
        return None
    html = fetcher.page_html(title)
    if not html:
        return None
    return parse_tm_page(html)