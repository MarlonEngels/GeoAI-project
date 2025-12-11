from dash import Output, Input, State
from dash.exceptions import PreventUpdate
from datetime import datetime, timedelta, timezone
from config import OSLO_INFO_POINTS
from src.api.ais_api import fetch_ais_geojson, current_position_feature_collection, fetch_ais_history_bbox, history_to_track_geojson
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
        
    @app.callback(
        Output("track-bbox-store", "data"),
        Input("edit-control", "geojson"),
    )
    def update_track_bbox(geojson):
        # No shapes drawn yet
        if not geojson or not geojson.get("features"):
            raise PreventUpdate

        # Use the last drawn feature (most recent rectangle or polygon)
        feature = geojson["features"][-1]
        geom = feature.get("geometry", {})
        if geom.get("type") != "Polygon":
            raise PreventUpdate

        # Polygon ring: list of [lon, lat] points, first = last
        coords = geom.get("coordinates", [[]])[0]
        if not coords:
            raise PreventUpdate

        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]

        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        bbox_str = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        return {"bbox": bbox_str}

    @app.callback(
        Output("selected-vessel-store", "data"),
        Output("track-status", "children"),
        Input("ais-geojson", "click_feature"),
    )
    def select_vessel(feature):
        if not feature:
            raise PreventUpdate

        props = feature.get("properties", {}) or {}
        mmsi = props.get("mmsi")
        name = props.get("ship_name", "Unknown vessel")

        if mmsi is None:
            return None, "Clicked feature has no MMSI."

        return int(mmsi), f"Selected vessel: {name} (MMSI {mmsi})"
    
    
    @app.callback(
        Output("track-geojson", "data"),
        Output("track-status", "children", allow_duplicate=True),
        Input("selected-vessel-store", "data"),
        Input("track-bbox-store", "data"),
        Input("track-window-dropdown", "value"),
        prevent_initial_call=True,
    )
    def update_track_from_history(selected_mmsi, bbox_data, window_minutes):
        if not selected_mmsi:
            return EMPTY_GEOJSON, "No vessel selected for track replay."

        # Use drawn bbox if present, else some default Oslofjord bbox
        if bbox_data and "bbox" in bbox_data:
            bbox_str = bbox_data["bbox"]
            bbox_info = f"bbox={bbox_str}"
        else:
            bbox_str = "10.0,58.5,11.5,60.0"  # fallback
            bbox_info = "default Oslofjord bbox"

        end = datetime.utcnow().replace(tzinfo=timezone.utc)
        start = end - timedelta(minutes=window_minutes)

        try:
            hist_json = fetch_ais_history_bbox(bbox_str, start, end)
            track_geojson = history_to_track_geojson(hist_json, int(selected_mmsi))

            n_pts = (
                len(track_geojson.get("features", [])[0]["geometry"]["coordinates"])
                if track_geojson.get("features")
                else 0
            )
            msg = (
                f"Track loaded for MMSI {selected_mmsi} "
                f"over last {window_minutes} min ({bbox_info}), {n_pts} points."
            )
            return track_geojson, msg

        except Exception as e:
            print("History error:", e)
            return EMPTY_GEOJSON, f"Error loading historic track: {e!s}"