"""
Runtime spatial-accuracy evaluation - surfaced in the Dash UI.

Mirrors the pytest suite in tests/spatial/test_crs_accuracy.py, but instead
of asserting, each check returns a structured result with:
    - passed:   bool
    - metric:   short human-readable scalar ("worst drift 3.21e-4 m")
    - features: GeoJSON Feature list, ready to drop into a dl.GeoJSON layer
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import yaml
from pyproj import Geod, Transformer


REPO_ROOT = Path(__file__).resolve().parents[2]
PORT_CSV = REPO_ROOT / "src" / "visir-2-code" / "scandinavia_port_codes.csv"
LANDMARKS_YAML = REPO_ROOT / "tests" / "spatial" / "reference_landmarks.yaml"

SYSTEM_CRSS = ["EPSG:3857", "EPSG:25832"]
DOMAIN_LAT = (58.0, 60.5)
DOMAIN_LON = (9.0, 11.7)
SCANDI_LAT = (53.0, 71.5)
SCANDI_LON = (4.0, 31.5)

SAMPLE_POINTS = [
    (59.9067, 10.7369),
    (59.6778, 10.6090),
    (59.4147, 10.4886),
    (59.0543, 10.0334),
    (58.5000, 10.5000),
    (60.4000, 11.6000),
]

ROUND_TRIP_TOL_M = 1e-3
FLAT_EARTH_REL_TOL = 0.005
GEOD = Geod(ellps="WGS84")


def _point(lat: float, lon: float, props: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def _line(lat1: float, lon1: float, lat2: float, lon2: float, props: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon1, lat1], [lon2, lat2]],
        },
        "properties": props,
    }


def _rect(south: float, north: float, west: float, east: float, props: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [west, south], [east, south], [east, north],
                [west, north], [west, south],
            ]],
        },
        "properties": props,
    }


def _load_landmarks() -> list[dict]:
    with open(LANDMARKS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_port_csv() -> dict[str, tuple[float, float, str]]:
    ports: dict[str, tuple[float, float, str]] = {}
    with open(PORT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("harb_code") or "").strip()
            if not code:
                continue
            try:
                name = (row.get("name_HR") or "").strip()
                ports[code] = (float(row["lat"]), float(row["lon"]), name)
            except (TypeError, ValueError):
                continue
    return ports


# ---------------------------------------------------------------------------
# A. Round-trip CRS
# ---------------------------------------------------------------------------
def run_a() -> dict:
    features = []
    worst_overall = 0.0
    all_pass = True

    for target in SYSTEM_CRSS:
        fwd = Transformer.from_crs("EPSG:4326", target, always_xy=True)
        inv = Transformer.from_crs(target, "EPSG:4326", always_xy=True)
        for lat, lon in SAMPLE_POINTS:
            x, y = fwd.transform(lon, lat)
            lon2, lat2 = inv.transform(x, y)
            _, _, dist = GEOD.inv(lon, lat, lon2, lat2)
            ok = dist < ROUND_TRIP_TOL_M
            all_pass &= ok
            worst_overall = max(worst_overall, dist)
            features.append(_point(lat, lon, {
                "test": "A",
                "crs": target,
                "drift_m": float(dist),
                "pass": bool(ok),
                "tooltip": f"A: {target}<br>drift {dist:.3e} m",
            }))

    return {
        "passed": bool(all_pass),
        "metric": f"worst drift {worst_overall:.2e} m",
        "features": features,
    }


# ---------------------------------------------------------------------------
# B. Reference landmarks
# ---------------------------------------------------------------------------
def run_b() -> dict:
    landmarks = _load_landmarks()
    ports = _load_port_csv()
    features: list[dict] = []
    fails = 0

    for lm in landmarks:
        truth_lat = float(lm["lat"])
        truth_lon = float(lm["lon"])
        name = lm["name"]
        tol = float(lm["tolerance_m"])
        code = lm.get("port_code")

        if code and code in ports:
            csv_lat, csv_lon, csv_name = ports[code]
            _, _, dist = GEOD.inv(truth_lon, truth_lat, csv_lon, csv_lat)
            ok = dist <= tol
            if not ok:
                fails += 1

            features.append(_point(truth_lat, truth_lon, {
                "test": "B", "name": name, "role": "truth",
                "pass": bool(ok), "offset_m": round(float(dist), 1),
                "tooltip": f"B truth: {name}<br>offset {dist:.0f} m (tol {tol:.0f})",
            }))
            features.append(_point(csv_lat, csv_lon, {
                "test": "B", "name": csv_name or code, "role": "csv", "code": code,
                "pass": bool(ok), "offset_m": round(float(dist), 1),
                "tooltip": f"B csv: {code} {csv_name}<br>offset {dist:.0f} m",
            }))
            features.append(_line(truth_lat, truth_lon, csv_lat, csv_lon, {
                "test": "B", "name": name,
                "pass": bool(ok), "offset_m": round(float(dist), 1),
                "tooltip": f"B offset line: {name} {dist:.0f} m",
            }))
        else:
            features.append(_point(truth_lat, truth_lon, {
                "test": "B", "name": name, "role": "truth",
                "pass": None,
                "tooltip": f"B: {name} (no port_code in CSV)",
            }))

    return {
        "passed": fails == 0,
        "metric": f"{fails} landmark mismatch(es)",
        "features": features,
    }


# ---------------------------------------------------------------------------
# C. Flat-earth approximation in density.py
# ---------------------------------------------------------------------------
def run_c() -> dict:
    cell_m = 1000.0
    features: list[dict] = []
    worst_rel = 0.0
    center_lon = 10.5

    for mid_lat in (58.0, 59.0, 60.0):
        deg_lat = cell_m / 111_320.0
        deg_lon = cell_m / (111_320.0 * max(0.1, math.cos(math.radians(mid_lat))))

        _, _, true_lat_m = GEOD.inv(center_lon, mid_lat, center_lon, mid_lat + deg_lat)
        _, _, true_lon_m = GEOD.inv(center_lon, mid_lat, center_lon + deg_lon, mid_lat)

        rel_lat = abs(true_lat_m - cell_m) / cell_m
        rel_lon = abs(true_lon_m - cell_m) / cell_m
        worst_rel = max(worst_rel, rel_lat, rel_lon)

        ok = max(rel_lat, rel_lon) < FLAT_EARTH_REL_TOL
        features.append(_rect(
            mid_lat - deg_lat / 2, mid_lat + deg_lat / 2,
            center_lon - deg_lon / 2, center_lon + deg_lon / 2,
            {
                "test": "C",
                "role": "approx_cell",
                "mid_lat": mid_lat,
                "err_lat_pct": round(rel_lat * 100, 3),
                "err_lon_pct": round(rel_lon * 100, 3),
                "pass": bool(ok),
                "tooltip": (
                    f"C: {mid_lat} N 1 km cell<br>"
                    f"lat err {rel_lat*100:.3f}% / lon err {rel_lon*100:.3f}%"
                ),
            },
        ))

    return {
        "passed": worst_rel < FLAT_EARTH_REL_TOL,
        "metric": f"worst rel error {worst_rel*100:.3f}%",
        "features": features,
    }


TESTS: dict[str, dict] = {
    "A": {
        "label": "A. CRS round-trip",
        "explanation": (
            "Transforms sample points through EPSG:3857 (Leaflet tiles) and "
            "EPSG:25832 (UTM 32N) and back to WGS84. Round-trip drift must "
            "stay below 1 mm - anything larger signals a CRS misconfiguration."
        ),
        "run": run_a,
    },
    "B": {
        "label": "B. Reference landmarks",
        "explanation": (
            "Known landmarks with authoritative WGS84 coords (in "
            "tests/spatial/reference_landmarks.yaml) are compared against the "
            "same feature in the port CSV. Offset must stay within the "
            "per-landmark tolerance."
        ),
        "run": run_b,
    },
    "C": {
        "label": "C. Flat-earth approximation",
        "explanation": (
            "density.py converts metres to degrees with the 111,320 m/deg "
            "constant and cos(lat). This compares it to the true WGS84 "
            "geodesic distance at 58, 59 and 60 N; relative error must stay "
            "under 0.5 %."
        ),
        "run": run_c,
    },
}


def run_all(enabled: list[str] | None = None) -> dict[str, dict]:
    ids = enabled if enabled is not None else list(TESTS)
    return {tid: TESTS[tid]["run"]() for tid in ids if tid in TESTS}
