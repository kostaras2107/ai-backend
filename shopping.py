from utils import normalize_text_ai
from db import get_db_connection
from utils import web_search_context
from utils import get_last_user_text
from utils import full_conversation
import psycopg2
import json
import re
import os
from openai import OpenAI
import os

CATEGORIES_CACHE = None
from db import get_all_categories

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

import re

def get_db_connection():
    return psycopg2.connect("postgresql://gorealaiuser:qN40CJZK3bxkZp8hFF41VEVYPKasEuyj@dpg-d6j2vr1aae7s739bvo60-a.frankfurt-postgres.render.com:5432/gorealai_0d5w")

def create_products_table():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            title TEXT,
            description TEXT,
            category TEXT,
            brand TEXT,
            price NUMERIC,
            url TEXT,
            normalized TEXT
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

    print("Products table ready.")   

  

def ai_extract_search_intent(conversation, client):

    user_texts = [
        m.get("text", "")
        for m in conversation
        if isinstance(m, dict) and m.get("isUser")
    ]

    full_text = " ".join(user_texts)

    prompt = f"""
You are a strict AI intent classifier for a Greek shopping assistant.

Your job is to decide EXACTLY what the user wants.

Conversation:
{full_text}

---

INTENT RULES:

1. knowledge_question:
   - User asks for information, comparisons, recommendations
   - "ποιο είναι το καλύτερο iPhone"
   - "what is the best laptop"
   - "ποια είναι η διαφορά μεταξύ A και B"

2. product_search:
   - User wants to BUY something specific
   - Writes a product name directly
   - Says "θέλω να αγοράσω", "ψάχνω", "θέλω"
   - "iphone 16 pro", "Samsung Galaxy A55"

3. product_question:
   - User needs help deciding what to buy
   - "χρειάζομαι βοήθεια", "δεν ξέρω τι να διαλέξω"
   - Asks for advice before buying

---

SEARCH KEYWORDS RULES (VERY IMPORTANT):

- Extract ONLY the product name/description
- NEVER include: "αγορά", "θέλω", "buy", "purchase", "ψάχνω", "χρειάζομαι"
- NEVER include shopping intent words
- Keep attributes like color, size, material

Good examples:
- "θέλω να αγοράσω iPhone 16" → search_keywords_en: "iPhone 16"
- "αγορά καναπέ μαύρος εξωτερικού χώρου" → search_keywords_gr: "καναπές εξωτερικού χώρου μαύρος"
- "ψάχνω laptop για φοιτητή 600 ευρώ" → search_keywords_en: "laptop student"
- "θέλω ακουστικά για τρέξιμο" → search_keywords_en: "running earphones"
- "θέλω να αγοράσω" (ΜΟΝΟ αυτό, χωρίς προϊόν) → search_keywords_en: ""

Bad examples (NEVER do this):
- "αγορά iPhone 16" ❌ (περιέχει "αγορά")
- "buy Samsung" ❌ (περιέχει "buy")
- "θέλω καναπέ" ❌ (περιέχει "θέλω")

---

BUDGET RULES:
- Extract numeric budget if mentioned
- "γύρω στα 100", "μέχρι 200", "under 150" → budget_max: 100/200/150
- "οικονομικό", "φτηνό", "cheap" → budget_max: null (no specific number)

---

ATTRIBUTES RULES:
- Extract specific features: color, size, material, use case
- "μαύρος", "black" → attributes: ["μαύρος"]
- "εξωτερικού χώρου" → attributes: ["εξωτερικού χώρου"]
- "για gaming" → attributes: ["gaming"]

---

Return ONLY valid JSON (no explanation, no markdown):

{{
  "intent_type": "knowledge_question | product_search | product_question",
  "category": null,
  "brand": null,
  "model": null,
  "attributes": [],
  "search_keywords_en": "",
  "search_keywords_gr": "",
  "budget_max": null
}}
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()
        
        # Καθαρισμός markdown αν υπάρχει
        result = result.replace("```json", "").replace("```", "").strip()
        
        print("AI RAW RESPONSE:", result, flush=True)

        try:
            data = json.loads(result)
        except:
            return {
                "intent_type": "product_search",
                "category": None,
                "brand": None,
                "model": None,
                "attributes": [],
                "search_keywords_en": "",
                "search_keywords_gr": "",
                "budget_max": None
            }

        # Safety fallbacks
        if "search_keywords_en" not in data:
            data["search_keywords_en"] = ""
        if "search_keywords_gr" not in data:
            data["search_keywords_gr"] = ""
        if "attributes" not in data:
            data["attributes"] = []

        return data

    except Exception as e:
        print("AI INTENT ERROR:", e, flush=True)
        return {
            "intent_type": "product_search",
            "category": None,
            "brand": None,
            "model": None,
            "attributes": [],
            "search_keywords_en": "",
            "search_keywords_gr": "",
            "budget_max": None
        }



def get_db_categories():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT category80
        FROM products
        WHERE category80 IS NOT NULL
        LIMIT 200
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    categories =  [r[0] for r in rows if r[0]]
    return categories

# =====================================================
# AI RESOLVE CATEGORY
# =====================================================        
def ai_resolve_category(user_query, categories, client):

    prompt = f"""
