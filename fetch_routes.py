#!/usr/bin/env python3
"""Fetch hiking routes from vpohid.com.ua via tiled bbox queries.

Recursively splits any tile that returns the API cap (100 routes) until each
tile is below the cap or hits a min-size guard. Writes routes.json.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from math import radians, sin, cos, asin, sqrt
from pathlib import Path

API = "https://vpohid.com.ua/json/routes/"

# Ukraine + a buffer (covers Carpathians, Crimea, etc.)
START_BBOX = {
    "south": 43.0,
    "north": 53.0,
    "west": 21.0,
    "east": 41.0,
}

API_CAP = 100
MIN_SPAN = 0.25  # degrees, smallest tile we'll split to
MAX_POINTS_PER_ROUTE = 500
TIMEOUT = 30
DELAY = 0.05  # seconds between requests, be polite

OUT = Path(__file__).parent / "routes.json"
TRACKS_FILE = Path(__file__).parent / "tracks.json"
CROSS_THRESHOLD_KM = 0.5  # within this distance = "crossed"


def fetch(bbox):
    params = [
        ("limit", 500),
        ("zoom", 10),
        ("disableextrainfo", "y"),
        ("includepoints", "y"),
        ("bounds[boundNorthEastLat]", bbox["north"]),
        ("bounds[boundSouthWestLat]", bbox["south"]),
        ("bounds[boundNorthEastLng]", bbox["east"]),
        ("bounds[boundSouthWestLng]", bbox["west"]),
    ]
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (my-trails-fetcher)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    if data.get("status") != "ok":
        raise RuntimeError(f"api error: {data.get('message')}")
    return data["response"]["routes"]["list"]


def split_bbox(b):
    midlat = (b["south"] + b["north"]) / 2
    midlng = (b["west"] + b["east"]) / 2
    return [
        {"south": b["south"], "north": midlat, "west": b["west"], "east": midlng},
        {"south": b["south"], "north": midlat, "west": midlng, "east": b["east"]},
        {"south": midlat, "north": b["north"], "west": b["west"], "east": midlng},
        {"south": midlat, "north": b["north"], "west": midlng, "east": b["east"]},
    ]


def crawl():
    seen = {}
    queue = [START_BBOX]
    requests_made = 0
    while queue:
        b = queue.pop()
        try:
            routes = fetch(b)
        except Exception as e:
            print(f"  ! fetch failed for {b}: {e}", file=sys.stderr)
            time.sleep(0.5)
            continue
        requests_made += 1
        new = sum(1 for r in routes if r["id"] not in seen)
        for r in routes:
            seen[r["id"]] = r
        span = max(b["north"] - b["south"], b["east"] - b["west"])
        print(f"[{requests_made:3d}] bbox span={span:.2f}  got={len(routes)}  new={new}  total={len(seen)}")
        if len(routes) >= API_CAP and span > MIN_SPAN:
            queue.extend(split_bbox(b))
        time.sleep(DELAY)
    return list(seen.values())


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(h))


def decimate(points, max_n):
    if len(points) <= max_n:
        return points
    step = len(points) / max_n
    return [points[int(i * step)] for i in range(max_n)] + [points[-1]]


def normalize(routes):
    out = []
    for r in routes:
        try:
            pts = [[float(p[0]), float(p[1])] for p in r.get("points", []) if p]
        except (ValueError, TypeError, IndexError):
            pts = []
        if not pts:
            continue
        pts = [[round(lat, 6), round(lng, 6)] for lat, lng in pts]
        pts = decimate(pts, MAX_POINTS_PER_ROUTE)
        length_m = _to_float(r.get("length"))
        link = r.get("link") or ""
        if link.startswith("/"):
            link = "https://vpohid.com.ua" + link
        out.append({
            "id": str(r["id"]),
            "title": r.get("title"),
            "short": r.get("shortdescription"),
            "length_km": round(length_m / 1000, 2) if length_m else None,
            "hours": _to_float(r.get("requirestimeinhours")),
            "level": r.get("leveltext") or r.get("level"),
            "color": r.get("color") or "#888",
            "marked": r.get("marked") == "y",
            "round": r.get("roundroute") == "y",
            "link": link or None,
            "points": pts,
        })
    return out


def _to_float(v):
    try:
        return float(v) if v not in (None, "", "0") else None
    except (ValueError, TypeError):
        return None


def my_tracks_index():
    """Load my tracks if present, build a coarse grid index for quick proximity tests."""
    if not TRACKS_FILE.exists():
        return None
    tracks = json.loads(TRACKS_FILE.read_text())
    cell_deg = 0.05  # ~5 km
    grid = {}
    for t in tracks:
        for lat, lng in t["points"]:
            key = (round(lat / cell_deg), round(lng / cell_deg))
            grid.setdefault(key, []).append((lat, lng))
    return {"grid": grid, "cell_deg": cell_deg}


def is_crossed(route_points, idx, threshold_km):
    if not idx:
        return False
    cell_deg = idx["cell_deg"]
    grid = idx["grid"]
    for lat, lng in route_points[::3]:  # sample every 3rd point
        cy = round(lat / cell_deg)
        cx = round(lng / cell_deg)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                cell = grid.get((cy + dy, cx + dx))
                if not cell:
                    continue
                for tlat, tlng in cell:
                    if haversine_km(lat, lng, tlat, tlng) <= threshold_km:
                        return True
    return False


def main():
    print(f"crawling {API} ...")
    raw = crawl()
    print(f"\nfetched {len(raw)} unique routes; normalizing ...")
    routes = normalize(raw)
    idx = my_tracks_index()
    if idx:
        print(f"computing 'crossed by me' flag (threshold={CROSS_THRESHOLD_KM}km) ...")
        crossed = 0
        for r in routes:
            r["crossed"] = is_crossed(r["points"], idx, CROSS_THRESHOLD_KM)
            if r["crossed"]:
                crossed += 1
        print(f"  crossed: {crossed} / {len(routes)}")
    OUT.write_text(json.dumps(routes, ensure_ascii=False))
    size_kb = OUT.stat().st_size / 1024
    print(f"\nwrote {OUT.name}  {len(routes)} routes  {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
