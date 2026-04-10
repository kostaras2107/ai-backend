import os
import pandas as pd

BASE_DIR = os.path.dirname(__file__)
FILE_PATH = os.path.join(BASE_DIR, "city_index.csv")

city_df = pd.read_csv(FILE_PATH)

city_df["city"] = city_df["city"].str.lower().str.strip()

CITY_MAP = dict(zip(city_df["city"], city_df["city_id"]))

def get_city_id(city_name):
    if not city_name:
        return None

    city_name = city_name.lower().strip()
    return CITY_MAP.get(city_name)

from difflib import get_close_matches

def fix_city_name(city_name):
    if not city_name:
        return None

    city_name = city_name.lower().strip()
    all_cities = list(CITY_MAP.keys())

    matches = get_close_matches(city_name, all_cities, n=1, cutoff=0.8)

    return matches[0] if matches else city_name    