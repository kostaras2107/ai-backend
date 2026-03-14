# -*- coding: utf-8 -*-
import psycopg2
import time
from openai import OpenAI
import os

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

DATABASE_URL = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)

cur = conn.cursor()

categories = [
"Clothing",
"Books",
"Home",
"Computers",
"Smartphones",
"Mobile_Accessories",
"Gaming",
"Beauty",
"Tools",
"Baby",
"Lighting",
"Jewelry",
"Fitness",
"Auto",
"Garden",
"Furniture",
"Health",
"Watches"
]

batch = 40
last_id = 5539990

print("Starting AI classification from id >", last_id)

while True:

    cur.execute("""
        SELECT id, title, description
        FROM products
        WHERE id > %s
        ORDER BY id
        LIMIT %s
    """, (last_id, batch))

    rows = cur.fetchall()

    if not rows:
        break

    prompt = "Classify these ecommerce products.\n\nCategories:\n"
    prompt += "\n".join(categories)
    prompt += "\n\nReturn ONLY the category name for each product.\n\n"

    # ---------------------------------------
    # Build prompt safely (unicode cleanup)
    # ---------------------------------------

    for i, r in enumerate(rows):

        pid, title, desc = r

        if title is None:
            title = ""

        # καθάρισμα unicode
        title = str(title).encode("utf-8", "ignore").decode("utf-8")
        title = title[:120]

        prompt += f"{i+1}. {title}\n"

    # ---------------------------------------
    # OpenAI request with retry
    # ---------------------------------------

    response = None

    for attempt in range(3):

        try:

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )

            break

        except Exception as e:

            print("OpenAI error:", e)
            print("Retrying...")
            time.sleep(5)

    if response is None:

        print("Skipping batch due to repeated API errors")
        last_id = rows[-1][0]
        continue

    # ---------------------------------------
    # Parse AI response safely
    # ---------------------------------------

    answers = response.choices[0].message.content.strip().split("\n")

    for i, r in enumerate(rows):

        pid = r[0]

        try:

            if i < len(answers):
                category = answers[i].strip()
            else:
                category = "OTHER"

            cur.execute(
                "UPDATE products SET category80 = %s WHERE id = %s",
                (category, pid)
            )

        except:
            pass

        last_id = pid

    conn.commit()

    print("Processed up to id:", last_id)

print("DONE")