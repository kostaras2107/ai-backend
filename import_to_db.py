import csv
import unicodedata
import sys
import html
import re
import requests
import psycopg2
from psycopg2.extras import execute_batch


csv.field_size_limit(sys.maxsize)

DATABASE_URL = "postgresql://gorealaiuser:qN40CJZK3bxkZp8hFF41VEVYPKasEuyj@dpg-d6j2vr1aae7s739bvo60-a.frankfurt-postgres.render.com/gorealai_0d5w"

BATCH_SIZE = 20000
ROW_LIMIT = 999999999


def clean_text(value):
    if not value:
        return None

    value = html.unescape(value)
    value = unicodedata.normalize("NFC", value)
    value = value.encode("utf-8", "ignore").decode("utf-8", "ignore")
    value = re.sub(r"[^\x20-\x7E\u0370-\u03FF]", "", value)

    return value.strip()


def import_csv(file_path):

    print(f"Importing {file_path}...")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    cur = conn.cursor()

    print("Resetting stock flags...")
    cur.execute("UPDATE products SET in_stock = false;")
    conn.commit()

    rows = []

    encodings_to_try = ["utf-8","windows-1253","iso-8859-7"]
    detected_encoding = None

    for enc in encodings_to_try:
        try:
            with open(file_path, newline="", encoding=enc) as f:
                f.read(10000)
            detected_encoding = enc
            break
        except:
            pass

    if not detected_encoding:
        detected_encoding = "utf-8"

    print(f"Using encoding: {detected_encoding}")

    with open(file_path, newline="", encoding=detected_encoding, errors="replace") as f:

        reader = csv.DictReader(f)
        count = 0

        for row in reader:

            count += 1

            if count > ROW_LIMIT:
                break

            product_id = clean_text(row.get("product_id"))
            if not product_id:
                continue

            full_category = clean_text(row.get("category",""))
            parts = [c.strip() for c in full_category.split(">")] if full_category else []

            root = parts[0] if len(parts) > 0 else None
            level1 = parts[1] if len(parts) > 1 else None
            level2 = parts[2] if len(parts) > 2 else None
            product_type = parts[-1] if parts else None

            price_raw = row.get("price") or row.get("full_price") or "0"
            price_raw = price_raw.replace(",", ".").replace("€", "").strip()

            try:
                price = float(price_raw)
            except:
                price = 0

            availability = clean_text(row.get("availability"))
            in_stock = True

            if availability and availability.lower() in ["out of stock","0","false","no"]:
                in_stock = False

            rows.append((
                product_id,
                clean_text(row.get("model_name")),
                clean_text(row.get("product_name")),
                clean_text(row.get("description")),
                clean_text(row.get("brand_name")),
                price,
                clean_text(row.get("tracking_url")),
                full_category,
                root,
                level1,
                level2,
                product_type,
                in_stock,
                availability
            ))

            if len(rows) >= BATCH_SIZE:

                execute_batch(cur, """
                    INSERT INTO products (
                        product_id,
                        model_name,
                        title,
                        description,
                        brand,
                        price,
                        url,
                        full_category,
                        root_category,
                        level_1,
                        level_2,
                        product_type,
                        in_stock,
                        availability,
                        last_seen_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (product_id) DO UPDATE SET
                        model_name = EXCLUDED.model_name,
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        brand = EXCLUDED.brand,
                        price = EXCLUDED.price,
                        url = EXCLUDED.url,
                        full_category = EXCLUDED.full_category,
                        root_category = EXCLUDED.root_category,
                        level_1 = EXCLUDED.level_1,
                        level_2 = EXCLUDED.level_2,
                        product_type = EXCLUDED.product_type,
                        in_stock = true,
                        availability = EXCLUDED.availability,
                        last_seen_at = NOW(),
                        search_vector = to_tsvector('simple',
                            coalesce(EXCLUDED.title,'') || ' ' ||
                            coalesce(EXCLUDED.description,'') || ' ' ||
                            coalesce(EXCLUDED.brand,'') || ' ' ||
                            coalesce(EXCLUDED.product_type,'') || ' ' ||
                            coalesce(EXCLUDED.category80,'')
                        );
                """, rows)

                conn.commit()
                rows = []

        if rows:

            execute_batch(cur, """
                INSERT INTO products (
                    product_id,
                    model_name,
                    title,
                    description,
                    brand,
                    price,
                    url,
                    full_category,
                    root_category,
                    level_1,
                    level_2,
                    product_type,
                    in_stock,
                    availability,
                    last_seen_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                    model_name = EXCLUDED.model_name,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    brand = EXCLUDED.brand,
                    price = EXCLUDED.price,
                    url = EXCLUDED.url,
                    full_category = EXCLUDED.full_category,
                    root_category = EXCLUDED.root_category,
                    level_1 = EXCLUDED.level_1,
                    level_2 = EXCLUDED.level_2,
                    product_type = EXCLUDED.product_type,
                    in_stock = true,
                    availability = EXCLUDED.availability,
                    last_seen_at = NOW(),
                    search_vector = to_tsvector('simple',
                        coalesce(EXCLUDED.title,'') || ' ' ||
                        coalesce(EXCLUDED.description,'') || ' ' ||
                        coalesce(EXCLUDED.brand,'') || ' ' ||
                        coalesce(EXCLUDED.product_type,'') || ' ' ||
                        coalesce(EXCLUDED.category80,'')
                    );""", rows)

                conn.commit()
                rows = []

        if rows:

            execute_batch(cur, """
                INSERT INTO products (
                    product_id,
                    model_name,
                    title,
                    description,
                    brand,
                    price,
                    url,
                    full_category,
                    root_category,
                    level_1,
                    level_2,
                    product_type,
                    in_stock,
                    availability,
                    last_seen_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                    model_name = EXCLUDED.model_name,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    brand = EXCLUDED.brand,
                    price = EXCLUDED.price,
                    url = EXCLUDED.url,
                    full_category = EXCLUDED.full_category,
                    root_category = EXCLUDED.root_category,
                    level_1 = EXCLUDED.level_1,
                    level_2 = EXCLUDED.level_2,
                    product_type = EXCLUDED.product_type,
                    in_stock = true,
                    availability = EXCLUDED.availability,
                    last_seen_at = NOW(),
                    search_vector = to_tsvector('simple',
                        coalesce(EXCLUDED.title,'') || ' ' ||
                        coalesce(EXCLUDED.description,'') || ' ' ||
                        coalesce(EXCLUDED.brand,'') || ' ' ||
                        coalesce(EXCLUDED.product_type,'') || ' ' ||
                        coalesce(EXCLUDED.category80,'')
                    );
            """, rows)

            conn.commit()

    cur.close()
    conn.close()

    print("Import finished.")


