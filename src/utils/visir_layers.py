"""Load VISIR-2 shoreline and bathymetry data as GeoJSON for map display."""

import json
import os

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from shapely.geometry import box

matplotlib.use("Agg")

_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "VISIR-2_v6", "__data"
)
_SHORELINE_SHP = os.path.join(
    _DATA_DIR, "shoreline", "ne_10m_coastline", "ne_10m_coastline.shp"
)
_BATHY_NC = os.path.join(_DATA_DIR, "bathymetry", "tyrr_bathy.nc")

# Bounding box for clipping (generous margin around the bathymetry extent)
_CLIP_BOX = box(9.0, 58.0, 11.5, 60.5)

# Bathymetry contour levels (metres below sea level)
_BATHY_LEVELS = [-500, -300, -200, -100, -50, -10, 0]

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}

# Cache so data is only loaded once
_shoreline_cache = None
_bathymetry_cache = None


def load_shoreline_geojson():
    """Return coastline GeoJSON clipped to the study area."""
    global _shoreline_cache
    if _shoreline_cache is not None:
        return _shoreline_cache

    if not os.path.isfile(_SHORELINE_SHP):
        print(f"[visir_layers] Shoreline shapefile not found: {_SHORELINE_SHP}")
        _shoreline_cache = EMPTY_GEOJSON
        return _shoreline_cache

    gdf = gpd.read_file(_SHORELINE_SHP)
    clipped = gpd.clip(gdf, _CLIP_BOX)
    if clipped.empty:
        _shoreline_cache = EMPTY_GEOJSON
    else:
        _shoreline_cache = json.loads(clipped.to_json())

    return _shoreline_cache


def load_bathymetry_geojson():
    """Return bathymetry contour lines as GeoJSON."""
    global _bathymetry_cache
    if _bathymetry_cache is not None:
        return _bathymetry_cache

    if not os.path.isfile(_BATHY_NC):
        print(f"[visir_layers] Bathymetry file not found: {_BATHY_NC}")
        _bathymetry_cache = EMPTY_GEOJSON
        return _bathymetry_cache

    ds = xr.open_dataset(_BATHY_NC)
    elev = ds["elevation"].values
    lats = ds["lat"].values
    lons = ds["lon"].values
    ds.close()

    # Mask land (positive elevation)
    bathy = np.where(elev < 0, elev, np.nan)

    fig, ax = plt.subplots()
    cs = ax.contour(lons, lats, bathy, levels=_BATHY_LEVELS)
    plt.close(fig)

    features = []
    for i, level in enumerate(_BATHY_LEVELS):
        segs = cs.allsegs[i] if i < len(cs.allsegs) else []
        for seg in segs:
            coords = seg.tolist()
            if len(coords) < 2:
                continue
            # Downsample long segments
            if len(coords) > 100:
                step = max(1, len(coords) // 100)
                coords = coords[::step]
                if coords[-1] != seg[-1].tolist():
                    coords.append(seg[-1].tolist())
            features.append(
                {
                    "type": "Feature",
                    "properties": {"depth": float(level)},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [round(c[0], 4), round(c[1], 4)] for c in coords
                        ],
                    },
                }
            )

    _bathymetry_cache = {"type": "FeatureCollection", "features": features}
    return _bathymetry_cache
