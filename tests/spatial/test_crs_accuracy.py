"""
Spatial accuracy evaluation #1 - Coordinate / CRS accuracy.

Three checks (each a separate pytest test, each produces a scalar metric):

  A. Round-trip transformation through every CRS the system uses.
     WGS84 -> target CRS -> WGS84 drift must stay below ROUND_TRIP_TOL_M.

  B. Datum / range consistency per declared data source.
     Catches swapped lat/lon, wrong-CRS-in-disguise, and out-of-domain rows.

  C. Reference landmark check.
     Authoritative WGS84 positions (tests/spatial/reference_landmarks.yaml)
     must be reproduced by every data source that references the same feature,
     within the per-landmark tolerance.

  D. Local flat-earth approximation used by src/utils/density.py.
     The hard-coded 111_320 m/deg conversion is compared against the true
     geodesic distance from pyproj.Geod; relative error must stay bounded.

Run:
    pip install pyproj pytest pyyaml
    pytest tests/spatial/test_crs_accuracy.py -v
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pytest
import yaml

pyproj = pytest.importorskip(
    "pyproj",
    reason="pyproj is required for CRS accuracy tests (pip install pyproj)",
)
from pyproj import Geod, Transformer


REPO_ROOT = Path(__file__).resolve().parents[2]
PORT_CSV = REPO_ROOT / "src" / "visir-2-code" / "scandinavia_port_codes.csv"
LANDMARKS_YAML = Path(__file__).parent / "reference_landmarks.yaml"

# ---------------------------------------------------------------------------
# System CRSs
# ---------------------------------------------------------------------------
# 4326  - WGS84 lat/lon, native for all stored and streamed coordinates
# 3857  - Web Mercator, used by the Leaflet tile basemap
# 25832 - ETRS89 / UTM 32N, natural local projected CRS for Oslo Fjord
#         (distance work in metres for density grids, buffers, etc.)
SYSTEM_CRSS = ["EPSG:3857", "EPSG:25832"]

# Oslo Fjord domain (from src/utils/env_data_downloader.py)
DOMAIN_LAT = (58.0, 60.5)
DOMAIN_LON = (9.0, 11.7)

# Scandinavia envelope (sanity range for port CSV rows)
SCANDI_LAT = (53.0, 71.5)
SCANDI_LON = (4.0, 31.5)

SAMPLE_POINTS = [
    (59.9067, 10.7369),  # Oslo
    (59.6778, 10.6090),  # Drøbak
    (59.4147, 10.4886),  # Horten
    (59.0543, 10.0334),  # Larvik
    (58.5000, 10.5000),  # Mid-domain
    (60.4000, 11.6000),  # Near upper corner
]

ROUND_TRIP_TOL_M = 1e-3  # 1 mm
FLAT_EARTH_REL_TOL = 0.005  # 0.5 %

GEOD = Geod(ellps="WGS84")


def _load_landmarks() -> list[dict]:
    with open(LANDMARKS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_port_csv() -> dict[str, tuple[float, float]]:
    rows: dict[str, tuple[float, float]] = {}
    with open(PORT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("harb_code") or "").strip()
            if not code:
                continue
            try:
                rows[code] = (float(row["lat"]), float(row["lon"]))
            except (TypeError, ValueError):
                continue
    return rows


# ===========================================================================
# A. Round-trip transformation
# ===========================================================================

@pytest.mark.parametrize("target_crs", SYSTEM_CRSS)
def test_roundtrip_wgs84_to_crs(target_crs: str) -> None:
    """WGS84 -> target_crs -> WGS84 must round-trip within ROUND_TRIP_TOL_M."""
    fwd = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    inv = Transformer.from_crs(target_crs, "EPSG:4326", always_xy=True)

    worst = 0.0
    for lat, lon in SAMPLE_POINTS:
        x, y = fwd.transform(lon, lat)
        lon2, lat2 = inv.transform(x, y)
        _, _, dist = GEOD.inv(lon, lat, lon2, lat2)
        worst = max(worst, dist)

    print(f"[A] {target_crs}: worst round-trip drift = {worst:.6e} m", file=sys.stderr)
    assert worst < ROUND_TRIP_TOL_M, (
        f"{target_crs}: round-trip drift {worst:.3e} m exceeds {ROUND_TRIP_TOL_M} m"
    )


# ===========================================================================
# B. Datum / range consistency
# ===========================================================================

def test_domain_is_inside_scandinavia_envelope() -> None:
    """Silent lat/lon swap would push the domain outside Scandinavia."""
    assert SCANDI_LAT[0] <= DOMAIN_LAT[0] < DOMAIN_LAT[1] <= SCANDI_LAT[1]
    assert SCANDI_LON[0] <= DOMAIN_LON[0] < DOMAIN_LON[1] <= SCANDI_LON[1]


def test_port_csv_rows_are_wgs84_scandinavia() -> None:
    """Every port in the CSV must plausibly be in Scandinavia in WGS84.

    Catches (a) lat/lon swap, (b) projected coords stored as if lat/lon,
    (c) degrees-minutes mistakenly stored as decimal degrees.
    """
    ports = _load_port_csv()
    assert ports, f"no rows in {PORT_CSV}"

    offenders = [
        (code, lat, lon)
        for code, (lat, lon) in ports.items()
        if not (SCANDI_LAT[0] <= lat <= SCANDI_LAT[1]
                and SCANDI_LON[0] <= lon <= SCANDI_LON[1])
    ]
    assert not offenders, f"{len(offenders)} rows out of Scandinavia range: {offenders[:5]}"


def test_env_download_bbox_declared_in_degrees() -> None:
    """env_data_downloader constants must parse as WGS84 degrees."""
    from src.utils import env_data_downloader as edd

    assert -90 <= edd.MIN_LAT < edd.MAX_LAT <= 90
    assert -180 <= edd.MIN_LON < edd.MAX_LON <= 180
    # and within Oslo Fjord envelope we reason about in this suite
    assert DOMAIN_LAT[0] <= edd.MIN_LAT and edd.MAX_LAT <= DOMAIN_LAT[1]
    assert DOMAIN_LON[0] <= edd.MIN_LON and edd.MAX_LON <= DOMAIN_LON[1]


# ===========================================================================
# C. Reference landmark check
# ===========================================================================

@pytest.mark.parametrize("lm", _load_landmarks(), ids=lambda lm: lm["name"])
def test_landmark_is_in_domain(lm: dict) -> None:
    """Every landmark must fall inside the Oslo Fjord domain bbox."""
    assert DOMAIN_LAT[0] <= lm["lat"] <= DOMAIN_LAT[1], f'{lm["name"]} lat out of range'
    assert DOMAIN_LON[0] <= lm["lon"] <= DOMAIN_LON[1], f'{lm["name"]} lon out of range'


@pytest.mark.parametrize(
    "lm",
    [lm for lm in _load_landmarks() if lm.get("port_code")],
    ids=lambda lm: lm["name"],
)
def test_landmark_matches_port_csv(lm: dict) -> None:
    """Port-coded landmarks must agree with the port CSV within tolerance."""
    ports = _load_port_csv()
    code = lm["port_code"]
    if code not in ports:
        pytest.skip(f"port {code} not in CSV; nothing to check")

    csv_lat, csv_lon = ports[code]
    _, _, dist_m = GEOD.inv(lm["lon"], lm["lat"], csv_lon, csv_lat)
    tol = float(lm["tolerance_m"])
    print(
        f"[C] {lm['name']} ({code}): CSV offset = {dist_m:.1f} m "
        f"(tolerance {tol:.0f} m)",
        file=sys.stderr,
    )
    assert dist_m <= tol, (
        f"{lm['name']} ({code}) CSV offset {dist_m:.1f} m exceeds tolerance {tol:.0f} m"
    )


# ===========================================================================
# D. Flat-earth approximation used in density.py
# ===========================================================================

def test_density_flat_earth_constant_is_accurate_within_tolerance() -> None:
    """
    density.py converts cell size metres -> degrees using 111_320 m/deg and
    cos(lat) for longitude. Verify the resulting degree steps match the
    true WGS84 geodesic distance within FLAT_EARTH_REL_TOL.
    """
    cell_m = 1000.0
    worst_rel = 0.0
    for mid_lat in (58.0, 59.0, 60.0):
        deg_lat = cell_m / 111_320.0
        deg_lon = cell_m / (111_320.0 * max(0.1, math.cos(math.radians(mid_lat))))

        # Compare to true geodesic distances at this latitude
        _, _, true_m_lat = GEOD.inv(10.5, mid_lat, 10.5, mid_lat + deg_lat)
        _, _, true_m_lon = GEOD.inv(10.5, mid_lat, 10.5 + deg_lon, mid_lat)

        rel_lat = abs(true_m_lat - cell_m) / cell_m
        rel_lon = abs(true_m_lon - cell_m) / cell_m
        worst_rel = max(worst_rel, rel_lat, rel_lon)
        print(
            f"[D] lat={mid_lat}: lat_err={rel_lat*100:.3f}%, lon_err={rel_lon*100:.3f}%",
            file=sys.stderr,
        )

    assert worst_rel < FLAT_EARTH_REL_TOL, (
        f"flat-earth approximation error {worst_rel*100:.2f}% "
        f"exceeds {FLAT_EARTH_REL_TOL*100:.2f}%"
    )
