import json
import requests

CITY_IDS = {}

def load_city_ids():
    global CITY_IDS
    try:
        with open("city_ids.json", "r") as f:
            CITY_IDS = json.load(f)
    except:
        CITY_IDS = {}

def save_city_ids():
    with open("city_ids.json", "w") as f:
        json.dump(CITY_IDS, f)

def get_city_id(destination):
    destination = destination.strip().lower()

    if destination in CITY_IDS:
        return CITY_IDS[destination]

    try:
        url = "https://www.agoda.com/api/en-gb/Main/GetDestinationSuggestions"

        params = {
            "searchText": destination,
            "isHotel": "true"
        }

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()

        print("AGODA RAW:", data)

        for item in data.get("data", []):
            if item.get("id"):
                city_id = item.get("id")

                CITY_IDS[destination] = city_id
                save_city_ids()

                print("NEW CITY:", destination, city_id)

                return city_id

    except Exception as e:
        print("CITY ERROR:", e)

    return None

# load once
load_city_ids()