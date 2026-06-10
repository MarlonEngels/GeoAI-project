from dash import html, dcc
import dash_leaflet as dl
from dash_extensions import EventListener
from dash_extensions.javascript import assign
from config import UPDATE_INTERVAL_MS, CENTER, ZOOM
from src.utils.visir_layers import load_shoreline_geojson, load_bathymetry_geojson
from src.utils.spatial_evaluation import TESTS as EVAL_TESTS
from src.utils.robustness_evaluation import TESTS as ROB_TESTS
import csv
import os

empty_geojson = {"type": "FeatureCollection", "features": []}

# ---------------------------------------------------------------------------
# Port & vessel data for route computation
# ---------------------------------------------------------------------------
_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "visir-2-code", "scandinavia_port_codes.csv"
)
ALL_PORT_OPTIONS = []
NORWAY_PORT_OPTIONS = []
try:
    with open(_CSV_PATH, newline="", encoding="utf-8") as _f:
        for _row in csv.DictReader(_f):
            _code = _row["harb_code"].strip()
            _name = (_row.get("name_HR") or "").strip()
            _label = f"{_name} ({_code})" if _name else _code
            _entry = {"label": _label, "value": _code}
            ALL_PORT_OPTIONS.append(_entry)
            if _code.startswith("NO"):
                NORWAY_PORT_OPTIONS.append(_entry)
    ALL_PORT_OPTIONS.sort(key=lambda x: x["label"])
    NORWAY_PORT_OPTIONS.sort(key=lambda x: x["label"])
except FileNotFoundError:
    pass

PORT_LABELS = {p["value"]: p["label"] for p in ALL_PORT_OPTIONS}

# Vessel options (mirrors VISIR-2_v6/__namelist/Navi/)
VESSEL_OPTIONS = [
    {"label": "Ferry (unizd)", "value": "unizd_Ferry"},
    {"label": "First 367 (unige)", "value": "unige_First_367"},
    {"label": "J24 (unige)", "value": "unige_J24"},
    {"label": "Swan 60FD (unige)", "value": "unige_Swan_60FD"},
    {"label": "First 367 (VISIR1)", "value": "VISIR1_First367"},
    {"label": "Marco Polo (VISIR1)", "value": "VISIR1_MarkoPolo"},
    {"label": "MIT (VISIR1)", "value": "VISIR1_MIT"},
]

# ---------------------------------------------------------------------------
# JS point-to-layer / style functions
# ---------------------------------------------------------------------------

WEATHER_POINT_TO_LAYER = assign(
    """
    function(feature, latlng, context) {
        const p = feature.properties || {};

        const airTemp = (p.air_temperature !== undefined && p.air_temperature !== null) ? p.air_temperature : "?";
        const windSpeed = (p.wind_speed !== undefined && p.wind_speed !== null) ? p.wind_speed : "?";
        const relHum = (p.relative_humidity !== undefined && p.relative_humidity !== null) ? p.relative_humidity : "?";
        const airPressure = (p.air_pressure_at_sea_level !== undefined && p.air_pressure_at_sea_level !== null) ? p.air_pressure_at_sea_level : "?";
        const cloudAreaFraction = (p.cloud_area_fraction !== undefined && p.cloud_area_fraction !== null) ? p.cloud_area_fraction : "?";

        const windFromDirDeg =
            (p.wind_from_direction !== undefined && p.wind_from_direction !== null)
                ? p.wind_from_direction
                : null;

        function degToCompass(deg) {
            if (deg === null || isNaN(deg)) return "?";

            const directions = [
                "N", "NNE", "NE", "ENE",
                "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW",
                "W", "WNW", "NW", "NNW"
            ];

            const normalized = ((deg % 360) + 360) % 360;
            const index = Math.round(normalized / 22.5) % 16;

            return directions[index];
        }

        const windFromDirText = degToCompass(windFromDirDeg);
        const windFromDirDisplay =
            windFromDirDeg !== null
                ? windFromDirText + " (" + Math.round(windFromDirDeg) + "&#176;)"
                : "?";
        const weatherId = p.weather_id || "";

        var popup = "<b>Weather station</b><br>" +
            "Temp: " + airTemp + " &#176;C<br>" +
            "Wind: " + windSpeed + " m/s<br>" +
            "Humidity: " + relHum + " %<br>" +
            "Pressure: " + airPressure + " hPa<br>" +
            "Cloud cover: " + cloudAreaFraction + " %<br>" +
            "Wind direction: " + windFromDirDisplay +
            "<br><button type='button' class='weather-remove-btn' data-weather-id='" + weatherId + "'" +
            " style='margin-top:6px;padding:2px 6px;cursor:pointer;' " +
            "onclick='event.stopPropagation();'>" +
            "Remove</button>";

        return L.circleMarker(latlng, {
            radius: 6,
            fillColor: "red",
            color: "black",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.85,
            bubblingMouseEvents: false
        }).bindPopup(popup);
    }
    """
)

