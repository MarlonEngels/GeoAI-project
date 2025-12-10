from dash import Output, Input, State
from datetime import datetime
from config import OSLO_INFO_POINTS
from src.api.ais_api import fetch_ais_geojson
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
        """Fetch AIS when layer enabled; keep previous data on error; clear map if layer disabled."""
        if "ais" not in (layers or []):
            return {}, previous_store, f"AIS layer disabled (interval #{n_intervals})"

        try:
            geojson = fetch_ais_geojson()
            features = geojson.get("features", [])
            count = len(features)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            status = f"AIS updated: {count} features — {ts} (interval #{n_intervals})"
            return geojson, geojson, status
        except Exception as e:
            err_msg = f"Error fetching AIS: {e!s} — showing last successful data (interval #{n_intervals})"
            fallback = previous_store or {"type": "FeatureCollection", "features": []}
            return fallback, previous_store or fallback, err_msg

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
