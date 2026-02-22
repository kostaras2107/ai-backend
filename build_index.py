import csv
import json

shopping_file = "shopping_feed.csv"
travel_file = "travel_feed.csv"

output_file = "product_index.jsonl"

products = []

def load_csv(file_path):

    print(f"Loading {file_path}...")

    rows = []

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for i, row in enumerate(reader):

            category = row.get("category", "").replace("&gt;", ">")
            parts = [p.strip() for p in category.split(">")]

            level_1 = parts[0] if len(parts) > 0 else ""
            level_2 = parts[1] if len(parts) > 1 else ""
            level_3 = parts[2] if len(parts) > 2 else ""

            try:
                price = float(row.get("price", 0))
            except:
                price = 0

            rows.append({
                "title": row.get("product_name"),
                "price": price,
                "url": row.get("tracking_url"),
               
            })

            if i % 200000 == 0:
                print(f"{file_path} → Processed {i} rows...")

    print(f"{file_path} → TOTAL: {len(rows)}")
    return rows


# Load both feeds
shopping_rows = load_csv(shopping_file)
travel_rows   = load_csv(travel_file)

# Merge
products = shopping_rows + travel_rows

print("TOTAL MERGED PRODUCTS:", len(products))

import random

products = random.sample(products, 300000)
print("RANDOM TRIMMED:", len(products))


# Save unified index
with open(output_file, "w", encoding="utf-8") as f:
    for p in products:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")

print("Unified index created successfully.")