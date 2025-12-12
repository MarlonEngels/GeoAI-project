from dash import html, dcc
import dash_leaflet as dl
from dash_extensions.javascript import assign
import textwrap3
from config import UPDATE_INTERVAL_MS, CENTER, ZOOM

empty_geojson = {"type": "FeatureCollection", "features": []}

WEATHER_POINT_TO_LAYER = assign(
    """
    function(feature, latlng, context) {
        const p = feature.properties || {};
        const airTemp = (p.air_temperature !== undefined && p.air_temperature !== null) ? p.air_temperature : "?";
        const windSpeed = (p.wind_speed !== undefined && p.wind_speed !== null) ? p.wind_speed : "?";
        const relHum = (p.relative_humidity !== undefined && p.relative_humidity !== null) ? p.relative_humidity : "?";

        var popup = "<b>Weather station</b><br>" +
            "Temp: " + airTemp + " °C<br>" +
            "Wind: " + windSpeed + " m/s<br>" +
            "Humidity: " + relHum + " %";

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
        const last_update = p.date_time_utc || "Unknown";
        

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
                    "Last update (UTC): " + last_update;

        const marker = L.marker(latlng, {icon: icon});
        marker.bindPopup(popup);
        return marker;
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
                    "Update interval: 15 seconds",
                    style={"fontSize": "12px", "color": "#555"},
                ),
                dcc.Interval(
                    id="interval", interval=UPDATE_INTERVAL_MS, n_intervals=0
                ),
                dcc.Store(id="ais-store", data=empty_geojson),
                dcc.Store(id="temp-store", data=empty_geojson),
            ],
        ),
        # Map
        html.Div(
            style={"flex": "1 1 auto"},
            children=[
                dl.Map(
                    id="map",
                    center=CENTER,
                    zoom=ZOOM,
                    style={"width": "100%", "height": "100%"},
                    children=[
                        dl.TileLayer(),

                        # AIS ship layer (existing)
                        dl.GeoJSON(
                            id="ais-geojson",
                            data=empty_geojson,
                            options=dict(
                                pointToLayer=AIS_POINT_TO_LAYER
                            ),
                        ),

                        # Weather (MET) points layer
                        dl.GeoJSON(
                            id="temp-geojson",
                            data=empty_geojson,
                            options=dict(
                                pointToLayer=WEATHER_POINT_TO_LAYER
                            ),
                        ),
                    ],
                )
            ],
        ),
    ],
)
