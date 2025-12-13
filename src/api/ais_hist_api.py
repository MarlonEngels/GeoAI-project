import requests
from datetime import datetime
from shapely.geometry import shape
from shapely.wkt import dumps as wkt_dumps
from config import AIS_HIST_URL

def fetch_positions_within_geom_time(
    geom_geojson: dict,
    start_utc: datetime,
    end_utc: datetime,
    min_speed: float = 0.0,
    timeout: int = 60,
) -> dict:
    wkt_geom = wkt_dumps(shape(geom_geojson), rounding_precision=6)

    candidates = [
        # 1) req wrapper + PascalCase
        {"req": {"Geom": wkt_geom, "Start": start_utc.isoformat(), "End": end_utc.isoformat(), "MinSpeed": float(min_speed)}},

        # 2) no wrapper + PascalCase
        {"Geom": wkt_geom, "Start": start_utc.isoformat(), "End": end_utc.isoformat(), "MinSpeed": float(min_speed)},

        # 3) req wrapper + camelCase
        {"req": {"geom": wkt_geom, "start": start_utc.isoformat(), "end": end_utc.isoformat(), "minSpeed": float(min_speed)}},

        # 4) no wrapper + camelCase
        {"geom": wkt_geom, "start": start_utc.isoformat(), "end": end_utc.isoformat(), "minSpeed": float(min_speed)},
    ]

    last_err = None
    for payload in candidates:
        r = requests.post(AIS_HIST_URL, json=payload, timeout=timeout)
        if r.ok:
            return r.json()
        last_err = f"{r.status_code} {r.text}"

    raise RuntimeError(last_err or "Request failed")