AIS_POINT_TO_LAYER = assign(
    """
    function(feature, latlng, context) {
        const p = feature.properties || {};

        const name = p.ship_name || "Unknown vessel";
        const mmsi = p.mmsi || "?";
        const speed = (p.speed !== undefined && p.speed !== null) ? p.speed : "?";
        const cog = (p.cog !== undefined && p.cog !== null) ? p.cog : "?";
        const heading = (p.true_heading !== undefined && p.true_heading !== null)
                        ? p.true_heading
                        : (p.cog || 0);
        const destination = p.destination || "Unknown";
        const ais_class = p.ais_class || "Unknown";
        const draught = p.draught || "Unknown";
        const last_update = (() => {
        const s = p.date_time_utc;
        if (!s) return "Unknown";

        // Accept: "YYYY-MM-DDTHH:MM:SS", "YYYY-MM-DD HH:MM:SS", with/without trailing Z
        const m = String(s).trim().match(
            /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z)?$/
        );
        if (!m) return String(s); // fallback: show raw value

        const year = Number(m[1]);
        const mon  = Number(m[2]);
        const day  = Number(m[3]);
        const hour = Number(m[4]);
        const min  = Number(m[5]);
        const sec  = Number(m[6]);

        // Build a UTC timestamp explicitly, then add +1h for CET
        const ms = Date.UTC(year, mon - 1, day, hour, min, sec) + 60 * 60 * 1000;
        const d = new Date(ms);

        const pad = (x) => String(x).padStart(2, "0");
        return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
                `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} CET`;
        })();

        const arrow = "&#129033;";

        const iconHtml =
            '<div style="transform: rotate(' + heading + 'deg);' +
                        'transform-origin: center center;' +
                        'font-size: 18px;' +
                        'color: blue;' +
                        'line-height: 18px;">' +
                arrow +
            '</div>';

        const icon = L.divIcon({
            html: iconHtml,
            className: "",
            iconSize: [18, 18],
            iconAnchor: [9, 9]
        });

        var popup = "<b>Vessel details</b><br>" +
                    "Name: " + name + "<br>" +
                    "MMSI: " + mmsi + "<br>" +
                    "Speed: " + speed + " kn<br>" +
                    "COG: " + cog + "&#176;<br>" +
                    "Destination: " + destination + "<br>" +
                    "Heading: " + heading + "&#176;<br>" +
                    "AIS Class: " + ais_class + "<br>" +
                    "Draught: " + draught + "<br>" +
                    "Last update: " + last_update +
                    "<br><button type='button' class='route-compute-btn' " +
                    "data-vessel-lat='" + latlng.lat + "' " +
                    "data-vessel-lon='" + latlng.lng + "' " +
                    "data-vessel-name='" + name.replace(/'/g, "&#39;") + "' " +
                    "style='margin-top:6px;padding:4px 10px;cursor:pointer;" +
                    "background:#1976D2;color:white;border:none;border-radius:3px;font-size:12px;' " +
                    "onclick='event.stopPropagation();'>" +
                    "Compute route</button>";

        const marker = L.marker(latlng, {icon: icon});
        marker.bindPopup(popup);
        return marker;
    }
    """
)

DENSITY_STYLE = assign(
    """
    function(feature, context) {
        const c = (feature.properties && feature.properties.count) ? feature.properties.count : 0;

        const h = context.hideout || {};
        const t1 = h.t1 ?? 1;
        const t2 = h.t2 ?? 2;
        const t3 = h.t3 ?? 3;

        let fill = "green";
        if (c >= t3) fill = "red";
        else if (c >= t2) fill = "orange";
        else if (c >= t1) fill = "yellow";

        return {
            color: "black",
            weight: 0.5,
            fillColor: fill,
            fillOpacity: 0.45
        };
    }
    """
)

# ---------------------------------------------------------------------------
# Route visualisation JS functions
# ---------------------------------------------------------------------------

ROUTE_STYLE = assign(
    """
    function(feature, context) {
        const rt = feature.properties.routeType;
        const colors = {"dist": "#1E90FF", "time": "#e53935", "CO2t": "#2e7d32"};
        const dashes = {"dist": "4 8", "time": "12 8"};
        return {
            color: colors[rt] || "#333",
            weight: 3.5,
            opacity: 0.9,
            dashArray: dashes[rt] || null
        };
    }
    """
)

ROUTE_POINT_TO_LAYER = assign(
    """
    function(feature, latlng, context) {
        const mt = feature.properties.markerType;
        if (mt === "start") {
            return L.marker(latlng, {
                icon: L.divIcon({
                    html: '<div style="font-size:22px;line-height:22px;">&#11088;</div>',
                    className: '',
                    iconSize: [22, 22],
                    iconAnchor: [11, 11]
                })
            }).bindTooltip("Departure");
        }
        return L.circleMarker(latlng, {
            radius: 7,
            fillColor: "#e53935",
            color: "white",
            weight: 2,
            fillOpacity: 1
        }).bindTooltip("Arrival");
    }
    """
)

