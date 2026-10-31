from dataclasses import dataclass, asdict
import html
import math
import re
from urllib.parse import urlsplit, urlunsplit


def source_url(value, recipe=True):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("Invalid source URL")
    u = urlsplit(value)
    if u.scheme != "https" or u.hostname not in ("www.bbcgoodfood.com", "bbcgoodfood.com") or u.username or u.password or u.port not in (None, 443):
        raise ValueError("Only HTTPS bbcgoodfood.com URLs are accepted")
    if any(x in u.path.lower() for x in ("..", "%", "\\")) or not re.fullmatch(r"/[a-zA-Z0-9/_.-]*", u.path):
        raise ValueError("Invalid source path")
    if recipe and not re.fullmatch(r"/recipes/[a-z0-9-]+/?", u.path):
        raise ValueError("Expected a direct public /recipes/<slug> URL")
    return urlunsplit(("https", "www.bbcgoodfood.com", u.path.rstrip("/") or "/", "", ""))


def text(value, limit=300):
    if not isinstance(value, str):
        return ""
    # Strings are displayed as plain text, never interpreted as instructions or markup.
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())[:limit]


@dataclass(frozen=True)
class Recipe:
    title: str
    url: str
    description: str = ""
    prep: str = ""
    cook: str = ""
    total: str = ""
    difficulty: str = ""
    rating: float | None = None
    rating_count: int | None = None
    image_url: str = ""
    image: str = ""
    credit: str = ""
    category: str = "trending"
    demonstration: bool = False

    def asdict(self):
        return asdict(self)

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("Recipe must be an object")
        title = text(raw.get("title"), 600)
        if not title:
            raise ValueError("Recipe title required")
        if isinstance(raw.get("credit"), str) and len(raw["credit"]) > 140:
            raise ValueError("Image credit must be at most 140 characters; never silently truncate attribution")
        rating = raw.get("rating")
        if rating is not None:
            try:
                rating = float(rating)
                if not math.isfinite(rating) or not 0 <= rating <= 5:
                    rating = None
            except (ValueError, TypeError):
                rating = None
        count = raw.get("rating_count")
        count = count if type(count) is int and 0 < count < 10000000 else None
        if count is None:
            rating = None  # Zero reviews is not a real zero-star rating.
        category = raw.get("category", "trending")
        if category not in ("trending", "new", "seasonal"):
            raise ValueError("category must be trending, new or seasonal")
        return cls(title, source_url(raw.get("url")),
                   **{k: text(raw.get(k), n) for k, n in (("description", 280), ("prep", 50), ("cook", 50), ("total", 50), ("difficulty", 40), ("credit", 140))},
                   rating=rating, rating_count=count,
                   image_url=text(raw.get("image_url"), 1000), image=text(raw.get("image"), 2000),
                   category=category, demonstration=raw.get("demonstration") is True)


def duration(value):
    if not isinstance(value, str):
        return ""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not m or not any(m.groups()):
        return ""
    d, h, mins, s = [int(x or 0) for x in m.groups()]
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{mins}m" if mins else "", f"{s}s" if s else ""]
    return " ".join(x for x in parts if x)
