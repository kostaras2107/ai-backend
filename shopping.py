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

  

# =====================================================
# AI EXTRACT SEARCH INTENT
# ===================================================== 

def ai_extract_search_intent(conversation, client):

    user_texts = [
        m.get("text", "")
        for m in conversation
        if isinstance(m, dict) and m.get("isUser")
    ]

    full_text = " ".join(user_texts)

    prompt = f"""
You are a strict AI intent classifier.

Your job is to decide EXACTLY what the user wants.

Conversation:
{full_text}

---

CRITICAL RULES:

1. If the user is asking for INFORMATION (latest model, what is, which is better, etc)
→ intent_type MUST be "knowledge_question"

2. If the user wants to BUY something
→ intent_type = "product_search"

3. If the user is asking about a product before buying
→ intent_type = "product_question"
4. If user says:
"Θέλω να αγοράσω"
→ intent_type = "product_search"

5. If user says:
"Χρειάζομαι βοήθεια"
→ intent_type = "product_question"

---

VERY IMPORTANT:

- If user writes ONLY a product name (e.g. "iphone 16 pro")
→ ALWAYS intent_type = "product_search"

- Questions like:
"ποιο είναι το τελευταίο iPhone"
"what is the best..."
"which is newer..."

→ ALWAYS knowledge_question

- DO NOT guess product_search unless user clearly wants to buy

---

Return ONLY JSON:

{{
"intent_type":"knowledge_question | product_search | product_question",
"category":null,
"brand":null,
"model":null,
"attributes":[],
"search_keywords_en":"",
"search_keywords_gr":"",
"budget_max":null
}}
"""

    try:

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()
        print("AI RAW RESPONSE:", result, flush=True)

        try:
            data = json.loads(result)
        except:
            return {
                "intent_type":"product_search",
                "category":None,
                "brand":None,
                "model":None,
                "attributes":[],
                "search_keywords_en":"",
                "search_keywords_gr":"",
                "budget_max":None
            }

        # safety fallback
        if "search_keywords_en" not in data:
            data["search_keywords_en"] = ""

        if "search_keywords_gr" not in data:
            data["search_keywords_gr"] = ""

        return data

    except Exception as e:

        print("AI INTENT ERROR:", e, flush=True)

        return {
            "intent_type":"product_search",
            "category":None,
            "brand":None,
            "model":None,
            "attributes":[],
            "search_keywords_en":"",
            "search_keywords_gr":"",
            "budget_max":None
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
- Αν υπάρχει τουλάχιστον 1 attribute (π.χ. camera, battery, gaming, cheap κτλ) → ΟΚ

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
                "title": "Δες στο BestPrice",
                "url": f"https://www.bestprice.gr/search?q={encoded}"
            }
        ]

        return {
            "reply": reply,
            "links": links,
            "showButton": True   ✅🔥
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
            "title": "Δες στο BestPrice",
            "url": f"https://www.bestprice.gr/search?q={encoded}"
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
        
