from dash import Output, Input, State, ctx
from dash.exceptions import PreventUpdate
from datetime import datetime, timezone
from src.api.ais_api import fetch_ais_geojson, current_position_feature_collection
from src.api.weather_api import fetch_weather_geojson_for_points
from src.utils.density import density_grid_geojson, points_in_polygon, extract_lon_lat_points
from src.api.ais_hist_api import fetch_positions_within_geom_time

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}


def _weather_point_id(lat, lon):
    return f"{float(lat):.6f},{float(lon):.6f}"


def _normalize_weather_points(points):
    normalized = []
    for point in (points or []):
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except Exception:
            continue

        normalized.append(
            {
                "id": str(point.get("id") or _weather_point_id(lat, lon)),
                "lat": lat,
                "lon": lon,
            }
        )
    return normalized


def _extract_marker_points_from_edit_geojson(geojson):
    points = []
    for feature in (geojson or {}).get("features", []):
        geom = feature.get("geometry", {}) or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon = float(coords[0])
        lat = float(coords[1])
        points.append({"id": _weather_point_id(lat, lon), "lat": lat, "lon": lon})
    return points


def _remove_editcontrol_markers(geojson):
    geojson = geojson or {"type": "FeatureCollection", "features": []}
    features = geojson.get("features", []) or []
    filtered = []
    removed_any = False
    for feature in features:
        geom = (feature or {}).get("geometry", {}) or {}
        if geom.get("type") == "Point":
            removed_any = True
            continue
        filtered.append(feature)
    return (
        {"type": "FeatureCollection", "features": filtered},
        removed_any,
    )


def _triggered_props_set():
    triggered_prop_ids = getattr(ctx, "triggered_prop_ids", None)
    if triggered_prop_ids:
        return set(triggered_prop_ids.keys())

    triggered = getattr(ctx, "triggered", None) or []
    props = set()
    for item in triggered:
        prop_id = item.get("prop_id")
        if prop_id:
            props.add(prop_id)
    return props

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
            return (
                EMPTY_GEOJSON,
                previous_store or EMPTY_GEOJSON,
                f"AIS layer disabled (interval #{n_intervals})",
            )

        try:
            raw_geojson = fetch_ais_geojson()
            
            points_geojson = current_position_feature_collection(raw_geojson)

            count = len(points_geojson.get("features", []))
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            status = (
                f"AIS updated: {count} Vessels "
                f"{ts}\nInterval #{n_intervals}"
            )

            return points_geojson, raw_geojson, status

        except Exception as e:
            err_msg = (
                f"Error fetching AIS: {e!s} - showing last successful data "
                f"(interval #{n_intervals})"
            )

            if previous_store:
                fallback_points = current_position_feature_collection(previous_store)
                return fallback_points, previous_store, err_msg
            else:
                return EMPTY_GEOJSON, EMPTY_GEOJSON, err_msg

    # ---- Weather (MET) callback ----
    @app.callback(
        Output("temp-geojson", "data"),
        Output("temp-store", "data"),
        Output("weather-points-store", "data"),
        Input("interval", "n_intervals"),
        Input("layer-checklist", "value"),
        Input("map", "clickData"),
        Input("weather-popup-events", "n_events"),
        State("weather-popup-events", "event"),
        State("temp-store", "data"),
        State("weather-points-store", "data"),
    )
    def update_weather(n_intervals, layers, map_click, popup_n_events, popup_event, previous_store, weather_points):
        del n_intervals, popup_n_events  # Trigger values only.

        layers = layers or []
        layer_enabled = "temp" in layers
        weather_points = _normalize_weather_points(weather_points)

        triggered_props = _triggered_props_set()
        interval_triggered = "interval.n_intervals" in triggered_props
        layers_triggered = "layer-checklist.value" in triggered_props
        map_triggered = "map.clickData" in triggered_props
        popup_triggered = "weather-popup-events.n_events" in triggered_props

        points_changed = False
        popup_remove_clicked = False

        if popup_triggered:
            evt = popup_event or {}
            target_class = str(evt.get("target.className") or "")
            remove_id = evt.get("target.dataset.weatherId") or evt.get("detail.weatherId")
            if "weather-remove-btn" in target_class and remove_id:
                popup_remove_clicked = True
                remove_id = str(remove_id)
                next_points = [p for p in weather_points if p["id"] != remove_id]
                if len(next_points) != len(weather_points):
                    weather_points = next_points
                    points_changed = True

        if map_triggered and not popup_remove_clicked and layer_enabled and map_click:
            latlng = (map_click or {}).get("latlng")
            lat = None
            lon = None

            if isinstance(latlng, dict):
                lat = latlng.get("lat")
                lon = latlng.get("lng", latlng.get("lon"))
            elif isinstance(latlng, (list, tuple)) and len(latlng) >= 2:
                lat = latlng[0]
                lon = latlng[1]

            if lat is not None and lon is not None:
                point_id = _weather_point_id(lat, lon)
                if point_id not in {p["id"] for p in weather_points}:
                    weather_points.append({"id": point_id, "lat": float(lat), "lon": float(lon)})
                    points_changed = True

        refresh_requested = points_changed or interval_triggered or layers_triggered

        if not layer_enabled:
            return EMPTY_GEOJSON, previous_store or EMPTY_GEOJSON, weather_points

        if not refresh_requested:
            raise PreventUpdate

        if not weather_points:
            return EMPTY_GEOJSON, EMPTY_GEOJSON, weather_points

        try:
            geojson = fetch_weather_geojson_for_points(weather_points)
            if weather_points and not geojson.get("features"):
                print("[Weather] Warning: no features in data")
            return geojson, geojson, weather_points
        except Exception as e:
            print(f"[Weather Callback] Error: {e}")
            return previous_store or EMPTY_GEOJSON, previous_store or EMPTY_GEOJSON, weather_points

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
