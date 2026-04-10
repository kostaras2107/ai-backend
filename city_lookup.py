import os
import pandas as pd

BASE_DIR = os.path.dirname(__file__)
FILE_PATH = os.path.join(BASE_DIR, "city_index.csv")

city_df = pd.read_csv(FILE_PATH)
city_df = city_df.dropna(subset=["city"])

city_df["city"] = city_df["city"].str.lower().str.strip()

CITY_MAP = dict(zip(city_df["city"], city_df["city_id"]))

def get_city_id(city_name):
    if not city_name:
        return None

    city_name = str(city_name).lower().strip()

    # 1️⃣ direct match
    if city_name in CITY_MAP:
        return CITY_MAP[city_name]

    # 2️⃣ partial match (🔥 ΤΟ ΚΛΕΙΔΙ)
    for city, cid in CITY_MAP.items():
        if city_name in city or city in city_name:
            return cid

    return None

from difflib import get_close_matches

def greek_to_english(text):
    mapping = {
        "α":"a","β":"v","γ":"g","δ":"d","ε":"e","ζ":"z","η":"i",
        "θ":"th","ι":"i","κ":"k","λ":"l","μ":"m","ν":"n","ξ":"x",
        "ο":"o","π":"p","ρ":"r","σ":"s","ς":"s","τ":"t","υ":"y",
        "φ":"f","χ":"x","ψ":"ps","ω":"o"
    }
    return "".join(mapping.get(c, c) for c in text.lower())

def fix_city_name(city_name):
    if not city_name:
        return None
    
    city_name = greek_to_english(city_name)
    city_name = str(city_name).lower().strip()

    all_cities = [c for c in CITY_MAP.keys() if isinstance(c, str)]

    matches = get_close_matches(city_name, all_cities, n=1, cutoff=0.6)

    return matches[0] if matches else city_name