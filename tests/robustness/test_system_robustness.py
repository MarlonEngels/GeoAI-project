"""
System robustness evaluation - three checks, one per branch of the
robustness factor:

  A. Failure visibility / observability.
     When an external dependency fails, what diagnostic information does
     the prototype emit?  Score the output against a checklist of fields
     a structured log line should contain.

  B. Transient-failure recovery.
     A stub server returns 503 for the first N attempts, then 200.  The
     prototype's HTTP clients are called once and we record whether they
     ever recover (i.e. whether retry/backoff exists).

  C. Concurrent-request throughput.
     A stub HTTP server holds a single threading.Lock around a 150 ms
     sleep, mimicking visir_runner.py's _visir_lock around MAIN_Tracce.
     We fire N concurrent compute_route calls (N = 1, 2, 4, 8) and
     measure wall time, per-request latency, and effective throughput.

Run:
    pip install pytest requests
    pytest tests/robustness/test_system_robustness.py -v -s
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.api import ais_api, visir_api  # noqa: E402


# ---------------------------------------------------------------------------
# Stub-server scaffolding shared by tests B and C
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SilentHandler(BaseHTTPRequestHandler):
    """HTTP handler that does not print to stderr on every request."""

    def log_message(self, format, *args):  # noqa: A002 - signature fixed by stdlib
        return


def _start_server(handler_cls) -> tuple[ThreadingHTTPServer, str]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


# ===========================================================================
# A. Failure visibility / observability
# ===========================================================================

# Fields a structured-log line should expose so that an operator can debug
# a failure without re-running the request.
OBS_FIELDS = (
    "url",          # which endpoint failed
    "error_class",  # exception type, not just a message
    "timestamp",    # when it failed
    "duration",     # how long the request took before failing
    "structured",   # emitted via logging/JSON, not via print()
)


def _score_observability(emitted: str, raised: BaseException | None) -> dict[str, bool]:
    """Score what was emitted on stdout/stderr plus any raised exception."""
    blob = (emitted or "") + " " + (str(raised) if raised else "")
    return {
        "url": bool(re.search(r"https?://", blob)),
        "error_class": (raised is not None) or ("Error" in blob),
        # ISO-8601-ish or HH:MM:SS
        "timestamp": bool(re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", blob)),
        # numeric ms / s in the diagnostic
        "duration": bool(re.search(r"\d+\s*(ms|s\b)", blob)),
        # structured == JSON object or key=value pairs (not free-text print)
        "structured": _looks_structured(blob),
    }


def _looks_structured(blob: str) -> bool:
    blob = blob.strip()
    if not blob:
        return False
    # JSON object?
    try:
        json.loads(blob)
        return True
    except Exception:
        pass
    # key=value pairs?
    return bool(re.search(r"\b[a-zA-Z_]+=\S+\b.*\b[a-zA-Z_]+=\S+\b", blob))


def _capture(callable_, *args, **kwargs) -> tuple[str, BaseException | None]:
    """Invoke callable_, returning (captured stdout/stderr, raised exception)."""
    import io
    import contextlib

    buf_out, buf_err = io.StringIO(), io.StringIO()
    raised: BaseException | None = None
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            callable_(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - we want them all
            raised = exc
    return (buf_out.getvalue() + buf_err.getvalue()), raised


def test_failure_visibility(monkeypatch) -> None:
    """For each external dep, force a failure and score the diagnostic output."""

    def boom_get(url, **_):
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    def boom_post(url, **_):
        raise requests.exceptions.ConnectionError(f"refused: {url}")

    monkeypatch.setattr(ais_api.requests, "get", boom_get)
    monkeypatch.setattr(visir_api.requests, "post", boom_post)

    cases = {
        "ais": lambda: ais_api.fetch_ais_geojson(),
        "visir": lambda: visir_api.compute_route({
            "graph": "tyrr_graph",
            "vessel": "unizd_Ferry",
            "endpoints": "coords",
            "departure_datetime": "2026-01-26T00:00:00Z",
        }),
    }

    scores: dict[str, dict[str, bool]] = {}
    for name, fn in cases.items():
        emitted, raised = _capture(fn)
        scores[name] = _score_observability(emitted, raised)
        print(
            f"[A] {name}: emitted={emitted.strip()[:120]!r} "
            f"raised={type(raised).__name__ if raised else None}",
            file=sys.stderr,
        )

    print(f"[A] checklist scores: {scores}", file=sys.stderr)

    # Reliability bar: every observability field present for every dep.
    missing = {
        name: [k for k, ok in s.items() if not ok]
        for name, s in scores.items()
    }
    print(f"[A] missing fields per dep: {missing}", file=sys.stderr)

    pass_count = sum(1 for s in scores.values() for v in s.values() if v)
    total = sum(len(s) for s in scores.values())
    print(f"[A] observability score: {pass_count}/{total}", file=sys.stderr)

    assert all(not v for v in missing.values()), (
        f"observability gaps: {missing}"
    )


# ===========================================================================
# B. Transient-failure recovery (retry / backoff)
# ===========================================================================

class _FlakyState:
    """Shared counter for the flaky stub."""

    def __init__(self, fail_n: int):
        self.fail_n = fail_n
        self.calls = 0
        self.lock = threading.Lock()

    def next(self) -> bool:
        """Return True if the response should fail, False if it should succeed."""
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
                # AIS clients call resp.json() and expect a FeatureCollection
                "type": "FeatureCollection",
                "features": [],
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


def test_transient_failure_recovery(monkeypatch) -> None:
    """One 503 then 200 — does either client recover?"""
    state = _FlakyState(fail_n=1)
    server, base = _start_server(_make_flaky_handler(state))
    try:
        monkeypatch.setattr(visir_api, "VISIR_URL", base)

        # AIS client: monkeypatch the URL it reads
        from src.api import ais_api as ais_mod
        monkeypatch.setattr(ais_mod, "AIS_URL", base + "/ais")

        results = {}

        # Reset counter and call AIS
        state.calls = 0
        t0 = time.perf_counter()
        try:
            ais_mod.fetch_ais_geojson(timeout=2)
            results["ais"] = {"recovered": True, "calls": state.calls}
        except requests.HTTPError as exc:
            results["ais"] = {
                "recovered": False, "calls": state.calls,
                "error": f"{type(exc).__name__}: {exc.response.status_code}",
            }
        results["ais"]["elapsed_ms"] = (time.perf_counter() - t0) * 1000

        # Reset counter and call VISIR
        state.calls = 0
        t0 = time.perf_counter()
        try:
            visir_api.compute_route({
                "graph": "tyrr_graph",
                "vessel": "unizd_Ferry",
                "endpoints": "coords",
                "departure_datetime": "2026-01-26T00:00:00Z",
            })
            results["visir"] = {"recovered": True, "calls": state.calls}
        except RuntimeError as exc:
            results["visir"] = {
                "recovered": False, "calls": state.calls,
                "error": str(exc)[:120],
            }
        results["visir"]["elapsed_ms"] = (time.perf_counter() - t0) * 1000

        for name, r in results.items():
            print(f"[B] {name}: {r}", file=sys.stderr)

        # Reliability bar: both should recover from a single transient 503.
        assert all(r["recovered"] for r in results.values()), (
            f"no retry/backoff layer: {results}"
        )
    finally:
        server.shutdown()


# ===========================================================================
# C. Concurrent-request throughput (lock-induced serialization)
# ===========================================================================

WORK_MS = 150  # simulated MAIN_Tracce.Main() cost
CONCURRENCY_LEVELS = (1, 2, 4, 8)


class _LockedState:
    """Mimic visir_runner._visir_lock around the routing call."""

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


def test_concurrent_route_throughput(monkeypatch) -> None:
    """Fire N concurrent compute_route calls; observe queueing under the lock."""
    state = _LockedState(work_s=WORK_MS / 1000.0)
    server, base = _start_server(_make_locked_handler(state))
    try:
        monkeypatch.setattr(visir_api, "VISIR_URL", base)

        params = {
            "graph": "tyrr_graph",
            "vessel": "unizd_Ferry",
            "endpoints": "coords",
            "departure_datetime": "2026-01-26T00:00:00Z",
        }

        report: dict[int, dict[str, float]] = {}
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
            throughput = n / wall  # requests / second

            report[n] = {
                "wall_ms": round(wall * 1000, 1),
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "throughput_rps": round(throughput, 2),
            }
            print(f"[C] N={n}: {report[n]}", file=sys.stderr)

        # Reliability bar: doubling N should not double per-request latency.
        # If the server lock fully serializes requests, p95 grows linearly.
        ratio = report[8]["p95_ms"] / max(report[1]["p95_ms"], 1.0)
        print(
            f"[C] p95 ratio N=8 / N=1 = {ratio:.2f}x "
            f"(linear queueing predicts ~8.0x)",
            file=sys.stderr,
        )
        assert ratio < 2.0, (
            f"p95 latency grows {ratio:.2f}x from N=1 to N=8 — "
            "concurrent requests are being serialized server-side"
        )
    finally:
        server.shutdown()


def _timed_call(params: dict) -> float:
    t0 = time.perf_counter()
    visir_api.compute_route(params)
    return time.perf_counter() - t0
