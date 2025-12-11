import dash
from src.layout.map_view import layout
from src.callbacks.map_callbacks import register_callbacks

app = dash.Dash(__name__)
app.title = "Oslo Fjord — AIS (Dash + dash-leaflet)"
app.layout = layout

register_callbacks(app)

if __name__ == "__main__":
    app.run(debug=True, port=8050)