SHORELINE_STYLE = assign(
    """
    function(feature, context) {
        return {
            color: "#e60000",
            weight: 2.5,
            opacity: 0.9,
            fill: false
        };
    }
    """
)

BATHY_STYLE = assign(
    """
    function(feature, context) {
        var d = feature.properties.depth;
        var color = "#b3d9ff";
        if (d <= -500) color = "#003366";
        else if (d <= -300) color = "#005599";
        else if (d <= -200) color = "#0077cc";
        else if (d <= -100) color = "#3399dd";
        else if (d <= -50) color = "#66b3e6";
        else if (d <= -10) color = "#99ccee";

        return {
            color: color,
            weight: 1.2,
            opacity: 0.7,
            fill: false
        };
    }
    """
)

BATHY_ON_EACH_FEATURE = assign(
    """
    function(feature, layer) {
        var d = feature.properties.depth;
        layer.bindTooltip(d + " m", {sticky: true, className: "bathy-tooltip"});
    }
    """
)

ROUTE_ON_EACH_FEATURE = assign(
    """
    function(feature, layer) {
        var p = feature.properties;
        if (p.routeType && feature.geometry.type === "LineString") {
            var labels = {"dist": "Shortest Distance", "time": "Fastest Time", "CO2t": "Lowest CO2"};
            layer.bindTooltip(
                "<b>" + (labels[p.routeType] || p.routeType) + "</b><br>" +
                "Distance: " + p.distance + " nmi<br>" +
                "Duration: " + p.duration + " hrs<br>" +
                "CO2: " + p.co2 + " t",
                {sticky: true}
            );
        }
    }
    """
)

# ---------------------------------------------------------------------------
# Spatial evaluation layer styling
# ---------------------------------------------------------------------------
def _eval_color_js():
    # green pass, red fail, orange undetermined
    return (
        "var p = feature.properties || {};"
        "var c = '#fb8c00';"
        "if (p.pass === true) c = '#2e7d32';"
        "else if (p.pass === false) c = '#c62828';"
    )

EVAL_STYLE = assign(
    "function(feature) {"
    + _eval_color_js()
    + "var t = feature.geometry && feature.geometry.type;"
    "if (t === 'Polygon' || t === 'MultiPolygon') {"
    "    return {color: c, weight: 2, fillColor: c, fillOpacity: 0.08, dashArray: '4 4'};"
    "}"
    "if (t === 'LineString') { return {color: c, weight: 2, opacity: 0.9}; }"
    "return {color: c, weight: 2};"
    "}"
)

EVAL_POINT_TO_LAYER = assign(
    "function(feature, latlng) {"
    + _eval_color_js()
    + "return L.circleMarker(latlng, {radius: 6, color: c, fillColor: c, fillOpacity: 0.75, weight: 2});"
    "}"
)

EVAL_ON_EACH_FEATURE = assign(
    "function(feature, layer) {"
    "var p = feature.properties || {};"
    "if (p.tooltip) { layer.bindTooltip(p.tooltip, {sticky: true}); }"
    "}"
)

# ---------------------------------------------------------------------------
# Route progress panel  (bottom of sidebar)
# ---------------------------------------------------------------------------
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

_STEP_ROW = {"fontSize": "12px", "padding": "2px 0", "display": "flex", "alignItems": "center"}

route_progress_panel = html.Div(
    id="route-progress-panel",
    style=_PROGRESS_HIDDEN,
    children=[
        html.Div(
            "Route computation",
            style={"fontWeight": "bold", "fontSize": "13px", "marginBottom": "6px"},
        ),
        html.Div(id="route-progress-steps", children=[]),
        # Progress bar track
        html.Div(
            style={
                "height": "4px",
                "backgroundColor": "#e0e0e0",
                "borderRadius": "2px",
                "marginTop": "8px",
                "overflow": "hidden",
            },
            children=[
                html.Div(
                    id="route-progress-bar",
                    style={
                        "height": "100%",
                        "width": "0%",
                        "backgroundColor": "#1976D2",
                        "borderRadius": "2px",
                        "transition": "width 0.3s ease",
                    },
                ),
            ],
        ),
        html.Div(
            id="route-progress-message",
            style={"fontSize": "11px", "color": "#888", "marginTop": "4px"},
        ),
        html.Div(
            style={"textAlign": "right", "marginTop": "6px"},
            children=[
                html.Button(
                    "Cancel",
                    id="route-progress-cancel-btn",
                    n_clicks=0,
                    style={
                        "padding": "4px 14px",
                        "fontSize": "12px",
                        "backgroundColor": "#e0e0e0",
                        "border": "none",
                        "borderRadius": "3px",
                        "cursor": "pointer",
                    },
                ),
            ],
        ),
        dcc.Interval(id="route-progress-interval", interval=800, disabled=True),
        dcc.Store(id="route-job-store", data=None),
    ],
)

