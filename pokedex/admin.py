from django.contrib import admin

from .models import Favourite


@admin.register(Favourite)
class FavouriteAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "item_id", "created")
    list_filter = ("kind",)
    search_fields = ("name", "item_id")