if __name__ == "__main__":

    print("Downloading shopping feed...")

    url = "https://affiliate.linkwi.se/feeds/1.2/CD28160/programs-joined/columns-product_id,model_name,product_name,description,category,brand_name,tracking_url,thumb_url,image_url,in_stock,availability,valid_from,valid_to,on_sale,currency,price,full_price,discount,city,times_bought,longitude,latitude,address,size,colour,custom,extra_images,variations/catinc-0/catex-0/proginc-11532-726,12858-2366,13987-2681,13208-2081,12125-1139,11920-1064,12218-1239,13306-2056,13527-2303,13806-2653,11036-369,12761-1652,14114-2761,11593-815,12560-1466,13990-2713,11834-955,11983-1078,13962-2677,12011-1042,13640-2370,11442-602,138-2273,12174-1176,12315-1323,13779-2538,13535-2262,13941-2644,12802-1676,14123-2770,10784-281,13240-2087,12471-1412,11388-564,11609-771,10553-1827,469-299,13026-1874,13993-2692,13754-2454,12056-1106,11432-621,11307-622,11641-847,12071-1114,12615-1512,12321-1361,11754-880,13604-2421,12569-1461,11537-2451,13775-2623/progex-0/feed.xml"

    response = requests.get(url)

    if response.status_code != 200:
        raise Exception(f"Failed to download feed: {response.status_code}")

    with open("shopping_feed.csv", "wb") as f:
        f.write(response.content)

    print("Download complete.")

    import_csv("shopping_feed.csv")