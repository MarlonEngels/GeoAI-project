import threading
import uuid

from dash import Output, Input, State, ctx, html, no_update, ALL
from dash.exceptions import PreventUpdate
from datetime import datetime, timezone
from src.api.ais_api import fetch_ais_geojson, current_position_feature_collection
from src.api.weather_api import fetch_weather_geojson_for_points
from src.utils.density import density_grid_geojson, points_in_polygon, extract_lon_lat_points
from src.api.ais_hist_api import fetch_positions_within_geom_time
from src.api.visir_api import check_health
from src.utils.route_job import (
    STEPS, STEP_LABELS,
    create_job, get_job, cancel_job, remove_job, run_pipeline,
)
from src.utils.saved_routes import (
    list_saved_routes, load_saved_route, delete_saved_route,
)
from src.utils import spatial_evaluation, robustness_evaluation
from src.layout.map_view import PORT_LABELS, VESSEL_OPTIONS

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


def _render_rob_details(tid: str, res: dict):
    """Render robustness test result as a readable Dash component tree."""
    rows = res.get("details", [])
    metric = res.get("metric", "")
    passed = res.get("passed", False)

    header_color = "#2e7d32" if passed else "#c62828"
    header = html.Div(
        [
            html.Span(
                "PASS " if passed else "FAIL ",
                style={"color": header_color, "fontWeight": "bold"},
            ),
            html.Span(metric, style={"color": "#333"}),
        ],
        style={"marginBottom": "6px", "fontSize": "12px"},
    )

    if not rows:
        return [header, html.Div("(no rows returned)", style={"color": "#888"})]

    # Pick columns based on test ID — keeps the rendering tight per test type.
    if tid == "ROB-A":
        cols = [("row", "Dependency"), ("score", "Score"),
                ("missing", "Missing fields"), ("raised", "Raised")]
    elif tid == "ROB-B":
        cols = [("row", "Client"), ("calls", "Calls"),
                ("elapsed_ms", "Elapsed (ms)"), ("error", "Error")]
    elif tid == "ROB-C":
        cols = [("row", "Concurrency"), ("wall_ms", "Wall (ms)"),
                ("p50_ms", "p50 (ms)"), ("p95_ms", "p95 (ms)"),
                ("throughput_rps", "rps")]
    else:
        cols = [(k, k) for k in (rows[0].keys() if rows else [])]

    th_style = {
        "textAlign": "left", "fontWeight": "bold", "color": "#444",
        "padding": "4px 8px", "borderBottom": "1px solid #ccc",
    }
    td_style = {"padding": "3px 8px", "borderBottom": "1px solid #eee"}

    def _fmt(value):
        if isinstance(value, list):
            return ", ".join(value) if value else "-"
        if value is None:
            return "-"
        return str(value)

    head = html.Tr([html.Th(label, style=th_style) for _, label in cols])
    body = []
    for row in rows:
        row_pass = row.get("pass")
        bg = "#f1f8e9" if row_pass else ("#fff" if row_pass is None else "#ffebee")
        cells = [html.Td(_fmt(row.get(key)), style=td_style) for key, _ in cols]
        body.append(html.Tr(cells, style={"backgroundColor": bg}))

    table = html.Table(
        [html.Thead(head), html.Tbody(body)],
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "fontSize": "11px",
            "fontFamily": "Consolas, Menlo, monospace",
        },
    )
    return [header, table]


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

    # ---- Shoreline / Bathymetry toggle ----
    @app.callback(
        Output("shoreline-geojson", "data"),
        Output("bathy-geojson", "data"),
        Input("layer-checklist", "value"),
        State("shoreline-store", "data"),
        State("bathy-store", "data"),
    )
    def toggle_visir_layers(layers, shoreline_data, bathy_data):
        layers = layers or []
        shore = shoreline_data if "shoreline" in layers else EMPTY_GEOJSON
        bathy = bathy_data if "bathy" in layers else EMPTY_GEOJSON
        return shore, bathy

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

            count = len(points_geojson.get("features") or [])
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
        Input("filtered-map-click", "data"),
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
        map_triggered = "filtered-map-click.data" in triggered_props
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

    # ---- Route modal callbacks ----

    _MODAL_HIDDEN = {
        "position": "fixed",
        "top": 0, "left": 0, "right": 0, "bottom": 0,
        "backgroundColor": "rgba(0,0,0,0.5)",
        "zIndex": 2000,
        "display": "none",
        "justifyContent": "center",
        "alignItems": "center",
    }
    _MODAL_VISIBLE = dict(_MODAL_HIDDEN, display="flex")

    @app.callback(
        Output("route-modal", "style"),
        Input("route-open-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def open_route_modal_sidebar(n_clicks):
        return _MODAL_VISIBLE

    @app.callback(
        Output("route-modal", "style", allow_duplicate=True),
        Output("route-origin-type", "value"),
        Output("route-origin-lat", "value"),
        Output("route-origin-lon", "value"),
        Input("weather-popup-events", "n_events"),
        State("weather-popup-events", "event"),
        prevent_initial_call=True,
    )
    def open_route_modal_from_vessel(n_events, event):
        evt = event or {}
        target_class = str(evt.get("target.className") or "")
        if "route-compute-btn" not in target_class:
            raise PreventUpdate

        lat = evt.get("target.dataset.vesselLat")
        lon = evt.get("target.dataset.vesselLon")

        try:
            lat_val = float(lat) if lat else None
            lon_val = float(lon) if lon else None
        except (TypeError, ValueError):
            lat_val = None
            lon_val = None

        return _MODAL_VISIBLE, "coords", lat_val, lon_val

    @app.callback(
        Output("route-modal", "style", allow_duplicate=True),
        Input("route-close-btn", "n_clicks"),
        Input("route-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def close_route_modal(close_clicks, cancel_clicks):
        return _MODAL_HIDDEN

    @app.callback(
        Output("route-coords-div", "style"),
        Output("route-port-div", "style"),
        Input("route-origin-type", "value"),
    )
    def toggle_origin_fields(origin_type):
        if origin_type == "coords":
            return {"display": "block"}, {"display": "none"}
        return {"display": "none"}, {"display": "block"}

    @app.callback(
        Output("route-depart-fields", "style"),
        Input("route-depart-now", "value"),
    )
    def toggle_depart_fields(depart_now):
        if "now" in (depart_now or []):
            return {"display": "none"}
        return {"display": "block"}

    # ------------------------------------------------------------------ #
    #  Submit route form  → validate, start background job, show panel   #
    # ------------------------------------------------------------------ #

    _PROGRESS_HIDDEN = {
        "display": "none",
        "position": "absolute",
        "bottom": 0, "left": 0, "right": 0,
        "backgroundColor": "#fff",
        "borderTop": "1px solid #ddd",
        "padding": "12px 16px",
        "zIndex": 10,
        "boxShadow": "0 -2px 6px rgba(0,0,0,0.08)",
    }
    _PROGRESS_VISIBLE = dict(_PROGRESS_HIDDEN, display="block")

    _ROUTE_INFO_HIDDEN = {"display": "none", "marginTop": "8px"}
    _ROUTE_INFO_VISIBLE = {"display": "block", "marginTop": "8px"}

    @app.callback(
        Output("route-status", "children"),
        Output("route-modal", "style", allow_duplicate=True),
        Output("route-job-store", "data"),
        Output("route-progress-panel", "style"),
        Output("route-progress-interval", "disabled"),
        Output("route-sidebar-status", "children"),
        Output("route-geojson", "data", allow_duplicate=True),
        Output("route-result-store", "data", allow_duplicate=True),
        Output("route-info-panel", "style", allow_duplicate=True),
        Output("route-historical-store", "data", allow_duplicate=True),
        Input("route-submit-btn", "n_clicks"),
        State("route-origin-type", "value"),
        State("route-origin-lat", "value"),
        State("route-origin-lon", "value"),
        State("route-origin-port", "value"),
        State("route-dest-port", "value"),
        State("route-vessel", "value"),
        State("route-depart-date", "date"),
        State("route-depart-time", "value"),
        State("route-depart-now", "value"),
        State("route-forcing", "value"),
        State("route-ndays", "value"),
        prevent_initial_call=True,
    )
    def submit_route_form(
        n_clicks, origin_type, origin_lat, origin_lon, origin_port,
        dest_port, vessel, depart_date, depart_time, depart_now,
        forcing, ndays,
    ):
        NU = no_update  # shorthand

        # --- validation ---
        if not dest_port:
            return "Please select a destination port.", NU, NU, NU, NU, NU, NU, NU, NU, NU
        if not vessel:
            return "Please select a vessel type.", NU, NU, NU, NU, NU, NU, NU, NU, NU
        if origin_type == "coords":
            if origin_lat is None or origin_lon is None:
                return "Please enter origin coordinates.", NU, NU, NU, NU, NU, NU, NU, NU, NU
        else:
            if not origin_port:
                return "Please select an origin port.", NU, NU, NU, NU, NU, NU, NU, NU, NU

        # --- departure datetime ---
        if "now" in (depart_now or []):
            departure_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            if not depart_date:
                return "Please select a departure date.", NU, NU, NU, NU, NU, NU, NU, NU, NU
            try:
                h, m = map(int, (depart_time or "12:00").strip().split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except (ValueError, TypeError):
                return "Invalid time format. Use HH:MM.", NU, NU, NU, NU, NU, NU, NU, NU, NU
            departure_dt = f"{depart_date[:10]}T{h:02d}:{m:02d}:00Z"

        # --- build params ---
        forcing_list = forcing or []
        forcing_dict = {
            "current": 1 if "current" in forcing_list else 0,
            "wave": 1 if "wave" in forcing_list else 0,
            "wind": 1 if "wind" in forcing_list else 0,
            "leeway": 1 if "leeway" in forcing_list else 0,
        }
        params = {
            "origin_type": origin_type,
            "destination_port": dest_port,
            "vessel": vessel,
            "departure_datetime": departure_dt,
            "forcing": forcing_dict,
            "n_days": int(ndays or 3),
            "Ntau": 80,
        }
        if origin_type == "coords":
            params["origin_coords"] = {"lat": float(origin_lat), "lon": float(origin_lon)}
        else:
            params["origin_port"] = origin_port

        # --- create background job ---
        job_id = uuid.uuid4().hex[:12]
        create_job(job_id)
        threading.Thread(
            target=run_pipeline, args=(job_id, params), daemon=True,
        ).start()

        return (
            "",                     # clear modal error
            _MODAL_HIDDEN,          # close modal
            job_id,                 # store job id
            _PROGRESS_VISIBLE,      # show panel
            False,                  # enable interval
            "Computing route...",   # sidebar status
            EMPTY_GEOJSON,          # clear old routes from map
            None,                   # clear old route data
            _ROUTE_INFO_HIDDEN,     # hide route info panel
            None,                   # clear historical marker
        )

    # ------------------------------------------------------------------ #
    #  Poll route-computation progress                                    #
    # ------------------------------------------------------------------ #

    def _step_icon(state):
        if state == "done":
            return html.Span("\u2713 ", style={"color": "#2e7d32", "fontWeight": "bold"})
        if state == "active":
            return html.Span("\u25cf ", style={"color": "#1976D2"})
        if state == "error":
            return html.Span("\u2717 ", style={"color": "#c62828", "fontWeight": "bold"})
        if state == "cancelled":
            return html.Span("\u2014 ", style={"color": "#999"})
        # pending
        return html.Span("\u25cb ", style={"color": "#bbb"})

    def _build_steps_ui(job):
        current_step = job.get("step")
        status = job.get("status", "pending")
        rows = []
        for s in STEPS:
            if status == "cancelled":
                state = "done" if _step_before(s, current_step) else "cancelled"
            elif status == "error":
                if s == current_step:
                    state = "error"
                elif _step_before(s, current_step):
                    state = "done"
                else:
                    state = "pending"
            elif s == current_step:
                state = "active" if status == "running" else "done"
            elif _step_before(s, current_step):
                state = "done"
            else:
                state = "pending"
            label_style = {"color": "#333"} if state in ("active", "done") else {"color": "#999"}
            rows.append(
                html.Div(
                    [_step_icon(state), html.Span(STEP_LABELS[s], style=label_style)],
                    style={"fontSize": "12px", "padding": "2px 0", "display": "flex", "alignItems": "center"},
                )
            )
        return rows

    def _step_before(a, b):
        """True if step *a* comes before step *b* in the pipeline."""
        if b is None or b == "done":
            return a in STEPS
        try:
            return STEPS.index(a) < STEPS.index(b)
        except ValueError:
            return False

    def _progress_pct(job):
        step = job.get("step")
        status = job.get("status")
        if status == "done" or step == "done":
            return 100
        if step is None:
            return 0
        try:
            idx = STEPS.index(step)
        except ValueError:
            return 0
        return int((idx / len(STEPS)) * 100)

    @app.callback(
        Output("route-progress-steps", "children"),
        Output("route-progress-bar", "style"),
        Output("route-progress-message", "children"),
        Output("route-progress-cancel-btn", "children"),
        Output("route-progress-interval", "disabled", allow_duplicate=True),
        Output("route-progress-panel", "style", allow_duplicate=True),
        Output("route-sidebar-status", "children", allow_duplicate=True),
        Output("route-geojson", "data", allow_duplicate=True),
        Output("route-result-store", "data", allow_duplicate=True),
        Output("route-info-panel", "style", allow_duplicate=True),
        Input("route-progress-interval", "n_intervals"),
        State("route-job-store", "data"),
        prevent_initial_call=True,
    )
    def poll_route_progress(n_intervals, job_id):
        if not job_id:
            raise PreventUpdate

        job = get_job(job_id)
        if job is None:
            raise PreventUpdate

        status = job.get("status", "pending")
        steps_ui = _build_steps_ui(job)
        pct = _progress_pct(job)

        bar_color = "#1976D2"
        if status == "error":
            bar_color = "#c62828"
        elif status == "cancelled":
            bar_color = "#999"
        elif status == "done":
            bar_color = "#2e7d32"

        bar_style = {
            "height": "100%",
            "width": f"{pct}%",
            "backgroundColor": bar_color,
            "borderRadius": "2px",
            "transition": "width 0.3s ease",
        }

        # Route outputs — only populated on completion
        route_geojson = no_update
        route_data = no_update
        route_info_style = no_update

        if status == "done":
            msg = "Route computation complete."
            btn_label = "Close"
            sidebar = "Route computation complete."
            route_geojson = job.get("route_geojson") or EMPTY_GEOJSON
            route_data = {"summary": job.get("route_summary") or {}}
            route_info_style = _ROUTE_INFO_VISIBLE
        elif status == "error":
            msg = f"Error: {job.get('error', 'unknown')}"
            btn_label = "Close"
            sidebar = f"Route failed: {job.get('error', 'unknown')}"
        elif status == "cancelled":
            msg = "Cancelled — files cleaned up."
            btn_label = "Close"
            sidebar = "Route computation cancelled."
        else:
            step_label = STEP_LABELS.get(job.get("step"), "Starting...")
            msg = f"{step_label}..."
            btn_label = "Cancel"
            sidebar = no_update

        # Stop polling when terminal
        still_running = status in ("pending", "running")

        return (
            steps_ui,
            bar_style,
            msg,
            btn_label,
            not still_running,   # disabled=True when done
            no_update if still_running else _PROGRESS_VISIBLE,
            sidebar,
            route_geojson,
            route_data,
            route_info_style,
        )

    # ------------------------------------------------------------------ #
    #  Cancel / Close button                                              #
    # ------------------------------------------------------------------ #

    @app.callback(
        Output("route-progress-panel", "style", allow_duplicate=True),
        Output("route-progress-interval", "disabled", allow_duplicate=True),
        Output("route-job-store", "data", allow_duplicate=True),
        Output("route-sidebar-status", "children", allow_duplicate=True),
        Input("route-progress-cancel-btn", "n_clicks"),
        State("route-job-store", "data"),
        prevent_initial_call=True,
    )
    def handle_cancel_or_close(n_clicks, job_id):
        if not job_id:
            raise PreventUpdate

        job = get_job(job_id)
        if job is None:
            return _PROGRESS_HIDDEN, True, None, no_update

        status = job.get("status", "pending")

        if status in ("done", "error", "cancelled"):
            # Terminal — just close the panel
            remove_job(job_id)
            return _PROGRESS_HIDDEN, True, None, no_update

        # Still running — cancel it
        cancel_job(job_id)
        return no_update, no_update, no_update, "Cancelling..."

    # ---- VISIR-2 environment status ----
    _STATUS_BASE = {
        "padding": "8px 12px",
        "borderRadius": "4px",
        "fontSize": "13px",
        "marginBottom": "12px",
    }
    _STATUS_STYLES = {
        "ready": {
            **_STATUS_BASE,
            "backgroundColor": "#e8f5e9",
            "color": "#2e7d32",
            "border": "1px solid #c8e6c9",
        },
        "starting": {
            **_STATUS_BASE,
            "backgroundColor": "#fff3e0",
            "color": "#e65100",
            "border": "1px solid #ffe0b2",
        },
        "unavailable": {
            **_STATUS_BASE,
            "backgroundColor": "#ffebee",
            "color": "#c62828",
            "border": "1px solid #ffcdd2",
        },
    }

    @app.callback(
        Output("visir-env-status", "children"),
        Output("visir-env-status", "style"),
        Input("visir-health-interval", "n_intervals"),
    )
    def update_visir_status(n_intervals):
        health = check_health()
        status = health["status"]
        msg = health["message"]
        icon = {
            "ready": "\u25cf ",       # filled circle
            "starting": "\u25cb ",    # hollow circle
            "unavailable": "\u25cf ", # filled circle
        }.get(status, "")
        return icon + msg, _STATUS_STYLES.get(status, _STATUS_BASE)

    # ------------------------------------------------------------------ #
    #  Route info panel (populated when route-result-store changes)       #
    # ------------------------------------------------------------------ #

    _ROUTE_COLORS = {"dist": "#1E90FF", "time": "#e53935", "CO2t": "#2e7d32"}
    _ROUTE_ORDER = ["dist", "time", "CO2t"]

    @app.callback(
        Output("route-info-content", "children"),
        Input("route-result-store", "data"),
        prevent_initial_call=True,
    )
    def update_route_info(route_data):
        if not route_data or not route_data.get("summary"):
            return []

        summary = route_data["summary"]
        rows = []

        for rt in _ROUTE_ORDER:
            if rt not in summary:
                continue
            s = summary[rt]
            if "error" in s:
                continue
            rows.append(
                html.Div(
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "gap": "8px",
                        "marginBottom": "6px",
                    },
                    children=[
                        html.Div(
                            style={
                                "width": "14px",
                                "height": "4px",
                                "backgroundColor": _ROUTE_COLORS.get(rt, "#333"),
                                "borderRadius": "1px",
                                "flexShrink": "0",
                            },
                        ),
                        html.Div([
                            html.Div(
                                s.get("label", rt),
                                style={"fontWeight": "bold", "fontSize": "12px"},
                            ),
                            html.Div(
                                f"{s['distance']} nmi  \u00b7  {s['duration']} hrs  \u00b7  {s['co2']} t CO\u2082",
                                style={"fontSize": "11px", "color": "#666"},
                            ),
                        ]),
                    ],
                )
            )
        return rows

    # ------------------------------------------------------------------ #
    #  Clear routes from the map                                          #
    # ------------------------------------------------------------------ #

    @app.callback(
        Output("route-geojson", "data", allow_duplicate=True),
        Output("route-result-store", "data", allow_duplicate=True),
        Output("route-info-panel", "style", allow_duplicate=True),
        Output("route-historical-store", "data", allow_duplicate=True),
        Input("route-clear-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_routes(n_clicks):
        return EMPTY_GEOJSON, None, _ROUTE_INFO_HIDDEN, None

    # ------------------------------------------------------------------ #
    #  Saved routes — list, load, delete                                  #
    # ------------------------------------------------------------------ #

    _VESSEL_LABELS = {v["value"]: v["label"] for v in VESSEL_OPTIONS}

    def _format_origin(params):
        if params.get("origin_type") == "coords":
            c = params.get("origin_coords") or {}
            lat = c.get("lat")
            lon = c.get("lon")
            if lat is None or lon is None:
                return "coordinates"
            return f"{float(lat):.3f}, {float(lon):.3f}"
        code = params.get("origin_port")
        return PORT_LABELS.get(code, code or "?")

    def _format_destination(params):
        code = params.get("destination_port")
        return PORT_LABELS.get(code, code or "?")

    def _format_saved_at(iso_str):
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return iso_str

    def _format_departure(iso_str):
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return iso_str

    def _saved_route_row(entry):
        route_id = entry["id"]
        params = entry.get("params") or {}
        summary = entry.get("summary") or {}

        origin = _format_origin(params)
        dest = _format_destination(params)
        vessel_label = _VESSEL_LABELS.get(params.get("vessel"), params.get("vessel", ""))
        departure = _format_departure(params.get("departure_datetime"))
        saved_at = _format_saved_at(entry.get("savedAt"))

        metric_rows = []
        for rt in ("dist", "time", "CO2t"):
            s = summary.get(rt)
            if not s or "error" in s:
                continue
            metric_rows.append(
                html.Div(
                    f"{s.get('label', rt)}: "
                    f"{s.get('distance', 0)} nmi \u00b7 "
                    f"{s.get('duration', 0)} hrs \u00b7 "
                    f"{s.get('co2', 0)} t CO\u2082",
                    style={"fontSize": "11px", "color": "#555"},
                )
            )

        return html.Div(
            style={
                "border": "1px solid #e0e0e0",
                "borderRadius": "4px",
                "padding": "10px 12px",
                "marginBottom": "8px",
                "backgroundColor": "#fafafa",
            },
            children=[
                html.Div(
                    f"{origin}  \u2192  {dest}",
                    style={"fontWeight": "bold", "fontSize": "13px", "marginBottom": "2px"},
                ),
                html.Div(
                    f"Vessel: {vessel_label}",
                    style={"fontSize": "11px", "color": "#666"},
                ),
                html.Div(
                    f"Departure: {departure}",
                    style={"fontSize": "11px", "color": "#666"},
                ),
                html.Div(
                    f"Saved: {saved_at}",
                    style={"fontSize": "11px", "color": "#888", "marginBottom": "6px"},
                ),
                *metric_rows,
                html.Div(
                    style={"display": "flex", "gap": "8px", "marginTop": "8px"},
                    children=[
                        html.Button(
                            "View on map",
                            id={"type": "load-saved-route", "index": route_id},
                            n_clicks=0,
                            style={
                                "padding": "4px 10px", "fontSize": "12px",
                                "backgroundColor": "#1976D2", "color": "white",
                                "border": "none", "borderRadius": "3px",
                                "cursor": "pointer",
                            },
                        ),
                        html.Button(
                            "Delete",
                            id={"type": "delete-saved-route", "index": route_id},
                            n_clicks=0,
                            style={
                                "padding": "4px 10px", "fontSize": "12px",
                                "backgroundColor": "#e0e0e0", "color": "#444",
                                "border": "none", "borderRadius": "3px",
                                "cursor": "pointer",
                            },
                        ),
                    ],
                ),
            ],
        )

    @app.callback(
        Output("saved-routes-list", "children"),
        Input("route-modal-tabs", "value"),
        Input("route-open-btn", "n_clicks"),
        Input("route-result-store", "data"),
        Input("saved-routes-version", "data"),
    )
    def populate_saved_routes(tab, open_clicks, route_result, version):
        entries = list_saved_routes()
        if not entries:
            return html.Div(
                "No saved routes yet. Compute a route and it will appear here.",
                style={"fontSize": "12px", "color": "#888", "padding": "12px 0"},
            )
        return [_saved_route_row(e) for e in entries]

    @app.callback(
        Output("route-geojson", "data", allow_duplicate=True),
        Output("route-result-store", "data", allow_duplicate=True),
        Output("route-info-panel", "style", allow_duplicate=True),
        Output("route-modal", "style", allow_duplicate=True),
        Output("route-historical-store", "data", allow_duplicate=True),
        Output("route-sidebar-status", "children", allow_duplicate=True),
        Input({"type": "load-saved-route", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def on_load_saved_route(n_clicks_list):
        if not n_clicks_list or not any(c for c in n_clicks_list if c):
            raise PreventUpdate
        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict) or triggered.get("type") != "load-saved-route":
            raise PreventUpdate
        route_id = triggered["index"]
        data = load_saved_route(route_id)
        if not data:
            raise PreventUpdate
        historical = {
            "id": route_id,
            "savedAt": data.get("savedAt"),
            "params": data.get("params") or {},
        }
        return (
            data.get("geojson") or EMPTY_GEOJSON,
            {"summary": data.get("summary") or {}},
            _ROUTE_INFO_VISIBLE,
            _MODAL_HIDDEN,
            historical,
            "Viewing saved route.",
        )

    @app.callback(
        Output("saved-routes-version", "data"),
        Input({"type": "delete-saved-route", "index": ALL}, "n_clicks"),
        State("saved-routes-version", "data"),
        prevent_initial_call=True,
    )
    def on_delete_saved_route(n_clicks_list, version):
        if not n_clicks_list or not any(c for c in n_clicks_list if c):
            raise PreventUpdate
        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict) or triggered.get("type") != "delete-saved-route":
            raise PreventUpdate
        delete_saved_route(triggered["index"])
        return (version or 0) + 1

    @app.callback(
        Output("route-historical-banner", "style"),
        Output("route-historical-details", "children"),
        Input("route-historical-store", "data"),
    )
    def toggle_historical_banner(historical):
        if not historical:
            return {"display": "none"}, ""
        params = historical.get("params") or {}
        origin = _format_origin(params)
        dest = _format_destination(params)
        vessel_label = _VESSEL_LABELS.get(params.get("vessel"), params.get("vessel", ""))
        departure = _format_departure(params.get("departure_datetime"))
        saved_at = _format_saved_at(historical.get("savedAt"))
        details = [
            html.Div(f"{origin} \u2192 {dest}", style={"fontWeight": "bold"}),
            html.Div(f"Vessel: {vessel_label}"),
            html.Div(f"Departure: {departure}"),
            html.Div(f"Computed: {saved_at}"),
        ]
        return {"display": "block"}, details

    # ------------------------------------------------------------------ #
    #  Spatial evaluation panel                                           #
    # ------------------------------------------------------------------ #

    @app.callback(
        Output("evaluation-panel", "style"),
        Input("evaluation-open-btn", "n_clicks"),
        State("evaluation-panel", "style"),
        prevent_initial_call=True,
    )
    def toggle_evaluation_panel(n_clicks, current_style):
        visible = (current_style or {}).get("display") != "none"
        return {"display": "none" if visible else "block", "marginTop": "8px"}

    _BADGE_PASS = {
        "fontSize": "11px", "padding": "2px 8px", "borderRadius": "10px",
        "backgroundColor": "#e8f5e9", "color": "#2e7d32",
    }
    _BADGE_FAIL = {
        "fontSize": "11px", "padding": "2px 8px", "borderRadius": "10px",
        "backgroundColor": "#ffebee", "color": "#c62828",
    }
    _BADGE_SKIP = {
        "fontSize": "11px", "padding": "2px 8px", "borderRadius": "10px",
        "backgroundColor": "#eeeeee", "color": "#555",
    }

    @app.callback(
        Output("evaluation-results-store", "data"),
        Output({"type": "eval-badge", "test": ALL}, "children"),
        Output({"type": "eval-badge", "test": ALL}, "style"),
        Input("evaluation-run-btn", "n_clicks"),
        State({"type": "eval-toggle", "test": ALL}, "value"),
        State({"type": "eval-toggle", "test": ALL}, "id"),
        prevent_initial_call=True,
    )
    def run_evaluation(n_clicks, toggle_values, toggle_ids):
        enabled = []
        for value, ident in zip(toggle_values or [], toggle_ids or []):
            tid = ident.get("test")
            if value and tid in value:
                enabled.append(tid)

        results = spatial_evaluation.run_all(enabled) if enabled else {}

        badge_text = []
        badge_style = []
        for ident in toggle_ids or []:
            tid = ident.get("test")
            res = results.get(tid)
            if res is None:
                badge_text.append("skipped")
                badge_style.append(_BADGE_SKIP)
            elif res["passed"]:
                badge_text.append(f"PASS - {res['metric']}")
                badge_style.append(_BADGE_PASS)
            else:
                badge_text.append(f"FAIL - {res['metric']}")
                badge_style.append(_BADGE_FAIL)

        return results, badge_text, badge_style

    @app.callback(
        Output("evaluation-geojson", "data"),
        Input({"type": "eval-show", "test": ALL}, "n_clicks"),
        Input("evaluation-clear-btn", "n_clicks"),
        State("evaluation-results-store", "data"),
        prevent_initial_call=True,
    )
    def show_evaluation_on_map(show_clicks, clear_clicks, results):
        triggered = getattr(ctx, "triggered_id", None)
        if triggered == "evaluation-clear-btn":
            return EMPTY_GEOJSON
        if not isinstance(triggered, dict) or triggered.get("type") != "eval-show":
            raise PreventUpdate
        if not show_clicks or not any(c for c in show_clicks if c):
            raise PreventUpdate
        tid = triggered.get("test")
        res = (results or {}).get(tid)
        if not res:
            raise PreventUpdate
        return {"type": "FeatureCollection", "features": res.get("features", [])}

    # ------------------------------------------------------------------ #
    #  System-robustness panel                                            #
    # ------------------------------------------------------------------ #

    @app.callback(
        Output("rob-results-store", "data"),
        Output({"type": "rob-badge", "test": ALL}, "children"),
        Output({"type": "rob-badge", "test": ALL}, "style"),
        Input("rob-run-btn", "n_clicks"),
        State({"type": "rob-toggle", "test": ALL}, "value"),
        State({"type": "rob-toggle", "test": ALL}, "id"),
        prevent_initial_call=True,
    )
    def run_robustness(n_clicks, toggle_values, toggle_ids):
        enabled = []
        for value, ident in zip(toggle_values or [], toggle_ids or []):
            tid = ident.get("test")
            if value and tid in value:
                enabled.append(tid)

        results = robustness_evaluation.run_all(enabled) if enabled else {}

        badge_text = []
        badge_style = []
        for ident in toggle_ids or []:
            tid = ident.get("test")
            res = results.get(tid)
            if res is None:
                badge_text.append("skipped")
                badge_style.append(_BADGE_SKIP)
            elif res["passed"]:
                badge_text.append(f"PASS - {res['metric']}")
                badge_style.append(_BADGE_PASS)
            else:
                badge_text.append(f"FAIL - {res['metric']}")
                badge_style.append(_BADGE_FAIL)

        return results, badge_text, badge_style

    @app.callback(
        Output("rob-details", "children"),
        Input({"type": "rob-show", "test": ALL}, "n_clicks"),
        Input("rob-clear-btn", "n_clicks"),
        State("rob-results-store", "data"),
        prevent_initial_call=True,
    )
    def show_robustness_details(show_clicks, clear_clicks, results):
        triggered = getattr(ctx, "triggered_id", None)
        if triggered == "rob-clear-btn":
            return "No details to show. Run a test, then click 'Show details'."
        if not isinstance(triggered, dict) or triggered.get("type") != "rob-show":
            raise PreventUpdate
        if not show_clicks or not any(c for c in show_clicks if c):
            raise PreventUpdate
        tid = triggered.get("test")
        res = (results or {}).get(tid)
        if not res:
            return f"No result yet for {tid}. Click 'Run' first."
        return _render_rob_details(tid, res)
