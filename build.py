#!/usr/bin/env python3
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "tracks.json"

NS = {"g": "http://www.topografix.com/GPX/1/1"}

PALETTE = [
    "#e6194B", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9A6324", "#800000", "#aaffc3",
]

MAX_POINTS = 600  # decimate to keep payload small


def clean_name(filename: str) -> str:
    name = re.sub(r"\.gpx$", "", filename, flags=re.I)
    name = re.sub(r"\s*\(\d+\)$", "", name)
    name = name.replace("_", " ").strip()
    return name


def decimate(points, max_n):
    if len(points) <= max_n:
        return points
    step = len(points) / max_n
    return [points[int(i * step)] for i in range(max_n)] + [points[-1]]


def parse_gpx(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    pts = []
    times = []
    eles = []
    for trkpt in root.iterfind(".//g:trkpt", NS):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele_el = trkpt.find("g:ele", NS)
        time_el = trkpt.find("g:time", NS)
        pts.append([round(lat, 6), round(lon, 6)])
        eles.append(float(ele_el.text) if ele_el is not None else None)
        times.append(time_el.text if time_el is not None else None)
    name_el = root.find(".//g:trk/g:name", NS)
    name = name_el.text if name_el is not None else clean_name(path.name)
    type_el = root.find(".//g:trk/g:type", NS)
    typ = type_el.text if type_el is not None else None
    start_time = times[0] if times else None
    return {
        "name": name,
        "type": typ,
        "start_time": start_time,
        "points": pts,
        "elevations": eles,
    }


def haversine_km(a, b):
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(h))


def stats(pts, eles):
    dist = 0.0
    for i in range(1, len(pts)):
        dist += haversine_km(pts[i - 1], pts[i])
    gain = 0.0
    if eles:
        valid = [e for e in eles if e is not None]
        for i in range(1, len(valid)):
            d = valid[i] - valid[i - 1]
            if d > 0:
                gain += d
    return round(dist, 2), round(gain)


def main():
    files = sorted([p for p in DATA_DIR.glob("*.gpx")])
    tracks = []
    for i, p in enumerate(files):
        print(f"parsing {p.name} ...")
        d = parse_gpx(p)
        full_pts = d["points"]
        dist_km, elev_gain = stats(full_pts, d["elevations"])
        slim = decimate(full_pts, MAX_POINTS)
        tracks.append({
            "id": i,
            "file": p.name,
            "name": clean_name(p.name) if d["name"] in (None, "Morning Hike", "Lunch Hike", "Evening Hike", "Afternoon Hike") else d["name"],
            "type": d["type"],
            "start_time": d["start_time"],
            "color": PALETTE[i % len(PALETTE)],
            "distance_km": dist_km,
            "elev_gain_m": elev_gain,
            "n_points": len(full_pts),
            "points": slim,
            "start": full_pts[0] if full_pts else None,
            "finish": full_pts[-1] if full_pts else None,
        })

    OUT.write_text(json.dumps(tracks, ensure_ascii=False))
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.name}  {len(tracks)} tracks  {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
