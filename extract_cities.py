import pandas as pd

INPUT_FILE = "C:/Users/bosinakos/desktop/E342B777-64FD-4A49-9C9F-FEF4BA635863_EN.csv"
OUTPUT_FILE = "city_index.csv"

chunk_size = 100000

city_map = {}

print("⏳ Processing...")

for chunk in pd.read_csv(INPUT_FILE, chunksize=chunk_size, low_memory=False):

    # κράτα μόνο όσα έχουν city
    chunk = chunk[["city", "city_id"]].dropna()

    for _, row in chunk.iterrows():
        city = str(row["city"]).strip().lower()
        city_id = int(row["city_id"])

        if city not in city_map:
            city_map[city] = city_id

print("💾 Saving...")

df = pd.DataFrame(list(city_map.items()), columns=["city", "city_id"])
df.to_csv(OUTPUT_FILE, index=False)

print("✅ DONE")