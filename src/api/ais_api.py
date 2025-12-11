import requests
from config import AIS_URL

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}


def current_position_feature_collection(line_geojson: dict) -> dict:
    """
    Convert a FeatureCollection of LineString vessel tracks into a
    FeatureCollection of Point features at the last coordinate of each line.
    """
    point_features = []

    for f in line_geojson.get("features", []):
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])

        if geom.get("type") == "LineString" and coords:
            last_lon, last_lat = coords[-1]
            point_features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [last_lon, last_lat],
                    },
                    "properties": f.get("properties", {}),
                }
            )

    return {"type": "FeatureCollection", "features": point_features}


def fetch_ais_geojson(timeout: int = 10) -> dict:
    resp = requests.get(AIS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()