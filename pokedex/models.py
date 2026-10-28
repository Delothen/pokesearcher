from django.db import models


class Favourite(models.Model):
    """One thing a visitor saved: a Pokémon, move, ability, item, sprite,
    card or comparison.

    There are no accounts. A visitor is an anonymous id kept in their
    session, which lasts a year (see SESSION_COOKIE_AGE), so favourites are
    stored in the database on the server and come back on the same browser.
    """

    owner = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=32, default="pokemon")
    item_id = models.CharField(max_length=128)
    name = models.CharField(max_length=64)
    sprite = models.CharField(max_length=512, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "kind", "item_id"],
                name="one_favourite_per_visitor_and_item",
            ),
        ]

    def __str__(self):
        return "%s: %s" % (self.kind, self.name)

    def as_dict(self):
        """The shape the favourites templates read."""
        entry = {"type": self.kind, "id": self.item_id, "name": self.name}
        if self.sprite:
            entry["sprite"] = self.sprite
        return entry
