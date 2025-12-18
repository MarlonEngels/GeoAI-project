from math import cos, floor, radians
from shapely.geometry import Point, shape

def points_in_polygon(points, polygon_geom):
    poly = shape(polygon_geom)
    return [(lon, lat) for lon, lat in points if poly.contains(Point(lon, lat))]

def density_grid_geojson(points, bbox_str: str, cell_m: float) -> dict:
    min_lon, min_lat, max_lon, max_lat = map(float, bbox_str.split(","))
    mid_lat = (min_lat + max_lat) / 2.0

    deg_lat = cell_m / 111_320.0
    deg_lon = cell_m / (111_320.0 * max(0.1, cos(radians(mid_lat))))

    counts = {}
    for lon, lat in points:
        ix = floor((lon - min_lon) / deg_lon)
        iy = floor((lat - min_lat) / deg_lat)
        counts[(ix, iy)] = counts.get((ix, iy), 0) + 1

    features = []
    for (ix, iy), c in counts.items():
        x0 = min_lon + ix * deg_lon
        y0 = min_lat + iy * deg_lat
        x1 = x0 + deg_lon
        y1 = y0 + deg_lat

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]
            },
            "properties": {"count": c}
        })

    return {"type": "FeatureCollection", "features": features}

def extract_lon_lat_points(history_json: dict) -> list[tuple[float, float]]:
    rows = history_json.get("data", [])
    if not rows:
        return []

    pts = []
    for row in rows:
        try:
            lon = float(row[2])
            lat = float(row[3])
            pts.append((lon, lat))
        except Exception:
            continue

    return pts