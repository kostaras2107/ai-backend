from utils import normalize_text
from db import get_db_connection
from utils import web_search_context
from utils import get_last_user_text
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

def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def get_full_conversation(conversation):
    texts = []
    for msg in conversation:
        if msg.get("isUser") and msg.get("text"):
            texts.append(msg.get("text"))
    return " ".join(texts)

def get_last_user_text(conversation):
    for msg in reversed(conversation):
        if msg.get("isUser"):
            return msg.get("text", "")
    return ""

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

def resolve_final_category(search_text, categories, client):
    ai_category = ai_resolve_category(search_text, categories, client)

    normalized = normalize_category(ai_category)

    if not normalized or normalized == "other":
        return ""

    if normalized not in categories:
        return ""

    return normalized    


def normalize_category(cat):
    if not cat:
        return None

    c = cat.lower().strip()

    mapping = {
        "phone": "smartphones",
        "smartphone": "smartphones",
        "mobile": "smartphones",
        "iphone": "smartphones",

        "book": "books",
        "toy": "toys",
        "game": "toys",

        "fitness": "sports",

        "instrument": "music",

        "bag": "bags",

        "clothing": "fashion",

        "tv": "electronics",
        "audio": "electronics",

        "coffee": "appliances"
    }

    return mapping.get(c, c)

# =====================================================
# COLORS
# =====================================================
def remove_color_tokens(tokens):

    colors = {
        "white","black","silver","gold","blue","red","green","yellow",
        "λευκο","ασπρο","μαυρο","χρυσο","ασημι","μπλε","κοκκινο"
    }

    return [t for t in tokens if t not in colors]

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

---

VERY IMPORTANT:

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

        clean = extract_json(result)
        data = json.loads(clean)
        print("AI RAW RESPONSE:", result, flush=True)
        print("AI CLEAN JSON:", clean, flush=True)

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

# =====================================================
# AI INTENT ENGINE
# =====================================================

def ai_extract_intent(conversation, client):

    prompt = f"""
Διάβασε τη συνομιλία και βρες τι προϊόν ψάχνει ο χρήστης.

Συνομιλία:
{full_conversation(conversation)}

Επέστρεψε ΜΟΝΟ JSON:

{{
 "product_type": "...",
 "keywords": "...",
 "budget_max": number or null
}}
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()

        return json.loads(result)

    except:
        return {
            "product_type": None,
            "keywords": None,
            "budget_max": None
        }   
# =====================================================
# BUILD DECISION
# =====================================================    


def build_decision_profile(conversation):

    # =========================================
    # 1️⃣ Συλλογή ΟΛΗΣ της συζήτησης (μόνο user)
    # =========================================

    if isinstance(conversation, list):

        user_texts = [
            m.get("text", "")
            for m in conversation
            if isinstance(m, dict) and m.get("isUser")
        ]

        full_text = " ".join(user_texts)

    elif isinstance(conversation, str):
        full_text = conversation

    else:
        full_text = str(conversation)

    full_text = normalize_text(full_text)

    # =========================================
    # 2️⃣ Profile base
    # =========================================

    profile = {
        "mode": "shopping",
        "budget_min": None,
        "budget_max": None,
        "query_text": full_text,
        "brands": [],
        "descriptive_tokens": [],
        "numeric_tokens": []
    }
    print("PROFILE CATEGORY:", profile.get("category"), flush=True)
    # =========================================
    # 3️⃣ Budget extraction (κρατάμε το μεγαλύτερο)
    # =========================================

    budgets = re.findall(r"(?:μεχρι|εως)?\s*(\d+)\s*(?:ευρω|euro|€)", full_text)
    if budgets:
        profile["budget_max"] = max(int(b) for b in budgets)

    # =========================================
    # 4️⃣ Numeric tokens (π.χ 128gb, 4k κλπ)
    # =========================================

    profile["numeric_tokens"] = re.findall(r"\d+", full_text)

    # =========================================
    # 5️⃣ Token extraction
    # =========================================

    stopwords = {
        "θελω","ψαχνω","να","και","μεχρι","ευρω","το","την","ενα","μια",
        "μου","για","που","σε","θα","με","στο","στη","εως","τι","ειναι",
        "κατι","καποιο","καποια","εχει","να","απο"
    }

    tokens = re.findall(r"\b[a-zA-Zα-ωΑ-Ω0-9]+\b", full_text)

    clean_tokens = [
        t for t in tokens
        if t not in stopwords 
    ]

    profile["descriptive_tokens"] = list(set(clean_tokens))

    # =========================================
    # 6️⃣ Brand detection (χωρίς stopwords)
    # =========================================

    # Τα brands ΔΕΝ είναι όλες οι λέξεις.
    # Είναι λέξεις που δεν είναι stopwords και δεν είναι περιγραφικές κοινές.

    possible_brands = [
        t for t in clean_tokens
        if t not in stopwords
    ]

    profile["brands"] = list(set(possible_brands))

    return profile
# =====================================================
# AI SINGLE QUESTION
# =====================================================
def ai_advisor_response(conversation):

    conversation_text = full_conversation(conversation)

    web_info = web_search_context(conversation_text)

    prompt = f"""