User query: "{user_query}"

Available categories:
{", ".join(categories)}

IMPORTANT:
- Return ONLY ONE category from the list above
- Do NOT create new categories
- If unsure, return "other"

Answer:
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )

    return completion.choices[0].message.content.strip() 

# =====================================================
# DATABASE PRODUCT FETCH
# =====================================================

def fetch_products_from_db(profile, limit=20):

    conn = get_db_connection()
    cur = conn.cursor()

    sql = """
    SELECT title, description, brand, price, url
    FROM products
    WHERE 1=1
    """

    params = []

    # ---------------------------------------
    # 🔍 BUILD SMART SEARCH
    # ---------------------------------------

    search_parts = []

    if profile.get("model"):
        search_parts.append(profile["model"])

    if profile.get("brand"):
        search_parts.append(profile["brand"])

    if profile.get("attributes"):
        search_parts.extend(profile["attributes"])

    search_query = " ".join(search_parts).strip().lower()

    # ---------------------------------------
    # 🔍 TOKEN SEARCH (IMPORTANT)
    # ---------------------------------------

    if search_query:
        tokens = search_query.split()

        for token in tokens:
            sql += " AND (LOWER(title) LIKE %s OR LOWER(description) LIKE %s)"
            params.append(f"%{token}%")
            params.append(f"%{token}%")

    # ---------------------------------------
    # 💰 BUDGET
    # ---------------------------------------

    if profile.get("budget_max"):
        sql += " AND price <= %s"
        params.append(profile["budget_max"])

    # ---------------------------------------
    # 🏷️ CATEGORY (optional, light filter)
    # ---------------------------------------

    if profile.get("category"):
        sql += " AND category80 ILIKE %s"
        params.append(f"%{profile['category']}%")

    # ---------------------------------------
    # LIMIT
    # ---------------------------------------

    sql += " LIMIT %s"
    params.append(limit)

    # ---------------------------------------
    # EXECUTE
    # ---------------------------------------

    cur.execute(sql, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "title": r[0],
            "description": r[1],
            "brand": r[2],
            "price": float(r[3]) if r[3] else 0,
            "url": r[4]
        }
        for r in rows
    ]

def apply_attribute_filters(products, attributes):

    if not attributes:
        return products

    filtered = []

    for p in products:
        text = (p["title"] + " " + p["description"]).lower()

        if all(attr.lower() in text for attr in attributes):
            filtered.append(p)

    return filtered        
# =========================================
# PROFILE BUILDER
# =========================================

