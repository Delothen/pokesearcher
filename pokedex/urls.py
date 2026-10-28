from django.urls import path
from . import views

app_name = "pokedex"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("cards/", views.cards_page, name="cards"),
    path("moves/", views.moves_page, name="moves"),
    path("abilities/", views.abilities_page, name="abilities"),
    path("items/", views.items_page, name="items"),
    path("sprites/", views.sprites_page, name="sprites"),
    path("compare/", views.compare_page, name="compare"),
    path("favourites/", views.favourites, name="favourites"),
    path(
        "favourites/add/",
        views.add_favourite,
        name="add_favourite",
    ),
    path(
        "favourites/remove/",
        views.remove_favourite,
        name="remove_favourite",
    ),
    path(
        "api/pokemon-list/",
        views.pokemon_list,
        name="pokemon_list",
    ),
    path(
        "api/move-list/",
        views.move_list,
        name="move_list",
    ),
    path(
        "api/ability-list/",
        views.ability_list,
        name="ability_list",
    ),
    path(
        "api/item-list/",
        views.item_list,
        name="item_list",
    ),
    path(
        "api/filter-data/",
        views.filter_data,
        name="filter_data",
    ),
    path(
        "api/move-meta/",
        views.move_meta,
        name="move_meta",
    ),
    path(
        "api/form-moves/",
        views.form_moves,
        name="form_moves",
    ),
    path(
        "game-info/",
        views.game_info,
        name="game_info",
    ),
]