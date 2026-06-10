"""
Generate VISIR-2 namelist YAML files from Dash app route parameters.

Workflow per run:
  1. Delete old tyrr_fields.yaml and tyrr_route.yaml
  2. Copy templates -> tyrr_fields.yaml / tyrr_route.yaml
  3. Fill in run-specific values
"""

import csv
import importlib.util
import os

from src.utils.env_data_downloader import floor_hour

# ---------------------------------------------------------------------------
# Import fill-yamls (hyphenated filename requires importlib)
# ---------------------------------------------------------------------------
_FILL_YAMLS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "visir-2-code", "fill-yamls.py")
)
_spec = importlib.util.spec_from_file_location("fill_yamls", _FILL_YAMLS_PATH)
_fill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fill)

create_new_yaml_files = _fill.create_new_yaml_files
fill_yaml = _fill.fill_yaml

# ---------------------------------------------------------------------------
# Port-code look-up
# ---------------------------------------------------------------------------
_PORT_CSV = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "visir-2-code", "scandinavia_port_codes.csv")
)

# Permanent graph namelist stem (in __namelist/_a_Grafi/).
_GRAPH_NAMELIST = "tyrr_graph"

_port_cache: dict | None = None


def _load_port_coords() -> dict:
    """Return ``{port_code: (lat, lon)}`` from the Scandinavian port CSV."""
    global _port_cache
    if _port_cache is not None:
        return _port_cache
    ports: dict = {}
    try:
        with open(_PORT_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["harb_code"].strip()
                lat = float(row["lat"])
                lon = float(row["lon"])
                ports[code] = (lat, lon)
    except Exception:
        pass
    _port_cache = ports
    return ports


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_namelists(params: dict) -> dict:
    """Delete old tyrr YAMLs, copy templates, fill in per-run values.

    Returns ``{"name_temp": "tyrr", "files": [...]}``.
    """
    forcing = params["forcing"]
    vessel = params["vessel"]
    departure_dt = params["departure_datetime"]
    n_days = int(params.get("n_days", 3))
    ntau = int(params.get("Ntau", 80))
    dtau = 30

    origin_type = params["origin_type"]
    dest_port = params["destination_port"]

    # Step 1+2: delete old files and copy fresh templates
    name_temp = create_new_yaml_files()

    f_current = int(forcing.get("current", 0))
    f_wave = int(forcing.get("wave", 0))
    f_wind = int(forcing.get("wind", 0))

    # Step 3a: fill _b_Campi YAML
    campi_filenames = {}
    if f_wave:
        campi_filenames["wave"] = "tyrr_wave.nc"
    if f_current:
        campi_filenames["current"] = "tyrr_current.nc"
    if f_wind:
        campi_filenames["wind"] = "tyrr_wind.nc"

    fill_yaml(name_temp, {
        "graph": _GRAPH_NAMELIST,
        "forcing": {
            "current": f_current,
            "wave": f_wave,
            "wind": f_wind,
        },
        "startTime": floor_hour(departure_dt),
        "n_days": n_days,
        "fileNames": campi_filenames,
    }, "campi")

    # Step 3b: fill _d_Tracce YAML
    tracce = {
        "run": name_temp,
        "graph": _GRAPH_NAMELIST,
        "vessel": vessel,
        "departureDateTime": departure_dt,
        "forcing": {
            "current": f_current,
            "wave": f_wave,
            "wind": f_wind,
            "leeway": 0,
        },
        "timeGrid": {"Dtau": dtau, "Ntau": ntau},
        "VesselFunEval": {
            "EvalType": "n",
            "deltaMethod": "it",
            "IterNumArcsin": 1,
        },
    }

    ports = _load_port_coords()

    if origin_type == "coords":
        tracce["endpoints"] = "coords"
        if dest_port not in ports:
            raise ValueError(f"Destination port {dest_port} not found in port database")
        tracce["coords"] = {
            "start_lat": params["origin_coords"]["lat"],
            "start_lon": params["origin_coords"]["lon"],
            "end_lat": ports[dest_port][0],
            "end_lon": ports[dest_port][1],
        }
    else:
        tracce["endpoints"] = "portCodes"
        tracce["portCodes"] = {
            "start": params["origin_port"],
            "arrival": dest_port,
        }

    fill_yaml(name_temp, tracce, "tracce")

    campi_path = os.path.normpath(
        os.path.join(_fill.NAMELIST_PATH, "_b_Campi", f"{name_temp}_fields.yaml")
    )
    tracce_path = os.path.normpath(
        os.path.join(_fill.NAMELIST_PATH, "_d_Tracce", f"{name_temp}_route.yaml")
    )

    return {"name_temp": name_temp, "files": [campi_path, tracce_path]}
