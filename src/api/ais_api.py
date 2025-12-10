import requests
from config import AIS_URL

def fetch_ais_geojson(timeout=10):
    resp = requests.get(AIS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()