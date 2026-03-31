from utils import normalize_text
from db import get_db_connection
from utils import web_search_context
from utils import get_last_user_text
import psycopg2
import json
import re
import os


def get_db_categories():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT category80 FROM products")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [r[0] for r in rows if r[0]]
CATEGORIES_CACHE = None

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
You are an advanced AI shopping intent engine.

Your job is to understand what PRODUCT the user wants to BUY.

Conversation:
{full_text}

Extract ONLY the real product attributes.

Keep ONLY:

• product type
• brand
• model
• color
• material
• size
• capacity
• numeric specifications
• key product descriptors

REMOVE:

• explanations
• usage descriptions
• sentences
• questions
• irrelevant words

IMPORTANT:

Return BOTH English and Greek search queries to improve database matching.

Examples:

User: θέλω καναπέ γωνιακό μαύρο υφασμάτινο
search_keywords_en: corner sofa black fabric
search_keywords_gr: γωνιακός καναπές μαύρος υφασμάτινος

User: θέλω iphone 16 pro 256gb άσπρο
search_keywords_en: iphone 16 pro 256gb white
search_keywords_gr: iphone 16 pro 256gb άσπρο

User: φακό για canon r100 για φεγγάρι
search_keywords_en: canon r100 lens
search_keywords_gr: φακός canon r100

User: ποδήλατο για ανηφόρες
search_keywords_en: mountain bike
search_keywords_gr: ποδήλατο βουνού

Also detect budget if mentioned.

Return ONLY JSON.

JSON FORMAT:

{{
"intent_type":"product_search | product_question | knowledge_question",
"category":"main product category",
"brand":"brand if detected",
"model":"model if detected",
"attributes":[list of attributes],
"search_keywords_en":"clean product search query in English",
"search_keywords_gr":"clean product search query in Greek",
"budget_max":number or null
}}
"""

    try:

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()

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

    if not search_query:
        cur.close()
        conn.close()
        return []

    print("FINAL SEARCH QUERY:", search_query, flush=True)

    tokens = search_query.split()
    search_query = " | ".join(tokens) + ":*"

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
        ts_rank(search_vector, to_tsquery('simple', %s)) AS rank
    FROM products
    WHERE
        search_vector @@ to_tsquery('simple', %s)
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
        sql += " AND category80 = %s"
        params.append(category)

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
        "budget": intent.get("budget_max"),
        "attributes": intent.get("attributes", [])
    }
# =========================================
# PROFILE COMPLETENESS CHECK
# =========================================

def is_profile_complete_ai(profile):

    prompt = f"""
Είσαι ειδικός σύμβουλος αγορών.

Αυτό είναι το προφίλ χρήστη:
{profile}

Πρέπει να αποφασίσεις:

Έχεις αρκετές πληροφορίες για να προτείνεις προϊόντα;

Κανόνες:
- Αν λείπουν σημαντικά στοιχεία → απάντα NO
- Αν είναι αρκετά συγκεκριμένος → YES

Απάντησε ΜΟΝΟ:
YES ή NO
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0
    )

    answer = response.choices[0].message.content.strip()

    return "YES" in answer

