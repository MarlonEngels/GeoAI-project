# dash_dashleaflet_ais.py
import requests
from datetime import datetime
from dash import Dash, html, dcc, Output, Input, State, no_update
import dash_leaflet as dl
import dash_leaflet.express as dlx

# ---- Settings ----
AIS_URL = "https://kystdatahuset.no/ws/api/ais/realtime/geojson"
UPDATE_INTERVAL_MS = 15_000
CENTER = (59.87377018730873, 10.68472801175671)
ZOOM = 11

# ---- Helper: fetch AIS GeoJSON ----
def fetch_ais_geojson(timeout=10):
    resp = requests.get(AIS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

# ---- Dash app ----
app = Dash(__name__)
app.title = "Oslo Fjord — AIS (Dash + dash-leaflet)"

# Initial empty GeoJSON
empty_geojson = {"type": "FeatureCollection", "features": []}

app.layout = html.Div(
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
                    options=[{"label": "AIS Ships (real-time)", "value": "ais"}],
                    value=["ais"],
                    inputStyle={"marginRight": "8px"},
                ),
                html.Hr(),
                html.Div(id="status", children="Waiting for first update...", style={"whiteSpace": "pre-wrap"}),
                html.Hr(),
                html.Div("Update interval: 15 seconds", style={"fontSize": "12px", "color": "#555"}),
                dcc.Interval(id="interval", interval=UPDATE_INTERVAL_MS, n_intervals=0),
                dcc.Store(id="ais-store", data=empty_geojson),  # keep last-successful data
            ],
        ),
        # Map area
        html.Div(
            style={"flex": "1 1 auto"},
            children=[
                dl.Map(
                    id="map",
                    center=CENTER,
                    zoom=ZOOM,
                    children=[
                        dl.TileLayer(),  # default basemap
                        # GeoJSON layer that will be updated
                        dl.GeoJSON(id="ais-geojson", data=empty_geojson),
                    ],
                    style={"width": "100%", "height": "100%"},
                )
            ],
        ),
    ],
)


# ---- Callback: Fetch & update AIS GeoJSON ----
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
    # If user turned AIS layer off: clear on-map display but keep store unchanged
    if "ais" not in (layers or []):
        return {}, previous_store, f"AIS layer disabled (interval #{n_intervals})"

    # Layer is enabled -> attempt fetch
    try:
        geojson = fetch_ais_geojson()
        features = geojson.get("features", [])
        count = len(features)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        status = f"AIS updated: {count} features — {ts} (interval #{n_intervals})"
        # Return both for immediate display and to update the store
        return geojson, geojson, status
    except Exception as e:
        # On error: keep previous data (avoid blanking the map), and show an error status
        err_msg = f"Error fetching AIS: {e!s} — showing last successful data (interval #{n_intervals})"
        # If we have no previous store, return empty geojson
        fallback = previous_store or {"type": "FeatureCollection", "features": []}
        return fallback, previous_store or fallback, err_msg


if __name__ == "__main__":
    app.run(debug=True, port=8050)
