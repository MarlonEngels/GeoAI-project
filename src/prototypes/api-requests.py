import time
import requests
import leafmap.foliumap as leafmap

AIS_URL = "https://kystdatahuset.no/ws/api/ais/realtime/geojson"
INTERVAL_SECONDS = 30

def fetch_ais_data():
    resp = requests.get(AIS_URL)
    resp.raise_for_status()
    return resp.json()

def show_on_map(geojson_data):
    center_coords = [59.87377018730873, 10.68472801175671]
    zoom_level = 11

    m = leafmap.Map(center=center_coords, zoom=zoom_level)
    m.add_geojson(geojson_data, layer_name="AIS Ships", zoom_to_layer=False)
    m.to_html("ais_map.html")
    print("Map updated: ais_map.html")


def main():
    while True:
        print(f"\nFetching AIS data at {time.strftime('%H:%M:%S')}")
        try:
            data = fetch_ais_data()
            show_on_map(data)
        except Exception as e:
            print("Error fetching data:", e)
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()