def build_profile_from_intent(intent):

    return {
        "category": intent.get("category"),
        "brand": intent.get("brand"),
        "model": intent.get("model"),
        "budget_max": intent.get("budget_max"),
        "attributes": intent.get("attributes", [])
    }

def get_missing_fields(profile):

    missing = []

    if not profile.get("budget_max"):
        missing.append("budget")

    if not profile.get("attributes"):
        missing.append("attributes")

    return missing    
# =========================================
# PROFILE COMPLETENESS CHECK
# =========================================

def is_profile_complete_ai(profile):

    prompt = f"""
Είσαι AI σύμβουλος αγορών.

Αυτό είναι το προφίλ χρήστη:
{profile}

Στόχος:
Να αποφασίσεις αν έχουμε αρκετές πληροφορίες για να προτείνουμε προϊόντα.

Κανόνες:
- Αν υπάρχει category → ΟΚ
- Αν υπάρχει budget → ΟΚ
- 🔥 Αν ο χρήστης πει "δεν ξέρω", "οτιδήποτε", "δεν έχω συγκεκριμένο" για budget → ΟΚ
- Αν υπάρχει τουλάχιστον 1 attribute → ΟΚ

ΔΕΝ χρειάζονται:
- brand
- model

Αν ισχύουν τα παραπάνω → YES
Αλλιώς → NO

Απάντησε ΜΟΝΟ με YES ή NO.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0
    )

    answer = response.choices[0].message.content.strip().upper()

    return "YES" in answer

def generate_next_question_ai(profile, history, client, missing):

    prompt = f"""
Είσαι elite AI σύμβουλος αγορών.

Δεν είσαι chatbot.
Είσαι προσωπικός βοηθός που βοηθά τον χρήστη να πάρει ΤΗΝ ΚΑΛΥΤΕΡΗ απόφαση.

ΣΤΟΧΟΣ:
Να καταλάβεις πραγματικά τι θέλει ο χρήστης και να τον καθοδηγήσεις.

---

PROFILE:
{profile}

---
ΣΗΜΑΝΤΙΚΟ:
- Αν ο χρήστης έχει πει "δεν ξέρω" ή "δεν έχω συγκεκριμένο" για budget → θεώρησέ το ΩΣ ΑΠΑΝΤΗΜΕΝΟ
- ΜΗΝ ξαναρωτάς για budget αν έχει ήδη απαντηθεί
- Κάνε ΜΟΝΟ 1 ερώτηση για αυτό που λείπει

ΟΔΗΓΙΕΣ:

1. ΜΗΝ κάνεις απλά ερωτήσεις.
→ ΚΑΘΟΔΗΓΗΣΕ τον χρήστη

2. ΚΑΝΕ ΕΞΥΠΝΕΣ ΠΡΟΤΑΣΕΙΣ:
πχ:
- "Θες τραπεζαρία μόνο ή με καρέκλες;"
- "Σε ενδιαφέρει πιο μοντέρνο ή κλασικό στυλ;"
- "Το θέλεις για καθημερινή χρήση ή πιο διακοσμητικό;"

3. ΔΕΙΞΕ ΟΤΙ ΣΚΕΦΤΕΣΑΙ:
- πρότεινε επιλογές
- βοήθα τον να αποφασίσει

4. ΜΙΛΑ ΣΑΝ ΑΝΘΡΩΠΟΣ:
- φιλικά
- φυσικά
- σύντομα

5. ΚΑΝΕ ΜΟΝΟ 1 ερώτηση κάθε φορά

---

