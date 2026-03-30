import psycopg2
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# 🔌 DB CONNECTION
import os

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# 🎯 ΟΛΕΣ ΟΙ ΚΑΤΗΓΟΡΙΕΣ ΣΟΥ (βάλε τις δικές σου 52)
CATEGORIES = [
    "fashion","books","home","beauty","appliances","jewelry",
    "electronics","toys","office","smartphones","tools","sports",
    "computers","kids","garden","automotive","gaming","baby_kids",
    "shoes","phone_cases","music","home_decor","wearables","food",
    "health","watches","furniture","tv_audio","kitchen","cameras",
    "pets","stationery","fishing","seasonal","bags","hardware",
    "home_textiles","computer","outdoor","printers","baby",
    "3d_printers","tables","storage","software","backpacks",
    "projectors","components","keyboards","car_accessories"
]

def categorize_batch(products):
    prompt = "Κατηγοριοποίησε τα παρακάτω προϊόντα σε ΜΙΑ από αυτές τις κατηγορίες:\n"
    prompt += ", ".join(CATEGORIES) + "\n\n"

    for i, p in enumerate(products):
        prompt += f"{i+1}. {p[1]}\n"

    prompt += "\nΑπάντησε ΜΟΝΟ έτσι:\n1 -> category\n2 -> category\n..."

    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt
    )

    return response.output_text


def main():
    while True:
        cur.execute("""
            SELECT id, title
            FROM products
            WHERE category80 = 'other' OR category80 IS NULL
            LIMIT 50
        """)
        rows = cur.fetchall()

        if not rows:
            print("DONE ✅")
            break

        result = categorize_batch(rows)
        lines = result.strip().split("\n")

        for line, product in zip(lines, rows):
            try:
                category = line.split("->")[1].strip()
                cur.execute(
                    "UPDATE products SET category80 = %s WHERE id = %s",
                    (category, product[0])
                )
            except:
                continue

        conn.commit()
        print(f"Updated {len(rows)} products")

if __name__ == "__main__":
    main()