def generate_next_question_ai(profile):

    prompt = f"""
Είσαι expert σύμβουλος αγορών.

Αυτό είναι το προφίλ χρήστη:
{profile}

Κάνε ΜΙΑ φυσική ερώτηση που θα σε βοηθήσει να καταλάβεις καλύτερα τι θέλει.

Κανόνες:
- Μην ρωτάς γενικά πράγματα
- Ρώτα το ΠΙΟ σημαντικό που λείπει
- Να ακούγεται ανθρώπινο (όχι ρομπότ)
- Μία μόνο ερώτηση

Παράδειγμα:
"Σε ενδιαφέρει περισσότερο κάμερα ή μπαταρία;"
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0.7
    )

    return response.choices[0].message.content.strip()  

def generate_recommendations(mode, conversation, user_id, client):

    print("ENTERED AI INTENT ENGINE", flush=True)

    print("STEP 1", flush=True)

    global CATEGORIES_CACHE

    if CATEGORIES_CACHE is None:
        print("STEP 2 - loading categories", flush=True)
        CATEGORIES_CACHE = [
        "accessories",
        "appliances",
        "automotive",
        "baby",
        "baby_kids",
        "bags",
        "beauty",
        "books",
        "computers",
        "electronics",
        "fashion",
        "fishing",
        "food",
        "furniture",
        "gaming",
        "garden",
        "hardware",
        "health",
        "home",
        "home_decor",
        "home_textiles",
        "jewelry",
        "kids",
        "kitchen",
        "music",
        "office",
        "other",
        "outdoor",
        "pets",
        "seasonal",
        "shoes",
        "smartphones",
        "sports",
        "stationery",
        "tools",
        "toys",
        "tv_audio",
        "watches"
    ]

    print("STEP 3 - after categories", flush=True)

    last_user = get_last_user_text(conversation)

    print("STEP 4 - got last_user:", last_user, flush=True)

    intent = ai_extract_search_intent(conversation, client)

    print("STEP 5 - intent built", flush=True)

    intent_type = intent.get("intent_type", "product_search")
    keywords_en = intent.get("search_keywords_en", "")
    keywords_gr = intent.get("search_keywords_gr", "")

    use_fallback = False

    if not keywords_en and not keywords_gr:
        print("FALLBACK TO BUILD DECISION PROFILE", flush=True)
        profile = build_decision_profile(conversation)
        use_fallback = True

    # =========================
    # SHOPPING SEARCH ENGINE
    # =========================
    
    print("AI INTENT:", intent, flush=True)

    full_text = get_full_conversation(conversation)
    last_user = get_last_user_text(conversation)

    web_info = ""

    # -----------------------------------------
    # KNOWLEDGE QUESTION
    # -----------------------------------------

    if intent_type == "knowledge_question":

        web_info = web_search_context(full_conversation(conversation))

        prompt = f"""
Ο χρήστης έκανε ερώτηση γνώσης.

Συνομιλία:
{full_conversation(conversation)}

Web πληροφορίες:
{web_info}

