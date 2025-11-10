AIS_URL = "https://kystdatahuset.no/ws/api/ais/realtime/geojson"
UPDATE_INTERVAL_MS = 30000
CENTER = (59.87377, 10.68472)
ZOOM = 11
LAYERS = {
  {"id": "ais", "name": "AIS Ships", "type": "realtime", "source": "api"},
}