"""Favourites: saved in the database, one set per anonymous visitor."""
import uuid

from .models import Favourite

VISITOR_KEY = "visitor_id"


def visitor_id(request, create=False):
    """The id of this browser's visitor ('' when it has none yet).

    It is only made when `create` is True (saving a favourite), so people who
    never save anything do not get a session.
    """
    vid = request.session.get(VISITOR_KEY, "")
    if not vid and create:
        vid = uuid.uuid4().hex
        request.session[VISITOR_KEY] = vid
    return vid


def _import_session_favourites(request, vid):
    """Favourites used to live in the session itself: move any into the
    database the first time this visitor comes back."""
    old = request.session.pop("favourites", None)
    for f in old or []:
        try:
            Favourite.objects.get_or_create(
                owner=vid, kind=f.get("type", "pokemon"), item_id=f["id"],
                defaults={"name": f.get("name", "")[:64],
                          "sprite": f.get("sprite", "")[:512]},
            )
        except (KeyError, TypeError, AttributeError):
            continue


def favourites_for(request):
    """This visitor's favourites, oldest first, as a list of dicts."""
    vid = visitor_id(request)
    if "favourites" in request.session:
        vid = visitor_id(request, create=True)
        _import_session_favourites(request, vid)
    if not vid:
        return []
    return [f.as_dict() for f in Favourite.objects.filter(owner=vid)]


def count_for(request):
    vid = visitor_id(request)
    return Favourite.objects.filter(owner=vid).count() if vid else 0
