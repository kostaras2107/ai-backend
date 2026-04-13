import requests

def resolve_destination(destination):
    try:
        url = "https://www.agoda.com/api/en-gb/Main/GetDestinationSuggestions"

        params = {
            "searchText": destination,
            "isHotel": "true"
        }

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.agoda.com/",
            "Origin": "https://www.agoda.com",
            "Accept-Language": "en-US,en;q=0.9"
        }

        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()

        print("AGODA RAW:", data, flush=True)

        for item in data.get("data", []):
            if item.get("type") in ["City","Region","Area","Island"]:
                return {
                    "city_id": item.get("id"),
                    "name": item.get("name")
                }

    except Exception as e:
        print("RESOLVE ERROR:", e)

    return {
        "city_id": None,
        "name": destination
    }