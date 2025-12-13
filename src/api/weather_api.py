# src/api/weather_api.py
import requests
from config import MET_URL, OSLO_INFO_POINTS

def get_weather_data(lat, lon):
    url = MET_URL.format(lat=lat, lon=lon)
    headers = {
        "User-Agent": "DashApp/1.0 (mgengels@stud.ntnu.no)"
    }

    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.raise_for_status()
        data = res.json()
        timeseries = data["properties"]["timeseries"]
        if not timeseries:
            print(f"[Weather API] No timeseries for ({lat},{lon})")
            return None
        details = timeseries[0]["data"]["instant"]["details"]
        # print(f"[Weather API] Fetched data for ({lat},{lon}): {details}")
        return {"lat": lat, "lon": lon, **details}
    except Exception as e:
        print(f"[Weather API] Error for ({lat}, {lon}): {e}")
        return None


def get_all_weather_data(points):
    data = []
    for lat, lon in points:
        entry = get_weather_data(lat, lon)
        if entry:
            data.append(entry)
        else:
            print(f"[Weather API] No data for ({lat},{lon})")
    print(f"[Weather API] Total valid points: {len(data)}")
    return data



def fetch_weather_geojson():
    
    data = get_all_weather_data(OSLO_INFO_POINTS)

    features = []
    for d in data:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": d
        })

    return {"type": "FeatureCollection", "features": features}
