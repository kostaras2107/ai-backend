import csv
import unicodedata
import sys
import html
import re
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


def create_search_vector(cur):

    print("Updating search_vector...")

    cur.execute("""
        UPDATE products
        SET search_vector =
            to_tsvector(
                'simple',
                coalesce(title,'') || ' ' ||
                coalesce(description,'') || ' ' ||
                coalesce(brand,'') || ' ' ||
                coalesce(product_type,'')
            );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_search_vector
        ON products USING GIN(search_vector);
    """)

    print("Search vector updated.")
    
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
                        last_seen_at = NOW();
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
                    last_seen_at = NOW();
            """, rows)

            conn.commit()

    create_search_vector(cur)

    cur.close()
    conn.close()

    print("Import finished.")


if __name__ == "__main__":
    import_csv("shopping_feed.csv")