Απάντησε σαν expert σύμβουλος τεχνολογίας.
Αν υπάρχει νέο μοντέλο προϊόντος πες ποιο είναι το τελευταίο.
"""

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0.3
        )

        return {
            "reply": completion.choices[0].message.content.strip(),
            "links": [],
            "showButton": False
        }

    # -----------------------------------------
    # BUILD SEARCH QUERY
    # -----------------------------------------

    keywords_en = intent.get("search_keywords_en", "")
    keywords_gr = intent.get("search_keywords_gr", "")
    category = intent.get("category", "")

    search_parts = []

    if keywords_en:
        search_parts.append(keywords_en)

    if keywords_gr:
        search_parts.append(keywords_gr)

    if category:
        search_parts.append(category)

    search_text = " ".join(search_parts).strip()

    categories = CATEGORIES_CACHE

    # category_prompt = f"""
    # User search:
    # {search_text}

    # Available categories:
    # {", ".join(categories)}

    # Return ONLY the best matching category.
    # If none fits, return NONE.
    # """

    # category_response = client.chat.completions.create(
    #     model="gpt-4o-mini",
    #     messages=[{"role": "user", "content": category_prompt}],
    #     temperature=0
    # )


    print("AI FINAL SEARCH QUERY:", search_text, flush=True)

    # -----------------------------------------
    # AI CATEGORY RESOLUTION
    # -----------------------------------------

    resolved_category = intent.get("category") or ""

    print("RESOLVED CATEGORY:", resolved_category, flush=True)

    if not use_fallback:
        profile = {
            "query_text": search_text,
            "descriptive_tokens": search_text.split(),
            "numeric_tokens": re.findall(r"\d+", search_text),
            "budget_max": intent.get("budget_max"),
            "category": resolved_category if resolved_category else ""
        }
    print("PROFILE CATEGORY:", profile.get("category"), flush=True)
        # 🔥 AI decides if ready
    if not is_profile_complete_ai(profile):

        question = generate_next_question_ai(profile)

        return {
            "reply": question,
            "links": [],
            "showButton": False
        }
    candidates = fetch_products_from_db(mode, profile, limit=20)

    print("DB CANDIDATES:", len(candidates), flush=True)

    exact_match = False

    for p in candidates:
        if search_text.lower() in p["title"].lower():
            exact_match = True
            break

    similar_found = False

    if candidates and not exact_match:
        similar_found = True

    # -----------------------------------------
    # RELAXED SEARCH
    # -----------------------------------------

    if not candidates:

        print("TRYING SIMILAR PRODUCTS SEARCH", flush=True)

        tokens = search_text.split()

        relaxed_tokens = remove_color_tokens(tokens)

        if not relaxed_tokens:
            relaxed_tokens = tokens

        relaxed_query = " ".join(relaxed_tokens)

        relaxed_profile = {
            "query_text": relaxed_query,
            "descriptive_tokens": relaxed_query.split(),
            "numeric_tokens": [],
            "budget_max": intent.get("budget_max"),
            "category": intent.get("category")
        }
        # =========================
        # 🔥 VECTOR SEARCH (FIXED)
        # =========================

        query_text = search_text if search_text else last_user

        conn = get_db_connection()
        cur = conn.cursor()

        query = """
        SELECT title, price, url,
        ts_rank(search_vector, websearch_to_tsquery('simple', %s)) AS rank
        FROM products
        WHERE search_vector @@ websearch_to_tsquery('simple', %s)
        ORDER BY rank DESC
        LIMIT 20;
        """

        cur.execute(query, (query_text, query_text))
        vector_results = cur.fetchall()

        cur.close()
        conn.close()

        if vector_results and len(vector_results) > 0:

            links = []
            for r in vector_results:
                links.append({
                    "title": r[0],
                    "price": r[1],
                    "url": r[2]
                })

            print("🔥 VECTOR SEARCH HIT:", len(links), flush=True)

            return {
                "reply": "Βρήκα αυτά για σένα 👇",
                "links": links,
                "showButton": True
            }
        candidates = fetch_products_from_db(mode, relaxed_profile, limit=20)

        print("RELAXED RESULTS:", len(candidates), flush=True)

    # -----------------------------------------
    # FALLBACK WEB SEARCH
    # -----------------------------------------

    if candidates:

        similar_found = True
    else:
        similar_found = False

    if not candidates:

        print("NO DB RESULTS — USING WEB SEARCH", flush=True)

        web_info = web_search_context(search_text)

        prompt = f"""
Ο χρήστης θέλει να αγοράσει προϊόν.

Search query:
{search_text}

Web πληροφορίες:
{web_info}

Δώσε σύντομη συμβουλή αγοράς.
"""

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.4
        )

        reply = completion.choices[0].message.content.strip()

        return {
            "reply": reply,
            "links": [],
            "showButton": True
        }

    # -----------------------------------------
    # SCORING
    # -----------------------------------------

    scored = score_products(candidates, profile)

    if not scored:
        return {
            "reply": "Χρειάζομαι λίγο πιο συγκεκριμένες πληροφορίες.",
            "links": [],
            "showButton": False
        }

    top_results = scored[:5]

    products_context = "\n".join(
        [f"{i+1}. {p['title']} – {p['price']}€" for i,p in enumerate(top_results)]
    )

    message_note = ""

    if similar_found:
        message_note = "Δεν βρήκα ακριβώς το ίδιο προϊόν, αλλά αυτά είναι τα πιο κοντινά διαθέσιμα."

    explanation_prompt = f"""
    Είσαι προσωπικός σύμβουλος αγορών.

    {message_note}

    Ο χρήστης ψάχνει:
    {profile["query_text"]}

    Βρέθηκαν αυτά τα προϊόντα:

    {products_context}

    Κανόνες:

    - Κράτα την απάντηση σύντομη (2 προτάσεις)
    - Μίλα φυσικά σαν άνθρωπος
    - Πες ποιο ξεχωρίζει
    """


    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": explanation_prompt}],
        temperature=0.4
    )

    advisor_reply = completion.choices[0].message.content.strip()

    links = [
        {
            "title": f"{item['title']} – {item['price']}€",
            "url": item["url"]
        }
        for item in top_results
    ]

    print("RETURNING PRODUCTS:", len(links), flush=True)

    return {
        "reply": advisor_reply + "\n\nΠαρακάτω υπάρχουν και άλλες επιλογές 👇",
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