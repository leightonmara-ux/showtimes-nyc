"""Best-of lists (NYT 100 of the 21st century, Rotten Tomatoes top 300, IMDb Top 250) from lists.json.

index() -> {imdb_id: [{"list": "nyt100", "short": "NYT 100", "rank": 12, "url": ...}, ...]}
Films are matched by IMDb id, the same id ratings.py resolves for each listing.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
LISTS = ROOT / "lists.json"
ORDER = ["nyt100", "rt300", "imdb250"]


def index():
    try:
        data = json.loads(LISTS.read_text())["lists"]
    except (OSError, ValueError, KeyError):
        return {}
    out = {}
    for key in sorted(data, key=lambda k: ORDER.index(k) if k in ORDER else 99):
        lst = data[key]
        for f in lst.get("films", []):
            if f.get("imdb_id"):
                out.setdefault(f["imdb_id"], []).append(
                    {"list": key, "short": lst.get("short", key), "name": lst.get("name", key),
                     "rank": f.get("rank"), "url": lst.get("url")})
    return out
