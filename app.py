import os
import threading

from dash import Dash, Output, Input
from src.layout.map_view import layout
from src.callbacks.map_callbacks import register_callbacks
from src.api.visir_api import start_visir_service

app = Dash(__name__)
app.title = "Oslo Fjord — AIS (Dash + dash-leaflet)"
app.layout = layout

# Filter out map clicks when a Leaflet Draw tool is active (crosshair cursor).
app.clientside_callback(
    """
    function(clickData) {
        var container = document.querySelector('.leaflet-container');
        if (container && container.classList.contains('leaflet-crosshair')) {
            return window.dash_clientside.no_update;
        }
        return clickData;
    }
    """,
    Output("filtered-map-click", "data"),
    Input("map", "clickData"),
)

register_callbacks(app)

if __name__ == "__main__":
    threading.Thread(target=start_visir_service, daemon=True).start()

    app.run(
        host="0.0.0.0",
        debug=True,
        port=8050,
        # Disable the Werkzeug auto-reloader and Dash hot-reload.
        # The reloader conflicts with the VISIR-2 subprocess (kills and
        # restarts it on every detected file change), and the hot-reload
        # triggers spurious browser refreshes because dash-extensions'
        # assign() rewrites assets/dashExtensions_default.js on every
        # import (same content, but updated mtime).
        # The debugger (error pages / tracebacks) remains active.
        use_reloader=False,
        dev_tools_hot_reload=False,
    )