ΑΠΑΝΤΗΣΗ:
"""

    messages = [
        {"role": "system", "content": prompt}
    ]

    # μετατρέπουμε history σε σωστό format
    for i, msg in enumerate(history[-5:]):
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            messages.append(msg)
        else:
            role = "user" if i % 2 == 0 else "assistant"

            if isinstance(msg, dict):
                content = msg.get("text") or msg.get("reply") or ""
            else:
                content = str(msg)

            messages.append({
                "role": role,
                "content": content
            })

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.4
    )

    return response.choices[0].message.content.strip()  

def generate_recommendations(mode, conversation, user_id, client):

    user_text = get_last_user_text(conversation)

    intent = ai_extract_search_intent(conversation, client)
    profile = build_profile_from_intent(intent)

    # =========================
    # COMPARISON MODE
    # =========================

    if intent.get("intent_type") == "product_question":

        user_text = get_last_user_text(conversation)

        compare_prompt = f"""
        Είσαι expert σύμβουλος αγορών.

        Ο χρήστης ρωτάει:
        {user_text}

        Στόχος:
        - Δώσε καθαρή απάντηση
        - Πρότεινε τι είναι καλύτερο
        - Κάνε μια μικρή ώθηση για αγορά

        ΤΕΛΕΙΩΣΕ με:
        μια φράση που οδηγεί σε επιλογές

        Απάντηση:
        """

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": compare_prompt}],
            temperature=0.5
        )

        reply = completion.choices[0].message.content.strip()

        # 👉 ΔΕΝ ΣΤΑΜΑΤΑΣ ΕΔΩ
        # 👉 Συνεχίζεις σε links

        query = intent.get("search_keywords_gr") or intent.get("search_keywords_en") or user_text

        import urllib.parse
        encoded = urllib.parse.quote(query)

        links = [
            {
                "title": "Δες στο Skroutz",
                "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"
            },
            {
                "title": "Δες στο Google Shopping",
                "url": f"https://www.google.com/search?q={encoded}&tbm=shop"
            }
        ]

        return {
            "reply": reply,
            "links": links,
            "showButton": True   
        }
    # =========================
    # PROFILE COMPLETENESS CHECK
    # =========================

    missing = get_missing_fields(profile)

    if not is_profile_complete_ai(profile):

        question = generate_next_question_ai(profile, conversation, client, missing)

        return {
            "reply": question,
            "links": [],
            "showButton": False
        }

    query = intent.get("search_keywords_gr") or intent.get("search_keywords_en")
    products = fetch_products_from_db(profile)

    products = apply_attribute_filters(products, profile.get("attributes"))

    top_products = products[:3]
    # =========================
    # DECISION ENGINE
    # =========================

    decision_prompt = f"""
    Είσαι elite AI σύμβουλος αγορών.

    Ο χρήστης θέλει:
    {profile}

    Βρήκες αυτά τα προϊόντα:
    {top_products}

    Στόχος:
    - Σύγκρινε τα προϊόντα
    - Πες ποιο είναι καλύτερο
    - Εξήγησε γιατί
    - Δώσε 1 ξεκάθαρη πρόταση

    Κανόνες:
    - Μίλα απλά και ξεκάθαρα
    - Μην μπερδεύεις τον χρήστη
    - Μην δίνεις πολλές επιλογές
    - Βοήθα τον να αποφασίσει

    Απάντηση:
    """
    

    decision = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": decision_prompt}],
        temperature=0.5
    )

    decision_reply = decision.choices[0].message.content.strip()

    if not query:
        query = profile.get("model") or profile.get("category") or "product"

    import urllib.parse
    encoded = urllib.parse.quote(query)
    
    # =========================
    # READY TO SHOW LINKS?
    # =========================

    ready_to_show_links = (
        profile.get("category") is not None
        and (profile.get("attributes") or profile.get("budget_max"))
    )

    links = [
            {
                "title": "Δες στο Skroutz",
                "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"
            },
            {
                "title": "Δες στο Google Shopping",
                "url": f"https://www.google.com/search?q={encoded}&tbm=shop"
            }
        ]

    if ready_to_show_links:

        advisor_prompt = f"""
        Ο χρήστης θέλει:
        {profile}

        Γράψε μια σύντομη φιλική πρόταση που:
        - δίνει σιγουριά
        - ενθαρρύνει την επιλογή
        """

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": advisor_prompt}],
            temperature=0.5
        )

        final_reply = completion.choices[0].message.content.strip()

        return {
            "reply": decision_reply + "\n\n" + final_reply,
            "links": links,
            "showButton": True,
            "phase": "ready"   # 🔥 ΠΡΟΣΘΗΚΗ
        }

    else:
        return {
            "reply": decision_reply,
            "links": [],
            "showButton": False,
            "phase": "explore"   # 🔥 ΠΡΟΣΘΗΚΗ
        }
        
def ai_analyze_image_shopping(image_base64, user_text, client):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                },
                {
                    "type": "text",
                    "text": f"""Analyze this product image and return ONLY a JSON object, no markdown, no explanation.

