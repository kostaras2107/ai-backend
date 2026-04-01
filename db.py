import psycopg2
import os

def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def get_all_categories():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT category80
        FROM products
        WHERE category80 IS NOT NULL
    """)

    rows = cur.fetchall()

    categories = [r[0] for r in rows]

    return categories    