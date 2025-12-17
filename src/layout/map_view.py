from dash import html, dcc
import dash_leaflet as dl
from dash_extensions.javascript import assign
from config import UPDATE_INTERVAL_MS, CENTER, ZOOM

empty_geojson = {"type": "FeatureCollection", "features": []}

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

        var popup = "<b>Weather station</b><br>" +
            "Temp: " + airTemp + " &#176;C<br>" +
            "Wind: " + windSpeed + " m/s<br>" +
            "Humidity: " + relHum + " %<br>" +
            "Pressure: " + airPressure + " hPa<br>" +
            "Cloud cover: " + cloudAreaFraction + " %<br>" +
            "Wind direction: " + windFromDirDisplay;

        return L.circleMarker(latlng, {
            radius: 6,
            fillColor: "orange",
            color: "black",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.85
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
                    "Last update: " + last_update;

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

layout = html.Div(
    style={"display": "flex", "height": "100vh", "fontFamily": "Segoe UI, Arial"},
    children=[
        # Sidebar
        html.Div(
            style={
                "width": "300px",
                "padding": "16px",
                "boxShadow": "2px 0 6px rgba(0,0,0,0.1)",
                "backgroundColor": "#f8f9fb",
            },
            children=[
                html.H3("Layers & Controls", style={"marginTop": 0}),
                dcc.Checklist(
                    id="layer-checklist",
                    options=[
                        {"label": "AIS Ships (real-time)", "value": "ais"},
                        {"label": "MET Weather (air, wind, humidity...)", "value": "temp"},
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
                dcc.DatePickerSingle(id="dens-start-date"),
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

                
                dcc.Store(id="ais-store", data=empty_geojson),
                dcc.Store(id="temp-store", data=empty_geojson),
                dcc.Store(id="draw-geom-store", data=None),
                dcc.Store(id="selected-vessel-store", data=None),
            ],
        ),
        # Map
        dl.Map(
            id="map",
            center=CENTER,
            zoom=ZOOM,
            style={"width": "100%", "height": "100%"},
            children=[
                dl.TileLayer(),
                
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
            ],
        )
    ],
)