User message: {user_text or 'What product is this?'}

Return this exact JSON format:
{{"product_name": "short product name", "brand": "brand name", "search_query": "max 4 words", "best_site": {{"name": "Site Name", "url": "full search URL"}}}}

RULES for search_query:
- Maximum 4 words
- Only brand + product type
- NO measurements, NO sizes, NO technical specs
- NO logos, NO packaging descriptions

Examples:
- Cigarette filters Long Extra Slim → search_query: "Long cigarette filters"
- Samsung Galaxy A55 128GB Blue → search_query: "Samsung Galaxy A55"
- Nescafe Classic 200g → search_query: "Nescafe Classic"
- Nike Air Max 90 White Size 42 → search_query: "Nike Air Max 90"

RULES for best_site - choose the MOST RELEVANT site based on product type:

📱 ELECTRONICS & PHONES:
- Smartphones, tablets → {{"name": "Public", "url": "https://www.public.gr/search/?q=QUERY"}}
- Laptops, computers → {{"name": "Plaisio", "url": "https://www.plaisio.gr/search?q=QUERY"}}
- TVs, monitors → {{"name": "Kotsovolos", "url": "https://www.kotsovolos.gr/search?q=QUERY"}}
- Cameras → {{"name": "Public", "url": "https://www.public.gr/search/?q=QUERY"}}
- Gaming consoles → {{"name": "Plaisio", "url": "https://www.plaisio.gr/search?q=QUERY"}}
- PC components → {{"name": "Multirama", "url": "https://www.multirama.gr/search?q=QUERY"}}
- Smart home → {{"name": "Kotsovolos", "url": "https://www.kotsovolos.gr/search?q=QUERY"}}

🏠 HOME & FURNITURE:
- Furniture, sofas, beds → {{"name": "IKEA", "url": "https://www.ikea.com/gr/el/search/?q=QUERY"}}
- Home decoration → {{"name": "Zara Home", "url": "https://www.zarahome.com/gr/search?q=QUERY"}}
- Kitchen appliances → {{"name": "Kotsovolos", "url": "https://www.kotsovolos.gr/search?q=QUERY"}}
- Cookware, kitchenware → {{"name": "IKEA", "url": "https://www.ikea.com/gr/el/search/?q=QUERY"}}
- Bedding, pillows → {{"name": "IKEA", "url": "https://www.ikea.com/gr/el/search/?q=QUERY"}}
- Bathroom accessories → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- Lighting → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- Storage → {{"name": "IKEA", "url": "https://www.ikea.com/gr/el/search/?q=QUERY"}}
- Curtains, rugs → {{"name": "IKEA", "url": "https://www.ikea.com/gr/el/search/?q=QUERY"}}

🔧 TOOLS & DIY:
- Power tools → {{"name": "Praktiker", "url": "https://www.praktiker.gr/search?q=QUERY"}}
- Hand tools → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- Building materials → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- Paint → {{"name": "Praktiker", "url": "https://www.praktiker.gr/search?q=QUERY"}}
- Garden tools → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}