Είσαι προσωπικός σύμβουλος αγορών.

Ο ρόλος σου είναι να βοηθάς τον χρήστη να βρει το ιδανικό προϊόν.

Σκέψου πρώτα τι πραγματικά θέλει να αγοράσει ο χρήστης
και ποια πληροφορία λείπει.

Think step-by-step about the user's real buying intent.

Συνομιλία:
{conversation_text}

Πληροφορίες από internet:
{web_info}

Κανόνες:

- Μην λες καλησπέρα ή καλωσόρισμα
- Κράτα την απάντηση σύντομη (2-3 προτάσεις)
- Μίλα φυσικά σαν άνθρωπος
- Κάνε ΜΙΑ έξυπνη ερώτηση αν λείπει πληροφορία
- Μην κάνεις μεγάλες αναλύσεις
- Βοήθησε τον χρήστη να καταλήξει σε επιλογή

Απάντησε σαν advisor.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0.4
    )

    return {
        "reply": completion.choices[0].message.content.strip(),
        "links": [],
        "showButton": False
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

def fetch_products_from_db(mode, profile, limit=40):

    conn = get_db_connection()
    cur = conn.cursor()
    params = []

    # ------------------------------------
    # BUILD SEARCH QUERY
    # ------------------------------------

    search_query = (
        (profile.get("search_keywords_en") or "") + " " +
        (profile.get("search_keywords_gr") or "") + " " +
        (profile.get("query_text") or "")
    ).strip()
    print("RAW SEARCH QUERY:", search_query, flush=True)

    if not search_query:
        cur.close()
        conn.close()
        return []

    print("FINAL SEARCH QUERY:", search_query, flush=True)

    search_query = search_query.strip()
    print("CLEAN SEARCH QUERY:", search_query, flush=True)

    # ------------------------------------
    # BASE SQL
    # ------------------------------------

    sql = """
    SELECT
        title,
        description,
        brand,
        product_type,
        price,
        url,
        ts_rank(search_vector, websearch_to_tsquery('simple', %s)) AS rank
    FROM products
    WHERE
        search_vector @@ websearch_to_tsquery('simple', %s)
        AND in_stock = true
    """

    # ✅ σωστά params για search
    params.append(search_query)
    params.append(search_query)

    # ------------------------------------
    # CATEGORY
    # ------------------------------------

    category = profile.get("category")

    if category:
        sql += " AND category80 ILIKE %s"
        params.append(f"%{category}%")



    print("CATEGORY USED:", category)

    # ------------------------------------
    # BUDGET
    # ------------------------------------

    budget = profile.get("budget_max")

    if budget:
        sql += " AND price <= %s"
        params.append(budget)

    # ------------------------------------
    # ORDER + LIMIT (ΜΟΝΟ ΜΙΑ ΦΟΡΑ)
    # ------------------------------------

    sql += """
    ORDER BY rank DESC, price ASC
    LIMIT %s
    """

    params.append(limit)

    # ------------------------------------
    # EXECUTE
    # ------------------------------------

    print("SQL:", sql)
    print("PARAMS:", params)
    print("FINAL QUERY SENT TO DB:", params[0], flush=True)

    cur.execute(sql, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    products = []

    for r in rows:
        products.append({
            "title": r[0],
            "description": r[1],
            "brand": r[2],
            "category": r[3],
            "price": float(r[4]) if r[4] else 0,
            "url": r[5]
        })

    print("DB RESULTS:", len(products), flush=True)

    return products

# =====================================================
# DETERMINISTIC SCORING ENGINE (DB VERSION)
# =====================================================


def score_products(products, profile):

    scored = []

    for p in products:

        score = 0

        title = (p["title"] or "").lower()
        desc = (p["description"] or "").lower()
        brand = (p["brand"] or "").lower()

        # ---------------------------------
        # Brand match
        # ---------------------------------

        for b in profile.get("brands", []):
            if b.lower() in brand:
                score += 8

        # ---------------------------------
        # Title tokens
        # ---------------------------------

        for token in profile.get("descriptive_tokens", []):
            if token in title:
                score += 6

        # ---------------------------------
        # Description tokens
        # ---------------------------------

        for token in profile.get("descriptive_tokens", []):
            if token in desc:
                score += 3

        # ---------------------------------
        # Numeric features
        # ---------------------------------


        numbers = extract_numbers(title + " " + desc)

        for num in profile.get("numeric_tokens", []):
            if num in numbers:
                score += 6
        # ---------------------------------
        # Budget proximity
        # ---------------------------------

        if profile.get("budget_max") and p.get("price"):

            diff = profile["budget_max"] - float(p["price"])

            if diff >= 0:
                score += 4 - (diff / profile["budget_max"])

        scored.append({
            **p,
            "decision_score": round(score, 2)
        })

    scored.sort(key=lambda x: x["decision_score"], reverse=True)

    return scored    

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

    if not profile.get("category"):
        missing.append("category")

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
Είσαι expert σύμβουλος αγορών.

Profile χρήστη:
{profile}

Λείπουν τα εξής:
{missing}

Κάνε ΜΟΝΟ ΜΙΑ στοχευμένη ερώτηση για να συμπληρώσεις το πιο σημαντικό.

Κανόνες:
- Αν λείπει budget → ρώτα για τιμή
- Αν λείπουν features → ρώτα τι προτιμά (π.χ. μέγεθος, χρήση)
- Αν υπάρχει model → ΜΗΝ ξαναρωτήσεις τι προϊόν θέλει
- Μίλα φυσικά σαν σύμβουλος

ΜΗΝ δώσεις επιλογές ακόμα.
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

def ai_select_category(user_input, categories, client):
    import re

    prompt = f"""
    Είσαι σύστημα κατηγοριοποίησης.

    Διαθέσιμες κατηγορίες (ΑΚΡΙΒΩΣ όπως είναι στη βάση):
    {categories}

    ΚΑΝΟΝΕΣ:
    - Διάλεξε ΜΟΝΟ από αυτές
    - Απάντησε με ΑΚΡΙΒΩΣ το string
    - Μην αλλάξεις λέξεις (ούτε singular/plural)

    User:
    {user_input}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0
    )

    raw = response.choices[0].message.content.strip().lower()

    clean = re.sub(r"[^a-z0-9_ ]", "", raw).strip()

    for cat in categories:
        if cat in clean:
            return cat

    return clean.split()[0]

def generate_recommendations(mode, conversation, user_id, client):

    print("=== NEW FLOW START ===", flush=True)

    user_text = get_last_user_text(conversation)
    full_text = get_full_conversation(conversation)

    # ---------------------------------------
    # 1. AI ADVISOR (ONE CALL ONLY)
    # ---------------------------------------

    prompt = f"""
Είσαι προσωπικός σύμβουλος αγορών.

Στόχος:
- Να καταλάβεις τι θέλει να αγοράσει ο χρήστης
- Να τον καθοδηγήσεις
- Να αποφασίσεις αν είσαι έτοιμος να δείξεις προϊόντα

VERY IMPORTANT:

- Αν ο χρήστης γράψει απλά όνομα προϊόντος (π.χ. "iphone 16 pro")
→ ΘΕΩΡΕΙΤΑΙ ΠΑΝΤΑ πρόθεση αγοράς

- ΜΗΝ δίνεις γενικές πληροφορίες
- ΜΗΝ απαντάς σαν Google
- Είσαι σύμβουλος αγοράς, όχι εγκυκλοπαίδεια

Συνομιλία:
{full_text}

---

ΑΝ ΔΕΝ ΕΧΕΙΣ ΑΡΚΕΤΑ ΣΤΟΙΧΕΙΑ:
- Κάνε 1 έξυπνη ερώτηση

ΑΝ ΕΧΕΙΣ ΑΡΚΕΤΑ ΣΤΟΙΧΕΙΑ:
- Πες: "Νομίζω κατάλαβα τι ψάχνεις 👇"

---

ΕΠΙΣΤΡΕΨΕ JSON:

{{
"reply": "...",
"ready": true/false,
"search_query": "..."
}}
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0.4
        )

        result = completion.choices[0].message.content.strip()

        data = json.loads(result)

    except Exception as e:
        print("AI ERROR:", e)
        return {
            "reply": "Πες μου λίγες περισσότερες λεπτομέρειες για να σε βοηθήσω καλύτερα.",
            "links": [],
            "showButton": False
        }

    reply = data.get("reply", "")
    ready = data.get("ready", False)
    search_query = data.get("search_query", "")

    print("AI READY:", ready, flush=True)
    print("AI QUERY:", search_query, flush=True)

    # ---------------------------------------
    # 2. ADVISOR MODE
    # ---------------------------------------

    if not ready:
        return {
            "reply": reply,
            "links": [],
            "showButton": False
        }

    # ---------------------------------------
    # 3. DB SEARCH
    # ---------------------------------------

    profile = {
        "query_text": search_query,
        "search_keywords_en": "",
        "search_keywords_gr": "",
        "descriptive_tokens": search_query.split(),
        "numeric_tokens": re.findall(r"\d+", search_query),
        "budget_max": None,
        "category": ""
    }

    print("GOING TO DB SEARCH", flush=True)

    candidates = fetch_products_from_db(mode, profile, limit=10)

    print("DB RESULTS:", len(candidates), flush=True)

    if not candidates:
        return {
            "reply": "Δεν βρήκα κάτι σχετικό. Θες να το ψάξουμε λίγο διαφορετικά;",
            "links": [],
            "showButton": False
        }

    # ---------------------------------------
    # 4. RESPONSE
    # ---------------------------------------

    links = [
        {
            "title": f"{item['title']} – {item['price']}€",
            "url": item["url"]
        }
        for item in candidates[:5]
    ]

    return {
        "reply": reply,
        "links": links,
        "showButton": True
    }
def recategorize_electronics_batch(client):
    import time
    import json
    import re

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, description
        FROM products
        WHERE category80 = 'electronics'
    """)

    rows = cur.fetchall()

    total = len(rows)
    print(f"TOTAL PRODUCTS: {total}")

    start_time = time.time()
    count = 0
    batch_size = 30

    def clean_json(text):
        # βρίσκει το πρώτο [...] JSON block
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    for i in range(0, total, batch_size):
        batch = rows[i:i+batch_size]

        prompt = """
Κατηγοριοποίησε τα παρακάτω προϊόντα.

Επιτρεπόμενες κατηγορίες:
smartphones, computers, accessories, tv, audio, gaming, tablets, cameras, wearables, other

ΑΠΑΝΤΗΣΗ:
Δώσε ΜΟΝΟ valid JSON.
ΜΗΝ γράψεις τίποτα άλλο.
Format:
[{"id": 123, "category": "smartphones"}]
"""

        for r in batch:
            pid = r[0]
            title = r[1] or ""
            desc = r[2] or ""

            prompt += f"\nID: {pid}\nΤίτλος: {title}\nΠεριγραφή: {desc}\n"

        success = False

        for attempt in range(3):  # retry έως 3 φορές
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}]
                )

                content = response.choices[0].message.content
                cleaned = clean_json(content)

                result = json.loads(cleaned)
                success = True
                break

            except Exception as e:
                print(f"RETRY {attempt+1} FAILED at batch {i}: {e}")
                time.sleep(1)

        if not success:
            print(f"SKIPPING BATCH {i}")
            continue

        # -----------------------------------
        # UPDATE DB
        # -----------------------------------
        for item in result:
            try:
                cur.execute("""
                    UPDATE products
                    SET category80 = %s
                    WHERE id = %s
                """, (item["category"], item["id"]))

                count += 1

            except Exception as e:
                print(f"UPDATE ERROR: {e}")

        conn.commit()

        # μικρό delay
        time.sleep(0.2)

        # progress
        if count % 1000 < batch_size:
            elapsed = time.time() - start_time
            print(f"PROCESSED: {count} | TIME: {round(elapsed, 2)} sec")

    total_time = time.time() - start_time
    print(f"DONE ✅ TOTAL TIME: {round(total_time, 2)} sec")

    cur.close()
    conn.close()

if __name__ == "__main__":
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


    recategorize_electronics_batch(client)