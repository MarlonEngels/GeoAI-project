import requests
from config import AIS_URL, AIS_HISTORY_URL
from datetime import datetime, timezone

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


def fetch_ais_history_bbox(
    bbox: str,
    start: datetime,
    end: datetime,
    min_speed: float = 0.0,
    timeout: int = 30,
) -> dict:
    """
    Call /api/ais/positions/within-geom-time and return the raw JSON.
    bbox: 'minLon,minLat,maxLon,maxLat'
    start/end: aware datetimes in UTC.
    """
    # Make sure we're in UTC
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    payload = {
        "bbox": bbox,
        "start": start.strftime("%Y%m%d%H%M"),
        "end": end.strftime("%Y%m%d%H%M"),
        "minSpeed": min_speed,
    }
    resp = requests.post(AIS_HISTORY_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def history_to_track_geojson(history_json: dict, target_mmsi: int) -> dict:
    """
    Convert AIS history (bbox+time) into a LineString track for a single vessel.
    """
    data = history_json.get("data", [])
    if not data or len(data) < 2:
        return EMPTY_GEOJSON

    header = data[0]
    rows = data[1:]

    # Helper to find column index by substring
    def find_idx(substr: str) -> int:
        for i, col in enumerate(header):
            if substr.lower() in str(col).lower():
                return i
        raise ValueError(f"Column containing '{substr}' not found in header: {header}")

    try:
        mmsi_idx = find_idx("mmsi")
        lon_idx = find_idx("longitude")
        lat_idx = find_idx("latitude")
        time_idx = find_idx("date")
    except ValueError as e:
        print("Header parsing error:", e)
        return EMPTY_GEOJSON

    points = []
    for row in rows:
        try:
            if int(row[mmsi_idx]) != int(target_mmsi):
                continue
            lon = float(row[lon_idx])
            lat = float(row[lat_idx])
            t_str = str(row[time_idx])
        except Exception:
            continue
        points.append((t_str, lon, lat))

    if not points:
        return EMPTY_GEOJSON

    # Sort by timestamp string (ISO-like, should sort correctly)
    points.sort(key=lambda x: x[0])

    coords = [[lon, lat] for (_, lon, lat) in points]

    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"mmsi": int(target_mmsi)},
    }

    return {"type": "FeatureCollection", "features": [feature]}
