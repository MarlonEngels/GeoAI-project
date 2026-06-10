"""Persistent storage for computed ship routes.

Routes are stored as individual JSON files in ``data/saved_routes/`` so a
user can recall previously computed routes across app restarts. Each file
contains the request params, per-route-type summary metrics, and the
GeoJSON FeatureCollection used to draw the route on the map.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SAVED_ROUTES_DIR = Path(__file__).resolve().parents[2] / "data" / "saved_routes"


def _ensure_dir() -> None:
    SAVED_ROUTES_DIR.mkdir(parents=True, exist_ok=True)


def save_route(params: dict, geojson: dict, summary: dict) -> str:
    _ensure_dir()
    saved_at = datetime.now(timezone.utc)
    route_id = (
        f"route_{saved_at.strftime('%Y%m%dT%H%M%S')}"
        f"_{saved_at.microsecond // 1000:03d}"
    )
    payload = {
        "id": route_id,
        "savedAt": saved_at.isoformat().replace("+00:00", "Z"),
        "params": params,
        "summary": summary,
        "geojson": geojson,
    }
    path = SAVED_ROUTES_DIR / f"{route_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return route_id


def list_saved_routes() -> list[dict]:
    _ensure_dir()
    entries = []
    for path in SAVED_ROUTES_DIR.glob("route_*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        entries.append({
            "id": data.get("id", path.stem),
            "savedAt": data.get("savedAt"),
            "params": data.get("params", {}),
            "summary": data.get("summary", {}),
        })
    entries.sort(key=lambda e: e.get("savedAt") or "", reverse=True)
    return entries


def load_saved_route(route_id: str) -> dict | None:
    _ensure_dir()
    path = SAVED_ROUTES_DIR / f"{route_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def delete_saved_route(route_id: str) -> bool:
    _ensure_dir()
    path = SAVED_ROUTES_DIR / f"{route_id}.json"
    try:
        os.remove(path)
        return True
    except OSError:
        return False
