"""
Background route-computation pipeline with progress tracking and cancellation.

Each job runs in its own thread and progresses through five steps:
  1. params  – write VISIR-2 namelist YAML files
  2. data    – download Copernicus environmental data
  3. fields  – run MAIN_Campi to process env data onto graph edges
  4. routes  – run MAIN_Tracce to compute optimal routes
  5. viz     – prepare route visualisation for the map

The Dash UI polls ``get_job()`` via a ``dcc.Interval`` and the user can
cancel at any time; created files are cleaned up automatically.
"""

import os
import threading

_jobs: dict = {}
_jobs_lock = threading.Lock()

STEPS = ["params", "data", "fields", "routes", "viz"]
STEP_LABELS = {
    "params": "Setting up route parameters",
    "data": "Retrieving environmental data",
    "fields": "Processing environmental fields",
    "routes": "Calculating routes",
    "viz": "Visualizing routes",
}

ROUTE_LABELS = {
    "dist": "Shortest Distance",
    "time": "Fastest Time",
    "CO2t": "Lowest CO2",
}


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def create_job(job_id: str) -> dict:
    with _jobs_lock:
        _jobs[job_id] = {
            "step": None,
            "status": "pending",       # pending | running | done | cancelled | error
            "error": None,
            "files": [],               # every file created (for cleanup)
            "cancel_event": threading.Event(),
            "name_temp": None,
            "route_geojson": None,
            "route_summary": None,
            "saved_route_id": None,
        }
        return dict(_jobs[job_id])


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        # Return a snapshot (skip the Event object — not serialisable)
        return {k: v for k, v in job.items() if k != "cancel_event"}


def _update(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def add_files(job_id: str, paths: list):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["files"].extend(paths)


def is_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job["cancel_event"].is_set())


def cancel_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["cancel_event"].set()
            job["status"] = "cancelled"


def remove_job(job_id: str):
    with _jobs_lock:
        _jobs.pop(job_id, None)


def cleanup_files(job_id: str):
    """Delete every file the job created."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        files = list(job["files"]) if job else []
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Route visualisation helpers
# ---------------------------------------------------------------------------

def _build_route_geojson(routes: dict) -> dict:
    """Convert VISIR-2 routes dict to a GeoJSON FeatureCollection."""
    features = []
    start_coord = None
    end_coord = None

    for route_type, waypoints in routes.items():
        if isinstance(waypoints, dict) and "error" in waypoints:
            continue
        if not waypoints or not isinstance(waypoints, list):
            continue

        coords = [
            [wp["lon"], wp["lat"]]
            for wp in waypoints
            if isinstance(wp, dict) and wp.get("lon") is not None and wp.get("lat") is not None
        ]
        if len(coords) < 2:
            continue

        first_wp = waypoints[0]
        last_wp = waypoints[-1]

        if start_coord is None:
            start_coord = coords[0]
            end_coord = coords[-1]

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "routeType": route_type,
                "routeLabel": ROUTE_LABELS.get(route_type, route_type),
                "distance": _safe_round(last_wp.get("cumDist"), 1),
                "duration": _safe_round(last_wp.get("cumTime"), 1),
                "co2": _safe_round(last_wp.get("cumCO2"), 2),
                "departure": first_wp.get("ISO_date", ""),
                "arrival": last_wp.get("ISO_date", ""),
                "nWaypoints": len(waypoints),
            },
        })

    # Start / end markers (shared across all route types)
    if start_coord:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": start_coord},
            "properties": {"markerType": "start"},
        })
    if end_coord:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": end_coord},
            "properties": {"markerType": "end"},
        })

    return {"type": "FeatureCollection", "features": features}


def _build_route_summary(routes: dict) -> dict:
    """Extract summary metrics from each route type."""
    summary = {}
    for route_type, waypoints in routes.items():
        if isinstance(waypoints, dict) and "error" in waypoints:
            summary[route_type] = {"error": waypoints["error"]}
            continue
        if not waypoints or not isinstance(waypoints, list):
            continue

        last_wp = waypoints[-1]
        first_wp = waypoints[0]

        sog_values = [
            wp["SOG"] for wp in waypoints
            if isinstance(wp, dict) and wp.get("SOG") is not None
        ]
        avg_sog = round(sum(sog_values) / len(sog_values), 1) if sog_values else 0

        summary[route_type] = {
            "label": ROUTE_LABELS.get(route_type, route_type),
            "distance": _safe_round(last_wp.get("cumDist"), 1),
            "duration": _safe_round(last_wp.get("cumTime"), 1),
            "co2": _safe_round(last_wp.get("cumCO2"), 2),
            "avgSOG": avg_sog,
            "departure": first_wp.get("ISO_date", ""),
            "arrival": last_wp.get("ISO_date", ""),
            "nWaypoints": len(waypoints),
        }
    return summary


def _safe_round(value, ndigits):
    if value is None:
        return 0
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Pipeline runner  (called in a background thread)
# ---------------------------------------------------------------------------

def run_pipeline(job_id: str, params: dict):
    """Execute the full route-computation pipeline."""
    from src.utils.namelist_writer import create_namelists
    from src.utils.env_data_downloader import (
        compute_time_range,
        download_env_data,
        get_env_file_paths,
    )
    from src.api.visir_api import run_campi, run_route

    try:
        # ---- Step 1: params ----
        _update(job_id, step="params", status="running")

        result = create_namelists(params)
        name_temp = result["name_temp"]  # always "tyrr"
        add_files(job_id, result["files"])
        _update(job_id, name_temp=name_temp)

        if is_cancelled(job_id):
            cleanup_files(job_id)
            return

        # ---- Step 2: data ----
        _update(job_id, step="data")

        forcing = params["forcing"]
        any_env = int(forcing.get("wave", 0)) or int(forcing.get("current", 0))

        if any_env:
            departure_dt = params["departure_datetime"]
            n_days = int(params.get("n_days", 3))
            start_dt, end_dt = compute_time_range(departure_dt, n_days)

            env_filenames = download_env_data(start_dt, end_dt, forcing)
            add_files(job_id, get_env_file_paths(env_filenames))

        if is_cancelled(job_id):
            cleanup_files(job_id)
            return

        # ---- Step 3: fields (MAIN_Campi) ----
        _update(job_id, step="fields")

        if any_env:
            run_campi("tyrr_fields")

        if is_cancelled(job_id):
            cleanup_files(job_id)
            return

        # ---- Step 4: routes ----
        _update(job_id, step="routes")

        routes = run_route("tyrr_route", name_temp)

        if is_cancelled(job_id):
            cleanup_files(job_id)
            return

        # ---- Step 5: viz ----
        _update(job_id, step="viz")

        route_geojson = _build_route_geojson(routes)
        route_summary = _build_route_summary(routes)
        _update(job_id, route_geojson=route_geojson, route_summary=route_summary)

        if is_cancelled(job_id):
            cleanup_files(job_id)
            return

        try:
            from src.utils.saved_routes import save_route
            saved_id = save_route(params, route_geojson, route_summary)
            _update(job_id, saved_route_id=saved_id)
        except Exception as exc:
            print(f"[route_job] Failed to save route to disk: {exc}")

        # ---- Done ----
        _update(job_id, step="done", status="done")

    except Exception as exc:
        _update(job_id, status="error", error=str(exc))
        cleanup_files(job_id)
