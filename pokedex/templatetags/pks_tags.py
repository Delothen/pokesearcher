from django import template

from pokedex import api

register = template.Library()


@register.filter
def split_before(value, char):
    parts = value.split(char, 1)
    return parts[0]


@register.filter
def split_after(value, char):
    parts = value.split(char, 1)
    if len(parts) > 1:
        return parts[1]
    return ''


@register.filter
def vg_label(value):
    return dict(api.VERSION_GROUPS).get(value, value)

@register.filter
def before(value, sep):
    parts = (value or "").split(sep, 1)
    return parts[0]


def _compare_parts(value):
    """(mode, a, b) of a comparison favourite id.

    Pokémon comparisons are "a~b"; the others are "move:a~b",
    "ability:a~b" and "item:a~b".
    """
    value = value or ""
    mode = "pokemon"
    for m in ("move", "ability", "item"):
        if value.startswith(m + ":"):
            mode = m
            value = value[len(m) + 1:]
            break
    a, _, b = value.partition("~")
    return mode, a, b


@register.filter
def cmp_mode(value):
    return _compare_parts(value)[0]


@register.filter
def cmp_a(value):
    return _compare_parts(value)[1]


@register.filter
def cmp_b(value):
    return _compare_parts(value)[2]


@register.filter
def fav_game(value):
    """Game key of a Pokémon favourite id ("name~game|s|f"), or ""."""
    game = split_after(value or "", "~").split("|", 1)[0]
    return "" if game == "default" else game


@register.filter
def fav_tags(value):
    """Shiny / female markers of a Pokémon favourite id, as labels."""
    flags = split_after(value or "", "~").split("|")[1:]
    labels = {"s": "✨ Shiny", "f": "♀ Female"}
    return [labels[f] for f in flags if f in labels]