👗 FASHION & CLOTHES:
- Women clothes → {{"name": "Zara", "url": "https://www.zara.com/gr/el/search?searchTerm=QUERY"}}
- Men clothes → {{"name": "Zara", "url": "https://www.zara.com/gr/el/search?searchTerm=QUERY"}}
- Kids clothes → {{"name": "H&M", "url": "https://www2.hm.com/el_gr/search-results.html?q=QUERY"}}
- Casual wear → {{"name": "H&M", "url": "https://www2.hm.com/el_gr/search-results.html?q=QUERY"}}
- Luxury fashion → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Underwear, socks → {{"name": "H&M", "url": "https://www2.hm.com/el_gr/search-results.html?q=QUERY"}}
- Swimwear → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Winter jackets → {{"name": "Zara", "url": "https://www.zara.com/gr/el/search?searchTerm=QUERY"}}

👟 SHOES:
- Sneakers → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Sports shoes → {{"name": "Intersport", "url": "https://www.intersport.gr/search?q=QUERY"}}
- Formal shoes → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Kids shoes → {{"name": "Jumbo", "url": "https://www.e-jumbo.gr/search?q=QUERY"}}
- Boots → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}

👜 BAGS & ACCESSORIES:
- Handbags, backpacks → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Watches → {{"name": "Skroutz", "url": "https://www.skroutz.gr/search?keyphrase=QUERY"}}
- Jewelry → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Sunglasses → {{"name": "Asos", "url": "https://www.asos.com/gr/search/?q=QUERY"}}
- Belts, scarves → {{"name": "Zara", "url": "https://www.zara.com/gr/el/search?searchTerm=QUERY"}}

💄 BEAUTY & COSMETICS:
- Perfumes → {{"name": "Notino", "url": "https://www.notino.gr/search/?phrase=QUERY"}}
- Makeup → {{"name": "Sephora", "url": "https://www.sephora.gr/search?q=QUERY"}}
- Skincare → {{"name": "Notino", "url": "https://www.notino.gr/search/?phrase=QUERY"}}
- Hair products → {{"name": "Hondos Center", "url": "https://www.hondoscenter.gr/search?q=QUERY"}}
- Men grooming → {{"name": "Notino", "url": "https://www.notino.gr/search/?phrase=QUERY"}}

💊 PHARMACY & HEALTH:
- Vitamins, supplements → {{"name": "Pharmex", "url": "https://www.pharmex.gr/search?q=QUERY"}}
- Medical devices → {{"name": "Pharmex", "url": "https://www.pharmex.gr/search?q=QUERY"}}
- Baby health → {{"name": "Pharmex", "url": "https://www.pharmex.gr/search?q=QUERY"}}

🍎 FOOD & SUPERMARKET:
- Fresh food, groceries → {{"name": "Sklavenitis", "url": "https://www.sklavenitis.gr/search?q=QUERY"}}
- Packaged food → {{"name": "AB Vassilopoulos", "url": "https://www.ab.gr/search?q=QUERY"}}
- Beverages → {{"name": "My Market", "url": "https://www.mymarket.gr/search?q=QUERY"}}
- Organic food → {{"name": "e-fresh", "url": "https://www.e-fresh.gr/search?q=QUERY"}}
- Coffee, tea → {{"name": "e-fresh", "url": "https://www.e-fresh.gr/search?q=QUERY"}}

🏋️ SPORTS & FITNESS:
- Running, training → {{"name": "Intersport", "url": "https://www.intersport.gr/search?q=QUERY"}}
- Gym equipment → {{"name": "Decathlon", "url": "https://www.decathlon.gr/search?Ntt=QUERY"}}
- Cycling → {{"name": "Decathlon", "url": "https://www.decathlon.gr/search?Ntt=QUERY"}}
- Football, basketball → {{"name": "Intersport", "url": "https://www.intersport.gr/search?q=QUERY"}}
- Outdoor, hiking → {{"name": "Decathlon", "url": "https://www.decathlon.gr/search?Ntt=QUERY"}}
- Sportswear → {{"name": "SportsDirect", "url": "https://www.sportsdirect.com/search?stext=QUERY"}}

