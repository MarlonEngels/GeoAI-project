import atexit
import os
import subprocess
import sys
import threading

import requests

VISIR_URL = os.environ.get("VISIR_URL", "http://localhost:5050").rstrip("/")
_TIMEOUT = 86400  # seconds

_visir_process = None
_process_lock = threading.Lock()


_EXTERNAL_SERVICE = not VISIR_URL.startswith("http://localhost")

_VISIR2_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "VISIR-2_v6")
)


def start_visir_service():
    global _visir_process
    if _EXTERNAL_SERVICE:
        return

    with _process_lock:
        if _visir_process is not None and _visir_process.poll() is None:
            return

        if not os.path.isdir(_VISIR2_DIR):
            print(f"[VISIR] VISIR-2 directory not found: {_VISIR2_DIR}")
            return

        cmd = [
            "conda", "run", "--no-capture-output",
            "-n", "visir-venv",
            "python", "visir_runner.py",
        ]
        
        child_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("WERKZEUG_")
        }
        try:
            _visir_process = subprocess.Popen(
                cmd,
                cwd=_VISIR2_DIR,
                env=child_env,
                shell=(sys.platform == "win32"),
            )
            print(f"[VISIR] Started VISIR-2 service (PID {_visir_process.pid})")
        except FileNotFoundError:
            print("[VISIR] 'conda' not found on PATH. Install Anaconda/Miniconda.")
        except Exception as exc:
            print(f"[VISIR] Failed to start service: {exc}")


def _shutdown_visir():
    global _visir_process
    with _process_lock:
        if _visir_process is not None and _visir_process.poll() is None:
            print("[VISIR] Shutting down VISIR-2 service...")
            _visir_process.terminate()
            try:
                _visir_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _visir_process.kill()


atexit.register(_shutdown_visir)


def check_health() -> dict:
    try:
        resp = requests.get(f"{VISIR_URL}/health", timeout=0.5)
        if resp.status_code == 200:
            return {"status": "ready", "message": "VISIR-2 service is ready"}
    except requests.exceptions.ConnectionError:
        pass
    except Exception:
        pass

    if not _EXTERNAL_SERVICE and _visir_process is not None:
        if _visir_process.poll() is None:
            return {
                "status": "starting",
                "message": "Conda environment is loading...",
            }
        return {
            "status": "unavailable",
            "message": "VISIR-2 process exited unexpectedly",
        }

    if _EXTERNAL_SERVICE:
        return {
            "status": "unavailable",
            "message": "VISIR-2 container is not reachable",
        }

    return {"status": "unavailable", "message": "VISIR-2 service is not running"}


def run_campi(job_name: str):
    try:
        resp = requests.post(
            f"{VISIR_URL}/run-campi",
            json={"job_name": job_name},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot reach VISIR-2 service at {VISIR_URL}."
        ) from exc

    payload = resp.json()
    if resp.status_code != 200 or payload.get("status") != "ok":
        raise RuntimeError(
            f"VISIR-2 Campi processing failed: {payload.get('message', resp.text)}"
        )


def run_route(job_name: str, run_name: str) -> dict:
    try:
        resp = requests.post(
            f"{VISIR_URL}/run",
            json={"job_name": job_name, "run_name": run_name},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot reach VISIR-2 service at {VISIR_URL}. "
            "Is the service running?"
        ) from exc

    payload = resp.json()
    if resp.status_code != 200 or payload.get("status") != "ok":
        raise RuntimeError(
            f"VISIR-2 service error: {payload.get('message', resp.text)}"
        )

    return payload.get("routes", {})


def compute_route(params: dict) -> dict:
    try:
        resp = requests.post(
            f"{VISIR_URL}/compute-route",
            json=params,
            timeout=_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot reach VISIR-2 service at {VISIR_URL}. "
            "Is the Docker container running?"
        ) from exc

    payload = resp.json()
    if resp.status_code != 200 or payload.get("status") != "ok":
        raise RuntimeError(
            f"VISIR-2 service error: {payload.get('message', resp.text)}"
        )

    return payload.get("routes", {})


def route_to_geojson(waypoints: list, properties: dict | None = None) -> dict:
    coords = [[wp["lon"], wp["lat"]] for wp in waypoints if "lon" in wp and "lat" in wp]
    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": properties or {},
    }
    return {"type": "FeatureCollection", "features": [feature]}
