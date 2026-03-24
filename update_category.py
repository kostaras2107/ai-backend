import psycopg2

conn = psycopg2.connect(
    "postgresql://gorealaiuser:qN40CJZK3bxkZp8hFF41VEVYPKasEuyj@dpg-d6j2vr1aae7s739bvo60-a.frankfurt-postgres.render.com:5432/gorealai_0d5w"
)

read_cur = conn.cursor()
write_cur = conn.cursor()


def map_category(cat):
    if not cat:
        return "other"

    c = cat.lower()

    # =========================
    # 👟 SHOES (ULTRA PRIORITY)
    # =========================
    if any(x in c for x in [
        "παπουτ","μποτα","μποτακια","μποτίν",
        "σανδαλ","πέδιλα","γόβα","γόβες",
        "εσπαντρι","sneaker","πανινα",
        "loafer","boot","clog","slide",
        "mule","flatform","running",
        "πεζοπορ","ορειβατικ","σαγιοναρ"
    ]):
        return "shoes"

    # =========================
    # 🎒 BAGS
    # =========================
    if any(x in c for x in [
        "τσάντα","τσάντες","bag",
        "backpack","σακιδιο",
        "τσαντακι","νεσεσερ",
        "βαλίτσα","luggage"
    ]):
        return "bags"

    # =========================
    # 👕 FASHION
    # =========================
    if any(x in c for x in [
        "μπλουζ","παντελ","φορεμα",
        "φουτερ","ζακετα","πουκαμισο",
        "τοπ","γιλεκο","παλτο",
        "μπουφαν","σορτς","μαγιο",
        "σουτιεν","slip","polo",
        "hoodie","bra","jean"
    ]):
        return "fashion"

    # =========================
    # 🪑 FURNITURE
    # =========================
    if any(x in c for x in [
        "καρεκλα","τραπεζ","πολυθρονα",
        "κομοδινο","ντουλαπι","ραφι",
        "επιπλο","σαλονι","σκαμπο",
        "κονσολα","σεζλονγκ"
    ]):
        return "furniture"

    # =========================
    # 🛏️ HOME TEXTILES
    # =========================
    if any(x in c for x in [
        "ριχταρι","παπλωμα","κουβερτα",
        "μαξιλαρ","τραβερσα","runner",
        "σεντον","πετσετα","μπουρνουζ",
        "χαλι","πατακι"
    ]):
        return "home_textiles"

    # =========================
    # 🍳 KITCHEN / COOKWARE
    # =========================
    if any(x in c for x in [
        "κατσαρολα","τηγανι","ταψι",
        "πιατο","ποτηρι","κουπα",
        "μπωλ","σερβιτσιο","μαχαιρι",
        "κουζιν","μαγειρ","φρυγανιερ",
        "τοστιερ","βραστηρα","ψησταρια",
        "φριτεζ","espresso"
    ]):
        return "kitchen"

    # =========================
    # ⚡ APPLIANCES
    # =========================
    if any(x in c for x in [
        "σκουπα","πλυντηριο","ψυγειο",
        "φουρνο","απορροφητηρα",
        "στεγνωτηριο","καφε","σιδερο",
        "heater","θερμο","air fryer"
    ]):
        return "appliances"

    # =========================
    # 💻 ELECTRONICS
    # =========================
    if any(x in c for x in [
        "router","wifi","ακουστικ",
        "speaker","ηχειο","μικροφων",
        "camera","monitor","tv",
        "printer","toner","keyboard",
        "mouse","ssd","hdd",
        "ipad","console"
    ]):
        return "electronics"

    # =========================
    # 🔌 HARDWARE / ELECTRICAL (NEW IMPORTANT)
    # =========================
    if any(x in c for x in [
        "πολυπριζο","διακοπτη","ασφαλ",
        "καλωδιο","adapter","ανταπτορ",
        "πριζα","switch","ρελε",
        "υδραυλ","σιφον","βαλβιδ"
    ]):
        return "hardware"

    # =========================
    # 🎮 TOYS / HOBBY
    # =========================
    if any(x in c for x in [
        "lego","παιχνιδ","φιγουρα",
        "playset","κουκλα","nerf",
        "minecraft","pokemon",
        "cube","tamagotchi"
    ]):
        return "toys"

    # =========================
    # 🎸 MUSIC / INSTRUMENTS
    # =========================
    if any(x in c for x in [
        "κιθαρα","πιανο","μουσικ",
        "cd","album","mozart",
        "bach","chopin","organ",
        "ταμπουρο","δοξαρι"
    ]):
        return "music"

    # =========================
    # 📚 BOOKS
    # =========================
    if any(x in c for x in [
        "βιβλ","μυθιστορημα",
        "οδηγος","ιστορια",
        "ζωη","πολιτικ"
    ]):
        return "books"

    # =========================
    # 🧴 BEAUTY
    # =========================
    if any(x in c for x in [
        "cream","αρωμα","μαλλι",
        "σαμπουαν","lip","gel",
        "skincare"
    ]):
        return "beauty"

    # =========================
    # 🧒 BABY
    # =========================
    if any(x in c for x in [
        "baby","βρεφ","θηλες",
        "μπιμπερο"
    ]):
        return "baby"

    # =========================
    # 🏀 SPORTS / OUTDOOR
    # =========================
    if any(x in c for x in [
        "μπαλα","ποδοσφαιρ",
        "ποδηλατο","ψαρεμα",
        "πισινα","camping",
        "σκην","θαλασσια"
    ]):
        return "sports"

    # =========================
    # ✏️ OFFICE / STATIONERY
    # =========================
    if any(x in c for x in [
        "στυλο","τετραδιο",
        "φακελο","marker",
        "σημειωμα","κλασερ"
    ]):
        return "office"

    # =========================
    # 💍 ACCESSORIES
    # =========================
    if any(x in c for x in [
        "ρολοι","ζωνη",
        "γυαλια","καπελο",
        "σκαρφ","μανικετο"
    ]):
        return "accessories"

    return "other"
    
# 🔥 ΠΡΟΣΟΧΗ: παίρνουμε TITLE όχι category80
read_cur.execute("""
    SELECT id, title 
    FROM products 
    WHERE category80 = 'other'
""")

batch_size = 1000
total = 0

while True:
    rows = read_cur.fetchmany(batch_size)

    if not rows:
        break

    for r in rows:
        pid = r[0]
        title = r[1]

        new = map_category(title)

        # ❗ ΜΗΝ ξαναγράφεις other
        if new == "other":
            continue

        write_cur.execute(
            "UPDATE products SET category80 = %s WHERE id = %s",
            (new, pid)
        )

        total += 1

    conn.commit()
    print(f"✅ Updated batch - total: {total}", flush=True)


read_cur.close()
write_cur.close()
conn.close()

print("🔥 DONE SUCCESSFULLY")