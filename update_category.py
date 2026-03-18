import psycopg2

conn = psycopg2.connect("postgresql://gorealaiuser:qN40CJZK3bxkZp8hFF41VEVYPKasEuyj@dpg-d6j2vr1aae7s739bvo60-a.frankfurt-postgres.render.com:5432/gorealai_0d5w")
cur = conn.cursor()


def map_category(cat):
    if not cat:
        return "other"

    c = cat.lower()

    if any(x in c for x in ["fashion","ρουχ","φορεμα","μπλουζ","παντελον","παπουτ","shoes","ζακετ","dress","men","women"]):
        return "fashion"

    if any(x in c for x in ["αρωμα","beauty","μαλλ","προσωπο","σωμα","καλλυντικ"]):
        return "beauty"

    if any(x in c for x in ["electronics","gadgets","drones","tv","audio"]):
        return "electronics"

    if any(x in c for x in ["computer","laptop","pc","εκτυπωτ"]):
        return "electronics"

    if any(x in c for x in ["home","decor","κουρτιν","μπανιο","furniture","καναπ","κρεβατ"]):
        return "home"

    if any(x in c for x in ["appliance","ψυγει","πλυντηρ","κουζιν"]):
        return "appliances"

    if any(x in c for x in ["sport","ποδηλατ","camping","outdoor","θαλασσ"]):
        return "sports"

    if any(x in c for x in ["auto","car","scooter"]):
        return "automotive"

    if any(x in c for x in ["toy","παιχνιδ"]):
        return "toys"

    if any(x in c for x in ["kids","baby","παιδικ"]):
        return "kids"

    if any(x in c for x in ["book","βιβλ","dvd","ταινι"]):
        return "books"

    if any(x in c for x in ["κοσμημ","jewel","αλυσιδ","μονοπετρ"]):
        return "jewelry"

    if any(x in c for x in ["ρολογ","watch"]):
        return "watches"

    if any(x in c for x in ["office","γραφ","στυλο"]):
        return "office"

    if any(x in c for x in ["garden","κηπο"]):
        return "garden"

    if any(x in c for x in ["pet","ζω"]):
        return "pets"

    if any(x in c for x in ["food","τροφ","μπισκοτ"]):
        return "food"

    if any(x in c for x in ["health","υγεια","βιταμιν"]):
        return "health"

    if any(x in c for x in ["tool","εργαλ","χρωμα"]):
        return "tools"

    if any(x in c for x in ["sale","offer","outlet","new","deal","uncategorized"]):
        return "other"

    return "other"


# -------------------------
# UPDATE DB
# -------------------------

cur.execute("SELECT id, category80 FROM products")
rows = cur.fetchall()

for r in rows:
    pid = r[0]
    old = r[1]

    new = map_category(old)

    cur.execute(
        "UPDATE products SET category80 = %s WHERE id = %s",
        (new, pid)
    )

conn.commit()
cur.close()
conn.close()

print("DONE ✅")