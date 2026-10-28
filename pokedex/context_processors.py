from .favourites import favourites_for


def favourites_count(request):
    favs = favourites_for(request)
    keys = [f"{f['type']}:{f['id']}" for f in favs]
    return {"favourites_count": len(favs), "favourite_keys": keys}
