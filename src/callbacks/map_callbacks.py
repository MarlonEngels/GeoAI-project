from dash import Output, Input, State
from dash.exceptions import PreventUpdate
from datetime import datetime, timezone
from config import OSLO_INFO_POINTS
from src.api.ais_api import fetch_ais_geojson, current_position_feature_collection
from src.api.weather_api import fetch_weather_geojson
from src.utils.density import density_grid_geojson, points_in_polygon, extract_lon_lat_points
from src.api.ais_hist_api import fetch_positions_within_geom_time

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


    # ---- Draw control callback ----
    @app.callback(
        Output("draw-geom-store", "data"),
        Input("edit-control", "geojson"),
    )
    def store_drawn_geometry(geojson):
        if not geojson or not geojson.get("features"):
            return None

        feature = geojson["features"][-1]
        geom = feature.get("geometry", {})
        if geom.get("type") != "Polygon":
            raise PreventUpdate

        ring = geom.get("coordinates", [[]])[0]
        if not ring:
            raise PreventUpdate

        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        bbox_str = f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}"

        return {"polygon": geom, "bbox": bbox_str}


    @app.callback(
        Output("density-geojson", "data"),
        Output("density-geojson", "hideout"),
        Output("dens-status", "children"),
        Input("dens-run", "n_clicks"),
        State("draw-geom-store", "data"),
        State("dens-start-date", "date"),
        State("dens-start-time", "value"),
        State("dens-end-date", "date"),
        State("dens-end-time", "value"),
        State("dens-cell-m", "value"),
        prevent_initial_call=True,
    )
    def compute_density(n_clicks, draw_data, start_date, start_time, end_date, end_time, cell_m):
        if not draw_data or "bbox" not in draw_data or "polygon" not in draw_data:
            return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, "Area cleared — density removed."

        if not start_date or not end_date or not start_time or not end_time:
            return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, "Select start/end date and time (UTC)."

        def parse_utc(date_str: str, time_str: str) -> datetime:
            try:
                h, m = map(int, time_str.strip().split(":"))
            except Exception:
                raise ValueError("Time must be in HH:MM format (e.g., 09:30).")
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("Time must be valid (00:00 to 23:59).")
            return datetime.fromisoformat(date_str).replace(
                hour=h, minute=m, second=0, microsecond=0, tzinfo=timezone.utc
            )

        try:
            start = parse_utc(start_date, start_time)
            end = parse_utc(end_date, end_time)
        except Exception as e:
            return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, f"Invalid time input: {e}"

        if end <= start:
            return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, "End must be after start."

        bbox = draw_data["bbox"]
        poly = draw_data["polygon"]

        try:
            hist = fetch_positions_within_geom_time(poly, start, end, min_speed=0.0)

            pts = extract_lon_lat_points(hist)
            pts = points_in_polygon(pts, poly)

            grid = density_grid_geojson(pts, bbox, float(cell_m or 500))

            # --- Option A: quantile thresholds (always gives a spread) ---
            counts = sorted(int(f["properties"]["count"]) for f in grid.get("features", []))
            if not counts:
                return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, "No density cells (no AIS points in area/time)."

            def q(p: float) -> int:
                idx = int(p * (len(counts) - 1))
                return counts[idx]

            t1 = q(0.25)
            t2 = q(0.50)
            t3 = q(0.75)

            t1 = max(1, t1)
            t2 = max(t1 + 1, t2)
            t3 = max(t2 + 1, t3)

            msg = f"Computed density: {len(pts)} AIS points inside area, {len(grid['features'])} grid cells."
            return grid, {"t1": t1, "t2": t2, "t3": t3}, msg

        except Exception as e:
            return EMPTY_GEOJSON, {"t1": 1, "t2": 2, "t3": 3}, f"Error: {e!s}"
        
    @app.callback(
        Output("density-geojson", "data", allow_duplicate=True),
        Input("draw-geom-store", "data"),
        prevent_initial_call=True,
    )
    def clear_density_when_no_shape(draw_data):
        if not draw_data:
            return EMPTY_GEOJSON
        raise PreventUpdate