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