# ---------------------------------------------------------------------------
# Route computation modal
# ---------------------------------------------------------------------------
_HELP = {"fontSize": "11px", "color": "#888", "marginTop": "2px", "marginBottom": "8px"}

route_modal = html.Div(
    id="route-modal",
    style={
        "position": "fixed",
        "top": 0, "left": 0, "right": 0, "bottom": 0,
        "backgroundColor": "rgba(0,0,0,0.5)",
        "zIndex": 2000,
        "display": "none",
        "justifyContent": "center",
        "alignItems": "center",
    },
    children=[
        html.Div(
            style={
                "backgroundColor": "white",
                "borderRadius": "8px",
                "padding": "24px",
                "width": "540px",
                "maxHeight": "90vh",
                "overflowY": "auto",
                "boxShadow": "0 4px 20px rgba(0,0,0,0.3)",
            },
            children=[
                # ---- Header ----
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"},
                    children=[
                        html.H3("Compute Ship Route", style={"margin": 0}),
                        html.Button(
                            "X", id="route-close-btn", n_clicks=0,
                            style={
                                "background": "none", "border": "none", "fontSize": "20px",
                                "cursor": "pointer", "color": "#888", "padding": "0 4px",
                            },
                        ),
                    ],
                ),
                html.Hr(),

                dcc.Tabs(
                    id="route-modal-tabs",
                    value="new",
                    children=[
                        dcc.Tab(label="New Route", value="new", children=[html.Div(style={"paddingTop": "12px"}, children=[

                # ---- VISIR-2 environment status ----
                html.Div(
                    id="visir-env-status",
                    style={
                        "padding": "8px 12px",
                        "borderRadius": "4px",
                        "fontSize": "13px",
                        "marginBottom": "12px",
                        "backgroundColor": "#fff3e0",
                        "color": "#e65100",
                        "border": "1px solid #ffe0b2",
                    },
                    children="Checking VISIR-2 service...",
                ),

                # ---- Origin ----
                html.H4("Origin", style={"marginBottom": "6px", "marginTop": "0"}),
                dcc.RadioItems(
                    id="route-origin-type",
                    options=[
                        {"label": " Coordinates", "value": "coords"},
                        {"label": " Port code", "value": "portCodes"},
                    ],
                    value="coords",
                    inline=True,
                    style={"marginBottom": "8px"},
                ),
                html.Div(
                    id="route-coords-div",
                    children=[
                        html.Div(
                            style={"display": "flex", "gap": "12px"},
                            children=[
                                html.Div([
                                    html.Label("Latitude:", style={"fontSize": "12px"}),
                                    dcc.Input(
                                        id="route-origin-lat", type="number",
                                        step=0.0001, placeholder="e.g. 59.91",
                                        style={"width": "100%"},
                                    ),
                                ], style={"flex": 1}),
                                html.Div([
                                    html.Label("Longitude:", style={"fontSize": "12px"}),
                                    dcc.Input(
                                        id="route-origin-lon", type="number",
                                        step=0.0001, placeholder="e.g. 10.75",
                                        style={"width": "100%"},
                                    ),
                                ], style={"flex": 1}),
                            ],
                        ),
                        html.Div(
                            "Tip: click 'Compute route' on a vessel popup to auto-fill.",
                            style=_HELP,
                        ),
                    ],
                ),
                html.Div(
                    id="route-port-div",
                    style={"display": "none"},
                    children=[
                        html.Label("Origin port:", style={"fontSize": "12px"}),
                        dcc.Dropdown(
                            id="route-origin-port",
                            options=ALL_PORT_OPTIONS,
                            placeholder="Select origin port...",
                            searchable=True,
                        ),
                    ],
                ),
                html.Hr(),

                # ---- Destination ----
                html.H4("Destination", style={"marginBottom": "6px", "marginTop": "0"}),
                html.Label("Norwegian harbour:", style={"fontSize": "12px"}),
                dcc.Dropdown(
                    id="route-dest-port",
                    options=NORWAY_PORT_OPTIONS,
                    placeholder="Select destination harbour...",
                    searchable=True,
                ),
                html.Hr(),

                # ---- Vessel ----
                html.H4("Vessel type", style={"marginBottom": "6px", "marginTop": "0"}),
                dcc.Dropdown(
                    id="route-vessel",
                    options=VESSEL_OPTIONS,
                    value="unizd_Ferry",
                    placeholder="Select vessel type...",
                ),
                html.Hr(),

                # ---- Departure ----
                html.H4("Departure", style={"marginBottom": "6px", "marginTop": "0"}),
                dcc.Checklist(
                    id="route-depart-now",
                    options=[{"label": " Depart now", "value": "now"}],
                    value=[],
                    inline=True,
                    style={"marginBottom": "8px"},
                ),
                html.Div(
                    id="route-depart-fields",
                    children=[
                        html.Div(
                            style={"display": "flex", "gap": "12px", "alignItems": "flex-end"},
                            children=[
                                html.Div([
                                    html.Label("Date:", style={"fontSize": "12px"}),
                                    dcc.DatePickerSingle(
                                        id="route-depart-date", placeholder="Select date",
                                    ),
                                ], style={"flex": 1}),
                                html.Div([
                                    html.Label("Time (UTC):", style={"fontSize": "12px"}),
                                    dcc.Input(
                                        id="route-depart-time", type="text",
                                        value="12:00", placeholder="HH:MM",
                                        style={"width": "100%"},
                                    ),
                                ], style={"flex": 1}),
                            ],
                        ),
                    ],
                ),
                html.Hr(),

                # ---- Environmental forcing ----
                html.H4("Environmental forcing", style={"marginBottom": "6px", "marginTop": "0"}),
                html.Div(
                    "Include environmental factors in the route optimisation.",
                    style=_HELP,
                ),
                dcc.Checklist(
                    id="route-forcing",
                    options=[
                        {"label": " Ocean currents", "value": "current"},
                        {"label": " Waves", "value": "wave"},
                        {"label": " Wind", "value": "wind"},
                        {"label": " Leeway", "value": "leeway"},
                    ],
                    value=[],
                    inputStyle={"marginRight": "4px"},
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
                html.Hr(),

                # ---- Environmental data ----
                html.H4("Environmental data", style={"marginBottom": "6px", "marginTop": "0"}),
                html.Label("Number of days:", style={"fontSize": "12px"}),
                dcc.Input(
                    id="route-ndays", type="number",
                    value=3, min=1, max=30, step=1,
                    style={"width": "100px"},
                ),
                html.Div(
                    "Days of environmental data to process from departure.",
                    style=_HELP,
                ),
                html.Hr(),

                # ---- Actions ----
                html.Div(
                    style={"display": "flex", "justifyContent": "flex-end", "gap": "10px"},
                    children=[
                        html.Button(
                            "Cancel", id="route-cancel-btn", n_clicks=0,
                            type="button",
                            style={
                                "padding": "8px 20px", "backgroundColor": "#e0e0e0",
                                "border": "none", "borderRadius": "4px",
                                "cursor": "pointer", "fontSize": "13px",
                            },
                        ),
                        html.Button(
                            "Compute route", id="route-submit-btn", n_clicks=0,
                            type="button",
                            style={
                                "padding": "8px 20px", "backgroundColor": "#1976D2",
                                "color": "white", "border": "none", "borderRadius": "4px",
                                "cursor": "pointer", "fontSize": "13px",
                            },
                        ),
                    ],
                ),
                html.Div(
                    id="route-status",
                    style={"fontSize": "12px", "marginTop": "8px", "color": "#c62828"},
                ),
                        ])]),
                        dcc.Tab(label="Saved Routes", value="saved", children=[
                            html.Div(
                                style={"paddingTop": "12px"},
                                children=[
                                    html.Div(
                                        "Routes are auto-saved after computation. "
                                        "Viewing a saved route shows the forecast data as it was at that time.",
                                        style={"fontSize": "12px", "color": "#666", "marginBottom": "12px"},
                                    ),
                                    html.Div(id="saved-routes-list"),
                                ],
                            ),
                        ]),
                    ],
                ),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Reliability-factor evaluation panels
# ---------------------------------------------------------------------------
SPATIAL_ACCENT = "#ffdf00"   # blue
ROBUST_ACCENT = "#32cd32"    # orange


def _factor_header(title: str, subtitle: str, accent: str) -> html.Div:
    return html.Div(
        style={
            "marginTop": "10px",
            "marginBottom": "6px",
            "paddingLeft": "8px",
            "borderLeft": f"3px solid {accent}",
        },
        children=[
            html.Div(
                title,
                style={"fontWeight": "bold", "fontSize": "13px", "color": accent},
            ),
            html.Div(
                subtitle,
                style={"fontSize": "11px", "color": "#777"},
            ),
        ],
    )


def _factor_button_row(run_id: str, clear_id: str, clear_label: str, accent: str) -> html.Div:
    return html.Div(
        style={"display": "flex", "gap": "6px", "marginBottom": "8px"},
        children=[
            html.Button(
                "Run",
                id=run_id,
                n_clicks=0,
                style={
                    "padding": "6px 12px",
                    "fontSize": "12px",
                    "backgroundColor": accent,
                    "color": "white",
                    "border": "none",
                    "borderRadius": "3px",
                    "cursor": "pointer",
                    "flex": 1,
                },
            ),
            html.Button(
                clear_label,
                id=clear_id,
                n_clicks=0,
                style={
                    "padding": "6px 12px",
                    "fontSize": "12px",
                    "backgroundColor": "#e0e0e0",
                    "border": "none",
                    "borderRadius": "3px",
                    "cursor": "pointer",
                },
            ),
        ],
    )


def _eval_row(tid: str, spec: dict) -> html.Div:
    """Spatial-accuracy test row — has a 'Show on map' action."""
    return html.Div(
        style={
            "border": "1px solid #e0e0e0",
            "borderRadius": "4px",
            "padding": "8px",
            "marginBottom": "6px",
            "backgroundColor": "#fff",
        },
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "justifyContent": "space-between"},
                children=[
                    dcc.Checklist(
                        id={"type": "eval-toggle", "test": tid},
                        options=[{"label": f" {spec['label']}", "value": tid}],
                        value=[tid],
                        inputStyle={"marginRight": "4px"},
                        style={"fontSize": "12px", "fontWeight": "bold"},
                    ),
                    html.Span(
                        id={"type": "eval-badge", "test": tid},
                        children="not run",
                        style={
                            "fontSize": "11px",
                            "padding": "2px 8px",
                            "borderRadius": "10px",
                            "backgroundColor": "#eeeeee",
                            "color": "#555",
                        },
                    ),
                ],
            ),
            html.Div(
                spec["explanation"],
                style={"fontSize": "11px", "color": "#666", "margin": "6px 0"},
            ),
            html.Button(
                "Show on map",
                id={"type": "eval-show", "test": tid},
                n_clicks=0,
                style={
                    "padding": "4px 10px",
                    "fontSize": "11px",
                    "backgroundColor": "#e3f2fd",
                    "border": "1px solid #90caf9",
                    "borderRadius": "3px",
                    "cursor": "pointer",
                },
            ),
        ],
    )


def _rob_row(tid: str, spec: dict) -> html.Div:
    """System-robustness test row — has a 'Show details' action (no map output)."""
    return html.Div(
        style={
            "border": "1px solid #e0e0e0",
            "borderRadius": "4px",
            "padding": "8px",
            "marginBottom": "6px",
            "backgroundColor": "#fff",
        },
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "justifyContent": "space-between"},
                children=[
                    dcc.Checklist(
                        id={"type": "rob-toggle", "test": tid},
                        options=[{"label": f" {spec['label']}", "value": tid}],
                        value=[tid],
                        inputStyle={"marginRight": "4px"},
                        style={"fontSize": "12px", "fontWeight": "bold"},
                    ),
                    html.Span(
                        id={"type": "rob-badge", "test": tid},
                        children="not run",
                        style={
                            "fontSize": "11px",
                            "padding": "2px 8px",
                            "borderRadius": "10px",
                            "backgroundColor": "#eeeeee",
                            "color": "#555",
                        },
                    ),
                ],
            ),
            html.Div(
                spec["explanation"],
                style={"fontSize": "11px", "color": "#666", "margin": "6px 0"},
            ),
            html.Button(
                "Show details",
                id={"type": "rob-show", "test": tid},
                n_clicks=0,
                style={
                    "padding": "4px 10px",
                    "fontSize": "11px",
                    "backgroundColor": "#fff3e0",
                    "border": "1px solid #ffcc80",
                    "borderRadius": "3px",
                    "cursor": "pointer",
                },
            ),
        ],
    )


