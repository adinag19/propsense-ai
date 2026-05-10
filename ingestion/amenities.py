"""
Download and process MRT station + primary school location data.

Sources (data.gov.sg, free, no auth):
  MRT exits:  d_b39d3a0871985372d7e1637193335da5 (LTA, GeoJSON)
  Schools:    d_688b934f82c1059ed0a6993d2a829089 (MOE, CSV with postal codes)

Geocodes schools via OneMap API (free, no auth).
Computes nearest MRT and nearest primary school for each planning area centroid.

Output: data/processed/area_amenities.csv
  planning_area, mrt_name, mrt_dist_m, school_name, school_dist_m

Usage:
    python -m ingestion.amenities
"""
from __future__ import annotations
import math, json, time
from pathlib import Path

import pandas as pd
import requests

RAW_DIR  = Path(__file__).parent.parent / "data" / "raw"
OUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "area_amenities.csv"

# Planning area centroids (lat, lng) — same as api/main.py _AREA_CENTROIDS
AREA_CENTROIDS: dict[str, tuple[float, float]] = {
    "Ang Mo Kio":    (1.3691, 103.8454), "Bedok":         (1.3236, 103.9273),
    "Bishan":        (1.3526, 103.8352), "Bukit Batok":   (1.3590, 103.7637),
    "Bukit Merah":   (1.2819, 103.8239), "Bukit Panjang": (1.3774, 103.7719),
    "Bukit Timah":   (1.3294, 103.8021), "Choa Chu Kang": (1.3840, 103.7470),
    "Clementi":      (1.3162, 103.7649), "Geylang":       (1.3201, 103.8818),
    "Hougang":       (1.3612, 103.8863), "Jurong East":   (1.3329, 103.7436),
    "Jurong West":   (1.3404, 103.7090), "Kallang":       (1.3100, 103.8640),
    "Marine Parade": (1.3020, 103.8967), "Novena":        (1.3203, 103.8431),
    "Pasir Ris":     (1.3721, 103.9494), "Punggol":       (1.4043, 103.9022),
    "Queenstown":    (1.2942, 103.7861), "Sembawang":     (1.4491, 103.8185),
    "Sengkang":      (1.3868, 103.8914), "Serangoon":     (1.3554, 103.8679),
    "Tampines":      (1.3496, 103.9568), "Toa Payoh":     (1.3343, 103.8563),
    "Woodlands":     (1.4382, 103.7866), "Yishun":        (1.4304, 103.8354),
    "Downtown Core": (1.2789, 103.8536), "Orchard":       (1.3048, 103.8318),
    "River Valley":  (1.2966, 103.8321), "Rochor":        (1.3042, 103.8565),
}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two WGS84 points."""
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def poll_download(dataset_id: str) -> str:
    url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["url"]


# ── MRT stations ──────────────────────────────────────────────────────────────

def load_mrt_stations() -> list[dict]:
    """Download LTA MRT exit GeoJSON → aggregate to station centroids."""
    print("Downloading MRT station exits...")
    url = poll_download("d_b39d3a0871985372d7e1637193335da5")
    data = requests.get(url, timeout=60).json()

    station_coords: dict[str, list[tuple[float, float]]] = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        geom  = feat.get("geometry", {})
        name  = props.get("STATION_NA") or props.get("STN_NAME") or props.get("NAME") or "Unknown"
        name  = str(name).strip()
        coords = geom.get("coordinates", [])
        if geom.get("type") == "Point" and len(coords) >= 2:
            lng, lat = float(coords[0]), float(coords[1])
            station_coords.setdefault(name, []).append((lat, lng))

    # Aggregate exits → station centroid
    stations = []
    for name, pts in station_coords.items():
        lat = sum(p[0] for p in pts) / len(pts)
        lng = sum(p[1] for p in pts) / len(pts)
        stations.append({"name": name, "lat": lat, "lng": lng})

    print(f"  {len(stations)} MRT stations loaded")
    return stations


# ── Schools ───────────────────────────────────────────────────────────────────

def geocode_onemap(postal: str) -> tuple[float, float] | None:
    """Geocode a Singapore postal code via OneMap API (free, no auth)."""
    try:
        url = f"https://www.onemap.gov.sg/api/common/elastic/search"
        params = {"searchVal": postal, "returnGeom": "Y", "getAddrDetails": "N"}
        r = requests.get(url, params=params, timeout=10)
        results = r.json().get("results", [])
        if results:
            return float(results[0]["LATITUDE"]), float(results[0]["LONGITUDE"])
    except Exception:
        pass
    return None


def load_primary_schools() -> list[dict]:
    """Download MOE school directory → geocode primary schools."""
    print("Downloading school directory...")
    url = poll_download("d_688b934f82c1059ed0a6993d2a829089")
    df = pd.read_csv(url)
    print(f"  Raw rows: {len(df)}, columns: {list(df.columns)}")

    # Filter primary schools
    type_col = next((c for c in df.columns if "mainlevel" in c.lower() or "school_t" in c.lower()), None)
    if type_col:
        primary = df[df[type_col].str.upper().str.contains("PRIMARY", na=False)].copy()
    else:
        primary = df.copy()  # fallback: use all schools
    print(f"  Primary schools: {len(primary)}")

    # Get postal code column
    postal_col = next((c for c in df.columns if "postal" in c.lower()), None)
    name_col   = next((c for c in df.columns if "school_n" in c.lower() or "name" in c.lower()), None)

    schools = []
    for _, row in primary.iterrows():
        name   = str(row.get(name_col, "")) if name_col else "School"
        postal = str(row.get(postal_col, "")).strip().zfill(6) if postal_col else ""
        if not postal or postal == "000000":
            continue
        coords = geocode_onemap(postal)
        if coords:
            schools.append({"name": name, "lat": coords[0], "lng": coords[1]})
        time.sleep(0.1)  # be gentle on OneMap

    print(f"  Geocoded {len(schools)} primary schools")
    return schools


# ── Main ──────────────────────────────────────────────────────────────────────

def build_amenities():
    mrt_stations = load_mrt_stations()
    primary_schools = load_primary_schools()

    rows = []
    for area, (lat, lng) in AREA_CENTROIDS.items():
        # Nearest MRT
        mrt_dists = [(haversine(lat, lng, s["lat"], s["lng"]), s["name"]) for s in mrt_stations]
        mrt_dists.sort()
        nearest_mrt_dist, nearest_mrt_name = mrt_dists[0] if mrt_dists else (None, None)

        # Nearest primary school
        sch_dists = [(haversine(lat, lng, s["lat"], s["lng"]), s["name"]) for s in primary_schools]
        sch_dists.sort()
        nearest_sch_dist, nearest_sch_name = sch_dists[0] if sch_dists else (None, None)

        rows.append({
            "planning_area":      area,
            "mrt_name":           nearest_mrt_name,
            "mrt_dist_m":         round(nearest_mrt_dist) if nearest_mrt_dist else None,
            "school_name":        nearest_sch_name,
            "school_dist_m":      round(nearest_sch_dist) if nearest_sch_dist else None,
        })
        print(f"  {area:20s} MRT: {nearest_mrt_name} ({round(nearest_mrt_dist)}m)  "
              f"School: {nearest_sch_name} ({round(nearest_sch_dist)}m)" if nearest_mrt_dist else f"  {area}: no data")

    df = pd.DataFrame(rows)
    df.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}")
    return df


if __name__ == "__main__":
    build_amenities()
