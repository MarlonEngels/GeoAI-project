from dash import Output, Input, State
from datetime import datetime
from config import OSLO_INFO_POINTS
from src.api.ais_api import fetch_ais_geojson, current_position_feature_collection
from src.api.weather_api import fetch_weather_geojson

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}

def register_callbacks(app):
    """Attach callbacks to the Dash app instance."""

    # ---- AIS callback ----
    @app.callback(
        Output("ais-geojson", "data"),
        Output("ais-store", "data"),
        Output("status", "children"),
        Input("interval", "n_intervals"),
        Input("layer-checklist", "value"),
        State("ais-store", "data"),
    )
    def update_ais(n_intervals, layers, previous_store):
        """
        Fetch AIS when layer enabled; show current positions as points on the map;
        keep original LineString data in ais-store; keep previous data on error;
        clear map if layer disabled.
        """
        if "ais" not in (layers or []):
            # Layer off: clear map, keep whatever was in the store
            return (
                EMPTY_GEOJSON,
                previous_store or EMPTY_GEOJSON,
                f"AIS layer disabled (interval #{n_intervals})",
            )

        try:
            # 1) Fetch raw LineString data from API
            raw_geojson = fetch_ais_geojson()

            # 2) Convert to current-position Point features for display
            points_geojson = current_position_feature_collection(raw_geojson)

            count = len(points_geojson.get("features", []))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            status = (
                f"AIS updated: {count} vessels (current positions) — "
                f"{ts} (interval #{n_intervals})"
            )

            # Map uses points; store keeps raw lines for later use (tracks, density, etc.)
            return points_geojson, raw_geojson, status

        except Exception as e:
            err_msg = (
                f"Error fetching AIS: {e!s} — showing last successful data "
                f"(interval #{n_intervals})"
            )

            # On error: use previous_store (raw lines) if available,
            # and compute points from that so the map still shows something.
            if previous_store:
                fallback_points = current_position_feature_collection(previous_store)
                return fallback_points, previous_store, err_msg
            else:
                return EMPTY_GEOJSON, EMPTY_GEOJSON, err_msg

    # ---- Weather (MET) callback ----
    @app.callback(
        Output("temp-geojson", "data"),
        Output("temp-store", "data"),
        Input("interval", "n_intervals"),
        Input("layer-checklist", "value"),
        State("temp-store", "data"),
    )
    def update_weather(n_intervals, layers, previous_store):
        if "temp" not in (layers or []):
            return EMPTY_GEOJSON, previous_store or EMPTY_GEOJSON

        try:
            geojson = fetch_weather_geojson()
            if not geojson.get("features"):
                print("[Weather] Warning: no features in data")
            return geojson, geojson
        except Exception as e:
            print(f"[Weather Callback] Error: {e}")
            return previous_store or EMPTY_GEOJSON, previous_store or EMPTY_GEOJSON