_REL_FACTOR_NOTE = {
    "fontSize": "11px",
    "color": "#666",
    "marginBottom": "6px",
    "fontStyle": "italic",
}


evaluation_panel = html.Div(
    id="evaluation-panel",
    style={"display": "none", "marginTop": "8px"},
    children=[
        # ---- Spatial accuracy factor ----
        _factor_header(
            "Spatial accuracy",
            "CRS handling, bounding, geodesy approximations",
            SPATIAL_ACCENT,
        ),
        _factor_button_row(
            run_id="evaluation-run-btn",
            clear_id="evaluation-clear-btn",
            clear_label="Clear map",
            accent=SPATIAL_ACCENT,
        ),
        html.Div([_eval_row(tid, spec) for tid, spec in EVAL_TESTS.items()]),

        # ---- System robustness factor ----
        _factor_header(
            "System robustness",
            "Logging, retry on failure, load handling",
            ROBUST_ACCENT,
        ),
        _factor_button_row(
            run_id="rob-run-btn",
            clear_id="rob-clear-btn",
            clear_label="Clear details",
            accent=ROBUST_ACCENT,
        ),
        html.Div([_rob_row(tid, spec) for tid, spec in ROB_TESTS.items()]),
        html.Div(
            id="rob-details",
            style={
                "marginTop": "8px",
                "padding": "8px",
                "backgroundColor": "#fafafa",
                "border": "1px solid #e0e0e0",
                "borderRadius": "4px",
                "fontSize": "11px",
                "fontFamily": "Consolas, Menlo, monospace",
                "minHeight": "30px",
                "color": "#555",
            },
            children="No details to show. Run a test, then click 'Show details'.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------

layout = html.Div(
    style={"display": "flex", "height": "100vh", "fontFamily": "Segoe UI, Arial"},
    children=[
        # Sidebar
        html.Div(
            id="sidebar",
            style={
                "width": "300px",
                "padding": "16px",
                "boxShadow": "2px 0 6px rgba(0,0,0,0.1)",
                "backgroundColor": "#f8f9fb",
                "overflowY": "auto",
                "position": "relative",
            },
            children=[
                html.H3("Layers & Controls", style={"marginTop": 0}),
                dcc.Checklist(
                    id="layer-checklist",
                    options=[
                        {"label": "AIS Ships (real-time)", "value": "ais"},
                        {"label": "MET Weather", "value": "temp"},
                        {"label": "Shoreline", "value": "shoreline"},
                        {"label": "Bathymetry contours", "value": "bathy"},
                    ],
                    value=["ais", "temp"],
                    inputStyle={"marginRight": "8px"},
                ),
                html.Hr(),
                html.Div(
                    id="status",
                    children="Waiting for first update...",
                    style={"whiteSpace": "pre-wrap"},
                ),
                html.Hr(),
                html.Div(
                    "Update interval: 30 seconds",
                    style={"fontSize": "12px", "color": "#555"},
                ),
                dcc.Interval(
                    id="interval", interval=UPDATE_INTERVAL_MS, n_intervals=0
                ),

                html.Div(
                    id="track-status",
                    style={"fontSize": "12px", "color": "#555"},
                ),

                html.Hr(),
                html.H4("Traffic density"),

                html.Div("1) Draw a rectangle or polygon on the map.", style={"fontSize": "12px"}),

                html.Label("Start (UTC):"),
                dcc.DatePickerSingle(id="dens-start-date", placeholder="Date"),
                dcc.Input(id="dens-start-time", type="text", value="00:00", placeholder="HH:MM"),

                html.Br(),
                html.Label("End (UTC):", style={"marginTop": "8px"}),
                dcc.DatePickerSingle(id="dens-end-date", placeholder="Date"),
                dcc.Input(id="dens-end-time", type="text", value="01:00", placeholder="HH:MM"),

                html.Br(),
                html.Label("Grid cell size (meters):", style={"marginTop": "8px"}),
                dcc.Input(id="dens-cell-m", type="number", value=250, min=50, step=50),

                html.Button("Compute density", id="dens-run", n_clicks=0, style={"marginTop": "10px"}),

                html.Div(id="dens-status", style={"fontSize": "12px", "marginTop": "6px", "color": "#555"}),

                html.Hr(),
                html.H4("Ship routing"),
                html.Button(
                    "Compute Ship Routes",
                    id="route-open-btn",
                    n_clicks=0,
                    style={
                        "padding": "8px 16px",
                        "backgroundColor": "#1976D2",
                        "color": "white",
                        "border": "none",
                        "borderRadius": "4px",
                        "cursor": "pointer",
                        "fontSize": "13px",
                        "width": "100%",
                    },
                ),
                html.Div(
                    id="route-sidebar-status",
                    style={"fontSize": "12px", "marginTop": "6px", "color": "#555"},
                ),

                # Route results info panel (visible after computation)
                html.Div(
                    id="route-info-panel",
                    style={"display": "none", "marginTop": "8px"},
                    children=[
                        html.Div(
                            id="route-historical-banner",
                            style={"display": "none"},
                            children=[
                                html.Div(
                                    "Historical route: forecast data from a previous run",
                                    style={
                                        "backgroundColor": "#fff3e0",
                                        "color": "#e65100",
                                        "padding": "6px 10px",
                                        "borderRadius": "4px",
                                        "fontSize": "11px",
                                        "fontWeight": "bold",
                                        "marginBottom": "6px",
                                        "border": "1px solid #ffe0b2",
                                    },
                                ),
                                html.Div(
                                    id="route-historical-details",
                                    style={
                                        "fontSize": "11px",
                                        "color": "#666",
                                        "marginBottom": "8px",
                                    },
                                ),
                            ],
                        ),
                        html.Div(
                            "Route Results",
                            style={"fontWeight": "bold", "fontSize": "13px", "marginBottom": "6px"},
                        ),
                        html.Div(id="route-info-content"),
                        html.Button(
                            "Clear routes",
                            id="route-clear-btn",
                            n_clicks=0,
                            style={
                                "padding": "4px 12px",
                                "fontSize": "12px",
                                "backgroundColor": "#e0e0e0",
                                "border": "none",
                                "borderRadius": "3px",
                                "cursor": "pointer",
                                "marginTop": "6px",
                            },
                        ),
                    ],
                ),

                html.Hr(),
                html.H4("Evaluation"),
                html.Button(
                    "Evaluation",
                    id="evaluation-open-btn",
                    n_clicks=0,
                    style={
                        "padding": "8px 16px",
                        "backgroundColor": "#455a64",
                        "color": "white",
                        "border": "none",
                        "borderRadius": "4px",
                        "cursor": "pointer",
                        "fontSize": "13px",
                        "width": "100%",
                    },
                ),
                evaluation_panel,

                dcc.Store(id="shoreline-store", data=load_shoreline_geojson()),
                dcc.Store(id="bathy-store", data=load_bathymetry_geojson()),
                dcc.Store(id="ais-store", data=empty_geojson),
                dcc.Store(id="temp-store", data=empty_geojson),
                dcc.Store(id="weather-points-store", data=[]),
                dcc.Store(id="draw-geom-store", data=None),
                dcc.Store(id="selected-vessel-store", data=None),
                dcc.Store(id="filtered-map-click", data=None),
                dcc.Store(id="route-params-store", data=None),
                dcc.Store(id="route-result-store", data=None),
                dcc.Store(id="route-historical-store", data=None),
                dcc.Store(id="saved-routes-version", data=0),
                dcc.Store(id="evaluation-results-store", data={}),
                dcc.Store(id="rob-results-store", data={}),
                dcc.Interval(id="visir-health-interval", interval=20000, n_intervals=0),

                # Route progress panel (fixed at bottom of sidebar)
                route_progress_panel,
            ],
        ),

        EventListener(
            id="weather-popup-events",
            style={"width": "100%", "height": "100%"},
            useCapture=True,
            events=[
                {
                    "event": "click",
                    "props": [
                        "target.className",
                        "target.dataset.weatherId",
                        "target.dataset.vesselLat",
                        "target.dataset.vesselLon",
                        "target.dataset.vesselName",
                    ],
                }
            ],
            children=dl.Map(
                id="map",
                center=CENTER,
                zoom=ZOOM,
                style={"width": "100%", "height": "100%"},
                children=[
                    dl.TileLayer(),

                    # Computed route layer
                    dl.GeoJSON(
                        id="route-geojson",
                        data=empty_geojson,
                        style=ROUTE_STYLE,
                        pointToLayer=ROUTE_POINT_TO_LAYER,
                        onEachFeature=ROUTE_ON_EACH_FEATURE,
                    ),

                    # Shoreline layer
                    dl.GeoJSON(
                        id="shoreline-geojson",
                        data=empty_geojson,
                        style=SHORELINE_STYLE,
                    ),

                    # Bathymetry contour layer
                    dl.GeoJSON(
                        id="bathy-geojson",
                        data=empty_geojson,
                        style=BATHY_STYLE,
                        onEachFeature=BATHY_ON_EACH_FEATURE,
                    ),

                    dl.FeatureGroup(
                        id="draw-layer",
                        children=[
                            dl.EditControl(
                                id="edit-control",
                                position="topleft",
                                draw={
                                    "polyline": False,
                                    "polygon": True,
                                    "circle": False,
                                    "rectangle": True,
                                    "marker": False,
                                    "circlemarker": False,
                                },
                                edit={"selectedPathOptions": {"maintainColor": True}},
                            ),
                        ],
                    ),

                    # AIS ship layer
                    dl.GeoJSON(
                        id="ais-geojson",
                        data=empty_geojson,
                        options=dict(pointToLayer=AIS_POINT_TO_LAYER),
                    ),

                    dl.GeoJSON(
                        id="track-geojson",
                        data=empty_geojson,
                        options={"style": {"color": "cyan", "weight": 3}},
                    ),

                    # Weather points layer
                    dl.GeoJSON(
                        id="temp-geojson",
                        data=empty_geojson,
                        options=dict(pointToLayer=WEATHER_POINT_TO_LAYER),
                    ),

                    # Density layer
                    dl.GeoJSON(
                        id="density-geojson",
                        data=empty_geojson,
                        options=dict(style=DENSITY_STYLE),
                        hideout={"t1": 10, "t2": 30, "t3": 60},
                    ),

                    # Spatial evaluation overlay
                    dl.GeoJSON(
                        id="evaluation-geojson",
                        data=empty_geojson,
                        options=dict(
                            style=EVAL_STYLE,
                            pointToLayer=EVAL_POINT_TO_LAYER,
                            onEachFeature=EVAL_ON_EACH_FEATURE,
                        ),
                    ),
                ],
            ),
        ),

        # Route computation modal (fixed overlay)
        route_modal,
    ],
)