🐾 PETS:
- Dog food, cat food → {{"name": "Zooplus", "url": "https://www.zooplus.gr/shop/search?keyword=QUERY"}}
- Pet accessories → {{"name": "Petshop365", "url": "https://www.petshop365.gr/search?q=QUERY"}}

👶 KIDS & BABY:
- Toys, games → {{"name": "Jumbo", "url": "https://www.e-jumbo.gr/search?q=QUERY"}}
- Baby clothes → {{"name": "H&M", "url": "https://www2.hm.com/el_gr/search-results.html?q=QUERY"}}
- Baby gear → {{"name": "Skroutz", "url": "https://www.skroutz.gr/search?keyphrase=QUERY"}}
- School supplies → {{"name": "Jumbo", "url": "https://www.e-jumbo.gr/search?q=QUERY"}}

📚 BOOKS & MUSIC:
- Books → {{"name": "Public", "url": "https://www.public.gr/search/?q=QUERY"}}
- Music instruments → {{"name": "Musicland", "url": "https://www.musicland.gr/search?q=QUERY"}}

🚗 AUTOMOTIVE:
- Car parts → {{"name": "Autodoc", "url": "https://www.autodoc.gr/search/QUERY"}}
- Car accessories → {{"name": "Skroutz", "url": "https://www.skroutz.gr/search?keyphrase=QUERY"}}
- Motorcycle parts → {{"name": "Moto Discount", "url": "https://www.motodiscount.gr/search?q=QUERY"}}

🌱 GARDEN & OUTDOOR:
- Plants, seeds → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- Garden furniture → {{"name": "Leroy Merlin", "url": "https://www.leroymerlin.gr/search?q=QUERY"}}
- BBQ, grills → {{"name": "Praktiker", "url": "https://www.praktiker.gr/search?q=QUERY"}}

🚬 TOBACCO:
- Cigarettes, filters, tobacco → {{"name": "Google Shopping", "url": "https://www.google.com/search?q=QUERY&tbm=shop"}}
- E-cigarettes → {{"name": "Skroutz", "url": "https://www.skroutz.gr/search?keyphrase=QUERY"}}

🎭 DEFAULT:
- Anything else → {{"name": "Google Shopping", "url": "https://www.google.com/search?q=QUERY&tbm=shop"}}

IMPORTANT: Replace QUERY in the URL with the actual search_query value (URL encoded, spaces as +).
"""
                }
            ]
        }],
        max_tokens=400
    )
    result = response.choices[0].message.content.strip()
    result = result.replace("```json", "").replace("```", "").strip()
    return json.loads(result)

import os
import requests

def search_products_serper(query, max_price=None):
    """Ψάχνει πραγματικά προϊόντα από Google Shopping μέσω Serper"""
    
    serper_key = os.environ.get("SERPER_API_KEY")
    
    headers = {
        "X-API-KEY": serper_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "q": query,
        "gl": "gr",  # Ελλάδα
        "hl": "el",  # Ελληνικά
        "num": 5     # 5 αποτελέσματα
    }
    
    response = requests.post(
        "https://google.serper.dev/shopping",
        headers=headers,
        json=payload
    )
    
    data = response.json()
    results = []
    
    for item in data.get("shopping", []):
        price_str = item.get("price", "")
        
        # Φίλτρο τιμής αν υπάρχει
        if max_price and price_str:
            try:
                price_num = float(price_str.replace("€", "").replace(",", ".").strip())
                if price_num > max_price:
                    continue
            except:
                pass
        
        results.append({
            "title": item.get("title", ""),
            "price": price_str,
            "source": item.get("source", ""),
            "link": item.get("link", ""),
            "imageUrl": item.get("imageUrl", "")
        })
    
    return results[:3]  # Επιστρέφει max 3 προϊόντα    