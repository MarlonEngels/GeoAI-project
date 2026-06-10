"""
Runtime system-robustness evaluation - surfaced in the Dash UI.

Mirrors the pytest suite in tests/robustness/test_system_robustness.py, but
each check returns a structured result with:
    - passed:   bool
    - metric:   short human-readable scalar ("p95 6.3x at N=8")
    - details:  list[dict] of per-row results, ready for tabular rendering
                in the sidebar (no GeoJSON - robustness has no map output)

Tests:
    A. Failure visibility    - score diagnostic output against five fields
    B. Transient retry       - 503 once then 200; record recovery
    C. Concurrent throughput - locked-sleep stub, fan out N=1,2,4,8 calls
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from src.api import ais_api, visir_api


# ---------------------------------------------------------------------------
# Stub-server scaffolding (shared by B and C)
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SilentHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature fixed by stdlib
        return


def _start_server(handler_cls) -> tuple[ThreadingHTTPServer, str]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# A. Failure visibility (logging / observability)
# ---------------------------------------------------------------------------
OBS_FIELDS = ("url", "error_class", "timestamp", "duration", "structured")


def _looks_structured(blob: str) -> bool:
    blob = blob.strip()
    if not blob:
        return False
    try:
        json.loads(blob)
        return True
    except Exception:
        pass
    return bool(re.search(r"\b[a-zA-Z_]+=\S+\b.*\b[a-zA-Z_]+=\S+\b", blob))


def _score_observability(emitted: str, raised: BaseException | None) -> dict[str, bool]:
    blob = (emitted or "") + " " + (str(raised) if raised else "")
    return {
        "url": bool(re.search(r"https?://", blob)),
        "error_class": (raised is not None) or ("Error" in blob),
        "timestamp": bool(re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", blob)),
        "duration": bool(re.search(r"\d+\s*(ms|s\b)", blob)),
        "structured": _looks_structured(blob),
    }


def _capture(callable_, *args, **kwargs) -> tuple[str, BaseException | None]:
    buf_out, buf_err = io.StringIO(), io.StringIO()
    raised: BaseException | None = None
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            callable_(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            raised = exc
    return (buf_out.getvalue() + buf_err.getvalue()), raised


def run_a() -> dict:
    original_get = ais_api.requests.get
    original_post = visir_api.requests.post

    def boom_get(url, **_):
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    def boom_post(url, **_):
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    ais_api.requests.get = boom_get
    visir_api.requests.post = boom_post
    try:
        cases = {
            "AIS API": lambda: ais_api.fetch_ais_geojson(),
            "VISIR-2 service": lambda: visir_api.compute_route({
                "graph": "tyrr_graph", "vessel": "unizd_Ferry",
                "endpoints": "coords",
                "departure_datetime": "2026-01-26T00:00:00Z",
            }),
        }
        details = []
        all_pass = True
        for name, fn in cases.items():
            emitted, raised = _capture(fn)
            score = _score_observability(emitted, raised)
            missing = [k for k, ok in score.items() if not ok]
            present = [k for k, ok in score.items() if ok]
            row_pass = not missing
            all_pass &= row_pass
            details.append({
                "row": name,
                "pass": row_pass,
                "score": f"{len(present)}/{len(score)}",
                "present": present,
                "missing": missing,
                "raised": type(raised).__name__ if raised else None,
            })
    finally:
        ais_api.requests.get = original_get
        visir_api.requests.post = original_post

    score_total = sum(len(d["present"]) for d in details)
    score_max = sum(len(OBS_FIELDS) for _ in details)
    return {
        "passed": all_pass,
        "metric": f"observability {score_total}/{score_max}",
        "details": details,
    }


# ---------------------------------------------------------------------------
# B. Transient-failure recovery (retry / backoff)
# ---------------------------------------------------------------------------
class _FlakyState:
    def __init__(self, fail_n: int):
        self.fail_n = fail_n
        self.calls = 0
        self.lock = threading.Lock()

    def next(self) -> bool:
        with self.lock:
            self.calls += 1
            return self.calls <= self.fail_n


def _make_flaky_handler(state: _FlakyState):
    class FlakyHandler(_SilentHandler):
        def _respond(self):
            if state.next():
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "transient"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "status": "ok",
                "routes": {"dist": [], "time": [], "CO2t": []},
                "type": "FeatureCollection", "features": [],
            }
            self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):  # noqa: N802
            self._respond()

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            self._respond()

    return FlakyHandler


def run_b() -> dict:
    state = _FlakyState(fail_n=1)
    server, base = _start_server(_make_flaky_handler(state))

    original_visir_url = visir_api.VISIR_URL
    from src.api import ais_api as ais_mod
    original_ais_url = getattr(ais_mod, "AIS_URL", None)

    visir_api.VISIR_URL = base
    ais_mod.AIS_URL = base + "/ais"

    details = []
    try:
        # AIS
        state.calls = 0
        t0 = time.perf_counter()
        recovered = False
        err = None
        try:
            ais_mod.fetch_ais_geojson(timeout=2)
            recovered = True
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:80]}"
        elapsed = (time.perf_counter() - t0) * 1000
        details.append({
            "row": "AIS API",
            "pass": recovered,
            "calls": state.calls,
            "elapsed_ms": round(elapsed, 1),
            "error": err,
        })

        # VISIR
        state.calls = 0
        t0 = time.perf_counter()
        recovered = False
        err = None
        try:
            visir_api.compute_route({
                "graph": "tyrr_graph", "vessel": "unizd_Ferry",
                "endpoints": "coords",
                "departure_datetime": "2026-01-26T00:00:00Z",
            })
            recovered = True
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:80]}"
        elapsed = (time.perf_counter() - t0) * 1000
        details.append({
            "row": "VISIR-2 service",
            "pass": recovered,
            "calls": state.calls,
            "elapsed_ms": round(elapsed, 1),
            "error": err,
        })
    finally:
        visir_api.VISIR_URL = original_visir_url
        if original_ais_url is not None:
            ais_mod.AIS_URL = original_ais_url
        server.shutdown()

    recovered_count = sum(1 for d in details if d["pass"])
    return {
        "passed": recovered_count == len(details),
        "metric": f"{recovered_count}/{len(details)} recovered",
        "details": details,
    }


# ---------------------------------------------------------------------------
# C. Concurrent throughput (lock-induced serialization)
# ---------------------------------------------------------------------------
WORK_MS = 150
CONCURRENCY_LEVELS = (1, 2, 4, 8)


class _LockedState:
    def __init__(self, work_s: float):
        self.work_s = work_s
        self.lock = threading.Lock()


def _make_locked_handler(state: _LockedState):
    class LockedHandler(_SilentHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            with state.lock:
                time.sleep(state.work_s)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"status": "ok", "routes": {"dist": [], "time": [], "CO2t": []}}
            self.wfile.write(json.dumps(payload).encode())

    return LockedHandler


def _timed_call(params: dict) -> float:
    t0 = time.perf_counter()
    visir_api.compute_route(params)
    return time.perf_counter() - t0


def run_c() -> dict:
    state = _LockedState(work_s=WORK_MS / 1000.0)
    server, base = _start_server(_make_locked_handler(state))

    original_visir_url = visir_api.VISIR_URL
    visir_api.VISIR_URL = base
    params = {
        "graph": "tyrr_graph", "vessel": "unizd_Ferry",
        "endpoints": "coords",
        "departure_datetime": "2026-01-26T00:00:00Z",
    }
    details = []
    try:
        for n in CONCURRENCY_LEVELS:
            latencies: list[float] = []
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n) as ex:
                futs = [ex.submit(_timed_call, params) for _ in range(n)]
                for fut in as_completed(futs):
                    latencies.append(fut.result())
            wall = time.perf_counter() - t0
            latencies.sort()
            p50 = latencies[len(latencies) // 2] * 1000
            p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)] * 1000
            details.append({
                "row": f"N={n}",
                "wall_ms": round(wall * 1000, 1),
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "throughput_rps": round(n / wall, 2),
            })
    finally:
        visir_api.VISIR_URL = original_visir_url
        server.shutdown()

    if not details:
        return {"passed": False, "metric": "no data", "details": []}

    p95_n1 = details[0]["p95_ms"] or 1.0
    p95_n8 = details[-1]["p95_ms"]
    ratio = p95_n8 / max(p95_n1, 1.0)
    # Reliability bar: p95 should not grow >2x from N=1 to N=8
    passed = ratio < 2.0
    for d in details:
        d["pass"] = True  # individual rows aren't the assertion - the ratio is
    return {
        "passed": passed,
        "metric": f"p95 {ratio:.1f}x at N=8 (lock-bound {round(1000/WORK_MS, 1)} rps)",
        "details": details,
        "ratio": round(ratio, 2),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
TESTS: dict[str, dict] = {
    "ROB-A": {
        "label": "A. Failure visibility",
        "explanation": (
            "Forces a connection error on each external dependency and scores "
            "the diagnostic output against five fields a structured log line "
            "should expose (url, error_class, timestamp, duration, structured)."
        ),
        "run": run_a,
    },
    "ROB-B": {
        "label": "B. Transient retry",
        "explanation": (
            "Stub server returns 503 once then 200. Calls AIS and VISIR-2 "
            "clients once each; passes only if both recover automatically."
        ),
        "run": run_b,
    },
    "ROB-C": {
        "label": "C. Concurrent throughput",
        "explanation": (
            "Stub holds a threading.Lock around a 150 ms sleep, mimicking "
            "visir_runner._visir_lock. Fans out N = 1, 2, 4, 8 concurrent "
            "compute_route calls; passes only if p95 stays below 2x at N=8."
        ),
        "run": run_c,
    },
}


def run_all(enabled: list[str] | None = None) -> dict[str, dict]:
    ids = enabled if enabled is not None else list(TESTS)
    return {tid: TESTS[tid]["run"]() for tid in ids if tid in TESTS}
