import csv
from collections import Counter

file_path = "datafeed.csv"

level_1 = Counter()
level_2 = Counter()
level_3 = Counter()

total_rows = 0

with open(file_path, newline='', encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        total_rows += 1

        category = row.get("category")
        if category:
            clean = category.replace("&gt;", ">")
            parts = [p.strip() for p in clean.split(">")]

            if len(parts) > 0:
                level_1[parts[0]] += 1
            if len(parts) > 1:
                level_2[parts[1]] += 1
            if len(parts) > 2:
                level_3[parts[2]] += 1

        if total_rows % 200000 == 0:
            print(f"Processed {total_rows} rows...")

print("\nTOTAL ROWS:", total_rows)

print("\nTOP LEVEL 1:")
for cat, count in level_1.most_common(20):
    print(count, "-", cat)

print("\nTOP LEVEL 2:")
for cat, count in level_2.most_common(20):
    print(count, "-", cat)

print("\nTOP LEVEL 3:")
for cat, count in level_3.most_common(20):
    print(count, "-", cat)